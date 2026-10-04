import threading
import time
import urllib.error

from app.services.tracker_control import TrackerControlClient


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.desired = {"revision": "revision-one", "blink_only": True}
        self.latest = {"status": "idle", "job_id": None}
        self.unavailable = False
        self.reserve_busy = False
        self.commit_failures = 0
        self.reserve_entered = None
        self.reserve_release = None

    def __call__(self, url, method="GET", payload=None):
        path = url.removeprefix("http://local")
        self.calls.append((path, method, payload))
        if self.unavailable:
            raise OSError("API unavailable")
        if path == "/v1/tracker-settings":
            return self.desired.copy()
        if path == "/v1/scan-jobs/latest":
            return self.latest.copy()
        if path == "/v1/tracker-settings/ack":
            return {"tracker_connected": True}
        if path == "/v1/page-turns/reserve":
            if self.reserve_entered:
                self.reserve_entered.set()
                assert self.reserve_release.wait(timeout=2)
            if self.reserve_busy:
                raise urllib.error.HTTPError(url, 409, "Camera busy", {}, None)
            self.latest = {
                "job_id": "job-one", "status": "reserved",
                "reservation_expires_at": time.time() + 5,
            }
            return self.latest.copy()
        if path.endswith("/commit"):
            if self.commit_failures:
                self.commit_failures -= 1
                raise TimeoutError("Response was lost")
            self.latest["status"] = "settling"
            return self.latest.copy()
        if path.endswith("/cancel"):
            self.latest["status"] = "cancelled"
            return self.latest.copy()
        if path == "/v1/scan-jobs/job-one":
            return self.latest.copy()
        raise AssertionError(f"Unexpected API call: {path}")


def make_client(api, *, clock=None, send=None):
    client = TrackerControlClient(
        "http://local/v1/auto-scans", eye_camera_index=1, camera_index=2,
        settle_seconds=1.5,
        send_command=send or (lambda command: api.calls.append(("SERIAL", command, None))),
        request=api, **({"clock": clock} if clock else {}),
    )
    client.poll_once()
    state = client.snapshot()
    client.applied(state.revision, state.blink_only)
    client.poll_once()
    return client


def finish_dispatch(client):
    assert client._dispatch_thread is not None
    client._dispatch_thread.join(timeout=2)
    assert not client._dispatch_thread.is_alive()


def test_camera_loop_must_apply_settings_before_ack_and_frame_stall_expires():
    api = FakeAPI()
    tick = [0.0]
    client = TrackerControlClient(
        "http://local", eye_camera_index=1, camera_index=2, settle_seconds=1.5,
        send_command=lambda command: None, request=api, clock=lambda: tick[0],
    )
    client.poll_once()
    state = client.snapshot()
    assert state.blink_only is True
    assert state.turns_blocked is True
    assert not any(path.endswith("/ack") for path, _, _ in api.calls)
    client.applied(state.revision, state.blink_only)
    client.poll_once()
    assert client.snapshot().turns_blocked is False
    api.desired = {"revision": "revision-two", "blink_only": False}
    client.poll_once()
    assert client.snapshot().turns_blocked is True
    assert client.snapshot().blink_only is False
    client.applied("revision-two", False)
    client.poll_once()
    assert client.snapshot().connected is True
    tick[0] = 3.0
    client.poll_once()
    assert client.snapshot().connected is False
    assert client.snapshot().turns_blocked is True


def test_reserve_precedes_serial_and_commit_and_guard_covers_completed_dispatch():
    api = FakeAPI()
    client = make_client(api)
    assert client.dispatch_flip()
    finish_dispatch(client)
    relevant = [path for path, _, _ in api.calls if path in {
        "/v1/page-turns/reserve", "SERIAL", "/v1/page-turns/job-one/commit",
    }]
    assert relevant == ["/v1/page-turns/reserve", "SERIAL", "/v1/page-turns/job-one/commit"]
    assert client.snapshot().turns_blocked
    client.poll_once()
    assert client.snapshot().turns_blocked
    api.latest["status"] = "accepted"
    client.poll_once()
    assert not client.snapshot().turns_blocked


def test_busy_race_denies_reservation_before_serial_and_offline_falls_back():
    api = FakeAPI()
    client = make_client(api)
    api.reserve_busy = True
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert not any(path == "SERIAL" for path, _, _ in api.calls)
    assert any("Page turn skipped" in message for _, message in client.drain_events())
    assert sum(path.endswith("/reserve") for path, _, _ in api.calls) == 1
    api.latest = {"job_id": "manual-job", "status": "framing"}
    client.poll_once()
    assert not client.dispatch_flip()
    api.unavailable = True
    client.poll_once()
    assert client.snapshot().blink_only is False
    assert client.snapshot().revision is None
    assert not client.dispatch_flip()


def test_serial_failure_cancels_reservation_then_can_retry_after_recovery():
    api = FakeAPI()

    def fail_serial(command):
        raise OSError("USB serial device unplugged")

    client = make_client(api, send=fail_serial)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert api.latest["status"] == "cancelled"
    assert not any(path.endswith("/commit") for path, _, _ in api.calls)
    client.poll_once()
    assert not client.snapshot().turns_blocked


def test_only_one_dispatch_and_lost_commit_response_never_repeats_hardware_send():
    api = FakeAPI()
    api.reserve_entered = threading.Event()
    api.reserve_release = threading.Event()
    api.commit_failures = 1
    client = make_client(api)
    assert client.dispatch_flip()
    assert api.reserve_entered.wait(timeout=1)
    assert not client.dispatch_flip()
    api.reserve_release.set()
    finish_dispatch(client)
    assert sum(path == "SERIAL" for path, _, _ in api.calls) == 1
    assert sum(path.endswith("/commit") for path, _, _ in api.calls) == 2


def test_shutdown_after_reserve_releases_it_without_sending_serial():
    api = FakeAPI()
    api.reserve_entered = threading.Event()
    api.reserve_release = threading.Event()
    client = make_client(api)
    assert client.dispatch_flip()
    assert api.reserve_entered.wait(timeout=1)
    client._stop.set()
    api.reserve_release.set()
    finish_dispatch(client)
    assert not any(path == "SERIAL" for path, _, _ in api.calls)
    assert api.latest["status"] == "cancelled"


def test_missing_or_near_expired_lease_never_sends_to_hardware():
    for expiry in (None, time.time() + 0.1, float("nan")):
        api = FakeAPI()

        def request(url, method="GET", payload=None, fake_api=api, lease_expiry=expiry):
            result = fake_api(url, method, payload)
            if url.endswith("/reserve"):
                result["reservation_expires_at"] = lease_expiry
            return result

        client = make_client(api)
        client._request = request
        assert client.dispatch_flip()
        finish_dispatch(client)
        assert not any(path == "SERIAL" for path, _, _ in api.calls)
        assert api.latest["status"] == "cancelled"


def test_busy_gestures_are_discarded_and_completion_requires_a_new_dispatch():
    api = FakeAPI()
    client = make_client(api)
    api.latest = {"job_id": "manual-job", "status": "transcribing"}
    client.poll_once()
    for _ in range(10):
        assert not client.dispatch_flip()
    assert not any(path.endswith("/reserve") for path, _, _ in api.calls)
    api.latest["status"] = "accepted"
    client.poll_once()
    # Completion only opens the gate; none of the ten old gestures is replayed.
    assert not client.snapshot().turns_blocked
    assert client._dispatch_thread is None
    assert not any(path.endswith("/reserve") for path, _, _ in api.calls)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert sum(path.endswith("/reserve") for path, _, _ in api.calls) == 1
    assert sum(path == "SERIAL" for path, _, _ in api.calls) == 1


def test_busy_reserve_conflict_is_never_retried_when_the_scan_finishes():
    api = FakeAPI()
    client = make_client(api)
    api.reserve_busy = True
    assert client.dispatch_flip()
    finish_dispatch(client)
    api.reserve_busy = False
    api.latest = {"job_id": "manual-job", "status": "accepted"}
    client.poll_once()
    assert not client.snapshot().turns_blocked
    assert sum(path.endswith("/reserve") for path, _, _ in api.calls) == 1
    assert not any(path == "SERIAL" for path, _, _ in api.calls)
    assert not any(path.endswith("/commit") for path, _, _ in api.calls)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert sum(path.endswith("/reserve") for path, _, _ in api.calls) == 2
    assert sum(path == "SERIAL" for path, _, _ in api.calls) == 1


def test_dispatch_drops_gesture_if_busy_poll_arrives_before_worker_reserves():
    api = FakeAPI()
    client = make_client(api)
    # The gesture was admitted, but its worker hasn't run yet. A manual scan
    # starts and the next status poll reaches us before the reservation call.
    client._dispatching = True
    api.latest = {"job_id": "manual-job", "status": "capturing"}
    client.poll_once()
    client._dispatch_flip()
    assert not any(path.endswith("/reserve") for path, _, _ in api.calls)
    assert not any(path == "SERIAL" for path, _, _ in api.calls)
    assert any("Page turn skipped" in message for _, message in client.drain_events())

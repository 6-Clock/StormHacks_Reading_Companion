import threading

from app.services.tracker_control import TrackerControlClient


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.desired = {"revision": "one", "blink_only": True}
        self.unavailable = False
        self.capture_error = False

    def __call__(self, url, method="GET", payload=None):
        path = url.removeprefix("http://local")
        self.calls.append((path, method, payload))
        if self.unavailable:
            raise OSError("API unavailable")
        if path == "/v1/tracker-settings":
            return self.desired.copy()
        if path == "/v1/tracker-settings/ack":
            return {"tracker_connected": True}
        if path == "/v1/scan-jobs":
            if self.capture_error:
                raise TimeoutError("Capture response lost")
            return {"job_id": "job-one", "status": "queued"}
        raise AssertionError(f"Unexpected API call: {path}")


def make_client(api, *, clock=None, send=None, camera_index=2):
    def completed_command(command):
        api.calls.append(("SERIAL", command, None))
        return True

    client = TrackerControlClient(
        "http://local", eye_camera_index=1, camera_index=camera_index,
        send_command=send or completed_command,
        request=api, **({"clock": clock} if clock else {}),
    )
    client.poll_once()
    client.applied("one", True)
    client.poll_once()
    return client


def finish_dispatch(client):
    client._dispatch_thread.join(timeout=2)
    assert not client._dispatch_thread.is_alive()


def test_mcu_completion_precedes_capture_without_additional_host_delay():
    api = FakeAPI()
    client = make_client(api)
    assert client.dispatch_flip()
    finish_dispatch(client)
    relevant = [(path, payload) for path, _, payload in api.calls
                if path in {"SERIAL", "/v1/scan-jobs"}]
    assert [path for path, _ in relevant] == ["SERIAL", "/v1/scan-jobs"]
    payload = relevant[1][1]
    assert payload["source"] == "automatic"
    assert payload["eye_camera_index"] == 1
    assert payload["camera_index"] == 2
    assert payload["trigger_id"]
    assert "settle_seconds" not in payload
    assert not client.snapshot().turns_blocked


def test_api_outage_does_not_gate_turns_or_reset_blink_mode():
    api = FakeAPI()
    tick = [0.0]
    client = make_client(api, clock=lambda: tick[0])
    tick[0] = 100
    assert not client.snapshot().connected
    assert not client.snapshot().turns_blocked
    api.unavailable = True
    client.poll_once()
    assert client.snapshot().blink_only
    assert client.snapshot().can_open_camera
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert sum(path == "SERIAL" for path, _, _ in api.calls) == 1
    assert any("completed; capture failed" in message for _, message in client.drain_events())


def test_camera_pause_blocks_until_release_then_allows_reopen():
    api = FakeAPI()
    client = make_client(api)
    api.desired["camera_pause_job_id"] = "job-one"
    client.poll_once()
    assert client.snapshot().should_pause
    assert not client.snapshot().can_open_camera
    assert not client.dispatch_flip()
    client.applied("one", True, eye_camera_state="released")
    client.poll_once()
    assert api.calls[-1][2]["eye_camera_state"] == "released"
    api.desired["camera_pause_job_id"] = None
    client.poll_once()
    assert client.snapshot().can_open_camera
    client.applied("one", True, eye_camera_state="open")
    assert client.dispatch_flip()
    finish_dispatch(client)


def test_serial_failure_never_requests_capture():
    api = FakeAPI()

    def send(command):
        raise OSError("USB serial unplugged")

    client = make_client(api, send=send)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert not any(path == "/v1/scan-jobs" for path, _, _ in api.calls)
    assert not client.snapshot().turns_blocked


def test_no_scan_camera_turns_without_a_dummy_job():
    api = FakeAPI()
    client = make_client(api, camera_index=None)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert sum(path == "SERIAL" for path, _, _ in api.calls) == 1
    assert not any(path == "/v1/scan-jobs" for path, _, _ in api.calls)


def test_preview_never_claims_a_hardware_send_or_requests_capture():
    api = FakeAPI()
    client = make_client(api, send=lambda command: False)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert not any(path == "/v1/scan-jobs" for path, _, _ in api.calls)
    assert any("preview; no serial command" in message for _, message in client.drain_events())


def test_capture_failure_never_retries_serial():
    api = FakeAPI()
    api.capture_error = True
    client = make_client(api)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert sum(path == "SERIAL" for path, _, _ in api.calls) == 1
    assert sum(path == "/v1/scan-jobs" for path, _, _ in api.calls) == 1
    assert not client.snapshot().turns_blocked


def test_single_dispatch_and_shutdown():
    api = FakeAPI()
    entered, release = threading.Event(), threading.Event()

    def send(command):
        entered.set()
        assert release.wait(timeout=2)
        api.calls.append(("SERIAL", command, None))
        return True

    client = make_client(api, send=send)
    assert client.dispatch_flip()
    assert entered.wait(timeout=1)
    assert not client.dispatch_flip()
    release.set()
    finish_dispatch(client)
    assert sum(path == "SERIAL" for path, _, _ in api.calls) == 1
    client.begin_shutdown()
    assert not client.dispatch_flip()
    assert not client.snapshot().can_open_camera


def test_settings_ack_comes_from_applied_camera_loop_without_action_gating():
    api = FakeAPI()
    client = TrackerControlClient(
        "http://local", eye_camera_index=1, camera_index=2,
        send_command=lambda command: None, request=api,
    )
    client.poll_once()
    assert not any(path.endswith("/ack") for path, _, _ in api.calls)
    client.applied("one", True)
    client.poll_once()
    assert client.snapshot().connected
    api.desired = {"revision": "two", "blink_only": False}
    client.poll_once()
    assert not client.snapshot().connected
    assert not client.snapshot().turns_blocked
    assert not client.snapshot().blink_only
    client.applied("two", False)
    client.poll_once()
    assert client.snapshot().connected


def test_capture_waits_for_mcu_completion_while_camera_loop_stays_responsive():
    api = FakeAPI()
    entered, done = threading.Event(), threading.Event()

    def send(command):
        entered.set()
        assert done.wait(timeout=2)
        return True

    client = make_client(api, send=send)
    assert client.dispatch_flip()
    assert entered.wait(timeout=1)
    assert client.snapshot().turns_blocked
    assert not client.dispatch_flip()
    client.poll_once()
    assert not any(path == "/v1/scan-jobs" for path, _, _ in api.calls)
    done.set()
    finish_dispatch(client)
    assert sum(path == "/v1/scan-jobs" for path, _, _ in api.calls) == 1
    assert any("acknowledged page-turn sequence completion" in message
               for _, message in client.drain_events())


def test_shutdown_cancels_pending_wait_and_never_captures():
    api = FakeAPI()
    entered, cancelled = threading.Event(), threading.Event()

    def send(command):
        entered.set()
        assert cancelled.wait(timeout=2)
        return True

    client = TrackerControlClient(
        "http://local", eye_camera_index=1, camera_index=2,
        send_command=send, cancel_command=cancelled.set, request=api,
    )
    client.applied("one", True)
    assert client.dispatch_flip()
    assert entered.wait(timeout=1)
    client.close()
    finish_dispatch(client)
    assert cancelled.is_set()
    assert not any(path == "/v1/scan-jobs" for path, _, _ in api.calls)


def test_missing_sender_confirmation_never_captures():
    api = FakeAPI()
    client = make_client(api, send=lambda command: None)
    assert client.dispatch_flip()
    finish_dispatch(client)
    assert not any(path == "/v1/scan-jobs" for path, _, _ in api.calls)

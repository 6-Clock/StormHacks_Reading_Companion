from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services import scan_jobs as scan_jobs_module
from app.services.scan_jobs import TERMINAL_STATUSES, ScanBusyError, ScanJobCoordinator


def fake_result(text="A freshly turned page."):
    return {"accepted": True, "reason": "openai_vision_accepted", "text": text,
            "metrics": {}, "capture_preview": None}


# Top-level targets exercise real spawned processes, including force termination.
def success_worker(config, messages, cancel, capture):
    messages.put({"kind": "progress", "status": "transcribing", "message": "Transcribing."})
    messages.put({"kind": "result",
                  "result": fake_result(f"Page from camera {config['camera_index']}.")})


def blocked_worker(config, messages, cancel, capture):
    messages.put({"kind": "progress", "status": "capturing", "message": "Camera read blocked."})
    time.sleep(60)


def framing_worker(config, messages, cancel, capture):
    messages.put({"kind": "progress", "status": "framing", "message": "Frame the page."})
    while not cancel.is_set() and not capture.wait(0.02):
        pass
    messages.put({"kind": "cancelled"} if cancel.is_set() else
                 {"kind": "result", "result": fake_result()})


def late_result_worker(config, messages, cancel, capture):
    messages.put({"kind": "progress", "status": "reviewing", "message": "Reviewing."})
    messages.put({"kind": "result", "result": fake_result()})
    time.sleep(60)  # Result exists, but the worker still owns camera resources.


@pytest.fixture
def coordinator_factory(monkeypatch):
    # Lifecycle tests need not wait eight seconds for every fake page turn.
    # Timing-specific tests below restore the production minimum explicitly.
    monkeypatch.setattr(scan_jobs_module, "MIN_PAGE_SETTLE_SECONDS", 0.0)
    instances = []

    def make(**kwargs):
        coordinator = ScanJobCoordinator(
            openai_api_key="test-key", openai_model="test-model",
            worker=kwargs.pop("worker", success_worker), cancellation_grace=0.1, **kwargs,
        )
        instances.append(coordinator)
        return coordinator

    yield make
    for coordinator in instances:
        coordinator.shutdown()


def wait_for(coordinator, job_id, statuses=TERMINAL_STATUSES):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = coordinator.get(job_id, include_result=True)
        if job["status"] in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Job never reached {statuses}: {coordinator.get(job_id)}")


def test_reservation_prevents_manual_scan_and_second_turn_before_hardware(coordinator_factory):
    coordinator = coordinator_factory()
    reserved = coordinator.reserve(trigger_id="flip-1", eye_camera_index=1,
                                   camera_index=2, settle_seconds=0)
    assert reserved["status"] == "reserved"
    assert reserved["reservation_expires_at"] > time.time()
    with pytest.raises(ScanBusyError):
        coordinator.enqueue(source="manual", camera_index=2)
    with pytest.raises(ScanBusyError):
        coordinator.reserve(trigger_id="flip-2", eye_camera_index=1,
                            camera_index=2, settle_seconds=0)
    assert coordinator.reserve(trigger_id="flip-1", eye_camera_index=1,
                               camera_index=2, settle_seconds=0)["job_id"] == reserved["job_id"]
    coordinator.commit(reserved["job_id"])
    coordinator.commit(reserved["job_id"])  # A network retry must not start another worker.
    assert wait_for(coordinator, reserved["job_id"])["status"] == "accepted"


def test_results_are_per_job_and_terminal_jobs_do_not_change(coordinator_factory):
    coordinator = coordinator_factory()
    first = coordinator.enqueue(camera_index=2, trigger_id="one")
    first_result = wait_for(coordinator, first["job_id"])
    second = coordinator.enqueue(camera_index=3, trigger_id="two")
    wait_for(coordinator, second["job_id"])
    assert coordinator.latest()["job_id"] == second["job_id"]
    assert "result" not in coordinator.latest()
    assert coordinator.get(first["job_id"], include_result=True) == first_result
    coordinator.cancel(first["job_id"])
    assert coordinator.get(first["job_id"], include_result=True) == first_result
    assert coordinator.enqueue(camera_index=2, trigger_id="one")["job_id"] == first["job_id"]
    with pytest.raises(ValueError):
        coordinator.enqueue(camera_index=3, trigger_id="one")


def test_cancel_stuck_camera_kills_worker_before_unlocking(coordinator_factory):
    coordinator = coordinator_factory(worker=blocked_worker)
    job = coordinator.enqueue(camera_index=2)
    wait_for(coordinator, job["job_id"], {"capturing"})
    runtime = coordinator._active
    assert runtime is not None
    assert coordinator.cancel(job["job_id"])["status"] == "cancelling"
    with pytest.raises(ScanBusyError):
        coordinator.enqueue(camera_index=2)
    completed = wait_for(coordinator, job["job_id"])
    assert completed["status"] == "cancelled"
    assert "result" not in completed
    # New work is accepted only once the process has been joined and gate released.
    assert coordinator.start_scan(camera_index=2)["status"] == "opening_camera"


def test_cancellation_wins_over_a_result_from_a_still_running_worker(coordinator_factory):
    coordinator = coordinator_factory(worker=late_result_worker)
    job = coordinator.enqueue(camera_index=2)
    wait_for(coordinator, job["job_id"], {"reviewing"})
    coordinator.cancel(job["job_id"])
    result = wait_for(coordinator, job["job_id"])
    assert result["status"] == "cancelled"
    assert "result" not in result


def test_browser_capture_and_framing_cancellation(coordinator_factory):
    coordinator = coordinator_factory(worker=framing_worker)
    job = coordinator.enqueue(camera_index=2)
    wait_for(coordinator, job["job_id"], {"framing"})
    coordinator.capture(job["job_id"])
    assert wait_for(coordinator, job["job_id"])["status"] == "accepted"
    next_job = coordinator.enqueue(camera_index=2)
    wait_for(coordinator, next_job["job_id"], {"framing"})
    coordinator.cancel(next_job["job_id"])
    assert wait_for(coordinator, next_job["job_id"])["status"] == "cancelled"


@pytest.mark.parametrize("kwargs", [
    {"stage_timeouts": {"capturing": 0.1}},
    {"overall_timeout": 0.3},
])
def test_deadlines_recover_a_blocked_worker(coordinator_factory, kwargs):
    coordinator = coordinator_factory(worker=blocked_worker, **kwargs)
    job = coordinator.enqueue(camera_index=2)
    assert wait_for(coordinator, job["job_id"])["status"] == "timed_out"
    assert coordinator._active is None


def test_expired_reservation_and_cancelled_settling_release_gate(coordinator_factory):
    coordinator = coordinator_factory(reservation_seconds=0.1)
    first = coordinator.reserve(trigger_id="expired", eye_camera_index=1,
                                camera_index=None, settle_seconds=0)
    assert wait_for(coordinator, first["job_id"])["status"] == "timed_out"
    assert coordinator.commit(first["job_id"])["status"] == "timed_out"
    second = coordinator.reserve(trigger_id="settling", eye_camera_index=1,
                                 camera_index=None, settle_seconds=1)
    coordinator.commit(second["job_id"])
    wait_for(coordinator, second["job_id"], {"settling"})
    coordinator.cancel(second["job_id"])
    assert wait_for(coordinator, second["job_id"])["status"] == "cancelled"
    third = coordinator.reserve(trigger_id="no-ocr", eye_camera_index=1,
                                camera_index=None, settle_seconds=0)
    coordinator.commit(third["job_id"])
    result = wait_for(coordinator, third["job_id"])
    assert result["status"] == "accepted"
    assert "result" not in result


def test_cancelled_reserve_holds_gate_through_dispatch_lease_and_settling(coordinator_factory):
    coordinator = coordinator_factory(reservation_seconds=0.2)
    started = time.monotonic()
    job = coordinator.reserve(trigger_id="cancel-before-delivery", eye_camera_index=1,
                              camera_index=None, settle_seconds=0.2)
    assert coordinator.cancel(job["job_id"])["status"] == "cancelling"
    # A tracker can still receive the original reservation response during its lease.
    assert coordinator.commit(job["job_id"])["status"] == "cancelling"
    time.sleep(0.25)
    with pytest.raises(ScanBusyError):
        coordinator.enqueue(camera_index=2)
    assert wait_for(coordinator, job["job_id"])["status"] == "cancelled"
    assert time.monotonic() - started >= 0.4


def test_cancelled_committed_turn_keeps_physical_settling_guard(coordinator_factory):
    coordinator = coordinator_factory()
    job = coordinator.reserve(trigger_id="turn-sent", eye_camera_index=1,
                              camera_index=None, settle_seconds=0.3)
    committed_at = time.monotonic()
    coordinator.commit(job["job_id"])
    wait_for(coordinator, job["job_id"], {"settling"})
    coordinator.cancel(job["job_id"])
    with pytest.raises(ScanBusyError):
        coordinator.enqueue(camera_index=2)
    assert wait_for(coordinator, job["job_id"])["status"] == "cancelled"
    assert time.monotonic() - committed_at >= 0.3


def test_result_arriving_between_empty_queue_and_worker_exit_is_kept(coordinator_factory):
    coordinator = coordinator_factory()

    class Messages(queue.Queue):
        def close(self):
            pass

    class ExitingProcess:
        pid = 1

        def __init__(self, *, args, **kwargs):
            self.messages = args[1]
            self.published = False

        def start(self):
            pass

        def is_alive(self):
            if not self.published:
                self.messages.put({"kind": "result", "result": fake_result()})
                self.published = True
            return False

        def join(self):
            pass

        def close(self):
            pass

    coordinator._context = SimpleNamespace(
        Event=threading.Event, Queue=Messages, Process=ExitingProcess,
    )
    job = coordinator.enqueue(camera_index=2)
    result = wait_for(coordinator, job["job_id"])
    assert result["status"] == "accepted"
    assert result["result"]["text"] == "A freshly turned page."


def test_duplicate_auto_text_keeps_reader_page(coordinator_factory):
    coordinator = coordinator_factory()
    for expected in ("accepted", "unchanged"):
        job = coordinator.enqueue(source="automatic", camera_index=2, eye_camera_index=1)
        assert wait_for(coordinator, job["job_id"])["status"] == expected


def test_api_aliases_share_lock_and_keep_specific_results(monkeypatch, coordinator_factory):
    coordinator = coordinator_factory(worker=framing_worker)
    monkeypatch.setattr(main, "scan_job_coordinator", coordinator)
    client = TestClient(main.app)
    response = client.post("/v1/scan-camera", json={"camera_index": 2})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert client.post("/v1/auto-scans", json={"trigger_id": "auto", "camera_index": 2,
                                              "eye_camera_index": 1}).status_code == 409
    assert client.post("/v1/page-turns/reserve", json={"trigger_id": "reserve",
                       "camera_index": 2, "eye_camera_index": 1}).status_code == 409
    wait_for(coordinator, job_id, {"framing"})
    assert client.post(f"/v1/scan-jobs/{job_id}/capture").status_code == 202
    wait_for(coordinator, job_id)
    result = client.get(f"/v1/scan-jobs/{job_id}?include_result=true")
    assert result.json()["result"]["text"] == "A freshly turned page."
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/v1/scan-jobs/missing").status_code == 404


def test_api_validates_cameras_and_configuration_before_camera_access(monkeypatch):
    coordinator = ScanJobCoordinator()
    monkeypatch.setattr(main, "scan_job_coordinator", coordinator)
    client = TestClient(main.app)
    assert client.post("/v1/scan-jobs", json={"camera_index": 2}).status_code == 503
    response = client.post("/v1/auto-scans", json={"trigger_id": "same-camera",
                            "camera_index": 1, "eye_camera_index": 1})
    assert response.status_code == 400
    assert "different camera" in response.json()["detail"]
    assert coordinator.latest()["status"] == "idle"
    coordinator.shutdown()


def test_every_busy_api_trigger_is_skipped_and_never_starts_later(
    monkeypatch, coordinator_factory,
):
    coordinator = coordinator_factory(worker=framing_worker)
    context = coordinator._context
    launched = []

    def track_process(**kwargs):
        launched.append(kwargs["args"][0]["source"])
        return context.Process(**kwargs)

    coordinator._context = SimpleNamespace(
        Event=context.Event, Queue=context.Queue, Process=track_process,
    )
    monkeypatch.setattr(main, "scan_job_coordinator", coordinator)
    client = TestClient(main.app)
    first = client.post("/v1/scan-jobs", json={"camera_index": 2, "source": "manual"})
    assert first.status_code == 202
    assert first.json()["status"] == "opening_camera"
    first_id = first.json()["job_id"]
    wait_for(coordinator, first_id, {"framing"})
    attempts = [
        ("/v1/scan-jobs", {"camera_index": 2, "source": source, "trigger_id": source})
        for source in ("manual", "test", "automatic")
    ] + [
        ("/v1/scan-camera", {"camera_index": 2}),
        ("/v1/auto-scans", {"camera_index": 2, "eye_camera_index": 1, "trigger_id": "legacy"}),
        ("/v1/page-turns/reserve", {"camera_index": 2, "eye_camera_index": 1,
                                   "trigger_id": "turn"}),
    ]
    for endpoint, request in attempts:
        skipped = client.post(endpoint, json=request)
        assert skipped.status_code == 409
        assert "skipped" in skipped.json()["detail"]
        assert "retry-after" not in skipped.headers
    assert list(coordinator._jobs) == [first_id]
    coordinator.capture(first_id)
    assert wait_for(coordinator, first_id)["status"] == "accepted"
    # Give the monitor several cycles: none of the skipped triggers may replay.
    time.sleep(0.2)
    assert launched == ["manual"]
    assert coordinator._active is None
    assert coordinator.latest()["job_id"] == first_id
    assert list(coordinator._jobs) == [first_id]
    # Only an explicit new request after completion starts the next scan.
    future = client.post("/v1/scan-jobs", json={"camera_index": 2, "source": "test"})
    assert future.status_code == 202
    future_id = future.json()["job_id"]
    wait_for(coordinator, future_id, {"framing"})
    assert launched == ["manual", "test"]
    coordinator.capture(future_id)
    assert wait_for(coordinator, future_id)["status"] == "unchanged"


def test_admission_does_not_wait_behind_a_locked_completion(coordinator_factory):
    coordinator = coordinator_factory()
    locked = threading.Event()
    release = threading.Event()

    def finish_with_lock():
        with coordinator._lock:
            locked.set()
            release.wait(2)

    thread = threading.Thread(target=finish_with_lock)
    thread.start()
    assert locked.wait(1)
    started = time.monotonic()
    try:
        with pytest.raises(ScanBusyError, match="skipped"):
            coordinator.start_scan(camera_index=2)
        with pytest.raises(ScanBusyError, match="skipped"):
            coordinator.reserve(trigger_id="busy-lock", eye_camera_index=1, camera_index=2)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        thread.join(2)
    assert coordinator.latest()["status"] == "idle"


def test_simultaneous_scan_sources_admit_exactly_one(coordinator_factory):
    coordinator = coordinator_factory(worker=framing_worker)
    barrier = threading.Barrier(3)
    accepted = []
    skipped = []

    def trigger(source):
        barrier.wait()
        try:
            accepted.append(coordinator.start_scan(source=source, camera_index=2))
        except ScanBusyError:
            skipped.append(source)

    threads = [threading.Thread(target=trigger, args=(source,))
               for source in ("manual", "test", "automatic")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert len(accepted) == 1
    assert len(skipped) == 2
    assert len(coordinator._jobs) == 1
    coordinator.cancel(accepted[0]["job_id"])
    wait_for(coordinator, accepted[0]["job_id"])


def test_page_turn_waits_eight_seconds_from_commit_before_any_camera_worker(
    monkeypatch, coordinator_factory,
):
    monkeypatch.setattr(scan_jobs_module, "MIN_PAGE_SETTLE_SECONDS", 8.0)
    tick = [100.0]
    coordinator = coordinator_factory(clock=lambda: tick[0])
    context = coordinator._context
    launched_at = []
    launched = threading.Event()

    def track_process(**kwargs):
        launched_at.append(tick[0])
        launched.set()
        return context.Process(**kwargs)

    coordinator._context = SimpleNamespace(
        Event=context.Event, Queue=context.Queue, Process=track_process,
    )
    reserved = coordinator.reserve(
        trigger_id="old-client", eye_camera_index=1, camera_index=2, settle_seconds=1.5,
    )
    assert reserved["settle_seconds"] == 8
    assert reserved["status"] == "reserved"
    assert "scan_starts_at" not in reserved
    # Serial dispatch takes four seconds; reservation time must not count toward settling.
    tick[0] = 104
    before_commit = time.time()
    committed = coordinator.commit(reserved["job_id"])
    assert committed["status"] == "settling"
    assert before_commit + 8 <= committed["scan_starts_at"] <= time.time() + 8
    assert coordinator.commit(reserved["job_id"])["scan_starts_at"] == committed["scan_starts_at"]
    for source in ("manual", "test", "automatic"):
        with pytest.raises(ScanBusyError, match="skipped"):
            coordinator.start_scan(source=source, camera_index=2)
    with pytest.raises(ScanBusyError, match="skipped"):
        coordinator.reserve(trigger_id="extra-turn", eye_camera_index=1, camera_index=2)
    tick[0] = 108  # Eight seconds after reservation is still only four after commit.
    assert not launched.wait(0.12)
    tick[0] = 111.999
    assert not launched.wait(0.12)
    assert coordinator.get(reserved["job_id"])["status"] == "settling"
    tick[0] = 112
    assert launched.wait(2)
    assert launched_at == [112]
    completed = wait_for(coordinator, reserved["job_id"])
    assert completed["status"] == "accepted"
    assert "scan_starts_at" not in completed
    time.sleep(0.12)
    assert launched_at == [112]  # Busy triggers were discarded, not replayed.
    future = coordinator.start_scan(source="manual", camera_index=2)
    assert future["settle_seconds"] == 0
    assert future["status"] == "opening_camera"
    assert "scan_starts_at" not in future
    assert wait_for(coordinator, future["job_id"])["status"] == "accepted"
    assert launched_at == [112, 112]


@pytest.mark.parametrize("endpoint,body,expected,status", [
    ("/v1/scan-jobs", {"source": "automatic", "settle_seconds": 1.5}, 8, "settling"),
    ("/v1/scan-jobs", {"source": "test"}, 8, "settling"),
    ("/v1/auto-scans", {"trigger_id": "legacy", "settle_seconds": 1.5}, 8, "settling"),
    ("/v1/auto-scans", {"trigger_id": "default"}, 8, "settling"),
    ("/v1/page-turns/reserve", {"trigger_id": "turn", "settle_seconds": 1.5}, 8, "reserved"),
    ("/v1/scan-jobs", {"source": "automatic", "settle_seconds": 30}, 30, "settling"),
    ("/v1/scan-jobs", {"source": "manual", "settle_seconds": 8}, 0, "opening_camera"),
    ("/v1/scan-camera", {"show_preview": True}, 0, "opening_camera"),
    ("/v1/scan-camera", {"show_preview": False}, 8, "settling"),
])
def test_api_enforces_post_turn_minimum_but_manual_capture_starts_immediately(
    monkeypatch, coordinator_factory, endpoint, body, expected, status,
):
    monkeypatch.setattr(scan_jobs_module, "MIN_PAGE_SETTLE_SECONDS", 8.0)
    tick = [100.0]
    coordinator = coordinator_factory(clock=lambda: tick[0])
    monkeypatch.setattr(main, "scan_job_coordinator", coordinator)
    client = TestClient(main.app)
    response = client.post(endpoint, json={"camera_index": 2, "eye_camera_index": 1, **body})
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == status
    assert job["settle_seconds"] == expected
    assert ("scan_starts_at" in job) is (status == "settling")
    coordinator.cancel(job["job_id"])
    # Expire the cancellation safety guard without running a camera/provider.
    tick[0] = 140
    assert wait_for(coordinator, job["job_id"])["status"] == "cancelled"

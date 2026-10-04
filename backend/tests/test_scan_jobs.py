from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest

from app.services.scan_jobs import ScanBusyError, ScanJobCoordinator


def captured_image():
    return {"data_url": "data:image/jpeg;base64,/9j/2Q==", "width": 640,
            "height": 360, "captured_at": 123.5, "page_detected": True}


# Spawn requires worker targets to be importable at module scope.
def success_worker(config, messages, cancel, capture):
    messages.put({"kind": "progress", "status": "capturing", "message": "Capturing."})
    messages.put({"kind": "result", "result": captured_image()})


def blocked_worker(config, messages, cancel, capture):
    messages.put({"kind": "progress", "status": "capturing", "message": "Blocked read."})
    time.sleep(60)


def framing_worker(config, messages, cancel, capture):
    first = {"data_url": "data:image/jpeg;base64,first", "width": 640,
             "height": 360, "captured_at": 1.0}
    last = {**first, "data_url": "data:image/jpeg;base64,last", "captured_at": 2.0}
    messages.put({"kind": "progress", "status": "framing", "message": "Frame the page."})
    messages.put({"kind": "frame", "frame": first})
    messages.put({"kind": "frame", "frame": last})
    while not cancel.is_set() and not capture.wait(0.02):
        pass
    if cancel.is_set():
        messages.put({"kind": "cancelled"})
    else:
        messages.put({"kind": "result", "result": captured_image()})


def failing_worker(config, messages, cancel, capture):
    messages.put({"kind": "error", "message": "Camera failed to open."})


class Tracker:
    def __init__(self):
        self.paused = None
        self.released = []
        self.release_check = None

    def request_camera_pause(self, job_id):
        self.paused = job_id

    def camera_pause_released(self, job_id):
        return True

    def clear_camera_pause(self, job_id):
        assert self.paused == job_id
        if self.release_check:
            self.release_check()
        self.paused = None
        self.released.append(job_id)


@pytest.fixture
def coordinator_factory():
    instances = []

    def make(**kwargs):
        coordinator = ScanJobCoordinator(
            worker=kwargs.pop("worker", success_worker), cancellation_grace=0.1, **kwargs,
        )
        instances.append(coordinator)
        return coordinator

    yield make
    for coordinator in instances:
        coordinator.shutdown()


def wait_for(coordinator, job_id, statuses=None):
    statuses = statuses or {"captured", "accepted", "rejected", "cancelled", "failed"}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = coordinator.get(job_id, include_result=True)
        if job["status"] in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Job never reached {statuses}: {coordinator.get(job_id)}")


def test_automatic_capture_needs_no_provider_or_settling_delay(coordinator_factory):
    coordinator = coordinator_factory()
    job = coordinator.start_scan(source="automatic", camera_index=2, eye_camera_index=1)
    assert job["status"] in {"waiting_for_eye_camera", "opening_camera"}
    result = wait_for(coordinator, job["job_id"])
    assert result["status"] == "captured"
    assert result["result"] == captured_image()
    assert "settle_seconds" not in result
    assert "reservation_expires_at" not in result


def test_capture_releases_tracker_and_next_camera_before_ocr(coordinator_factory):
    tracker = Tracker()
    coordinator = coordinator_factory(tracker_settings_store=tracker)
    first = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, first["job_id"], {"captured"})
    assert tracker.paused is None
    assert tracker.released == [first["job_id"]]
    second = coordinator.start_scan(camera_index=3)
    assert second["job_id"] != first["job_id"]
    wait_for(coordinator, second["job_id"], {"captured"})
    assert coordinator.get(first["job_id"])["status"] == "captured"
    assert tracker.released == [first["job_id"], second["job_id"]]


def test_same_camera_index_waits_for_actual_tracker_release(coordinator_factory):
    released = threading.Event()
    tracker = Tracker()
    tracker.camera_pause_released = lambda job_id: released.is_set()
    coordinator = coordinator_factory(tracker_settings_store=tracker)
    job = coordinator.start_scan(camera_index=2, eye_camera_index=2)
    assert job["status"] == "waiting_for_eye_camera"
    assert coordinator._active["process"] is None
    released.set()
    assert wait_for(coordinator, job["job_id"])["status"] == "captured"
    assert tracker.paused is None


def test_completed_text_is_published_and_rejected_capture_keeps_it(coordinator_factory):
    coordinator = coordinator_factory()
    first = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, first["job_id"], {"captured"})
    completed = coordinator.complete(first["job_id"], accepted=True,
                                     text="Accepted reading text.", reason="readable")
    assert completed["status"] == "accepted"
    assert completed["result"]["accepted"] is True
    previous = coordinator.get(first["job_id"], include_result=True)
    assert previous["result"]["text"] == "Accepted reading text."
    second = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, second["job_id"], {"captured"})
    rejected = coordinator.complete(second["job_id"], accepted=False, text="", reason="blurred")
    assert rejected["status"] == "rejected"
    assert rejected["result"]["reason"] == "blurred"
    assert coordinator.get(first["job_id"], include_result=True) == previous


def test_stale_completion_cannot_replace_current_page(coordinator_factory):
    coordinator = coordinator_factory()
    first = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, first["job_id"], {"captured"})
    second = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, second["job_id"], {"captured"})
    coordinator.complete(second["job_id"], accepted=True, text="Current page.", reason="readable")
    with pytest.raises(ValueError):
        coordinator.complete(first["job_id"], accepted=True, text="Old page.", reason="readable")
    result = coordinator.get(second["job_id"], include_result=True)["result"]
    assert result["text"] == "Current page."


def test_repeated_page_text_is_accepted_without_duplicate_suppression(coordinator_factory):
    coordinator = coordinator_factory()
    for _ in range(2):
        job = coordinator.start_scan(source="automatic", camera_index=2)
        wait_for(coordinator, job["job_id"], {"captured"})
        result = coordinator.complete(job["job_id"], accepted=True,
                                      text="The same words.", reason="readable")
        assert result["status"] == "accepted"


def test_cancel_pending_ocr_prevents_late_acceptance(coordinator_factory):
    coordinator = coordinator_factory()
    job = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, job["job_id"], {"captured"})
    assert coordinator.cancel(job["job_id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        coordinator.complete(job["job_id"], accepted=True, text="Late text.", reason="readable")


def test_completed_jobs_are_stable_when_cancelled(coordinator_factory):
    coordinator = coordinator_factory()
    job = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, job["job_id"], {"captured"})
    coordinator.complete(job["job_id"], accepted=True, text="Page.", reason="readable")
    before = coordinator.get(job["job_id"], include_result=True)
    coordinator.cancel(job["job_id"])
    assert coordinator.get(job["job_id"], include_result=True) == before


def test_cancel_stuck_capture_joins_worker_before_releasing_tracker(coordinator_factory):
    tracker = Tracker()
    coordinator = coordinator_factory(worker=blocked_worker, tracker_settings_store=tracker)
    job = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, job["job_id"], {"capturing"})
    process = coordinator._active["process"]
    assert process.is_alive()

    def require_worker_exit():
        assert not process.is_alive()
        assert process.exitcode is not None

    tracker.release_check = require_worker_exit
    assert coordinator.cancel(job["job_id"])["status"] == "cancelling"
    assert tracker.paused == job["job_id"]
    assert wait_for(coordinator, job["job_id"], {"cancelled"})["status"] == "cancelled"
    assert tracker.paused is None
    assert tracker.released == [job["job_id"]]
    tracker.release_check = None
    next_job = coordinator.start_scan(camera_index=2)
    assert next_job["status"] in {"waiting_for_eye_camera", "opening_camera"}


def test_browser_capture_uses_latest_preview_without_expiry(coordinator_factory):
    coordinator = coordinator_factory(worker=framing_worker)
    job = coordinator.start_scan(camera_index=2)
    wait_for(coordinator, job["job_id"], {"framing"})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        preview = coordinator.preview(job["job_id"])
        if preview["frame"] and preview["frame"]["captured_at"] == 2.0:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("Latest framing image was not published.")
    assert preview["frame"]["data_url"].endswith("last")
    with pytest.raises(ScanBusyError):
        coordinator.start_scan(camera_index=2)
    coordinator.capture(job["job_id"])
    assert wait_for(coordinator, job["job_id"], {"captured"})["status"] == "captured"


def test_worker_failure_is_reported_and_camera_is_released(coordinator_factory):
    tracker = Tracker()
    coordinator = coordinator_factory(worker=failing_worker, tracker_settings_store=tracker)
    job = coordinator.start_scan(camera_index=2)
    failed = wait_for(coordinator, job["job_id"], {"failed"})
    assert "Camera failed to open" in failed["message"]
    assert tracker.paused is None
    assert "result" not in failed


def test_result_queued_during_worker_exit_is_retained(coordinator_factory):
    coordinator = coordinator_factory()

    class Messages(queue.Queue):
        def close(self):
            pass

        def join_thread(self):
            pass

    class ExitingProcess:
        pid = 1
        exitcode = 0

        def __init__(self, *, args, **kwargs):
            self.messages = args[1]
            self.published = False

        def start(self):
            pass

        def is_alive(self):
            if not self.published:
                self.messages.put({"kind": "result", "result": captured_image()})
                self.published = True
            return False

        def join(self, timeout=None):
            pass

        def close(self):
            pass

    coordinator._context = SimpleNamespace(
        Event=threading.Event, Queue=Messages, Process=ExitingProcess,
    )
    job = coordinator.start_scan(camera_index=2)
    result = wait_for(coordinator, job["job_id"], {"captured"})
    assert result["result"] == captured_image()

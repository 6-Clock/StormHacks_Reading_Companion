"""One cancellable camera capture, followed by explicitly published model results."""

from __future__ import annotations

import copy
import multiprocessing
import queue
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.services.scan_worker import run_scan_worker
from app.services.tracker_settings import TrackerSettingsStore

TERMINAL_STATUSES = {"captured", "accepted", "rejected", "cancelled", "failed"}


class ScanBusyError(RuntimeError):
    pass


class ScanJobCoordinator:
    def __init__(
        self, *, worker: Callable[..., None] = run_scan_worker, cancellation_grace: float = 1.0,
        tracker_settings_store: TrackerSettingsStore | None = None,
    ) -> None:
        self._worker = worker
        self._cancellation_grace = cancellation_grace
        self._tracker_settings = tracker_settings_store or TrackerSettingsStore()
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._active: dict[str, Any] | None = None
        self._latest_id: str | None = None
        self._closed = False

    @staticmethod
    def _snapshot(job: dict[str, Any], include_result: bool = False) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in job.items()
                              if include_result or key != "result"})

    def _transition(self, job: dict[str, Any], status: str, message: str) -> None:
        now = time.time()
        job.update(status=status, message=message, updated_at=now)
        job["events"].append({"id": f"{job['job_id']}-{len(job['events'])}", "time": now,
                              "type": "scan_job", "message": message})

    def start_scan(
        self, *, source: str = "manual", camera_index: int,
        eye_camera_index: int | None = None, trigger_id: str | None = None,
    ) -> dict[str, Any]:
        if not 0 <= camera_index <= 10:
            raise ValueError("Camera index must be between 0 and 10.")
        with self._lock:
            if self._closed:
                raise RuntimeError("The scanner is shutting down.")
            if self._active is not None:
                raise ScanBusyError("A camera capture is already in progress.")
            job_id = uuid4().hex
            now = time.time()
            job = {"job_id": job_id, "trigger_id": trigger_id or job_id, "source": source,
                   "camera_index": camera_index, "eye_camera_index": eye_camera_index,
                   "queued_at": now, "updated_at": now, "events": []}
            runtime = {"job": job, "process": None, "messages": None,
                       "cancel": self._context.Event(), "capture": self._context.Event(),
                       "preview_frame": None, "cancel_since": None}
            self._tracker_settings.request_camera_pause(job_id)
            self._active = runtime
            self._jobs[job_id] = job
            self._latest_id = job_id
            while len(self._jobs) > 32:
                self._jobs.popitem(last=False)
            self._transition(job, "waiting_for_eye_camera", "Waiting for the eye camera release.")
            thread = threading.Thread(target=self._monitor, args=(runtime,), daemon=True,
                                      name=f"loob-capture-{job_id[:8]}")
            runtime["thread"] = thread
            thread.start()
            return self._snapshot(job)

    def capture(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job["status"] != "framing" or self._active is None:
                raise ScanBusyError("Capture is available while framing the page.")
            self._active["capture"].set()
            self._transition(job, "capturing", "Capture requested.")
            return self._snapshot(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job["status"] == "captured":
                self._transition(job, "cancelled", "Pending page transcription cancelled.")
            elif self._active is not None and self._active["job"] is job:
                self._request_cancel(self._active)
            return self._snapshot(job)

    def _request_cancel(self, runtime: dict[str, Any]) -> None:
        if runtime["cancel_since"] is None:
            runtime["cancel_since"] = time.monotonic()
            runtime["cancel"].set()
            self._transition(runtime["job"], "cancelling", "Releasing the camera.")

    def _finish(
        self, runtime: dict[str, Any], status: str, message: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        job = runtime["job"]
        job["completed_at"] = time.time()
        job["duration_ms"] = round((time.time() - job["queued_at"]) * 1000)
        if result is not None:
            job["result"] = result
        self._tracker_settings.clear_camera_pause(job["job_id"])
        if self._active is runtime:
            self._active = None
        self._transition(job, status, message)

    def _drain_messages(self, runtime: dict[str, Any], pending: dict[str, Any] | None):
        while True:
            try:
                event = runtime["messages"].get_nowait()
            except queue.Empty:
                return pending
            if event["kind"] == "frame":
                if runtime["job"]["status"] == "framing":
                    runtime["preview_frame"] = event["frame"]
            elif event["kind"] == "progress":
                self._transition(runtime["job"], event["status"], event["message"])
            else:
                pending = event

    def _monitor(self, runtime: dict[str, Any]) -> None:
        pending = None
        try:
            while True:
                time.sleep(0.02)
                with self._lock:
                    process = runtime["process"]
                    if runtime["cancel_since"] is not None:
                        if process is not None and process.is_alive():
                            elapsed = time.monotonic() - runtime["cancel_since"]
                            if elapsed >= self._cancellation_grace:
                                process.kill()
                            continue
                        if process is not None:
                            process.join()
                        self._finish(runtime, "cancelled", "Capture cancelled; camera released.")
                        return
                    if process is None:
                        job_id = runtime["job"]["job_id"]
                        if not self._tracker_settings.camera_pause_released(job_id):
                            continue
                        runtime["messages"] = self._context.Queue(maxsize=8)
                        process = self._context.Process(
                            target=self._worker,
                            args=({"camera_index": runtime["job"]["camera_index"],
                                   "source": runtime["job"]["source"]},
                                  runtime["messages"], runtime["cancel"], runtime["capture"]),
                            daemon=True,
                        )
                        runtime["process"] = process
                        self._transition(
                            runtime["job"], "opening_camera", "Opening the page camera.",
                        )
                        process.start()
                    pending = self._drain_messages(runtime, pending)
                    if process.is_alive():
                        continue
                    process.join()
                    pending = self._drain_messages(runtime, pending)
                    if pending is None:
                        self._finish(runtime, "failed", "Capture worker exited without a result.")
                    elif pending["kind"] == "result":
                        self._finish(runtime, "captured", "Page captured; ready for transcription.",
                                     pending["result"])
                    else:
                        status = "cancelled" if pending["kind"] == "cancelled" else "failed"
                        self._finish(runtime, status, pending.get("message", "Capture cancelled."))
                    return
        except Exception as error:
            process = runtime["process"]
            if process is not None and process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join()
            with self._lock:
                self._finish(runtime, "failed", f"Capture failed ({type(error).__name__}).")
        finally:
            if runtime["messages"] is not None:
                runtime["messages"].close()
            process = runtime["process"]
            if process is not None and process.pid is not None and not process.is_alive():
                process.close()

    def complete(
        self, job_id: str, *, accepted: bool, text: str, reason: str,
    ) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job_id != self._latest_id or job["status"] != "captured":
                raise ValueError("Only the current captured page can publish a transcription.")
            if accepted and not text.strip():
                raise ValueError("An accepted page needs readable text.")
            job["result"].update(accepted=accepted, text=text if accepted else "", reason=reason)
            job["summary"] = {"accepted": accepted, "reason": reason}
            self._transition(job, "accepted" if accepted else "rejected", reason)
            return self._snapshot(job, include_result=True)

    def latest(self, *, include_result: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._latest_id is None:
                return {"job_id": None, "trigger_id": None, "source": None, "status": "idle",
                        "message": "Ready for a page capture.", "queued_at": None,
                        "updated_at": time.time(), "events": []}
            return self._snapshot(self._jobs[self._latest_id], include_result)

    def get(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(self._jobs[job_id], include_result)

    def preview(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            runtime = self._active
            frame = None
            if runtime is not None and runtime["job"] is job and job["status"] == "framing":
                frame = copy.deepcopy(runtime["preview_frame"])
            return {"job_id": job_id, "status": job["status"],
                    "camera_index": job["camera_index"], "frame": frame}

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            runtime = self._active
            if runtime is not None:
                self._request_cancel(runtime)
        if runtime is not None:
            runtime["thread"].join(timeout=self._cancellation_grace + 5)

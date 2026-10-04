"""One cancellable camera job shared by manual scans and reserved page turns.

The camera lock is released only after the isolated worker has exited. Terminal
jobs retain their own results, so a newer job cannot overwrite an older request.
Busy requests are skipped, never retained or replayed. Run this local hardware
API with one Uvicorn worker.
"""

from __future__ import annotations

import copy
import hashlib
import multiprocessing
import queue
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.services.scan_worker import run_scan_worker

TERMINAL_STATUSES = {"accepted", "rejected", "unchanged", "cancelled", "failed", "timed_out"}
MIN_PAGE_SETTLE_SECONDS = 8.0
MAX_PAGE_SETTLE_SECONDS = 30.0
STAGE_TIMEOUTS = {
    "settling": MAX_PAGE_SETTLE_SECONDS + 5.0, "opening_camera": 15.0, "framing": 60.0,
    "capturing": 15.0, "transcribing": 55.0, "reviewing": 55.0, "preview": 8.0,
}


class ScanBusyError(RuntimeError):
    pass


BUSY_MESSAGE = (
    "A page turn or OCR scan is in progress; this request was skipped. "
    "Try again after it finishes."
)


class ScanJobCoordinator:
    def __init__(
        self, *, openai_api_key: str = "", openai_model: str = "",
        openai_revision_model: str = "", worker: Callable[..., None] = run_scan_worker,
        reservation_seconds: float = 5.0, cancellation_grace: float = 1.0,
        overall_timeout: float = 180.0, stage_timeouts: dict[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = {"openai_api_key": openai_api_key, "openai_model": openai_model,
                          "openai_revision_model": openai_revision_model}
        self._worker = worker
        self._clock = clock
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._active: dict[str, Any] | None = None
        self._latest_id: str | None = None
        self._last_text_hash: str | None = None
        self._reservation_seconds = reservation_seconds
        self._cancellation_grace = cancellation_grace
        self._overall_timeout = overall_timeout
        self._stage_timeouts = {**STAGE_TIMEOUTS, **(stage_timeouts or {})}
        self._closed = False

    @staticmethod
    def _snapshot(job: dict[str, Any], include_result: bool = False) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in job.items()
                              if include_result or key != "result"})

    def _transition(self, job: dict[str, Any], status: str, message: str, **fields: Any) -> None:
        now = time.time()
        if status != "settling":
            job.pop("scan_starts_at", None)
        job.update(status=status, message=message, updated_at=now, **fields)
        job["events"].append({"id": f"{job['job_id']}-{len(job['events'])}", "time": now,
                              "type": "scan_job", "message": message})
        if self._active is not None:
            self._active["stage_since"] = self._clock()
        print(f"[OCR {job['job_id'][:8]}] {status}: {message}", flush=True)

    def _create(
        self, *, source: str, camera_index: int | None, eye_camera_index: int | None,
        trigger_id: str | None, settle_seconds: float, reserved: bool,
    ) -> dict[str, Any]:
        if camera_index is not None and camera_index == eye_camera_index:
            raise ValueError("Eye tracking and OCR must use different camera indexes.")
        if not 0 <= settle_seconds <= MAX_PAGE_SETTLE_SECONDS:
            raise ValueError("Page settling must be between 0 and 30 seconds.")
        # Old clients may still send 0 or 1.5 seconds. The server owns this minimum.
        settle_seconds = (
            max(MIN_PAGE_SETTLE_SECONDS, settle_seconds)
            if source in {"automatic", "test"} or reserved else 0.0
        )
        if camera_index is not None and not 0 <= camera_index <= 10:
            raise ValueError("Camera index must be between 0 and 10.")
        # Admission must not wait behind completion/cleanup and then start an old
        # request. A caller retries only in response to a new user action/gesture.
        if not self._lock.acquire(blocking=False):
            raise ScanBusyError(BUSY_MESSAGE)
        try:
            if self._closed:
                raise RuntimeError("The scanner is shutting down.")
            if trigger_id:
                for job in self._jobs.values():
                    if job["trigger_id"] == trigger_id:
                        if (job["camera_index"], job["eye_camera_index"], job["source"],
                            job["settle_seconds"]) != (
                            camera_index, eye_camera_index, source, settle_seconds
                        ):
                            raise ValueError("That trigger ID already belongs to another request.")
                        return self._snapshot(job)
            if self._active is not None:
                raise ScanBusyError(BUSY_MESSAGE)
            # Fail before opening hardware or reserving a physical page turn.
            if camera_index is not None and not all(
                self._provider[key] for key in ("openai_api_key", "openai_model")
            ):
                raise RuntimeError(
                    "AI OCR needs OPENAI_API_KEY and OPENAI_OCR_MODEL in backend/.env."
                )
            job_id = uuid4().hex
            now = time.time()
            job = {"job_id": job_id, "trigger_id": trigger_id or job_id, "source": source,
                   "camera_index": camera_index, "eye_camera_index": eye_camera_index,
                   "settle_seconds": settle_seconds, "queued_at": now, "updated_at": now,
                   "events": []}
            if reserved:
                job["reservation_expires_at"] = now + self._reservation_seconds
            runtime = {"job": job, "process": None, "cancel": self._context.Event(),
                       "capture": self._context.Event(), "messages": None,
                       "stage_since": self._clock(), "created": self._clock(),
                       "scan_start_monotonic": None,
                       "cancel_since": None, "cancel_status": "cancelled", "thread": None}
            # A reserve response can already be in transit when Cancel is clicked.
            # Keep ownership until its final possible dispatch has had time to settle.
            runtime["guard_until"] = (
                runtime["created"] + self._reservation_seconds + settle_seconds
                if reserved else 0.0
            )
            self._active = runtime
            self._jobs[job_id] = job
            self._latest_id = job_id
            # Bound retained text/photos; active jobs are always the newest job.
            while len(self._jobs) > 32:
                self._jobs.popitem(last=False)
            if reserved:
                self._transition(
                    job, "reserved", "Page turn reserved; waiting for hardware command.",
                )
            else:
                self._start_stage(job)
            thread = threading.Thread(target=self._monitor, args=(runtime,), daemon=True,
                                      name=f"loob-ocr-{job_id[:8]}")
            runtime["thread"] = thread
            thread.start()
            return self._snapshot(job)
        finally:
            self._lock.release()

    def start_scan(self, *, source: str = "manual", camera_index: int,
                   eye_camera_index: int | None = None, trigger_id: str | None = None,
                   settle_seconds: float = 0) -> dict[str, Any]:
        """Start only if idle; busy requests are rejected without storing a job."""
        return self._create(source=source, camera_index=camera_index,
                            eye_camera_index=eye_camera_index, trigger_id=trigger_id,
                            settle_seconds=settle_seconds, reserved=False)

    # Compatibility for callers from older local code; this never queues work.
    enqueue = start_scan

    def _start_stage(self, job: dict[str, Any]) -> None:
        assert self._active is not None
        # For real turns this is called by commit, after serial dispatch. Reserving
        # ownership does not start the page-turn countdown.
        deadline = self._clock() + job["settle_seconds"]
        self._active["scan_start_monotonic"] = deadline
        if job["source"] == "automatic":
            self._active["guard_until"] = deadline
        if job["settle_seconds"] > 0 or job["camera_index"] is None:
            self._transition(
                job, "settling", f"Waiting {job['settle_seconds']:.1f}s for page to settle.",
                scan_starts_at=time.time() + job["settle_seconds"],
            )
        else:
            self._transition(job, "opening_camera", f"Opening camera {job['camera_index']}.")

    def reserve(self, *, trigger_id: str, eye_camera_index: int, camera_index: int | None,
                settle_seconds: float = MIN_PAGE_SETTLE_SECONDS) -> dict[str, Any]:
        return self._create(source="automatic", camera_index=camera_index,
                            eye_camera_index=eye_camera_index, trigger_id=trigger_id,
                            settle_seconds=settle_seconds, reserved=True)

    def commit(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job["status"] == "reserved":
                assert self._active is not None
                if self._clock() - self._active["created"] >= self._reservation_seconds:
                    self._request_cancel(
                        self._active, "timed_out",
                        "Page-turn reservation expired; waiting for safety delay.",
                    )
                    raise ValueError("Page-turn reservation expired; no scan was started.")
                self._start_stage(job)
            return self._snapshot(job)

    def capture(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job["status"] != "framing" or self._active is None:
                raise ScanBusyError("Capture is available while framing the page.")
            self._active["capture"].set()
            return self._snapshot(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job["status"] not in TERMINAL_STATUSES and self._active is not None:
                self._request_cancel(
                    self._active, "cancelled", "Cancelling scan and releasing camera.",
                )
            return self._snapshot(job)

    def _request_cancel(self, runtime: dict[str, Any], status: str, message: str) -> None:
        if runtime["cancel_since"] is None:
            runtime["cancel_status"] = status
            runtime["cancel_message"] = message
            runtime["cancel_since"] = self._clock()
            runtime["cancel"].set()
            self._transition(runtime["job"], "cancelling", message)

    def _finish(self, runtime: dict[str, Any], status: str, message: str,
                result: dict[str, Any] | None = None) -> None:
        job = runtime["job"]
        fields: dict[str, Any] = {"completed_at": time.time(),
                                 "duration_ms": round((time.time() - job["queued_at"]) * 1000)}
        if result is not None:
            fields["result"] = result
            fields["summary"] = {"accepted": result.get("accepted") is True,
                                  "reason": result.get("reason", ""),
                                  "word_count": len(str(result.get("text", "")).split())}
        self._transition(job, status, message, **fields)
        if self._active is runtime:
            self._active = None

    def _drain_messages(self, runtime: dict[str, Any], pending: dict[str, Any] | None):
        while True:
            try:
                event = runtime["messages"].get_nowait()
            except queue.Empty:
                return pending
            if event["kind"] == "progress":
                stage = event["status"]
                if stage in self._stage_timeouts:
                    self._transition(runtime["job"], stage, event["message"])
            else:
                pending = event

    def _monitor(self, runtime: dict[str, Any]) -> None:
        pending: dict[str, Any] | None = None
        try:
            while True:
                time.sleep(0.04)
                with self._lock:
                    job = runtime["job"]
                    if job["status"] in TERMINAL_STATUSES:
                        return
                    now = self._clock()
                    process = runtime["process"]
                    if runtime["cancel_since"] is not None:
                        if process is None or not process.is_alive():
                            if now < runtime["guard_until"]:
                                continue
                            if process is not None:
                                process.join()
                            status = runtime["cancel_status"]
                            message = ("Scan cancelled; camera released." if status == "cancelled"
                                       else runtime["cancel_message"])
                            self._finish(runtime, status, message)
                            return
                        if now - runtime["cancel_since"] >= self._cancellation_grace:
                            process.kill()
                        continue
                    if job["status"] == "reserved":
                        if now - runtime["created"] >= self._reservation_seconds:
                            self._request_cancel(
                                runtime, "timed_out",
                                "Page-turn reservation expired; waiting for safety delay.",
                            )
                        continue
                    if (now - runtime["created"] >= self._overall_timeout
                        or now - runtime["stage_since"] >= self._stage_timeouts.get(
                            job["status"], self._overall_timeout)):
                        self._request_cancel(
                            runtime, "timed_out",
                            f"Scan timed out during {job['status']}; releasing camera.",
                        )
                        continue
                    if process is None:
                        if now < runtime["scan_start_monotonic"]:
                            continue
                        if job["camera_index"] is None:
                            self._finish(
                                runtime, "accepted", "Page turn settled; blink detection ready.",
                            )
                            return
                        messages = self._context.Queue()
                        config = {**self._provider, "camera_index": job["camera_index"],
                                  "source": job["source"]}
                        process = self._context.Process(
                            target=self._worker,
                            args=(config, messages, runtime["cancel"], runtime["capture"]),
                            daemon=True, name=f"loob-ocr-worker-{job['job_id'][:8]}",
                        )
                        runtime.update(process=process, messages=messages)
                        self._transition(
                            job, "opening_camera", f"Opening camera {job['camera_index']}.",
                        )
                        process.start()
                    pending = self._drain_messages(runtime, pending)
                    if process.is_alive():
                        continue
                    process.join()
                    # A result can arrive between the first empty read and exit.
                    # Join guarantees the worker's queue feeder has flushed.
                    pending = self._drain_messages(runtime, pending)
                    if pending is None:
                        self._finish(runtime, "failed", "OCR worker exited without a result.")
                    elif pending["kind"] == "result":
                        result = pending["result"]
                        text = " ".join(str(result.get("text", "")).split())
                        fingerprint = hashlib.sha256(text.casefold().encode()).hexdigest()
                        status = (
                            "accepted" if result.get("accepted") is True and text else "rejected"
                        )
                        if result.get("reason") == "scan_cancelled":
                            status = "cancelled"
                        if status == "accepted":
                            if job["source"] != "manual" and fingerprint == self._last_text_hash:
                                status = "unchanged"
                            self._last_text_hash = fingerprint
                        message = {
                            "accepted": f"OCR accepted {len(text.split())} words.",
                            "rejected": f"OCR rejected: {result.get('reason', 'unreadable page')}.",
                            "unchanged": "OCR matches the previous page; reader page kept.",
                            "cancelled": "Scan cancelled; camera released.",
                        }[status]
                        self._finish(runtime, status, message, result)
                    else:
                        status = pending["kind"]
                        if status not in {"failed", "cancelled", "timed_out"}:
                            status = "failed"
                        self._finish(
                            runtime, status,
                            pending.get("message", "Scan cancelled; camera released."),
                        )
                    return
        except Exception as error:
            # Termination must precede unlocking even if the monitor itself fails.
            process = runtime["process"]
            if process is not None and process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join()
            with self._lock:
                self._finish(runtime, "failed", f"Could not run OCR ({type(error).__name__}).")
        finally:
            if runtime["messages"] is not None:
                runtime["messages"].close()
            process = runtime["process"]
            if process is not None and process.pid is not None and not process.is_alive():
                process.close()

    def latest(self, *, include_result: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._latest_id is None:
                return {"job_id": None, "trigger_id": None, "source": None, "status": "idle",
                        "message": "Ready for a page turn or scan.", "queued_at": None,
                        "updated_at": time.time(), "events": []}
            return self._snapshot(self._jobs[self._latest_id], include_result)

    def get(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(self._jobs[job_id], include_result)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            runtime = self._active
            if runtime is not None:
                self._request_cancel(runtime, "cancelled", "Server stopping; releasing camera.")
        if runtime is not None:
            safety_wait = max(0, runtime["guard_until"] - self._clock())
            runtime["thread"].join(timeout=safety_wait + self._cancellation_grace + 5)

"""Spawn-safe OCR worker. Camera handles and native windows live in this process."""

from __future__ import annotations

import queue
import time
from typing import Any

CAMERA_HANDOFF_TIMEOUT_SECONDS = 10.0


def run_scan_worker(config: dict[str, Any], messages: Any, cancel: Any, capture: Any) -> None:
    # Import the camera/vision dependencies only inside the worker.
    from app.services.book_scanner import ScanCancelled, scan_camera
    from app.services.camera_lease import CameraLease

    def progress(status: str, message: str) -> None:
        messages.put({"kind": "progress", "status": status, "message": message})

    def frame_callback(frame: dict[str, Any]) -> None:
        # Preview is optional and must never stall capture behind a slow reader.
        try:
            messages.put_nowait({"kind": "frame", "frame": frame})
        except (queue.Full, OSError, ValueError):
            pass

    camera_lease = CameraLease()
    try:
        progress("waiting_for_eye_camera", "Waiting for the eye camera to release.")
        handoff_deadline = time.monotonic() + CAMERA_HANDOFF_TIMEOUT_SECONDS
        while True:
            if cancel.is_set():
                raise ScanCancelled()
            if time.monotonic() >= handoff_deadline:
                raise TimeoutError(
                    "The eye camera did not release within 10 seconds; OCR was not started. "
                    "Stop the eye tracker or other camera task and try again."
                )
            if camera_lease.acquire():
                break
            cancel.wait(0.05)
        if cancel.is_set():
            raise ScanCancelled()
        progress("opening_camera", f"Opening camera {config['camera_index']}.")
        result = scan_camera(
            config["camera_index"],
            openai_api_key=config["openai_api_key"],
            openai_model=config["openai_model"],
            openai_revision_model=config["openai_revision_model"],
            show_preview=config["source"] == "manual",
            cancel_event=cancel,
            capture_event=capture,
            progress=progress,
            frame_callback=frame_callback if config["source"] == "manual" else None,
        )
        if cancel.is_set():
            raise ScanCancelled()
        messages.put({"kind": "result", "result": result})
    except ScanCancelled:
        messages.put({"kind": "cancelled"})
    except TimeoutError as error:
        messages.put({"kind": "timed_out", "message": str(error)})
    except RuntimeError as error:
        # Scanner RuntimeErrors contain deliberate, user-facing messages.
        messages.put({"kind": "failed", "message": str(error)})
    except Exception as error:
        # Provider exceptions may contain request details. Never serialize them.
        messages.put({"kind": "failed", "message": f"OCR worker failed ({type(error).__name__})."})
    finally:
        # scan_camera has unwound its capture handle before returning/raising.
        # If native camera code stalls, process termination releases the OS lock.
        camera_lease.release()

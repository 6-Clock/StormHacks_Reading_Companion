"""Spawn-safe OCR worker. Camera handles and native windows live in this process."""

from __future__ import annotations

from typing import Any


def run_scan_worker(config: dict[str, Any], messages: Any, cancel: Any, capture: Any) -> None:
    # Import the camera/vision dependencies only inside the worker.
    from app.services.book_scanner import ScanCancelled, scan_camera

    def progress(status: str, message: str) -> None:
        messages.put({"kind": "progress", "status": status, "message": message})

    try:
        result = scan_camera(
            config["camera_index"],
            openai_api_key=config["openai_api_key"],
            openai_model=config["openai_model"],
            openai_revision_model=config["openai_revision_model"],
            show_preview=config["source"] == "manual",
            cancel_event=cancel,
            capture_event=capture,
            progress=progress,
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

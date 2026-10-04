"""Spawn-safe camera worker; native capture calls can be cancelled by termination."""

from __future__ import annotations

import queue
from typing import Any


def run_scan_worker(config: dict[str, Any], messages: Any, cancel: Any, capture: Any) -> None:
    from app.services.book_scanner import ScanCancelled, scan_camera
    from app.services.camera_lease import CameraLease

    def progress(status: str, message: str) -> None:
        messages.put({"kind": "progress", "status": status, "message": message})

    def frame_callback(frame: dict[str, Any]) -> None:
        # Framing must not block capture when the browser reads slowly.
        try:
            messages.put_nowait({"kind": "frame", "frame": frame})
        except queue.Full:
            pass

    lease = CameraLease()
    try:
        while not lease.acquire():
            if cancel.wait(0.05):
                raise ScanCancelled()
        result = scan_camera(
            config["camera_index"], show_preview=config["source"] == "manual",
            cancel_event=cancel, capture_event=capture, progress=progress,
            frame_callback=frame_callback if config["source"] == "manual" else None,
        )
        if cancel.is_set():
            raise ScanCancelled()
        lease.release()
        messages.put({"kind": "result", "result": result})
    except ScanCancelled:
        messages.put({"kind": "cancelled"})
    except RuntimeError as error:
        messages.put({"kind": "failed", "message": str(error)})
    except Exception as error:
        messages.put({"kind": "failed", "message": f"Capture failed ({type(error).__name__})."})
    finally:
        lease.release()

"""Capture one corrected page JPEG, without OCR or model configuration."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import cv2

from app.services import camera_capture
from app.services.scan_preview import capture_preview


class ScanCancelled(Exception):
    pass


def _check_cancel(cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled()


def scan_camera(
    camera_index: int, *, show_preview: bool = True, cancel_event: Any = None,
    capture_event: Any = None, progress: Callable[[str, str], None] | None = None,
    frame_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    _check_cancel(cancel_event)
    if progress:
        progress("opening_camera", f"Opening camera {camera_index}.")
    camera = camera_capture.open_camera(camera_index)
    try:
        if not camera.isOpened():
            raise RuntimeError(f"Camera {camera_index} could not be opened.")
        if show_preview:
            if frame_callback is None or capture_event is None:
                raise ValueError("Manual framing needs a preview callback and capture event.")
            if progress:
                progress("framing", "Frame the page, then click Capture now.")
            last_preview_at = -float("inf")
            while not capture_event.is_set():
                _check_cancel(cancel_event)
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError(f"Camera {camera_index} did not return a frame.")
                now = time.monotonic()
                if now - last_preview_at >= 0.2:
                    frame_callback(capture_preview(
                        cv2.flip(frame, -1), max_edge=640, quality=65,
                    ))
                    last_preview_at = now
        _check_cancel(cancel_event)
        if progress:
            progress("capturing", "Selecting a sharp page frame.")
        frame = camera_capture.capture_best_frame(
            camera, check_cancel=lambda: _check_cancel(cancel_event),
        )
    finally:
        camera.release()
    _check_cancel(cancel_event)
    if frame is None:
        raise RuntimeError(f"Camera {camera_index} did not return a page frame.")
    # The page camera is mounted upside down.
    page, page_detected = camera_capture.detect_and_rectify_page(cv2.flip(frame, -1))
    result = capture_preview(page)
    result["page_detected"] = page_detected
    return result

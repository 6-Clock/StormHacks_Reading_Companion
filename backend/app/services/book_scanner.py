"""Adapter that makes the standalone camera OCR engine available to the API."""

from __future__ import annotations

import base64
import importlib.util
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from app.services.scan_preview import capture_preview

ENGINE_PATH = Path(__file__).resolve().parents[2] / "computer vision" / "book_ocr.py"
LIVE_PREVIEW_INTERVAL = 0.2
LIVE_PREVIEW_MAX_EDGE = 640
MAX_LIVE_PREVIEW_BYTES = 200 * 1024


class ScanCancelled(Exception):
    """Cancellation requested; unwind camera/window resources cooperatively."""


def _check_cancel(cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled()


def _open_scan_camera(engine: ModuleType, camera_index: int) -> Any:
    """Identify camera-open failures separately from display/preview failures."""
    camera = None
    try:
        camera = engine.open_camera(camera_index)
        if camera.isOpened():
            return camera
    except engine.cv2.error as error:
        if camera is not None:
            camera.release()
        raise RuntimeError(
            f"Camera {camera_index} failed to open. "
            "Check its connection and whether another app is using it."
        ) from error
    camera.release()
    raise RuntimeError(
        f"Camera {camera_index} could not be opened. "
        "Close other camera apps and check the selected camera index."
    )


def _live_preview_frame(engine: ModuleType, frame: Any, captured_at: float) -> dict[str, Any]:
    """Encode a bounded browser preview without altering the original OCR frame."""
    height, width = frame.shape[:2]
    scale = min(1.0, LIVE_PREVIEW_MAX_EDGE / max(width, height))
    preview = frame
    if scale < 1.0:
        preview = engine.cv2.resize(
            frame, (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=engine.cv2.INTER_AREA,
        )
    for quality in (65, 45, 30):
        ok, encoded = engine.cv2.imencode(
            ".jpg", preview, [engine.cv2.IMWRITE_JPEG_QUALITY, quality],
        )
        if not ok:
            raise RuntimeError("Could not prepare the live camera preview image.")
        data_url = "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")
        if len(data_url) <= MAX_LIVE_PREVIEW_BYTES:
            return {
                "data_url": data_url, "width": preview.shape[1], "height": preview.shape[0],
                "captured_at": captured_at,
            }
    raise RuntimeError("The live camera preview image exceeded its size limit.")


def _capture_with_calibration_preview(
    engine: ModuleType, camera_index: int, *, cancel_event: Any = None,
    capture_event: Any = None, progress: Callable[[str, str], None] | None = None,
    frame_callback: Callable[[dict[str, Any]], None] | None = None,
) -> list[Any] | None:
    """Let the reader frame the page before collecting the sharp OCR samples."""
    camera = _open_scan_camera(engine, camera_index)

    window_name = "LOOB OCR calibration — C: capture | Q: cancel"
    framing_started = time.monotonic()
    framing_announced = False
    last_preview_at = -float("inf")
    try:
        while True:
            _check_cancel(cancel_event)
            if time.monotonic() - framing_started > 60:
                raise TimeoutError("Page framing timed out after 60 seconds.")
            ok, frame = camera.read()
            captured_at = time.time()
            _check_cancel(cancel_event)
            if not ok:
                raise RuntimeError(
                    f"Camera {camera_index} did not return a frame. "
                    "Check the connection and selected camera index."
                )
            if not framing_announced:
                if progress:
                    instruction = "Frame the page in the live preview, then click Capture now."
                    if frame_callback is None:
                        instruction = (
                            "Frame the page; click Capture now or press C in the camera window."
                        )
                    progress("framing", instruction)
                framing_announced = True

            key = -1
            if frame_callback is not None:
                now = time.monotonic()
                if now - last_preview_at >= LIVE_PREVIEW_INTERVAL:
                    frame_callback(_live_preview_frame(engine, frame, captured_at))
                    last_preview_at = now
            else:
                preview = frame.copy()
                height, width = preview.shape[:2]
                top, bottom = int(height * 0.10), int(height * 0.90)
                left, right = int(width * 0.08), int(width * 0.92)
                engine.cv2.rectangle(preview, (left, top), (right, bottom), (255, 255, 0), 2)
                engine.cv2.putText(
                    preview,
                    "Center the page in the box, then press C to scan (Q cancels)",
                    (24, max(30, height - 28)),
                    engine.cv2.FONT_HERSHEY_SIMPLEX,
                    0.62,
                    (255, 255, 255),
                    2,
                    engine.cv2.LINE_AA,
                )
                engine.cv2.imshow(window_name, preview)
                key = engine.cv2.waitKey(1) & 0xFF
                if engine.cv2.getWindowProperty(window_name, engine.cv2.WND_PROP_VISIBLE) < 1:
                    return None
                if key in (ord("q"), 27):
                    return None
            _check_cancel(cancel_event)
            if key == ord("c") or (capture_event is not None and capture_event.is_set()):
                _check_cancel(cancel_event)
                if progress:
                    progress("capturing", "Capturing sharp page frames.")
                return engine.capture_best_frames(
                    camera, check_cancel=lambda: _check_cancel(cancel_event),
                )
    except engine.cv2.error as error:
        if frame_callback is not None:
            raise RuntimeError(
                f"Camera {camera_index} failed while preparing the live preview. "
                "Check its connection and try again."
            ) from error
        raise RuntimeError(
            "Could not open the OCR calibration window. "
            "Install opencv-python, not opencv-python-headless."
        ) from error
    finally:
        camera.release()
        if frame_callback is None:
            try:
                engine.cv2.destroyWindow(window_name)
            except engine.cv2.error:
                pass


@lru_cache
def _engine() -> ModuleType:
    """Load the OCR script once despite its legacy directory containing a space."""
    spec = importlib.util.spec_from_file_location("loob_book_ocr", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("The book OCR engine could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def scan_camera(
    camera_index: int,
    *,
    openai_api_key: str = "",
    openai_model: str = "",
    openai_revision_model: str = "",
    show_preview: bool = True,
    cancel_event: Any = None,
    capture_event: Any = None,
    progress: Callable[[str, str], None] | None = None,
    frame_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Capture a page and transcribe it with image-grounded AI OCR only."""
    web_framing = show_preview and frame_callback is not None
    if not web_framing and (not openai_api_key or not openai_model):
        raise RuntimeError(
            "AI OCR needs OPENAI_API_KEY and OPENAI_OCR_MODEL or OPENAI_MODEL in backend/.env."
        )
    _check_cancel(cancel_event)
    engine = _engine()
    if progress:
        progress("opening_camera", f"Opening camera {camera_index}.")
    if show_preview:
        frames = _capture_with_calibration_preview(
            engine, camera_index, cancel_event=cancel_event,
            capture_event=capture_event, progress=progress, frame_callback=frame_callback,
        )
        if frames is None:
            return {
                "accepted": False,
                "reason": "scan_cancelled",
                "text": "",
                "metrics": {},
                "openai_review": None,
                "openai_review_status": "not_requested",
                "openai_revision": None,
                "openai_revision_status": "not_requested",
                "capture_preview": None,
            }
    else:
        camera = _open_scan_camera(engine, camera_index)
        try:
            _check_cancel(cancel_event)
            if progress:
                progress("capturing", "Capturing sharp page frames.")
            frames = engine.capture_best_frames(
                camera, check_cancel=lambda: _check_cancel(cancel_event),
            )
        finally:
            camera.release()

    if not frames:
        return {
            "accepted": False,
            "reason": "no_camera_frames",
            "text": "",
            "metrics": {},
            "openai_review": None,
            "openai_review_status": "not_requested",
            "openai_revision": None,
            "openai_revision_status": "not_requested",
            "capture_preview": None,
        }

    _check_cancel(cancel_event)
    if not openai_api_key or not openai_model:
        raise RuntimeError(
            "OCR needs OPENAI_API_KEY and OPENAI_OCR_MODEL or OPENAI_MODEL in backend/.env. "
            "The camera preview works without these settings."
        )
    _, sharpness, brightness = engine.frame_quality(frames[0])
    page, page_detected = engine.detect_and_rectify_page(frames[0])
    if progress:
        progress("transcribing", "Transcribing the captured page.")
    review = engine.transcribe_page_with_openai(page, openai_api_key, openai_model)
    _check_cancel(cancel_event)
    if review.reason == "openai_request_timed_out":
        raise TimeoutError("OCR transcription timed out. Please try again.")
    if review.reason == "openai_request_failed":
        raise RuntimeError("OCR provider request failed. Check the API settings and connection.")
    print(
        "AI OCR transcription "
        f"model={openai_model} accepted={review.accepted} "
        f"reason={review.reason} confidence={review.confidence:.2f}"
    )
    revision = None
    review_status = "requested"
    revision_status = "not_requested"
    accepted = review.accepted
    text = review.text if review.accepted else ""
    reason = "openai_vision_accepted" if review.accepted else review.reason
    if review.accepted and engine.should_request_openai_revision(review):
        revision_status = "requested"
        if progress:
            progress("reviewing", "Checking uncertain text against the captured page.")
        revision = engine.revise_page_with_openai(
            page,
            review.text,
            openai_api_key,
            openai_revision_model or openai_model,
        )
        _check_cancel(cancel_event)
        if revision.reason == "openai_revision_timed_out":
            raise TimeoutError("OCR text review timed out. Please try again.")
        print(
            "AI OCR revision "
            f"model={openai_revision_model or openai_model} accepted={revision.accepted} "
            f"reason={revision.reason} confidence={revision.confidence:.2f}"
        )
        if revision.accepted:
            accepted = True
            text = revision.text
            reason = "openai_vision_revised"
        elif revision.reason not in {"openai_revision_request_failed", "invalid_openai_response"}:
            accepted = False
            text = ""
            reason = f"openai_revision_rejected;{revision.reason}"
    elif review.accepted:
        revision_status = "skipped_high_confidence"

    if progress:
        progress("preview", "Preparing the scan photo and word boxes.")
    preview = capture_preview(engine, page)
    _check_cancel(cancel_event)
    return {
        "accepted": accepted,
        "reason": reason,
        "text": text,
        "metrics": {
            "sharpness": round(sharpness, 2),
            "brightness": round(brightness, 2),
            "page_detected": page_detected,
            "vision_confidence": review.confidence,
        },
        "openai_review": asdict(review) if review else None,
        "openai_review_status": review_status,
        "openai_revision": asdict(revision) if revision else None,
        "openai_revision_status": revision_status,
        "capture_preview": preview,
    }

"""Adapter that makes the standalone camera OCR engine available to the API."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from app.services.scan_preview import capture_preview

ENGINE_PATH = Path(__file__).resolve().parents[2] / "computer vision" / "book_ocr.py"


def _capture_with_calibration_preview(engine: ModuleType, camera_index: int) -> list[Any] | None:
    """Let the reader frame the page before collecting the sharp OCR samples."""
    camera = engine.open_camera(camera_index)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError(
            f"Camera {camera_index} could not be opened. "
            "Close other camera apps and try another index."
        )

    window_name = "LOOB OCR calibration — C: capture | Q: cancel"
    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("The camera stopped sending frames. Reconnect it and try again.")

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
            if key == ord("c"):
                return engine.capture_best_frames(camera)
            if key in (ord("q"), 27):
                return None
    except engine.cv2.error as error:
        raise RuntimeError(
            "Could not open the OCR calibration window. "
            "Install opencv-python, not opencv-python-headless."
        ) from error
    finally:
        camera.release()
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
) -> dict[str, Any]:
    """Capture a page and transcribe it with image-grounded AI OCR only."""
    engine = _engine()
    if show_preview:
        frames = _capture_with_calibration_preview(engine, camera_index)
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
        camera = engine.open_camera(camera_index)
        if not camera.isOpened():
            camera.release()
            raise RuntimeError(
                f"Camera {camera_index} could not be opened. "
                "Close other camera apps and try another index."
            )
        try:
            frames = engine.capture_best_frames(camera)
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

    if not openai_api_key or not openai_model:
        raise RuntimeError(
            "AI OCR needs OPENAI_API_KEY and OPENAI_OCR_MODEL or OPENAI_MODEL in backend/.env."
        )

    _, sharpness, brightness = engine.frame_quality(frames[0])
    page, page_detected = engine.detect_and_rectify_page(frames[0])
    review = engine.transcribe_page_with_openai(page, openai_api_key, openai_model)
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
        revision = engine.revise_page_with_openai(
            page,
            review.text,
            openai_api_key,
            openai_revision_model or openai_model,
        )
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
        "capture_preview": capture_preview(engine, page),
    }

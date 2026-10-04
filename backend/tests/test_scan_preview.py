import base64
import threading
from dataclasses import dataclass
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.services import book_scanner
from app.services.scan_preview import capture_preview, normalized_words

TSV_HEADER = "level\tleft\ttop\twidth\theight\tconf\ttext\n"


def preview_engine(run_ocr):
    return SimpleNamespace(
        cv2=cv2,
        pytesseract=SimpleNamespace(run_and_get_output=run_ocr),
        LANG="eng",
    )


def test_preview_image_and_real_word_boxes_share_resized_coordinates():
    page = np.full((1600, 2400, 3), (30, 90, 210), dtype=np.uint8)
    observed = {}

    def local_ocr(rgb, **kwargs):
        observed.update(shape=rgb.shape, pixel=rgb[0, 0].tolist(), options=kwargs)
        return TSV_HEADER + "5\t120\t80\t240\t160\t94.5\tDoor\n"

    result = capture_preview(preview_engine(local_ocr), page)
    assert result["width"] == 1200
    assert result["height"] == 800
    jpeg = base64.b64decode(result["data_url"].split(",", 1)[1])
    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (800, 1200, 3)
    assert observed["shape"] == decoded.shape
    assert observed["pixel"] == [210, 90, 30]  # Tesseract receives RGB; JPEG uses BGR.
    assert observed["options"]["timeout"] == 2.0
    assert observed["options"]["extension"] == "tsv"
    assert result["boxes_status"] == "available"
    assert result["words"] == [{
        "text": "Door", "x": 0.1, "y": 0.1, "width": 0.2,
        "height": 0.2, "confidence": 0.945,
    }]
    assert page.shape == (1600, 2400, 3)
    assert page[0, 0].tolist() == [30, 90, 210]


def test_geometry_clips_bounds_and_discards_invalid_or_low_confidence_words():
    words = normalized_words(
        TSV_HEADER
        + "5\t-10\t-20\t30\t40\t80\tEdge\n"
        + "5\t90\t90\t30\t40\t90\tEnd\n"
        + "5\t100\t100\t20\t20\t90\tOutside\n"
        + "5\t10\t10\t-20\t20\t90\tNegative\n"
        + "5\t10\t10\t20\t20\t15\tUncertain\n"
        + "5\tNaN\t10\t20\t20\t90\tInvalid\n"
        + "5\t10\t10\t20\t20\tInfinity\tInvalid\n"
        + "4\t0\t0\t100\t100\t99\tNot a word\n",
        100,
        100,
    )
    assert len(words) == 2
    assert words[0] == {
        "text": "Edge", "x": 0, "y": 0, "width": 0.2,
        "height": 0.2, "confidence": 0.8,
    }
    assert words[1]["x"] + words[1]["width"] == 1
    assert words[1]["y"] + words[1]["height"] == 1


@pytest.mark.parametrize("error", [FileNotFoundError("No Tesseract"), RuntimeError("Timeout")])
def test_optional_local_ocr_failure_still_returns_captured_photo(error):
    def unavailable(*args, **kwargs):
        raise error

    result = capture_preview(preview_engine(unavailable), np.zeros((80, 120, 3), dtype=np.uint8))
    assert result["data_url"].startswith("data:image/jpeg;base64,")
    assert result["words"] == []
    assert result["boxes_status"] == "unavailable"


def test_successful_local_ocr_with_no_confident_words_is_explicit():
    engine = preview_engine(lambda *args, **kwargs: TSV_HEADER)
    result = capture_preview(engine, np.zeros((80, 120, 3), dtype=np.uint8))
    assert result["words"] == []
    assert result["boxes_status"] == "no_words"


@dataclass
class FakeReview:
    accepted: bool
    text: str = "AI-verified reading text."
    reason: str = "accepted"
    model: str = "test-vision"
    confidence: float = 0.97


@pytest.mark.parametrize("accepted", [True, False])
def test_scan_keeps_ai_text_and_captured_preview_even_for_rejected_page(monkeypatch, accepted):
    source_frame = np.zeros((160, 240, 3), dtype=np.uint8)
    corrected_page = np.full((80, 120, 3), 180, dtype=np.uint8)
    release_calls = []
    camera = SimpleNamespace(isOpened=lambda: True, release=lambda: release_calls.append(True))

    def transcribe(page, *args):
        assert page is corrected_page
        return FakeReview(accepted, reason="accepted" if accepted else "non_text")

    engine = preview_engine(lambda *args, **kwargs: TSV_HEADER + "5\t12\t8\t24\t16\t94\tLOCAL\n")
    engine.open_camera = lambda index: camera
    engine.capture_best_frames = lambda camera, **kwargs: [source_frame]
    engine.frame_quality = lambda frame: (1, 100, 180)
    engine.detect_and_rectify_page = lambda frame: (corrected_page, True)
    engine.transcribe_page_with_openai = transcribe
    engine.should_request_openai_revision = lambda review: False
    monkeypatch.setattr(book_scanner, "_engine", lambda: engine)

    result = book_scanner.scan_camera(
        0, openai_api_key="test-key", openai_model="test-vision", show_preview=False,
    )

    assert release_calls == [True]
    assert result["accepted"] is accepted
    assert result["text"] == ("AI-verified reading text." if accepted else "")
    assert result["capture_preview"]["width"] == 120
    assert result["capture_preview"]["height"] == 80
    assert result["capture_preview"]["words"][0]["text"] == "LOCAL"
    assert result["capture_preview"]["words"][0]["x"] == 0.1


def test_cancelled_calibration_has_no_captured_preview(monkeypatch):
    monkeypatch.setattr(book_scanner, "_engine", lambda: SimpleNamespace())
    monkeypatch.setattr(
        book_scanner, "_capture_with_calibration_preview", lambda *args, **kwargs: None,
    )

    result = book_scanner.scan_camera(0, openai_api_key="test", openai_model="test")

    assert result["reason"] == "scan_cancelled"
    assert result["capture_preview"] is None


def test_missing_provider_config_does_not_load_or_open_camera(monkeypatch):
    def camera_must_not_open():
        raise AssertionError("Camera was accessed before validating settings.")

    monkeypatch.setattr(book_scanner, "_engine", camera_must_not_open)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        book_scanner.scan_camera(2)


def calibration_engine(*, visible=1):
    released = []
    closed = []
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    camera = SimpleNamespace(
        isOpened=lambda: True, read=lambda: (True, frame),
        release=lambda: released.append(True),
    )
    cv = SimpleNamespace(
        error=cv2.error, FONT_HERSHEY_SIMPLEX=0, LINE_AA=0, WND_PROP_VISIBLE=0,
        rectangle=lambda *args: None, putText=lambda *args: None,
        imshow=lambda *args: None, waitKey=lambda *_: -1,
        getWindowProperty=lambda *args: visible,
        destroyWindow=lambda *_: closed.append(True),
    )
    engine = SimpleNamespace(cv2=cv, open_camera=lambda _: camera,
                             capture_best_frames=lambda *args, **kwargs: [frame])
    return engine, released, closed


def test_closing_native_calibration_cancels_and_releases_camera():
    engine, released, closed = calibration_engine(visible=0)
    assert book_scanner._capture_with_calibration_preview(engine, 2) is None
    assert released == closed == [True]


def test_browser_capture_works_without_focusing_native_window():
    engine, released, closed = calibration_engine()
    capture = threading.Event()
    capture.set()
    stages = []
    frames = book_scanner._capture_with_calibration_preview(
        engine, 2, capture_event=capture,
        progress=lambda stage, _: stages.append(stage),
    )
    assert len(frames) == 1
    assert stages == ["framing", "capturing"]
    assert released == closed == [True]


def test_calibration_deadline_releases_camera(monkeypatch):
    engine, released, closed = calibration_engine()
    times = iter([0, 61])
    monkeypatch.setattr(book_scanner.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError, match="60 seconds"):
        book_scanner._capture_with_calibration_preview(engine, 2)
    assert released == closed == [True]

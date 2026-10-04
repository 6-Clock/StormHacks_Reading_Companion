import ast
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


def web_preview_engine():
    frame = np.full((720, 1280, 3), (35, 90, 185), dtype=np.uint8)
    opened, released, captured = [], [], []
    camera = SimpleNamespace(
        isOpened=lambda: True, read=lambda: (True, frame),
        release=lambda: released.append(True),
    )

    def open_camera(index):
        opened.append(index)
        return camera

    def capture_best_frames(handle, *, check_cancel):
        assert handle is camera
        check_cancel()
        captured.append(handle)
        return [frame]

    # Deliberately expose no GUI API. Browser framing must not require HighGUI.
    cv = SimpleNamespace(
        error=cv2.error, resize=cv2.resize, imencode=cv2.imencode,
        INTER_AREA=cv2.INTER_AREA, IMWRITE_JPEG_QUALITY=cv2.IMWRITE_JPEG_QUALITY,
    )
    engine = SimpleNamespace(
        cv2=cv, open_camera=open_camera, capture_best_frames=capture_best_frames,
    )
    return engine, camera, frame, opened, released, captured


def test_browser_live_preview_and_capture_share_one_handle_without_native_window():
    engine, camera, source, opened, released, captured = web_preview_engine()
    capture = threading.Event()
    previews, stages = [], []

    def preview(frame):
        previews.append(frame)
        capture.set()

    frames = book_scanner._capture_with_calibration_preview(
        engine, 2, capture_event=capture, frame_callback=preview,
        progress=lambda stage, message: stages.append((stage, message)),
    )
    assert opened == [2]
    assert captured == [camera]
    assert released == [True]
    assert frames[0] is source
    assert source.shape == (720, 1280, 3)
    assert source[0, 0].tolist() == [35, 90, 185]
    assert [stage for stage, _ in stages] == ["framing", "capturing"]
    assert "live preview" in stages[0][1]
    assert previews[0]["width"] == 640
    assert previews[0]["height"] == 360
    assert previews[0]["captured_at"] > 0
    jpeg = base64.b64decode(previews[0]["data_url"].split(",", 1)[1])
    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (360, 640, 3)
    assert decoded[0, 0].tolist() == pytest.approx([35, 90, 185], abs=4)


def test_live_preview_is_throttled_and_keeps_original_frame_capture_time(monkeypatch):
    engine, camera, source, _, released, captured = web_preview_engine()
    capture = threading.Event()
    previews = []
    tick = [0.0]
    reads = []

    def read():
        tick[0] += 0.05
        reads.append(tick[0])
        if len(reads) == 10:
            capture.set()
        return True, source

    camera.read = read
    monkeypatch.setattr(book_scanner.time, "monotonic", lambda: tick[0])
    monkeypatch.setattr(book_scanner.time, "time", lambda: 1_000 + tick[0])
    book_scanner._capture_with_calibration_preview(
        engine, 2, capture_event=capture, frame_callback=previews.append,
    )
    assert len(reads) == 10
    assert 2 <= len(previews) <= 3
    times = [frame["captured_at"] for frame in previews]
    assert times[0] == 1_000.05
    assert all(b - a >= 0.199 for a, b in zip(times, times[1:], strict=False))
    assert released == [True]
    assert len(captured) == 1


def test_live_preview_limits_portrait_geometry_and_wire_size_without_mutating_source():
    engine, *_ = web_preview_engine()
    random = np.random.default_rng(12)
    frame = random.integers(0, 256, (1400, 900, 3), dtype=np.uint8)
    before = frame.copy()
    preview = book_scanner._live_preview_frame(engine, frame, 123.5)
    assert max(preview["width"], preview["height"]) <= 640
    assert preview["height"] == 640
    assert len(preview["data_url"]) <= book_scanner.MAX_LIVE_PREVIEW_BYTES
    assert preview["captured_at"] == 123.5
    np.testing.assert_array_equal(frame, before)


def test_web_preview_without_provider_key_can_be_cancelled_and_releases_camera(monkeypatch):
    engine, _, _, opened, released, captured = web_preview_engine()
    cancel = threading.Event()
    previews = []

    def preview(frame):
        previews.append(frame)
        cancel.set()

    monkeypatch.setattr(book_scanner, "_engine", lambda: engine)
    with pytest.raises(book_scanner.ScanCancelled):
        book_scanner.scan_camera(2, cancel_event=cancel, frame_callback=preview)
    assert opened == [2]
    assert len(previews) == 1
    assert released == [True]
    assert not captured


def test_capture_without_provider_key_fails_after_closing_the_preview_camera(monkeypatch):
    engine, _, _, opened, released, captured = web_preview_engine()
    capture = threading.Event()
    previews = []

    def preview(frame):
        previews.append(frame)
        capture.set()

    monkeypatch.setattr(book_scanner, "_engine", lambda: engine)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        book_scanner.scan_camera(2, capture_event=capture, frame_callback=preview)
    assert len(previews) == 1
    assert opened == [2]
    assert len(captured) == 1
    assert released == [True]


def test_missing_first_frame_never_claims_browser_preview_is_ready():
    engine, camera, _, _, released, captured = web_preview_engine()
    camera.read = lambda: (False, None)
    stages, previews = [], []
    with pytest.raises(RuntimeError, match="Camera 2 did not return a frame"):
        book_scanner._capture_with_calibration_preview(
            engine, 2, frame_callback=previews.append,
            progress=lambda stage, _: stages.append(stage),
        )
    assert not stages
    assert not previews
    assert not captured
    assert released == [True]


def test_camera_open_error_does_not_blame_the_native_display():
    engine, *_ = web_preview_engine()

    def failed_open(index):
        raise cv2.error("Failed camera backend")

    engine.open_camera = failed_open
    with pytest.raises(RuntimeError, match="Camera 2 failed to open"):
        book_scanner._capture_with_calibration_preview(engine, 2, frame_callback=lambda frame: None)


def camera_opener_for_test(platform, opened):
    """Load just the camera factory; don't import provider clients or open hardware."""
    calls, released, settings = [], [], []
    camera = SimpleNamespace(
        isOpened=lambda: opened, release=lambda: released.append(True),
        set=lambda prop, value: settings.append((prop, value)),
    )

    def video_capture(*args):
        calls.append(args)
        return camera

    cv = SimpleNamespace(
        VideoCapture=video_capture, CAP_DSHOW=700,
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_BUFFERSIZE=38,
    )
    parsed = ast.parse(book_scanner.ENGINE_PATH.read_text(encoding="utf-8"))
    function = next(node for node in parsed.body
                    if isinstance(node, ast.FunctionDef) and node.name == "open_camera")
    namespace = {"cv2": cv, "sys": SimpleNamespace(platform=platform)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "camera_factory", "exec"), namespace)
    return namespace["open_camera"], camera, calls, released, settings


def test_windows_camera_failure_releases_directshow_without_default_backend_fallback():
    open_camera, _, calls, released, settings = camera_opener_for_test("win32", False)
    with pytest.raises(RuntimeError, match="Windows DirectShow could not open camera 2"):
        open_camera(2)
    assert calls == [(2, 700)]
    assert released == [True]
    assert not settings


def test_windows_camera_success_uses_directshow_and_configures_capture():
    open_camera, camera, calls, released, settings = camera_opener_for_test("win32", True)
    assert open_camera(2) is camera
    assert calls == [(2, 700)]
    assert not released
    assert settings == [(3, 1280), (4, 720), (38, 1)]


def test_non_windows_camera_keeps_opencv_default_backend():
    open_camera, camera, calls, _, settings = camera_opener_for_test("linux", True)
    assert open_camera(1) is camera
    assert calls == [(1,)]
    assert settings == [(3, 1280), (4, 720), (38, 1)]

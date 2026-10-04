import base64
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.services import book_scanner, camera_capture
from app.services.scan_preview import capture_preview


def decode(result):
    encoded = base64.b64decode(result["data_url"].split(",", 1)[1])
    return cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)


@pytest.mark.parametrize("shape, expected", [
    ((1600, 2400, 3), (1333, 2000, 3)),
    ((2400, 1600, 3), (2000, 1333, 3)),
])
def test_capture_encodes_resized_jpeg_without_mutating_page(shape, expected):
    page = np.full(shape, (30, 90, 210), dtype=np.uint8)
    before = page.copy()
    result = capture_preview(page, captured_at=123.5)
    assert set(result) == {"data_url", "width", "height", "captured_at"}
    assert result["data_url"].startswith("data:image/jpeg;base64,")
    image = decode(result)
    assert image.shape == expected
    assert (result["height"], result["width"]) == expected[:2]
    assert image[0, 0].tolist() == pytest.approx([30, 90, 210], abs=4)
    assert result["captured_at"] == 123.5
    np.testing.assert_array_equal(page, before)


def test_small_capture_keeps_original_dimensions():
    result = capture_preview(np.zeros((80, 120, 3), dtype=np.uint8))
    assert decode(result).shape == (80, 120, 3)
    assert result["width"] == 120
    assert result["height"] == 80
    assert result["captured_at"] > 0


def test_preview_size_is_explicitly_configurable():
    result = capture_preview(
        np.full((720, 1280, 3), 180, dtype=np.uint8), max_edge=640, quality=70,
    )
    assert decode(result).shape == (360, 640, 3)


def test_camera_is_released_before_rectification_and_encoding(monkeypatch):
    source = np.arange(80 * 120 * 3, dtype=np.uint8).reshape((80, 120, 3))
    released = []
    camera = SimpleNamespace(isOpened=lambda: True, release=lambda: released.append(True))
    monkeypatch.setattr(camera_capture, "open_camera", lambda _: camera)
    monkeypatch.setattr(camera_capture, "capture_best_frame", lambda *args, **kwargs: source)

    def rectify(frame):
        assert released == [True]
        np.testing.assert_array_equal(frame, cv2.flip(source, -1))
        return frame, False

    monkeypatch.setattr(camera_capture, "detect_and_rectify_page", rectify)
    result = book_scanner.scan_camera(2, show_preview=False)
    assert released == [True]
    assert result["page_detected"] is False
    assert decode(result).shape == source.shape


def test_cancel_during_browser_framing_releases_camera(monkeypatch):
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    released = []
    camera = SimpleNamespace(isOpened=lambda: True, read=lambda: (True, frame),
                             release=lambda: released.append(True))
    monkeypatch.setattr(camera_capture, "open_camera", lambda _: camera)
    cancel = threading.Event()
    previews = []

    def preview(image):
        previews.append(image)
        cancel.set()

    with pytest.raises(book_scanner.ScanCancelled):
        book_scanner.scan_camera(2, cancel_event=cancel, capture_event=threading.Event(),
                                 frame_callback=preview)
    assert len(previews) == 1
    assert released == [True]


def test_failed_page_detection_preserves_full_frame_margins():
    frame = np.full((480, 640, 3), 160, dtype=np.uint8)
    page, detected = camera_capture.detect_and_rectify_page(frame)
    assert detected is False
    np.testing.assert_array_equal(page, frame)

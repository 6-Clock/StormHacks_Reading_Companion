"""Camera opening, sharp-frame selection and page perspective correction."""

from __future__ import annotations

import sys

import cv2
import numpy as np


def order_corners(points: np.ndarray) -> np.ndarray:
    """Return four page corners in top-left, top-right, bottom-right, bottom-left order."""
    points = points.astype("float32")
    ordered = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(differences)]
    ordered[3] = points[np.argmax(differences)]
    return ordered


def detect_and_rectify_page(frame: np.ndarray) -> tuple[np.ndarray, bool]:
    """Find a large quadrilateral page and correct its perspective when possible."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    min_page_area = height * width * 0.16
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(contour) < min_page_area:
            break
        perimeter = cv2.arcLength(contour, True)
        corners = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(corners) != 4 or not cv2.isContourConvex(corners):
            continue

        source = order_corners(corners.reshape(4, 2))
        top_width = np.linalg.norm(source[1] - source[0])
        bottom_width = np.linalg.norm(source[2] - source[3])
        left_height = np.linalg.norm(source[3] - source[0])
        right_height = np.linalg.norm(source[2] - source[1])
        target_width = max(int(max(top_width, bottom_width)), 1)
        target_height = max(int(max(left_height, right_height)), 1)
        if target_width < 200 or target_height < 200:
            continue

        destination = np.array(
            [[0, 0], [target_width - 1, 0],
             [target_width - 1, target_height - 1], [0, target_height - 1]],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(source, destination)
        return cv2.warpPerspective(frame, matrix, (target_width, target_height)), True

    return frame, False


def frame_quality(frame: np.ndarray) -> float:
    """Score frames by focus and usable exposure; larger scores are better."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    exposure_penalty = min(abs(brightness - 135.0) / 135.0, 0.85)
    return sharpness * (1.0 - exposure_penalty)


def capture_best_frame(
    cap: cv2.VideoCapture, sample_count: int = 10, *, check_cancel=None,
) -> np.ndarray | None:
    """Select a sharp, well-exposed frame from the camera's next samples."""
    best_frame = None
    best_score = -float("inf")
    for _ in range(sample_count):
        if check_cancel:
            check_cancel()
        ok, frame = cap.read()
        if ok:
            score = frame_quality(frame)
            if score > best_score:
                best_score, best_frame = score, frame
    return best_frame


def open_camera(camera_index: int) -> cv2.VideoCapture:
    """Use DirectShow on Windows; its default backend can hang during opening."""
    if sys.platform == "win32":
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"Windows DirectShow could not open camera {camera_index}. "
                "Close other camera apps or check the selected camera index."
            )
    else:
        cap = cv2.VideoCapture(camera_index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap




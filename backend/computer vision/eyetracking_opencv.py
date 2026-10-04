"""OpenCV + MediaPipe gaze and blink controller for the reading companion."""

from __future__ import annotations

import argparse
import csv
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR / "vendor_vision"))
sys.path.insert(0, str(PROJECT_DIR / "venv" / "Lib" / "site-packages"))

import cv2
import mediapipe as mp
import serial

from read_mode_state import ReadModeController


DEFAULT_CAMERA_INDEX = 1
DEFAULT_CAPTURE_WIDTH = 640
DEFAULT_CAPTURE_HEIGHT = 480
DEFAULT_CAPTURE_FPS = 30
EYE_OPEN_THRESHOLD = 0.18
CALIBRATION_SECONDS = 1.0
CAMERA_GAZE_X = (0.38, 0.62)
CAMERA_GAZE_Y = (0.30, 0.70)
FACE_CENTER_X = (0.38, 0.62)
MIN_FACE_WIDTH_PIXELS = 55
GAZE_SMOOTHING = 0.35

# (one-eye closure ratio, two-eye average closure ratio, sharp-drop ratio)
BLINK_SENSITIVITY = {
    "low": (0.55, 0.62, 0.20),
    "normal": (0.68, 0.75, 0.12),
    "high": (0.78, 0.85, 0.08),
}

LEFT_EYE = {"outer": 33, "inner": 133, "top": 159, "bottom": 145, "iris": 468}
RIGHT_EYE = {"outer": 263, "inner": 362, "top": 386, "bottom": 374, "iris": 473}


class LatestFrameReader:
    """Continuously drain the webcam and expose only its newest frame."""

    def __init__(self, capture: cv2.VideoCapture) -> None:
        self._capture = capture
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: tuple[int, float, object] | None = None
        self._frame_id = 0
        self.capture_fps = 0.0
        self.read_failures = 0
        self._last_capture_at: float | None = None

    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(target=self._read_forever, daemon=True, name="camera-reader")
        self._thread.start()

    def _read_forever(self) -> None:
        while self._running.is_set():
            ok, frame = self._capture.read()
            captured_at = time.monotonic()
            if not ok:
                self.read_failures += 1
                time.sleep(0.01)
                continue
            with self._lock:
                if self._last_capture_at is not None:
                    instant_fps = 1.0 / max(captured_at - self._last_capture_at, 1e-6)
                    self.capture_fps = instant_fps if self.capture_fps == 0.0 else self.capture_fps * 0.85 + instant_fps * 0.15
                self._last_capture_at = captured_at
                self._frame_id += 1
                self._latest = (self._frame_id, captured_at, frame)

    def newest(self) -> tuple[int, float, object] | None:
        with self._lock:
            if self._latest is None:
                return None
            frame_id, captured_at, frame = self._latest
            return frame_id, captured_at, frame.copy()

    def close(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._capture.release()


class SerialCommandSender:
    """Send newline-delimited commands to future book-flipper hardware."""

    def __init__(self, port: str | None, baud: int) -> None:
        self.connection: serial.Serial | None = None
        if port:
            self.connection = serial.Serial(port=port, baudrate=baud, timeout=1, write_timeout=1)
            print(f"Serial hardware connected: {port} at {baud} baud")
        else:
            print("Serial preview mode: use --port COM3 when hardware is connected.")

    def send(self, command: str) -> None:
        payload = f"{command}\n".encode("ascii")
        if self.connection is not None:
            self.connection.write(payload)
            self.connection.flush()
            print(f"[SERIAL TX] {command}")
        else:
            print(f"[SERIAL PREVIEW] {command}")

    def close(self) -> None:
        if self.connection is not None and self.connection.is_open:
            self.connection.close()


class GazeCalibration:
    """Per-user neutral iris-position calibration."""

    def __init__(self) -> None:
        self.center_x: float | None = None
        self.center_y: float | None = None
        self.smoothed_x: float | None = None
        self.smoothed_y: float | None = None

    def learn_center(self, gaze_x: float, gaze_y: float) -> None:
        if self.center_x is None or self.center_y is None:
            self.center_x, self.center_y = gaze_x, gaze_y
        else:
            self.center_x = self.center_x * 0.94 + gaze_x * 0.06
            self.center_y = self.center_y * 0.94 + gaze_y * 0.06

    def reset(self) -> None:
        self.center_x = self.center_y = None
        self.smoothed_x = self.smoothed_y = None

    def smooth_gaze(self, gaze_x: float, gaze_y: float) -> tuple[float, float]:
        if self.smoothed_x is None or self.smoothed_y is None:
            self.smoothed_x, self.smoothed_y = gaze_x, gaze_y
        else:
            self.smoothed_x = self.smoothed_x * (1 - GAZE_SMOOTHING) + gaze_x * GAZE_SMOOTHING
            self.smoothed_y = self.smoothed_y * (1 - GAZE_SMOOTHING) + gaze_y * GAZE_SMOOTHING
        return self.smoothed_x, self.smoothed_y


@dataclass(frozen=True)
class EyeReading:
    eyes_open: bool
    phase: str
    left_ratio: float
    right_ratio: float
    calibration_progress: float
    calibration_finished: bool
    sample_count: int


class RelativeBlinkDetector:
    """Detect eyelid closure relative to a per-user open-eye baseline."""

    def __init__(self, sensitivity: str) -> None:
        self.single_close_ratio, self.pair_close_ratio, self.sharp_drop_ratio = BLINK_SENSITIVITY[sensitivity]
        self.reset()

    def reset(self) -> None:
        self.left_baseline: float | None = None
        self.right_baseline: float | None = None
        self._calibration_started_at: float | None = None
        self._samples: list[tuple[float, float]] = []
        self._previous_average_ratio: float | None = None
        self._was_closed = False
        self.phase = "NO CALIBRATION"

    def begin_calibration(self, now: float) -> None:
        self._calibration_started_at = now
        self._samples = []
        self._previous_average_ratio = None
        self._was_closed = False
        self.phase = "CALIBRATING"

    def observe(self, now: float, left: float, right: float, *, eyes_visible: bool) -> EyeReading:
        calibration_finished = False
        if not eyes_visible:
            self._previous_average_ratio = None
            self._was_closed = False
            self.phase = "NO FACE"
            return EyeReading(False, self.phase, 0.0, 0.0, self._calibration_progress(now), False, len(self._samples))

        if self._calibration_started_at is not None and left >= 0.06 and right >= 0.06:
            self._samples.append((left, right))
        if self._calibration_started_at is not None and now - self._calibration_started_at >= CALIBRATION_SECONDS:
            if self._samples:
                left_values, right_values = zip(*self._samples)
                self.left_baseline = float(sorted(left_values)[len(left_values) // 2])
                self.right_baseline = float(sorted(right_values)[len(right_values) // 2])
                calibration_finished = True
                # Ratios before calibration used the fallback scale. Do not
                # compare that scale to the first calibrated frame.
                self._previous_average_ratio = None
            self._calibration_started_at = None

        left_ratio = left / max(self.left_baseline or EYE_OPEN_THRESHOLD, 1e-6)
        right_ratio = right / max(self.right_baseline or EYE_OPEN_THRESHOLD, 1e-6)
        average_ratio = (left_ratio + right_ratio) / 2.0
        sharp_drop = (
            self._previous_average_ratio is not None
            and self._previous_average_ratio - average_ratio >= self.sharp_drop_ratio
        )
        closure = (
            min(left_ratio, right_ratio) <= self.single_close_ratio
            or average_ratio <= self.pair_close_ratio
            or sharp_drop
        )
        if closure:
            self.phase = "CLOSED" if self._was_closed else "CLOSING"
        elif self._was_closed:
            self.phase = "REOPENING"
        elif self._calibration_started_at is not None:
            self.phase = "CALIBRATING"
        else:
            self.phase = "OPEN"
        self._was_closed = closure
        self._previous_average_ratio = average_ratio
        return EyeReading(
            eyes_open=not closure,
            phase=self.phase,
            left_ratio=left_ratio,
            right_ratio=right_ratio,
            calibration_progress=self._calibration_progress(now),
            calibration_finished=calibration_finished,
            sample_count=len(self._samples),
        )

    def _calibration_progress(self, now: float) -> float:
        if self._calibration_started_at is None:
            return 0.0
        return min(1.0, (now - self._calibration_started_at) / CALIBRATION_SECONDS)


def distance(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def eye_metrics(landmarks, indexes: dict[str, int]) -> tuple[float, float, float]:
    outer = landmarks[indexes["outer"]]
    inner = landmarks[indexes["inner"]]
    top = landmarks[indexes["top"]]
    bottom = landmarks[indexes["bottom"]]
    iris = landmarks[indexes["iris"]]
    width = max(distance(outer, inner), 1e-6)
    openness = distance(top, bottom) / width
    min_x, max_x = sorted((outer.x, inner.x))
    min_y, max_y = sorted((top.y, bottom.y))
    gaze_x = (iris.x - min_x) / max(max_x - min_x, 1e-6)
    gaze_y = (iris.y - min_y) / max(max_y - min_y, 1e-6)
    return openness, gaze_x, gaze_y


def draw_eye(frame, landmarks, indexes: dict[str, int], color) -> None:
    height, width = frame.shape[:2]
    for index in indexes.values():
        point = landmarks[index]
        cv2.circle(frame, (int(point.x * width), int(point.y * height)), 3, color, -1)


def text(frame, row: int, value: str, color=(255, 255, 255)) -> None:
    cv2.putText(frame, value, (18, 30 + row * 27), cv2.FONT_HERSHEY_SIMPLEX, 0.60, color, 2, cv2.LINE_AA)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=DEFAULT_CAMERA_INDEX)
    parser.add_argument("--port", help="Hardware serial port, for example COM3. Omit for preview mode.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--width", type=int, default=DEFAULT_CAPTURE_WIDTH, help="Requested camera width (default: 640).")
    parser.add_argument("--height", type=int, default=DEFAULT_CAPTURE_HEIGHT, help="Requested camera height (default: 480).")
    parser.add_argument("--fps", type=int, default=DEFAULT_CAPTURE_FPS, help="Requested camera FPS (default: 30).")
    parser.add_argument("--inference-width", type=int, default=640, help="Max width sent to MediaPipe (default: 640).")
    parser.add_argument("--blink-sensitivity", choices=tuple(BLINK_SENSITIVITY), default="normal")
    parser.add_argument("--diagnostics-csv", type=Path, help="Optional CSV path for per-frame blink diagnostics.")
    parser.add_argument(
        "--min-face-pixels",
        type=int,
        default=MIN_FACE_WIDTH_PIXELS,
        help="Minimum detected face width for reliable gaze gestures (default: 55).",
    )
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Camera index {args.camera} could not be opened. Try --camera 0 for the laptop camera.")
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    reader = LatestFrameReader(cap)
    reader.start()

    try:
        command_sender = SerialCommandSender(args.port, args.baud)
    except serial.SerialException as error:
        print(f"Could not open serial port {args.port}: {error}")
        reader.close()
        return 2

    diagnostic_file = None
    diagnostic_writer = None
    if args.diagnostics_csv:
        args.diagnostics_csv.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_file = args.diagnostics_csv.open("w", newline="", encoding="utf-8")
        diagnostic_writer = csv.writer(diagnostic_file)
        diagnostic_writer.writerow(("captured_at", "frame_age_ms", "capture_fps", "inference_fps", "left_open", "right_open", "left_ratio", "right_ratio", "phase", "face_px", "eyes_visible", "mode"))
        print(f"Writing blink diagnostics to {args.diagnostics_csv}")

    pending_commands: list[str] = []
    controller = ReadModeController(pending_commands.append)
    gaze_calibration = GazeCalibration()
    blink_detector = RelativeBlinkDetector(args.blink_sensitivity)
    blink_flash_until = 0.0
    previous_frame_id = 0
    inference_fps = 0.0
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.50,
        min_tracking_confidence=0.50,
    )

    try:
        while True:
            newest = reader.newest()
            if newest is None or newest[0] == previous_frame_id:
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                continue
            frame_id, captured_at, frame = newest
            previous_frame_id = frame_id
            now = captured_at
            frame = cv2.flip(frame, 1)
            analysis_frame = frame
            if args.inference_width > 0 and frame.shape[1] > args.inference_width:
                scale = args.inference_width / frame.shape[1]
                analysis_frame = cv2.resize(frame, (args.inference_width, max(1, int(frame.shape[0] * scale))))
            inference_started = time.monotonic()
            result = face_mesh.process(cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2RGB))
            inference_seconds = max(time.monotonic() - inference_started, 1e-6)
            instant_inference_fps = 1.0 / inference_seconds
            inference_fps = instant_inference_fps if inference_fps == 0.0 else inference_fps * 0.85 + instant_inference_fps * 0.15
            frame_age_ms = (inference_started - captured_at) * 1000.0

            eyes_visible = camera_gaze = False
            left_openness = right_openness = gaze_x = gaze_y = face_width_px = 0.0
            eye_reading = EyeReading(False, "NO FACE", 0.0, 0.0, 0.0, False, 0)
            if result.multi_face_landmarks:
                landmarks = result.multi_face_landmarks[0].landmark
                left_openness, left_gaze_x, left_gaze_y = eye_metrics(landmarks, LEFT_EYE)
                right_openness, right_gaze_x, right_gaze_y = eye_metrics(landmarks, RIGHT_EYE)
                raw_gaze_x = (left_gaze_x + right_gaze_x) / 2.0
                raw_gaze_y = (left_gaze_y + right_gaze_y) / 2.0
                gaze_x, gaze_y = gaze_calibration.smooth_gaze(raw_gaze_x, raw_gaze_y)

                left_cheek, right_cheek, nose = landmarks[234], landmarks[454], landmarks[1]
                face_center = (nose.x - left_cheek.x) / max(right_cheek.x - left_cheek.x, 1e-6)
                face_width_px = abs(right_cheek.x - left_cheek.x) * frame.shape[1]
                eyes_visible = face_width_px >= args.min_face_pixels
                camera_gaze = (
                    CAMERA_GAZE_X[0] <= gaze_x <= CAMERA_GAZE_X[1]
                    and CAMERA_GAZE_Y[0] <= gaze_y <= CAMERA_GAZE_Y[1]
                    and FACE_CENTER_X[0] <= face_center <= FACE_CENTER_X[1]
                )
                if min(left_openness, right_openness) >= 0.08 and camera_gaze and controller.mode == "STOP":
                    gaze_calibration.learn_center(gaze_x, gaze_y)
                eye_reading = blink_detector.observe(now, left_openness, right_openness, eyes_visible=eyes_visible)
                draw_eye(frame, landmarks, LEFT_EYE, (0, 255, 0) if camera_gaze else (0, 190, 255))
                draw_eye(frame, landmarks, RIGHT_EYE, (0, 255, 0) if camera_gaze else (0, 190, 255))
            else:
                eye_reading = blink_detector.observe(now, 0.0, 0.0, eyes_visible=False)

            if eye_reading.calibration_finished:
                print(f"[CALIBRATION] eye baseline left={blink_detector.left_baseline:.3f} right={blink_detector.right_baseline:.3f} ({eye_reading.sample_count} samples)")
            snapshot = controller.update(
                now,
                eyes_visible=eyes_visible,
                eyes_open=eyes_visible and eye_reading.eyes_open,
                looking_at_camera=camera_gaze,
            )
            if snapshot.blink_recorded:
                blink_flash_until = now + 0.35
                print(f"[BLINK] {snapshot.blink_count}/3 ({snapshot.last_blink_duration:.2f}s)")
            while pending_commands:
                command_sender.send(pending_commands.pop(0))
            if diagnostic_writer is not None:
                diagnostic_writer.writerow((f"{captured_at:.6f}", f"{frame_age_ms:.1f}", f"{reader.capture_fps:.1f}", f"{inference_fps:.1f}", f"{left_openness:.4f}", f"{right_openness:.4f}", f"{eye_reading.left_ratio:.3f}", f"{eye_reading.right_ratio:.3f}", eye_reading.phase, f"{face_width_px:.1f}", eyes_visible, snapshot.display_mode))

            mode_color = {"READ": (0, 220, 0), "STOP": (0, 0, 255), "SIGNAL": (255, 80, 255)}[snapshot.display_mode]
            text(frame, 0, f"MODE: {snapshot.display_mode}", mode_color)
            text(frame, 1, f"Camera {args.camera}: {frame.shape[1]}x{frame.shape[0]}  capture={reader.capture_fps:.1f} FPS  age={frame_age_ms:.0f}ms")
            text(frame, 2, f"MediaPipe={inference_fps:.1f} FPS  sensitivity={args.blink_sensitivity}  face={face_width_px:.0f}px")
            text(frame, 3, f"Camera gaze: {camera_gaze}  hold={snapshot.look_progress:.1f}/3.0s")
            blink_color = (0, 255, 0) if now < blink_flash_until else (255, 255, 255)
            text(frame, 4, f"Blink recorded: {snapshot.blink_count}/3  action=flip right", blink_color)
            phase_color = (0, 255, 0) if eye_reading.phase in {"CLOSING", "CLOSED", "REOPENING"} else (255, 255, 255)
            text(frame, 5, f"Eyelids: {eye_reading.phase}  raw L/R={left_openness:.3f}/{right_openness:.3f}", phase_color)
            text(frame, 6, f"Relative L/R={eye_reading.left_ratio:.2f}/{eye_reading.right_ratio:.2f}  close thresholds={blink_detector.single_close_ratio:.2f}/{blink_detector.pair_close_ratio:.2f}")
            if eye_reading.calibration_progress:
                text(frame, 7, f"Calibrating open eyes: {eye_reading.calibration_progress * 100:.0f}%", (0, 220, 255))
            elif snapshot.toggle_armed:
                text(frame, 7, "3-second toggle: ARMED", (0, 220, 0))
            else:
                text(frame, 7, "3-second toggle: look away briefly to RE-ARM", (0, 190, 255))
            text(frame, 8, "C=1s eye calibration  R=reset  Q/ESC=quit")

            cv2.imshow("OpenCV + MediaPipe Read Controller", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                controller.reset()
                gaze_calibration.reset()
                blink_detector.reset()
                print("[RESET] reading mode and calibration cleared")
            if key == ord("c") and eyes_visible:
                gaze_calibration.center_x, gaze_calibration.center_y = gaze_x, gaze_y
                blink_detector.begin_calibration(now)
                print(f"[CALIBRATION] center=({gaze_x:.3f}, {gaze_y:.3f}); keep eyes open for 1 second")
    finally:
        face_mesh.close()
        reader.close()
        cv2.destroyAllWindows()
        command_sender.close()
        if diagnostic_file is not None:
            diagnostic_file.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

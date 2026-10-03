"""Ver2: OpenCV + MediaPipe real-time gaze controller (no Beam)."""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR / "vendor_vision"))
sys.path.insert(0, str(PROJECT_DIR / "venv" / "Lib" / "site-packages"))

import cv2
import mediapipe as mp
import serial

from read_mode_state import ReadModeController


DEFAULT_CAMERA_INDEX = 1  # USB1 / second camera
EYE_OPEN_THRESHOLD = 0.18
CAMERA_GAZE_X = (0.38, 0.62)
CAMERA_GAZE_Y = (0.30, 0.70)
FACE_CENTER_X = (0.38, 0.62)
MIN_DOWN_GAZE_DELTA = 0.025

LEFT_EYE = {"outer": 33, "inner": 133, "top": 159, "bottom": 145, "iris": 468}
RIGHT_EYE = {"outer": 263, "inner": 362, "top": 386, "bottom": 374, "iris": 473}


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
    """Per-user iris calibration with automatic center learning."""

    def __init__(self) -> None:
        self.center_x: float | None = None
        self.center_y: float | None = None

    def learn_center(self, gaze_x: float, gaze_y: float) -> None:
        if self.center_x is None or self.center_y is None:
            self.center_x, self.center_y = gaze_x, gaze_y
        else:
            self.center_x = self.center_x * 0.94 + gaze_x * 0.06
            self.center_y = self.center_y * 0.94 + gaze_y * 0.06

    def reset_center(self) -> None:
        self.center_x = self.center_y = None

    def relative_position(self, gaze_x: float, gaze_y: float) -> tuple[float, float]:
        center_x = self.center_x if self.center_x is not None else 0.50
        center_y = self.center_y if self.center_y is not None else 0.50
        delta_x = gaze_x - center_x
        delta_y = gaze_y - center_y
        return delta_x, delta_y


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
    cv2.putText(frame, value, (18, 35 + row * 30), cv2.FONT_HERSHEY_SIMPLEX, 0.70, color, 2, cv2.LINE_AA)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=DEFAULT_CAMERA_INDEX)
    parser.add_argument("--port", help="Hardware serial port, for example COM3. Omit for preview mode.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--down-command",
        choices=("flip right", "flip left"),
        default="flip right",
        help="Command produced by looking slightly down and blinking twice.",
    )
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Camera index {args.camera} could not be opened. Try --camera 0 for the laptop camera.")
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    try:
        command_sender = SerialCommandSender(args.port, args.baud)
    except serial.SerialException as error:
        print(f"Could not open serial port {args.port}: {error}")
        cap.release()
        return 2

    controller = ReadModeController(command_sender.send)
    calibration = GazeCalibration()
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.60,
        min_tracking_confidence=0.60,
    )

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera frame read failed.")
                break

            now = time.monotonic()
            frame = cv2.flip(frame, 1)
            result = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

            eyes_visible = eyes_open = camera_gaze = lower_left = lower_right = False
            openness = gaze_x = gaze_y = delta_x = delta_y = 0.0
            if result.multi_face_landmarks:
                landmarks = result.multi_face_landmarks[0].landmark
                left = eye_metrics(landmarks, LEFT_EYE)
                right = eye_metrics(landmarks, RIGHT_EYE)
                # Use the less-open eye so either a two-eye blink or a
                # deliberate one-eye wink can produce a blink event.
                openness = min(left[0], right[0])
                gaze_x = (left[1] + right[1]) / 2.0
                gaze_y = (left[2] + right[2]) / 2.0
                eyes_visible = True
                eyes_open = openness >= EYE_OPEN_THRESHOLD

                left_cheek, right_cheek, nose = landmarks[234], landmarks[454], landmarks[1]
                face_center = (nose.x - left_cheek.x) / max(right_cheek.x - left_cheek.x, 1e-6)
                camera_gaze = (
                    eyes_open
                    and CAMERA_GAZE_X[0] <= gaze_x <= CAMERA_GAZE_X[1]
                    and CAMERA_GAZE_Y[0] <= gaze_y <= CAMERA_GAZE_Y[1]
                    and FACE_CENTER_X[0] <= face_center <= FACE_CENTER_X[1]
                )
                # Learn the user's neutral iris location while they hold a
                # straight camera gaze in STOP mode. Freeze it during READ.
                if eyes_open and camera_gaze and controller.mode == "STOP":
                    calibration.learn_center(gaze_x, gaze_y)
                delta_x, delta_y = calibration.relative_position(gaze_x, gaze_y)
                looking_down = eyes_open and delta_y >= MIN_DOWN_GAZE_DELTA
                # Reuse the shared directional state-machine channel, but the
                # gesture itself depends only on vertical iris movement.
                lower_left = looking_down and args.down_command == "flip left"
                lower_right = looking_down and args.down_command == "flip right"
                draw_eye(frame, landmarks, LEFT_EYE, (0, 255, 0) if camera_gaze else (0, 190, 255))
                draw_eye(frame, landmarks, RIGHT_EYE, (0, 255, 0) if camera_gaze else (0, 190, 255))

            snapshot = controller.update(
                now,
                eyes_visible=eyes_visible,
                eyes_open=eyes_open,
                looking_at_camera=camera_gaze,
                looking_lower_left=lower_left,
                looking_lower_right=lower_right,
            )
            mode_color = {"READ": (0, 220, 0), "STOP": (0, 0, 255), "SIGNAL": (255, 80, 255)}[snapshot.display_mode]
            text(frame, 0, f"MODE: {snapshot.display_mode}", mode_color)
            text(frame, 1, f"USB camera index: {args.camera}")
            text(frame, 2, f"Camera gaze: {camera_gaze}  hold={snapshot.look_progress:.1f}/3.0s")
            looking_down = lower_left or lower_right
            text(frame, 3, f"Iris looking down: {looking_down}  blink={snapshot.blink_count}/2  action={args.down_command}")
            text(frame, 4, f"Eyes: {'OPEN' if eyes_open else 'CLOSED / NOT FOUND'}  closed={snapshot.closed_duration:.1f}/10.0s")
            text(frame, 5, f"openness={openness:.3f} iris=({gaze_x:.2f},{gaze_y:.2f}) delta=({delta_x:+.2f},{delta_y:+.2f})")
            if snapshot.toggle_armed:
                toggle_text = "3-second toggle: ARMED"
                toggle_color = (0, 220, 0)
            else:
                toggle_text = "3-second toggle: look away briefly to RE-ARM"
                toggle_color = (0, 190, 255)
            text(frame, 6, toggle_text, toggle_color)
            text(frame, 7, "Calibration: look normally at the camera and press C")
            text(frame, 8, "Q/ESC: quit   R=reset modes")

            cv2.imshow("Ver2 OpenCV + MediaPipe Read Controller", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                controller.reset()
            if key == ord("c") and eyes_open:
                calibration.center_x, calibration.center_y = gaze_x, gaze_y
                print(f"[CALIBRATION] center=({gaze_x:.3f}, {gaze_y:.3f})")
    finally:
        face_mesh.close()
        cap.release()
        cv2.destroyAllWindows()
        command_sender.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

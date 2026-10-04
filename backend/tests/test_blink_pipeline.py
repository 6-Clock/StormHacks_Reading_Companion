"""Exercise the live detector's eyelid measurements through the flip controller.

These are 30 FPS landmark-measurement scenarios, not a camera/hardware test or
the UI's simulated three-blink button.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

VISION = Path(__file__).resolve().parents[1] / "computer vision"


def load_vision(name):
    spec = importlib.util.spec_from_file_location(f"pipeline_{name}", VISION / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


detector_module = load_vision("blink_detector")
mode_module = load_vision("read_mode_state")


class BlinkPipeline:
    def __init__(self, *, sensitivity="normal", blink_only=True):
        self.detector = detector_module.RelativeBlinkDetector(sensitivity)
        self.commands = []
        self.controller = mode_module.ReadModeController(self.commands.append)
        self.controller.set_blink_only(blink_only)
        self.frame = 0
        self.snapshots = []

    @property
    def now(self):
        return self.frame / 30

    def frames(self, count, *, left=0.26, right=0.26, visible=True, looking=False, busy=False):
        for _ in range(count):
            self.frame += 1
            eyes = self.detector.observe(self.now, left, right, eyes_visible=visible)
            snapshot = self.controller.update(
                self.now,
                eyes_visible=visible,
                eyes_open=visible and eyes.eyes_open,
                looking_at_camera=looking,
                calibrated=self.detector.calibrated,
                turns_blocked=busy,
            )
            self.snapshots.append(snapshot)
        return self.snapshots[-1]

    def calibrate(self):
        self.detector.begin_calibration(self.now)
        self.frames(32)
        assert self.detector.calibrated
        assert not self.commands

    def blink(self, *, closed_frames=3, busy=False, visible=True):
        self.frames(closed_frames, left=0.08, right=0.08, busy=busy, visible=visible)
        return self.frames(5, busy=busy, visible=visible)


@pytest.mark.parametrize("sensitivity", ["low", "normal", "high"])
@pytest.mark.parametrize("blink_only", [False, True])
def test_calibrated_measurements_emit_once_only_after_third_blink(sensitivity, blink_only):
    pipeline = BlinkPipeline(sensitivity=sensitivity, blink_only=blink_only)
    pipeline.calibrate()
    if not blink_only:
        pipeline.frames(95, looking=True)
        assert pipeline.controller.mode == "READ"

    assert pipeline.blink().blink_count == 1
    assert not pipeline.commands
    assert pipeline.blink().blink_count == 2
    assert not pipeline.commands
    pipeline.blink()
    assert pipeline.commands == ["flip right"]
    third = next(snapshot for snapshot in pipeline.snapshots if snapshot.blink_count == 3)
    assert third.display_mode == "SIGNAL" and third.blink_recorded

    # Three more physical closure/reopen patterns during the guard add no turns.
    for _ in range(3):
        pipeline.blink()
    assert pipeline.commands == ["flip right"]


def test_normal_mode_needs_gaze_but_blink_only_needs_no_camera_gaze():
    pipeline = BlinkPipeline(blink_only=False)
    pipeline.calibrate()
    for _ in range(3):
        pipeline.blink()
    assert not pipeline.commands
    pipeline.controller.set_blink_only(True)
    for _ in range(3):
        pipeline.blink()
    assert pipeline.commands == ["flip right"]


def test_one_frame_natural_blinks_are_detected_at_30_fps():
    pipeline = BlinkPipeline()
    pipeline.calibrate()
    for _ in range(3):
        pipeline.blink(closed_frames=1)
    assert pipeline.commands == ["flip right"]


def test_expired_two_second_window_requires_three_new_blinks():
    pipeline = BlinkPipeline()
    pipeline.calibrate()
    pipeline.blink()
    pipeline.blink()
    pipeline.frames(65)
    assert pipeline.blink().blink_count == 1
    assert not pipeline.commands
    pipeline.blink()
    pipeline.blink()
    assert pipeline.commands == ["flip right"]


@pytest.mark.parametrize("interruption", ["lost_face", "busy", "calibrating", "long_closure"])
def test_invalid_interruption_discards_partial_sequence(interruption):
    pipeline = BlinkPipeline()
    pipeline.calibrate()
    pipeline.blink()
    pipeline.blink()

    if interruption == "lost_face":
        # E.g. reflected glasses make the eye landmarks unavailable. Visibility
        # loss is supplied by the camera layer; this test cannot classify glare.
        pipeline.blink(visible=False)
    elif interruption == "busy":
        pipeline.blink(busy=True)
    elif interruption == "calibrating":
        pipeline.calibrate()
    else:
        pipeline.blink(closed_frames=25)  # 0.83s exceeds a deliberate blink.

    assert not pipeline.commands
    pipeline.frames(1)  # Visible, unblocked open eyes rearm a complete gesture.
    assert pipeline.blink().blink_count == 1
    assert not pipeline.commands
    pipeline.blink()
    pipeline.blink()
    assert pipeline.commands == ["flip right"]


def test_calibration_requires_multiple_visible_open_eye_samples():
    pipeline = BlinkPipeline()
    pipeline.detector.begin_calibration(pipeline.now)
    pipeline.frames(32, visible=False)
    pipeline.frames(1)
    assert not pipeline.detector.calibrated
    for _ in range(3):
        pipeline.blink()
    assert not pipeline.commands
    pipeline.calibrate()

    # A failed recalibration must not silently reuse an old baseline.
    pipeline.detector.begin_calibration(pipeline.now)
    pipeline.frames(32, visible=False)
    pipeline.frames(1)
    assert not pipeline.detector.calibrated


@pytest.mark.parametrize("interruption", ["lost_face", "busy"])
def test_closure_started_during_interruption_cannot_become_a_blink(interruption):
    pipeline = BlinkPipeline()
    pipeline.calibrate()
    pipeline.blink()
    pipeline.blink()
    pipeline.frames(
        1, left=0.08, right=0.08,
        visible=interruption != "lost_face", busy=interruption == "busy",
    )
    # The eyes remain closed when tracking/control resumes. Only the next
    # closure after a visible open frame can start a fresh blink.
    pipeline.frames(2, left=0.08, right=0.08)
    assert pipeline.frames(5).blink_count == 0
    pipeline.blink()
    pipeline.blink()
    assert not pipeline.commands
    pipeline.blink()
    assert pipeline.commands == ["flip right"]


def test_small_open_eye_landmark_jitter_is_not_a_blink():
    pipeline = BlinkPipeline()
    pipeline.calibrate()
    for _ in range(30):
        for ratio in (0.99, 1.02, 0.97, 1.0):
            pipeline.frames(1, left=0.26 * ratio, right=0.26 / ratio)
    assert not pipeline.commands
    assert not any(snapshot.blink_recorded for snapshot in pipeline.snapshots)

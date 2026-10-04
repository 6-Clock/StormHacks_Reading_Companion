from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "computer vision" / "read_mode_state.py"
SPEC = importlib.util.spec_from_file_location("read_mode_state", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
read_mode_state = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = read_mode_state
SPEC.loader.exec_module(read_mode_state)


def update(controller, now: float, *, open_eyes: bool = True, looking: bool = True):
    return controller.update(
        now,
        eyes_visible=True,
        eyes_open=open_eyes,
        looking_at_camera=looking,
    )


def blink(controller, started_at: float) -> None:
    update(controller, started_at, open_eyes=False)
    update(controller, started_at + 0.1)


def test_three_blinks_work_without_gaze_and_repeat_after_flip() -> None:
    commands: list[str] = []
    controller = read_mode_state.ReadModeController(commands.append)
    assert update(controller, 0, looking=False).display_mode == "READY"
    for now in (0.1, 0.4, 0.7):
        update(controller, now, open_eyes=False, looking=False)
        flip = update(controller, now + 0.1, looking=False)
    assert flip.display_mode == "SIGNAL"
    assert flip.blink_count == 3
    assert commands == ["flip right"]
    assert update(controller, 0.9, looking=False).display_mode == "READY"
    for now in (1.0, 1.3, 1.6):
        update(controller, now, open_eyes=False, looking=False)
        update(controller, now + 0.1, looking=False)
    assert commands == ["flip right", "flip right"]

def test_blink_only_accepts_next_three_blinks_without_a_settling_guard():
    commands = []
    controller = read_mode_state.ReadModeController(commands.append)
    controller.set_blink_only(True)
    blink(controller, 0)
    blink(controller, 0.3)
    blink(controller, 0.6)
    assert commands == ["flip right"]
    blink(controller, 0.9)
    blink(controller, 1.2)
    assert update(controller, 1.3).blink_count == 2
    assert controller.mode == "READY"
    blink(controller, 1.4)
    assert commands == ["flip right", "flip right"]


def test_legacy_setting_changes_clear_partial_gestures_without_gaze_gate():
    commands = []
    controller = read_mode_state.ReadModeController(commands.append)
    update(controller, 0)
    update(controller, 2.9)
    controller.set_blink_only(True)
    assert update(controller, 3.1).display_mode == "READY"
    blink(controller, 3.2)
    blink(controller, 3.5)
    controller.set_blink_only(False)
    blink(controller, 3.8)
    assert not commands
    assert update(controller, 4.0).display_mode == "READY"
    assert update(controller, 7.0).display_mode == "READY"


def test_busy_camera_and_invalid_face_clear_all_partial_blinks():
    for rejected_input in (
        {"turns_blocked": True}, {"calibrated": False}, {"eyes_visible": False},
    ):
        commands = []
        controller = read_mode_state.ReadModeController(commands.append)
        controller.set_blink_only(True)
        blink(controller, 0)
        blink(controller, 0.3)
        args = {"eyes_visible": True, "eyes_open": True, "looking_at_camera": True}
        blocked = controller.update(0.5, **{**args, **rejected_input})
        assert blocked.blink_count == 0
        update(controller, 0.55)  # Observe open eyes after tracking/control resumes.
        blink(controller, 0.6)
        assert not commands
        blink(controller, 0.9)
        blink(controller, 1.2)
        assert commands == ["flip right"]


def test_long_closure_and_window_expiry_cannot_complete_old_sequence():
    commands = []
    controller = read_mode_state.ReadModeController(commands.append)
    controller.set_blink_only(True)
    blink(controller, 0)
    blink(controller, 0.3)
    blink(controller, 2.5)
    assert not commands
    update(controller, 3, open_eyes=False)
    assert update(controller, 13, open_eyes=False).blink_count == 0
    update(controller, 13.1)
    blink(controller, 13.2)
    blink(controller, 13.5)
    assert not commands
    blink(controller, 13.8)
    assert commands == ["flip right"]


def test_flip_signal_is_one_display_frame_and_setting_change_resets_gesture():
    commands = []
    controller = read_mode_state.ReadModeController(commands.append)
    controller.set_blink_only(True)
    for now in (0, 0.3, 0.6):
        blink(controller, now)
    controller.set_blink_only(False)
    assert update(controller, 0.8).display_mode == "READY"
    controller.set_blink_only(True)
    assert update(controller, 1.0).display_mode == "READY"
    assert update(controller, 1.5).display_mode == "READY"


def test_new_setting_revision_clears_partial_sequence_even_when_value_unchanged():
    commands = []
    controller = read_mode_state.ReadModeController(commands.append)
    controller.set_blink_only(True)
    blink(controller, 0)
    blink(controller, 0.3)
    # For example, the UI switches off and back on between tracker polls.
    controller.set_blink_only(True, force=True)
    blink(controller, 0.6)
    assert not commands
    assert update(controller, 0.8).blink_count == 1

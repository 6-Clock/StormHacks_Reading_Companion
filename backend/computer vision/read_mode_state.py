"""State machine for gaze-controlled reading and page-flip commands.

The camera layer supplies landmark-derived booleans. This module owns timing,
debouncing, and the command decision so the behavior is testable without a webcam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


TOGGLE_HOLD_SECONDS = 3.0
BLINK_MIN_SECONDS = 0.07
BLINK_MAX_SECONDS = 0.70
BLINK_WINDOW_SECONDS = 2.0
SAFETY_CLOSE_SECONDS = 10.0
SIGNAL_SECONDS = 0.75


@dataclass(frozen=True)
class ReadModeSnapshot:
    display_mode: str
    look_progress: float
    blink_count: int
    closed_duration: float
    toggle_armed: bool


class ReadModeController:
    """Turn camera gaze and blinks into deliberate reading-mode actions."""

    def __init__(self, send_command: Callable[[str], None]) -> None:
        self._send_command = send_command
        self.reset()

    def reset(self) -> None:
        """Return to a safe stopped mode and clear all partial gestures."""
        self.mode = "STOP"
        self._toggle_armed = True
        self._camera_gaze_started_at: float | None = None
        self._eyes_closed_started_at: float | None = None
        self._blink_count = 0
        self._last_blink_at: float | None = None
        self._signal_until: float | None = None

    def _clear_blinks(self) -> None:
        self._blink_count = 0
        self._last_blink_at = None

    def _toggle_mode(self) -> None:
        self.mode = "READ" if self.mode == "STOP" else "STOP"
        self._toggle_armed = False
        self._camera_gaze_started_at = None
        self._clear_blinks()

    def _record_blink(self, now: float, closed_duration: float) -> None:
        if not BLINK_MIN_SECONDS <= closed_duration <= BLINK_MAX_SECONDS:
            return
        if self._last_blink_at is None or now - self._last_blink_at > BLINK_WINDOW_SECONDS:
            self._blink_count = 1
        else:
            self._blink_count += 1
        self._last_blink_at = now

    def _emit_flip(self, command: str, now: float) -> None:
        self._send_command(command)
        self.mode = "SIGNAL"
        self._signal_until = now + SIGNAL_SECONDS
        self._clear_blinks()

    def update(
        self,
        now: float,
        *,
        eyes_visible: bool,
        eyes_open: bool,
        looking_at_camera: bool,
        looking_lower_left: bool,
        looking_lower_right: bool,
    ) -> ReadModeSnapshot:
        """Advance the controller and return a display-ready immutable snapshot."""
        if self.mode == "SIGNAL" and self._signal_until is not None and now >= self._signal_until:
            self.mode = "READ"
            self._signal_until = None

        if not eyes_visible or not eyes_open:
            if self._eyes_closed_started_at is None:
                self._eyes_closed_started_at = now
            closed_duration = now - self._eyes_closed_started_at
            if closed_duration >= SAFETY_CLOSE_SECONDS:
                self.mode = "STOP"
                self._toggle_armed = True
                self._camera_gaze_started_at = None
                self._clear_blinks()
            return ReadModeSnapshot(
                display_mode=self.mode,
                look_progress=0.0,
                blink_count=self._blink_count,
                closed_duration=closed_duration,
                toggle_armed=self._toggle_armed,
            )

        closed_duration = 0.0
        if self._eyes_closed_started_at is not None:
            self._record_blink(now, now - self._eyes_closed_started_at)
            self._eyes_closed_started_at = None

        if not looking_at_camera:
            self._toggle_armed = True
            self._camera_gaze_started_at = None
        elif self._toggle_armed:
            if self._camera_gaze_started_at is None:
                self._camera_gaze_started_at = now
            elif now - self._camera_gaze_started_at >= TOGGLE_HOLD_SECONDS:
                self._toggle_mode()

        if self._last_blink_at is not None and now - self._last_blink_at > BLINK_WINDOW_SECONDS:
            self._clear_blinks()

        if self.mode == "READ" and self._blink_count >= 2:
            if looking_lower_left:
                self._emit_flip("flip left", now)
            elif looking_lower_right:
                self._emit_flip("flip right", now)

        look_progress = 0.0
        if self._camera_gaze_started_at is not None:
            look_progress = min(now - self._camera_gaze_started_at, TOGGLE_HOLD_SECONDS)
        return ReadModeSnapshot(
            display_mode=self.mode,
            look_progress=look_progress,
            blink_count=self._blink_count,
            closed_duration=closed_duration,
            toggle_armed=self._toggle_armed,
        )

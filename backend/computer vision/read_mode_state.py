"""State machine for gaze-controlled reading and page-flip commands.

The camera layer supplies landmark-derived booleans. This module owns timing,
debouncing, and the command decision so the behavior is testable without a webcam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


TOGGLE_HOLD_SECONDS = 3.0
# A 30 FPS camera can observe a quick natural blink for only one frame
# (about 0.033 seconds). Keep this below one frame so it is not discarded.
BLINK_MIN_SECONDS = 0.02
BLINK_MAX_SECONDS = 0.70
# The first blink starts one fixed window. The next two must arrive before it
# expires; later blinks never extend the deadline.
BLINK_WINDOW_SECONDS = 3.5
REQUIRED_FLIP_BLINKS = 3
# One recovered open-eye frame is enough to confirm a short blink. The
# three-blink requirement is the guard against an isolated bad frame.
OPEN_STABLE_FRAMES = 1
SAFETY_CLOSE_SECONDS = 10.0
SIGNAL_SECONDS = 0.75


@dataclass(frozen=True)
class ReadModeSnapshot:
    display_mode: str
    look_progress: float
    blink_count: int
    closed_duration: float
    toggle_armed: bool
    blink_recorded: bool
    last_blink_duration: float | None


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
        self._blink_sequence_started_at: float | None = None
        self._last_blink_at: float | None = None
        self._pending_blink_duration: float | None = None
        self._open_frames = 0
        self._signal_until: float | None = None

    def _clear_blinks(self) -> None:
        self._blink_count = 0
        self._blink_sequence_started_at = None
        self._last_blink_at = None
        self._pending_blink_duration = None
        self._open_frames = 0

    def _toggle_mode(self) -> None:
        self.mode = "READ" if self.mode == "STOP" else "STOP"
        self._toggle_armed = False
        self._camera_gaze_started_at = None
        self._clear_blinks()

    def _record_blink(self, now: float, closed_duration: float) -> bool:
        if not BLINK_MIN_SECONDS <= closed_duration <= BLINK_MAX_SECONDS:
            return False
        if (
            self._blink_sequence_started_at is None
            or now - self._blink_sequence_started_at > BLINK_WINDOW_SECONDS
        ):
            self._blink_count = 1
            self._blink_sequence_started_at = now
        else:
            self._blink_count += 1
        self._last_blink_at = now
        return True

    def _emit_flip(self, now: float) -> None:
        self._send_command("flip right")
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
    ) -> ReadModeSnapshot:
        """Advance the controller and return a display-ready immutable snapshot."""
        blink_recorded = False
        last_blink_duration: float | None = None
        if self.mode == "SIGNAL" and self._signal_until is not None and now >= self._signal_until:
            self.mode = "READ"
            self._signal_until = None

        if not eyes_visible:
            # Landmark loss is common on low-resolution cameras. Do not treat it
            # as a blink because it would create false page flips.
            self._eyes_closed_started_at = None
            self._pending_blink_duration = None
            self._open_frames = 0
            return ReadModeSnapshot(
                display_mode=self.mode,
                look_progress=0.0,
                blink_count=self._blink_count,
                closed_duration=0.0,
                toggle_armed=self._toggle_armed,
                blink_recorded=False,
                last_blink_duration=None,
            )

        if not eyes_open:
            if self._eyes_closed_started_at is None:
                self._eyes_closed_started_at = now
            self._open_frames = 0
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
                blink_recorded=False,
                last_blink_duration=None,
            )

        closed_duration = 0.0
        if self._eyes_closed_started_at is not None:
            closed_duration = now - self._eyes_closed_started_at
            # Blinks are an input only while actively reading. Ignoring them
            # in STOP and the short post-flip SIGNAL state prevents stale
            # counts such as 3/3 or 4/3 without a corresponding page flip.
            self._pending_blink_duration = (
                closed_duration if self.mode == "READ" else None
            )
            self._eyes_closed_started_at = None
            self._open_frames = 0
        self._open_frames += 1
        if self._pending_blink_duration is not None and self._open_frames >= OPEN_STABLE_FRAMES:
            last_blink_duration = self._pending_blink_duration
            blink_recorded = self._record_blink(now, last_blink_duration)
            self._pending_blink_duration = None

        if not looking_at_camera:
            self._toggle_armed = True
            self._camera_gaze_started_at = None
        elif self._toggle_armed:
            if self._camera_gaze_started_at is None:
                self._camera_gaze_started_at = now
            elif now - self._camera_gaze_started_at >= TOGGLE_HOLD_SECONDS:
                self._toggle_mode()

        if self.mode != "READ":
            self._clear_blinks()

        if (
            self._blink_sequence_started_at is not None
            and now - self._blink_sequence_started_at > BLINK_WINDOW_SECONDS
        ):
            self._clear_blinks()

        reported_blink_count = self._blink_count
        if self.mode == "READ" and self._blink_count >= REQUIRED_FLIP_BLINKS:
            self._emit_flip(now)

        look_progress = 0.0
        if self._camera_gaze_started_at is not None:
            look_progress = min(now - self._camera_gaze_started_at, TOGGLE_HOLD_SECONDS)
        return ReadModeSnapshot(
            display_mode=self.mode,
            look_progress=look_progress,
            # Preserve the successful final count for this frame even though
            # the next gesture starts cleanly after a page-flip command.
            blink_count=reported_blink_count,
            closed_duration=closed_duration,
            toggle_armed=self._toggle_armed,
            blink_recorded=blink_recorded,
            last_blink_duration=last_blink_duration if blink_recorded else None,
        )

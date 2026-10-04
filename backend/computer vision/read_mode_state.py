"""State machine for calibrated blink page-flip commands.

The camera layer supplies landmark-derived booleans. This module owns timing,
debouncing, and the command decision so the behavior is testable without a webcam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# A 30 FPS camera can observe a quick natural blink for only one frame
# (about 0.033 seconds). Keep this below one frame so it is not discarded.
BLINK_MIN_SECONDS = 0.02
BLINK_MAX_SECONDS = 0.70
# The first blink starts one fixed window. The next two must arrive before it
# expires; later blinks never extend the deadline.
BLINK_WINDOW_SECONDS = 2.0
REQUIRED_FLIP_BLINKS = 3
# One recovered open-eye frame is enough to confirm a short blink. The
# three-blink requirement is the guard against an isolated bad frame.
OPEN_STABLE_FRAMES = 1


@dataclass(frozen=True)
class ReadModeSnapshot:
    display_mode: str
    look_progress: float  # Legacy telemetry field; always zero.
    blink_count: int
    closed_duration: float
    toggle_armed: bool  # Legacy snapshot field; gaze toggling is disabled.
    blink_recorded: bool
    last_blink_duration: float | None


class ReadModeController:
    """Turn calibrated blinks into page-flip commands."""

    def __init__(self, send_command: Callable[[str], None]) -> None:
        self._send_command = send_command
        self.blink_only = False
        self.reset()

    def reset(self) -> None:
        """Clear partial gestures without requiring a gaze hold."""
        self.mode = "READY"
        self._eyes_closed_started_at: float | None = None
        self._blink_count = 0
        self._blink_sequence_started_at: float | None = None
        self._last_blink_at: float | None = None
        self._pending_blink_duration: float | None = None
        self._open_frames = 0
        self._awaiting_open = False

    def set_blink_only(self, enabled: bool, *, force: bool = False) -> None:
        """Legacy setting changes clear gestures; both values allow direct blinks."""
        if self.blink_only != enabled or force:
            self.blink_only = enabled
            self.reset()

    def _clear_blinks(self) -> None:
        self._blink_count = 0
        self._blink_sequence_started_at = None
        self._last_blink_at = None
        self._pending_blink_duration = None
        self._open_frames = 0

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

    def _emit_flip(self) -> None:
        self._send_command("flip right")
        self.mode = "READY"
        self._clear_blinks()

    def update(
        self,
        now: float,
        *,
        eyes_visible: bool,
        eyes_open: bool,
        looking_at_camera: bool,
        turns_blocked: bool = False,
        calibrated: bool = True,
    ) -> ReadModeSnapshot:
        """Advance the controller and return a display-ready immutable snapshot."""
        blink_recorded = False
        last_blink_duration: float | None = None
        interrupted = turns_blocked or not calibrated or not eyes_visible
        if interrupted:
            self._awaiting_open = True
        if interrupted or (self._awaiting_open and not eyes_open):
            # Landmark loss is common on low-resolution cameras. Do not treat it
            # as a blink. No partial gesture survives a busy scanner, calibration,
            # or lost face, including a closure across these states.
            self._eyes_closed_started_at = None
            self._clear_blinks()
            return ReadModeSnapshot(
                display_mode=self.mode,
                look_progress=0.0,
                blink_count=self._blink_count,
                closed_duration=0.0,
                toggle_armed=False,
                blink_recorded=False,
                last_blink_duration=None,
            )

        # First observe open eyes after an interruption. A closure that began
        # while tracking was unavailable cannot become the first new blink.
        self._awaiting_open = False

        if not eyes_open:
            if self._eyes_closed_started_at is None:
                self._eyes_closed_started_at = now
            self._open_frames = 0
            closed_duration = now - self._eyes_closed_started_at
            if closed_duration > BLINK_MAX_SECONDS:
                # A sustained closure breaks the gesture. Otherwise two old
                # blinks plus a later blink could flip after an invalid closure.
                self._clear_blinks()
            return ReadModeSnapshot(
                display_mode=self.mode,
                look_progress=0.0,
                blink_count=self._blink_count,
                closed_duration=closed_duration,
                toggle_armed=False,
                blink_recorded=False,
                last_blink_duration=None,
            )

        closed_duration = 0.0
        if self._eyes_closed_started_at is not None:
            closed_duration = now - self._eyes_closed_started_at
            self._pending_blink_duration = closed_duration
            self._eyes_closed_started_at = None
            self._open_frames = 0
        self._open_frames += 1
        if self._pending_blink_duration is not None and self._open_frames >= OPEN_STABLE_FRAMES:
            last_blink_duration = self._pending_blink_duration
            blink_recorded = self._record_blink(now, last_blink_duration)
            self._pending_blink_duration = None

        if (
            self._blink_sequence_started_at is not None
            and now - self._blink_sequence_started_at > BLINK_WINDOW_SECONDS
        ):
            self._clear_blinks()

        reported_blink_count = self._blink_count
        flipped = self._blink_count >= REQUIRED_FLIP_BLINKS
        if flipped:
            self._emit_flip()

        return ReadModeSnapshot(
            display_mode="SIGNAL" if flipped else self.mode,
            look_progress=0.0,
            # Preserve the successful final count for this frame even though
            # the next reading session starts with a clean gesture.
            blink_count=reported_blink_count,
            closed_duration=closed_duration,
            toggle_armed=False,
            blink_recorded=blink_recorded,
            last_blink_duration=last_blink_duration if blink_recorded else None,
        )

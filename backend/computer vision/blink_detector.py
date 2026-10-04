"""Camera-independent eyelid measurements used by the live eye tracker."""

from __future__ import annotations

from dataclasses import dataclass

EYE_OPEN_THRESHOLD = 0.18
CALIBRATION_SECONDS = 1.0
MIN_CALIBRATION_SAMPLES = 10

# (one-eye closure ratio, two-eye average closure ratio, sharp-drop ratio)
BLINK_SENSITIVITY = {
    "low": (0.55, 0.62, 0.20),
    "normal": (0.68, 0.75, 0.12),
    "high": (0.78, 0.85, 0.08),
}


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
        self.single_close_ratio, self.pair_close_ratio, self.sharp_drop_ratio = (
            BLINK_SENSITIVITY[sensitivity]
        )
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
        self.left_baseline = self.right_baseline = None
        self._samples = []
        self._previous_average_ratio = None
        self._was_closed = False
        self.phase = "CALIBRATING"

    @property
    def calibrated(self) -> bool:
        return (
            self.left_baseline is not None and self.right_baseline is not None
            and self._calibration_started_at is None
        )

    def observe(self, now: float, left: float, right: float, *, eyes_visible: bool) -> EyeReading:
        calibration_finished = False
        if not eyes_visible:
            self._previous_average_ratio = None
            self._was_closed = False
            self.phase = "NO FACE"
            return EyeReading(
                False, self.phase, 0.0, 0.0, self._calibration_progress(now),
                False, len(self._samples),
            )

        if self._calibration_started_at is not None and left >= 0.06 and right >= 0.06:
            self._samples.append((left, right))
        if (
            self._calibration_started_at is not None
            and now - self._calibration_started_at >= CALIBRATION_SECONDS
        ):
            # A single recovered landmark frame is not an open-eye baseline.
            if len(self._samples) >= MIN_CALIBRATION_SAMPLES:
                left_values, right_values = zip(*self._samples, strict=True)
                self.left_baseline = float(sorted(left_values)[len(left_values) // 2])
                self.right_baseline = float(sorted(right_values)[len(right_values) // 2])
                calibration_finished = True
                # The fallback and calibrated ratios use different scales.
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

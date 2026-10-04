"""Small local snapshot bridge between the standalone tracker and the API.

Only derived measurements and a bounded event log are written; no camera frames.
This module deliberately uses only the standard library so the standalone tracker
does not need to import FastAPI or the provider clients.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "eyes.json"
STALE_SECONDS = 2.0
PUBLISH_INTERVAL = 0.2
MAX_EVENTS = 32
MAX_SNAPSHOT_BYTES = 32_768
EVENT_TYPES = {"tracker", "mode", "blink", "calibration", "command", "reset"}
PHASES = {"OPEN", "CLOSING", "CLOSED", "REOPENING", "NO FACE", "CALIBRATING", "NO CALIBRATION"}
logger = logging.getLogger(__name__)


def _number(value: Any, minimum: float = 0, maximum: float = math.inf) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        return None
    return number


def _pair(value: Any, keys: tuple[str, str], maximum: float) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    first, second = (_number(value.get(key), maximum=maximum) for key in keys)
    if first is None or second is None:
        return None
    return {keys[0]: first, keys[1]: second}


def _events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    valid = []
    for event in value[-MAX_EVENTS:]:
        if not isinstance(event, dict):
            continue
        timestamp = _number(event.get("time"))
        event_id = event.get("id")
        message = event.get("message")
        event_type = event.get("type")
        if (
            timestamp is None
            or not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 80
            or not isinstance(message, str)
            or not 1 <= len(message) <= 200
            or not isinstance(event_type, str)
            or event_type not in EVENT_TYPES
        ):
            continue
        valid.append({"id": event_id, "time": timestamp, "type": event_type, "message": message})
    return valid


def sanitize_snapshot(value: Any, *, now: float | None = None) -> dict[str, Any]:
    """Return the public contract, clearing measurements when the producer is stale."""
    data = value if isinstance(value, dict) else {}
    timestamp = _number(data.get("updated_at"))
    now = time.time() if now is None else now
    connected = (
        data.get("connected") is True
        and timestamp is not None
        and -1.0 <= now - timestamp <= STALE_SECONDS
    )
    camera_index = _number(data.get("camera_index"), maximum=100)
    camera_index = int(camera_index) if camera_index is not None else None
    mode = data.get("mode")
    mode = mode if isinstance(mode, str) and mode in {"STOP", "READ", "SIGNAL"} else None
    phase = data.get("phase")
    phase = phase if isinstance(phase, str) and phase in PHASES else None
    visible = connected and data.get("eyes_visible") is True
    blink_count = _number(data.get("blink_count"), maximum=3)
    return {
        "connected": connected,
        "updated_at": timestamp,
        "camera_index": camera_index,
        "mode": mode if connected else None,
        "eyes_visible": visible,
        "gaze": _pair(data.get("gaze"), ("x", "y"), 1.0) if visible else None,
        "openness": _pair(data.get("openness"), ("left", "right"), 10.0) if visible else None,
        "phase": phase if connected else None,
        "blink_count": int(blink_count or 0) if connected else 0,
        "look_progress": (_number(data.get("look_progress"), maximum=3) or 0) if connected else 0,
        "capture_fps": _number(data.get("capture_fps")) if connected else None,
        "inference_fps": _number(data.get("inference_fps")) if connected else None,
        "frame_age_ms": _number(data.get("frame_age_ms")) if connected else None,
        "events": _events(data.get("events")),
    }


def read_eye_snapshot(path: Path | None = None) -> dict[str, Any]:
    """Missing, unreadable, oversized or partially written files mean disconnected."""
    try:
        with (path or SNAPSHOT_PATH).open("rb") as snapshot_file:
            contents = snapshot_file.read(MAX_SNAPSHOT_BYTES + 1)
        if len(contents) > MAX_SNAPSHOT_BYTES:
            return sanitize_snapshot(None)
        return sanitize_snapshot(json.loads(contents))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return sanitize_snapshot(None)


class EyeTelemetryPublisher:
    """Publish at most five snapshots per second without breaking camera operation."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SNAPSHOT_PATH
        self._session = uuid4().hex
        self._temporary = self.path.with_name(f".eyes-{self._session}.tmp")
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._sequence = 0
        self._last_write = -math.inf
        self._last_state: dict[str, Any] = {}
        self._write_failed = False

    def event(self, event_type: str, message: str) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown eye telemetry event type: {event_type}")
        self._sequence += 1
        self._events.append({
            "id": f"{self._session}-{self._sequence}",
            "time": time.time(),
            "type": event_type,
            "message": message[:200],
        })

    def publish(self, measurements: dict[str, Any], *, force: bool = False) -> bool:
        self._last_state = measurements
        monotonic_now = time.monotonic()
        if not force and monotonic_now - self._last_write < PUBLISH_INTERVAL:
            return False
        self._last_write = monotonic_now
        wall_time = time.time()
        payload = sanitize_snapshot({
            **measurements,
            "updated_at": wall_time,
            "events": list(self._events),
        }, now=wall_time)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._temporary.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
            os.replace(self._temporary, self.path)
            self._write_failed = False
            return True
        except OSError:
            if not self._write_failed:
                logger.warning("Eye diagnostics could not be saved; camera tracking continues.")
            self._write_failed = True
            return False

    def close(self) -> None:
        self.event("tracker", "Eye tracker stopped")
        self.publish({**self._last_state, "connected": False}, force=True)
        try:
            self._temporary.unlink(missing_ok=True)
        except OSError:
            pass

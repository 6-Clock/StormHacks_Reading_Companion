"""Bounded measurement contract shared by the HTTP tracker publisher and API.

No camera frames or file transport are accepted. Keep this module standard-library
only so the standalone tracker can reuse the sanitizer without importing FastAPI.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

STALE_SECONDS = 2.0
MAX_FUTURE_SECONDS = 0.25
PUBLISH_INTERVAL = 0.2
MAX_EVENTS = 32
MAX_SNAPSHOT_BYTES = 32_768
EVENT_TYPES = {"tracker", "mode", "blink", "calibration", "command", "auto_scan", "reset"}
PHASES = {"OPEN", "CLOSING", "CLOSED", "REOPENING", "NO FACE", "CALIBRATING", "NO CALIBRATION"}
SNAPSHOT_FIELDS = {
    "connected", "updated_at", "camera_index", "mode", "eyes_visible", "gaze", "openness",
    "phase", "blink_count", "look_progress", "capture_fps", "inference_fps", "frame_age_ms",
    "events", "calibrated", "turns_blocked", "blink_only",
}


def parse_snapshot_envelope(contents: bytes) -> dict[str, Any]:
    """Validate publisher identity/envelope before taking the shared ownership lock."""
    if len(contents) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Eye diagnostics must be at most 32768 bytes.")
    value = json.loads(contents)
    if not isinstance(value, dict) or set(value) != {
        "tracker_session_id", "sequence", "snapshot",
    }:
        raise ValueError("Send tracker_session_id, sequence, and snapshot only.")
    session = value["tracker_session_id"]
    sequence = value["sequence"]
    snapshot = value["snapshot"]
    if not isinstance(session, str) or not 1 <= len(session) <= 100:
        raise ValueError("A tracker session ID of 1 to 100 characters is required.")
    if type(sequence) is not int or not 1 <= sequence <= 2**63 - 1:
        raise ValueError("Snapshot sequence must be a positive integer.")
    if not isinstance(snapshot, dict) or set(snapshot) - SNAPSHOT_FIELDS:
        raise ValueError("Only derived eye measurements and bounded events are accepted.")
    return value


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
        and -MAX_FUTURE_SECONDS <= now - timestamp < STALE_SECONDS
    )
    camera_index = _number(data.get("camera_index"), maximum=100)
    camera_index = int(camera_index) if camera_index is not None else None
    mode = data.get("mode")
    mode = mode if isinstance(mode, str) and mode in {"STOP", "READ", "READY", "SIGNAL"} else None
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
        **{
            field: data[field] if connected and isinstance(data.get(field), bool) else None
            for field in ("calibrated", "turns_blocked", "blink_only")
        },
        "events": _events(data.get("events")),
    }

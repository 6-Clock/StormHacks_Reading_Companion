import json

from fastapi.testclient import TestClient

from app import main
from app.services import eye_telemetry as telemetry


def measurements() -> dict:
    return {
        "connected": True,
        "camera_index": 1,
        "mode": "READ",
        "eyes_visible": True,
        "gaze": {"x": 0.6, "y": 0.7},
        "openness": {"left": 0.82, "right": 1.1},
        "phase": "OPEN",
        "blink_count": 2,
        "look_progress": 1.5,
        "capture_fps": 30,
        "inference_fps": 24,
        "frame_age_ms": 12,
    }


def test_api_live_snapshot_then_stale_clears_measurements(tmp_path, monkeypatch):
    path = tmp_path / "eyes.json"
    monkeypatch.setattr(telemetry, "SNAPSHOT_PATH", path)
    monkeypatch.setattr(telemetry.time, "time", lambda: 1000.0)
    publisher = telemetry.EyeTelemetryPublisher()
    publisher.event("command", "Serial preview: flip right")
    assert publisher.publish(measurements())
    client = TestClient(main.app)

    response = client.get("/v1/diagnostics/eyes")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    live = response.json()
    assert live["connected"] is True
    assert live["updated_at"] == 1000
    assert live["gaze"] == {"x": 0.6, "y": 0.7}
    assert live["openness"] == {"left": 0.82, "right": 1.1}
    assert live["events"][0]["message"] == "Serial preview: flip right"

    monkeypatch.setattr(telemetry.time, "time", lambda: 1002.1)
    stale = client.get("/v1/diagnostics/eyes").json()
    assert stale["connected"] is False
    assert stale["updated_at"] == 1000
    assert stale["mode"] is None
    assert stale["gaze"] is None
    assert stale["openness"] is None
    assert stale["capture_fps"] is None
    assert stale["eyes_visible"] is False
    assert stale["events"] == live["events"]


def test_missing_corrupt_and_oversized_snapshots_are_disconnected(tmp_path):
    path = tmp_path / "eyes.json"
    assert telemetry.read_eye_snapshot(path)["connected"] is False
    for content in (b'{"connected":', b"\xff\xfe", b"[]", b"x" * 32_769):
        path.write_bytes(content)
        result = telemetry.read_eye_snapshot(path)
        assert result["connected"] is False
        assert result["updated_at"] is None
        assert result["gaze"] is None


def test_nonfinite_and_unavailable_measurements_never_reach_json():
    raw = {
        **measurements(),
        "updated_at": 1000,
        "gaze": {"x": float("nan"), "y": 0.5},
        "openness": {"left": -0.1, "right": 1},
        "capture_fps": float("inf"),
        "inference_fps": 10**400,
        "frame_age_ms": True,
        "mode": ["READ"],
        "events": [{"id": "x", "time": 1000, "type": [], "message": "bad"}],
    }
    result = telemetry.sanitize_snapshot(raw, now=1000)
    assert result["connected"] is True
    for field in ("gaze", "openness", "capture_fps", "inference_fps", "frame_age_ms", "mode"):
        assert result[field] is None
    assert result["events"] == []
    json.dumps(result, allow_nan=False)

    hidden = telemetry.sanitize_snapshot(
        {**measurements(), "updated_at": 1000, "eyes_visible": False}, now=1000,
    )
    assert hidden["connected"] is True
    assert hidden["gaze"] is None
    assert hidden["openness"] is None

    calibrating = telemetry.sanitize_snapshot(
        {**measurements(), "updated_at": 1000, "phase": "CALIBRATING"}, now=1000,
    )
    assert calibrating["phase"] == "CALIBRATING"


def test_throttled_atomic_write_preserves_previous_snapshot_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "eyes.json"
    ticks = [1.0]
    monkeypatch.setattr(telemetry.time, "monotonic", lambda: ticks[0])
    publisher = telemetry.EyeTelemetryPublisher(path)
    assert publisher.publish(measurements())
    before = path.read_bytes()
    ticks[0] = 1.1
    assert publisher.publish({**measurements(), "mode": "STOP"}) is False
    assert path.read_bytes() == before

    def fail_replace(*args):
        raise PermissionError("Reader temporarily has the file open")

    monkeypatch.setattr(telemetry.os, "replace", fail_replace)
    ticks[0] = 1.3
    assert publisher.publish({**measurements(), "mode": "STOP"}) is False
    assert path.read_bytes() == before
    assert telemetry.read_eye_snapshot(path)["mode"] == "READ"


def test_bounded_events_and_close_publish_disconnection(tmp_path):
    path = tmp_path / "eyes.json"
    publisher = telemetry.EyeTelemetryPublisher(path)
    for number in range(50):
        publisher.event("blink", f"Blink event {number}")
    publisher.publish(measurements())
    live = telemetry.read_eye_snapshot(path)
    assert len(live["events"]) == telemetry.MAX_EVENTS
    assert len({event["id"] for event in live["events"]}) == telemetry.MAX_EVENTS
    assert live["events"][0]["message"] == "Blink event 18"

    publisher.close()
    stopped = telemetry.read_eye_snapshot(path)
    assert stopped["connected"] is False
    assert stopped["mode"] is None
    assert stopped["events"][-1]["message"] == "Eye tracker stopped"

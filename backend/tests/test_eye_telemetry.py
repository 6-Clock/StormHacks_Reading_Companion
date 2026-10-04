import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services import eye_telemetry as telemetry
from app.services import tracker_settings as settings_module


def measurements() -> dict:
    return {
        "connected": True, "camera_index": 1, "mode": "READ", "eyes_visible": True,
        "gaze": {"x": 0.6, "y": 0.7}, "openness": {"left": 0.82, "right": 1.1},
        "phase": "OPEN", "blink_count": 2, "look_progress": 1.5,
        "capture_fps": 30, "inference_fps": 24, "frame_age_ms": 12,
        "calibrated": True, "turns_blocked": False, "blink_only": True,
    }


@pytest.fixture
def diagnostics_api(monkeypatch):
    clock = {"wall": 1000.0, "mono": 10.0}
    store = settings_module.TrackerSettingsStore(
        clock=lambda: clock["mono"], wall_clock=lambda: clock["wall"],
    )
    monkeypatch.setattr(settings_module, "tracker_settings", store)
    return TestClient(main.app), store, clock


def ack(client, session="camera-session"):
    desired = client.get("/v1/tracker-settings").json()
    return client.post("/v1/tracker-settings/ack", json={
        "revision": desired["revision"], "blink_only": desired["blink_only"],
        "tracker_session_id": session, "eye_camera_index": 1,
    })


def push(client, *, sequence=1, session="camera-session", timestamp=1000.0, **snapshot):
    return client.post("/v1/diagnostics/eyes", json={
        "tracker_session_id": session, "sequence": sequence,
        "snapshot": {**measurements(), "updated_at": timestamp, **snapshot},
    })


def test_live_snapshot_then_stale_clears_measurements(diagnostics_api):
    client, store, clock = diagnostics_api
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is False
    assert push(client).status_code == 409
    assert ack(client).status_code == 200
    event = {"id": "one", "time": 1000, "type": "command", "message": "Serial preview: flip right"}
    assert push(client, events=[event]).status_code == 200
    response = client.get("/v1/diagnostics/eyes")
    assert response.headers["cache-control"] == "no-store"
    live = response.json()
    assert live["connected"] is True
    assert live["updated_at"] == 1000
    assert live["gaze"] == {"x": 0.6, "y": 0.7}
    assert live["openness"] == {"left": 0.82, "right": 1.1}
    assert live["calibrated"] is True
    assert live["turns_blocked"] is False
    assert live["blink_only"] is True
    clock.update(wall=1002.1, mono=12.1)
    stale = client.get("/v1/diagnostics/eyes").json()
    assert stale["connected"] is False
    assert stale["updated_at"] == 1000
    for field in ("mode", "gaze", "openness", "capture_fps", "calibrated", "blink_only"):
        assert stale[field] is None
    assert stale["eyes_visible"] is False
    assert stale["events"] == [event]
    assert store.snapshot()["tracker_connected"] is True


def test_nonfinite_and_unavailable_measurements_never_reach_json():
    raw = {
        **measurements(), "updated_at": 1000,
        "gaze": {"x": float("nan"), "y": 0.5},
        "openness": {"left": -0.1, "right": 1},
        "capture_fps": float("inf"), "inference_fps": 10**400,
        "frame_age_ms": True, "mode": ["READ"], "calibrated": "true",
        "events": [{"id": "x", "time": 1000, "type": [], "message": "bad"}],
    }
    result = telemetry.sanitize_snapshot(raw, now=1000)
    assert result["connected"] is True
    for field in ("gaze", "openness", "capture_fps", "inference_fps", "frame_age_ms", "mode"):
        assert result[field] is None
    assert result["calibrated"] is None
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


def test_bounded_events_and_disconnect_keep_only_derived_fields(diagnostics_api):
    client, _, _ = diagnostics_api
    ack(client)
    events = [{"id": str(i), "time": 1000, "type": "blink", "message": f"Blink {i}"}
              for i in range(50)]
    assert push(client, events=events).status_code == 200
    assert client.get("/v1/diagnostics/eyes").json()["events"] == events[-32:]
    assert push(client, sequence=2, connected=False).status_code == 200
    stopped = client.get("/v1/diagnostics/eyes").json()
    assert stopped["connected"] is False
    assert stopped["mode"] is None
    assert push(client, sequence=3, image="data:image/jpeg;base64,abc").status_code == 422


def test_duplicate_out_of_order_and_frozen_capture_do_not_renew_freshness(diagnostics_api):
    client, _, clock = diagnostics_api
    ack(client)
    assert push(client, sequence=2).json()["accepted"] is True
    clock.update(wall=1001, mono=11)
    for sequence in (2, 1):
        response = push(client, sequence=sequence, timestamp=1001, mode="STOP")
        assert response.status_code == 200
        assert response.json()["accepted"] is False
    assert client.get("/v1/diagnostics/eyes").json()["mode"] == "READ"
    clock.update(wall=999.9, mono=11.9)
    assert push(client, sequence=3).status_code == 200
    clock["mono"] = 12.01
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is False


def test_capture_age_at_receipt_sets_monotonic_remaining_deadline(diagnostics_api):
    client, _, clock = diagnostics_api
    ack(client)
    clock.update(wall=1001.5, mono=11.5)
    assert push(client, timestamp=1000).status_code == 200
    clock.update(wall=10_000, mono=11.9)
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is True
    clock.update(wall=999, mono=12)
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is False


@pytest.mark.parametrize("timestamp", [998, 997, 1000.251, -1, None, True])
def test_stale_future_or_invalid_capture_is_rejected(diagnostics_api, timestamp):
    client, _, _ = diagnostics_api
    ack(client)
    assert push(client, timestamp=timestamp).status_code == 422
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is False


def test_new_owner_clears_snapshot_and_rejects_old_disconnect(diagnostics_api):
    client, _, clock = diagnostics_api
    ack(client, "first")
    push(client, session="first", sequence=99)
    assert ack(client, "second").status_code == 409
    clock.update(wall=1004, mono=14)
    assert ack(client, "second").status_code == 200
    empty = client.get("/v1/diagnostics/eyes").json()
    assert empty["updated_at"] is None
    assert empty["events"] == []
    assert push(client, session="second", timestamp=1004).status_code == 200
    assert push(client, session="first", sequence=100, timestamp=1004,
                connected=False).status_code == 409
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is True


def test_diagnostics_do_not_extend_heartbeat_or_reset_on_mode_change(diagnostics_api):
    client, _, clock = diagnostics_api
    ack(client)
    push(client)
    client.patch("/v1/tracker-settings", json={"blink_only": True})
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is True
    assert ack(client).status_code == 200
    clock.update(wall=1002.5, mono=12.5)
    assert push(client, timestamp=1002.5, sequence=2).status_code == 200
    clock.update(wall=1003.1, mono=13.1)
    assert client.get("/v1/tracker-settings").json()["tracker_connected"] is False
    assert client.get("/v1/diagnostics/eyes").json()["connected"] is False
    assert push(client, timestamp=1003.1, sequence=3).status_code == 409


def test_restart_has_no_snapshot_or_owner(diagnostics_api, monkeypatch):
    client, _, _ = diagnostics_api
    ack(client)
    push(client)
    monkeypatch.setattr(settings_module, "tracker_settings", settings_module.TrackerSettingsStore())
    assert client.get("/v1/diagnostics/eyes").json()["updated_at"] is None
    assert push(client, sequence=2).status_code == 409


@pytest.mark.parametrize("contents", [b"[]", b'{"connected":', b"\xff\xfe", b"null"])
def test_invalid_envelope_is_rejected_before_state_change(diagnostics_api, contents):
    client, _, _ = diagnostics_api
    ack(client)
    assert client.post("/v1/diagnostics/eyes", content=contents).status_code == 422
    assert client.get("/v1/diagnostics/eyes").json()["updated_at"] is None


@pytest.mark.parametrize("sequence", [True, 0, -1, 1.1, "1"])
def test_sequence_is_a_strict_positive_integer(diagnostics_api, sequence):
    client, _, _ = diagnostics_api
    ack(client)
    assert push(client, sequence=sequence).status_code == 422


def test_streamed_size_limit_does_not_trust_content_length_or_parse_oversize(monkeypatch):
    parsed = []
    monkeypatch.setattr(main, "parse_snapshot_envelope", lambda _: parsed.append(True))

    async def exercise():
        sent = []
        chunks = iter([
            {"type": "http.request", "body": b" " * 20_000, "more_body": True},
            {"type": "http.request", "body": b" " * 20_000, "more_body": True},
        ])

        async def receive():
            return next(chunks)

        async def send(message):
            sent.append(message)

        await main.app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/v1/diagnostics/eyes",
            "raw_path": b"/v1/diagnostics/eyes", "query_string": b"", "root_path": "",
            "headers": [(b"content-length", b"1")],
            "client": ("127.0.0.1", 1234), "server": ("localhost", 8001),
        }, receive, send)
        assert next(message for message in sent if message["type"] == "http.response.start")[
            "status"
        ] == 413

    asyncio.run(exercise())
    assert parsed == []

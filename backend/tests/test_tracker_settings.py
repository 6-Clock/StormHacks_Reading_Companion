import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import tracker_settings as module


def test_settings_wait_for_actual_ack_and_expire_when_camera_stops(monkeypatch):
    tick = [0.0]
    store = module.TrackerSettingsStore(clock=lambda: tick[0])
    monkeypatch.setattr(module, "tracker_settings", store)
    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)
    default = client.get("/v1/tracker-settings").json()
    assert default["blink_only"] is False
    assert default["tracker_connected"] is False
    desired = client.patch("/v1/tracker-settings", json={"blink_only": True}).json()
    assert desired["revision"] != default["revision"]
    assert desired["applied_revision"] is None
    assert client.patch("/v1/tracker-settings", json={"blink_only": 1}).status_code == 422
    body = {
        "revision": desired["revision"], "blink_only": True,
        "tracker_session_id": "camera-session", "eye_camera_index": 1,
    }
    stale_ack = client.post("/v1/tracker-settings/ack", json={**body, "revision": "stale"})
    assert stale_ack.status_code == 409
    result = client.post("/v1/tracker-settings/ack", json=body).json()
    assert result["tracker_connected"] is True
    assert result["applied_blink_only"] is True
    assert result["applied_revision"] == desired["revision"]
    assert client.get("/v1/tracker-settings").headers["cache-control"] == "no-store"
    # A second process cannot pretend to own an already-live camera session.
    second = {**body, "tracker_session_id": "second"}
    assert client.post("/v1/tracker-settings/ack", json=second).status_code == 409
    tick[0] = 4.0
    offline = client.get("/v1/tracker-settings").json()
    assert offline["tracker_connected"] is False
    assert offline["applied_revision"] is None
    assert offline["applied_blink_only"] is None
    assert client.post("/v1/tracker-settings/ack", json=second).status_code == 200


def test_changed_setting_rejects_old_ack_and_new_api_starts_with_gaze_gate():
    store = module.TrackerSettingsStore()
    before = store.snapshot()
    store.acknowledge(
        revision=before["revision"], blink_only=False,
        tracker_session_id="one", eye_camera_index=1,
    )
    changed = store.update(True)
    assert changed["applied_revision"] == before["revision"]
    assert changed["revision"] != changed["applied_revision"]
    with pytest.raises(ValueError):
        store.acknowledge(
            revision=before["revision"], blink_only=False,
            tracker_session_id="one", eye_camera_index=1,
        )
    fresh_api = module.TrackerSettingsStore().snapshot()
    assert fresh_api["blink_only"] is False
    assert fresh_api["revision"] != changed["revision"]


def test_camera_handoff_waits_for_actual_release_not_heartbeat_expiry():
    tick = [0.0]
    store = module.TrackerSettingsStore(clock=lambda: tick[0])
    desired = store.snapshot()
    acknowledgement = {
        "revision": desired["revision"], "blink_only": False,
        "tracker_session_id": "one", "eye_camera_index": 1,
    }
    store.acknowledge(**acknowledgement, eye_camera_state="open")
    store.request_camera_pause("capture-one")
    assert not store.camera_pause_released("capture-one")
    tick[0] = 100
    assert not store.snapshot()["tracker_connected"]
    assert not store.camera_pause_released("capture-one")
    store.acknowledge(**acknowledgement, eye_camera_state="released")
    assert store.camera_pause_released("capture-one")
    store.clear_camera_pause("capture-one")
    assert store.snapshot()["camera_pause_job_id"] is None

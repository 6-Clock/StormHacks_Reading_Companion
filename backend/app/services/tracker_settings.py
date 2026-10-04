"""Desired tracker controls and acknowledgements from the live camera loop."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, StrictBool

from app.services.eye_telemetry import (
    MAX_FUTURE_SECONDS,
    STALE_SECONDS,
    _number,
    sanitize_snapshot,
)

HEARTBEAT_TIMEOUT_SECONDS = 3.0
EyeCameraState = Literal["released", "opening", "open", "closing", "error"]


class TrackerSettingsStore:
    """The API starts with gaze gating on; only a current acknowledgement is live."""

    def __init__(self, *, clock=time.monotonic, wall_clock=time.time) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._wall_clock = wall_clock
        self._blink_only = False
        self._revision = uuid4().hex
        self._applied_revision: str | None = None
        self._applied_blink_only: bool | None = None
        self._session: str | None = None
        self._eye_camera: int | None = None
        self._eye_camera_state: EyeCameraState = "released"
        self._camera_pause_job_id: str | None = None
        self._camera_pause_required_session: str | None = None
        self._camera_pause_release_seen = False
        self._last_ack: float | None = None
        self._diagnostics: dict | None = None
        self._diagnostics_sequence = 0
        self._diagnostics_expires = -float("inf")
        self._capture_timestamp: float | None = None

    def _snapshot(self) -> dict:
        connected = (
            self._last_ack is not None
            and 0 <= self._clock() - self._last_ack <= HEARTBEAT_TIMEOUT_SECONDS
        )
        return {
            "blink_only": self._blink_only,
            "revision": self._revision,
            "applied_revision": self._applied_revision if connected else None,
            "applied_blink_only": self._applied_blink_only if connected else None,
            "tracker_connected": connected,
            "tracker_session_id": self._session if connected else None,
            "eye_camera_index": self._eye_camera if connected else None,
            "eye_camera_state": self._eye_camera_state if connected else None,
            "camera_pause_job_id": self._camera_pause_job_id,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def update(self, blink_only: bool) -> dict:
        with self._lock:
            if self._blink_only != blink_only:
                self._blink_only = blink_only
                self._revision = uuid4().hex
            return self._snapshot()

    def request_camera_pause(self, job_id: str) -> None:
        """Keep the eye camera paused until this exact job finishes cleanup."""
        with self._lock:
            if self._camera_pause_job_id not in (None, job_id):
                raise RuntimeError("Another camera handoff is active.")
            if self._camera_pause_job_id == job_id:
                return
            self._camera_pause_job_id = job_id
            # Retain ownership evidence even after heartbeat expiry. An older
            # tracker may ignore pauses and may not yet use the OS camera lease.
            self._camera_pause_required_session = self._session
            self._camera_pause_release_seen = (
                self._session is None or self._eye_camera_state == "released"
            )

    def camera_pause_released(self, job_id: str) -> bool:
        """A stale heartbeat must never stand in for an explicit camera release."""
        with self._lock:
            return (
                self._camera_pause_job_id == job_id
                and self._camera_pause_release_seen
                and (self._session is None or self._eye_camera_state == "released")
            )

    def clear_camera_pause(self, job_id: str) -> None:
        with self._lock:
            if self._camera_pause_job_id == job_id:
                self._camera_pause_job_id = None
                self._camera_pause_required_session = None
                self._camera_pause_release_seen = False

    def acknowledge(
        self, *, revision: str, blink_only: bool, tracker_session_id: str, eye_camera_index: int,
        eye_camera_state: EyeCameraState = "open",
    ) -> dict:
        with self._lock:
            if revision != self._revision or blink_only != self._blink_only:
                raise ValueError("Tracker setting changed; fetch and apply the current revision.")
            connected = self._snapshot()["tracker_connected"]
            if connected and self._session != tracker_session_id:
                raise ValueError("Another eye tracker is active; stop it before starting this one.")
            if self._session != tracker_session_id:
                # Ownership and telemetry reset share this lock. An old HTTP request
                # cannot publish into the new session, including a delayed disconnect.
                self._diagnostics = None
                self._diagnostics_sequence = 0
                self._diagnostics_expires = -float("inf")
                self._capture_timestamp = None
            self._applied_revision = revision
            self._applied_blink_only = blink_only
            self._session = tracker_session_id
            self._eye_camera = eye_camera_index
            self._eye_camera_state = eye_camera_state
            if self._camera_pause_job_id is not None:
                if self._camera_pause_required_session is None:
                    self._camera_pause_required_session = tracker_session_id
                if self._camera_pause_required_session == tracker_session_id:
                    self._camera_pause_release_seen = eye_camera_state == "released"
            self._last_ack = self._clock()
            return self._snapshot()

    def publish_diagnostics(
        self, *, tracker_session_id: str, sequence: int, snapshot: dict,
    ) -> dict:
        with self._lock:
            if self._session != tracker_session_id or not self._snapshot()["tracker_connected"]:
                raise PermissionError("Acknowledge tracker settings before publishing diagnostics.")
            if sequence <= self._diagnostics_sequence:
                return {"accepted": False, "sequence": self._diagnostics_sequence,
                        "reason": "duplicate_or_out_of_order"}
            timestamp = _number(snapshot.get("updated_at"))
            if timestamp is None:
                raise ValueError("Snapshot updated_at must be the original frame capture time.")
            wall_now = self._wall_clock()
            age = wall_now - timestamp
            if age < -MAX_FUTURE_SECONDS:
                raise ValueError("Snapshot capture time is in the future.")
            live = snapshot.get("connected") is True
            if live and age >= STALE_SECONDS:
                raise ValueError("Snapshot is stale; send a freshly captured frame.")
            if live and self._capture_timestamp is not None and timestamp < self._capture_timestamp:
                raise ValueError("Snapshot capture time is older than the current frame.")
            if snapshot.get("camera_index") != self._eye_camera:
                raise ValueError("Snapshot camera must match the acknowledged eye camera.")
            expiry = self._clock() + max(0, STALE_SECONDS - max(0, age)) if live else -float("inf")
            if self._capture_timestamp == timestamp:
                # A frozen frame with a higher sequence may change UI metadata,
                # but cannot gain freshness, even if the wall clock moves backward.
                expiry = min(expiry, self._diagnostics_expires)
            self._diagnostics = sanitize_snapshot(snapshot, now=wall_now)
            self._diagnostics_sequence = sequence
            self._diagnostics_expires = expiry
            self._capture_timestamp = timestamp
            return {"accepted": True, "sequence": sequence}

    def diagnostics(self) -> dict:
        with self._lock:
            snapshot = self._diagnostics
            if snapshot is None:
                return sanitize_snapshot(None)
            if self._clock() < self._diagnostics_expires and self._snapshot()["tracker_connected"]:
                return deepcopy(snapshot)
            # Use stored capture time only for schema cleanup; monotonic time alone
            # decides expiry after receipt, so a system-clock change cannot revive it.
            return sanitize_snapshot({**snapshot, "connected": False}, now=snapshot["updated_at"])


class TrackerSettingsPatch(BaseModel):
    blink_only: StrictBool


class TrackerSettingsAck(TrackerSettingsPatch):
    revision: str = Field(min_length=1, max_length=100)
    tracker_session_id: str = Field(min_length=1, max_length=100)
    eye_camera_index: int = Field(ge=0, le=10)
    eye_camera_state: EyeCameraState = "open"


tracker_settings = TrackerSettingsStore()
router = APIRouter(prefix="/v1/tracker-settings", tags=["tracker"])


@router.get("")
def get_tracker_settings(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return tracker_settings.snapshot()


@router.patch("")
def patch_tracker_settings(request: TrackerSettingsPatch, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return tracker_settings.update(request.blink_only)


@router.post("/ack")
def acknowledge_tracker_settings(request: TrackerSettingsAck, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return tracker_settings.acknowledge(**request.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

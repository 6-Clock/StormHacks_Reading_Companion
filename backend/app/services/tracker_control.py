"""Settings and page-turn bridge; networking stays off the camera loop."""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

POLL_SECONDS = 0.5
CONTROL_STALE_SECONDS = 2.5


@dataclass(frozen=True)
class TrackerControlSnapshot:
    blink_only: bool
    revision: str | None
    turns_blocked: bool
    connected: bool
    camera_pause_job_id: str | None
    should_pause: bool
    can_open_camera: bool


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method,
    )
    with urllib.request.urlopen(request, timeout=1.5) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Invalid tracker API response")
    return result


class TrackerControlClient:
    def __init__(
        self, api_url: str, *, eye_camera_index: int, camera_index: int | None,
        send_command: Callable[[str], bool],
        cancel_command: Callable[[], None] | None = None,
        request: Callable = request_json, clock: Callable = time.monotonic,
    ) -> None:
        self.base_url = api_url.rstrip("/")
        self.eye_camera_index = eye_camera_index
        self.camera_index = camera_index
        self.session_id = uuid4().hex
        self._request = request
        self._send_command = send_command
        self._cancel_command = cancel_command
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._poll_wakeup = threading.Event()
        self._released_acknowledged = threading.Event()
        self._events: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._poll_thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._desired_blink_only = False
        self._desired_revision: str | None = None
        self._received_at: float | None = None
        self._applied: tuple[str, bool] | None = None
        self._acknowledged_revision: str | None = None
        self._camera_pause_job_id: str | None = None
        self._eye_camera_state = "released"
        self._dispatching = False
        self._failed = False
        self._closing = False

    def start(self) -> None:
        self._poll_thread = threading.Thread(
            target=self._poll_forever, name="tracker-control-poll", daemon=True,
        )
        self._poll_thread.start()

    def _event(self, event_type: str, message: str) -> None:
        self._events.put((event_type, message))
        print(f"[TRACKER CONTROL] {message}", flush=True)

    def drain_events(self) -> list[tuple[str, str]]:
        result = []
        while True:
            try:
                result.append(self._events.get_nowait())
            except queue.Empty:
                return result

    def snapshot(self) -> TrackerControlSnapshot:
        with self._lock:
            connected = (
                self._received_at is not None
                and 0 <= self._clock() - self._received_at <= CONTROL_STALE_SECONDS
                and self._acknowledged_revision == self._desired_revision
            )
            paused = self._camera_pause_job_id is not None
            closing = self._closing or self._stop.is_set()
            return TrackerControlSnapshot(
                blink_only=self._desired_blink_only,
                revision=self._desired_revision,
                turns_blocked=(
                    self._dispatching or paused or closing or self._eye_camera_state != "open"
                ),
                connected=connected,
                camera_pause_job_id=self._camera_pause_job_id,
                should_pause=paused,
                can_open_camera=not paused and not closing,
            )

    def applied(
        self, revision: str | None, blink_only: bool, *, eye_camera_state: str = "open",
    ) -> None:
        if eye_camera_state not in {"released", "opening", "open", "closing", "error"}:
            raise ValueError("Unknown eye camera state")
        with self._lock:
            changed = self._eye_camera_state != eye_camera_state
            self._eye_camera_state = eye_camera_state
            self._applied = None if revision is None else (revision, blink_only)
            if changed:
                self._released_acknowledged.clear()
        if changed:
            self._poll_wakeup.set()

    def poll_once(self) -> None:
        try:
            desired = self._request(f"{self.base_url}/v1/tracker-settings")
            if not isinstance(desired.get("blink_only"), bool) or not desired.get("revision"):
                raise ValueError("Invalid tracker settings response")
            with self._lock:
                self._desired_blink_only = desired["blink_only"]
                self._desired_revision = desired["revision"]
                self._received_at = self._clock()
                self._camera_pause_job_id = desired.get("camera_pause_job_id")
                applied = self._applied
                eye_camera_state = self._eye_camera_state
                if self._closing and eye_camera_state == "released":
                    applied = (desired["revision"], desired["blink_only"])
            if applied == (desired["revision"], desired["blink_only"]):
                self._request(f"{self.base_url}/v1/tracker-settings/ack", "POST", {
                    "revision": applied[0], "blink_only": applied[1],
                    "tracker_session_id": self.session_id,
                    "eye_camera_index": self.eye_camera_index,
                    "eye_camera_state": eye_camera_state,
                })
                with self._lock:
                    self._acknowledged_revision = applied[0]
                    if eye_camera_state == "released" and self._eye_camera_state == "released":
                        self._released_acknowledged.set()
            if self._failed:
                self._event("tracker", "Tracker control API reconnected.")
            self._failed = False
        except (OSError, ValueError, KeyError) as error:
            with self._lock:
                self._received_at = None
                self._acknowledged_revision = None
            if not self._failed:
                self._event("tracker", f"Tracker controls unavailable: {error}")
            self._failed = True

    def _poll_forever(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._poll_wakeup.wait(POLL_SECONDS)
            self._poll_wakeup.clear()

    def dispatch_flip(self) -> bool:
        with self._lock:
            if (
                self._dispatching or self._closing or self._stop.is_set()
                or self._camera_pause_job_id is not None or self._eye_camera_state != "open"
            ):
                return False
            self._dispatching = True
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_flip, name="page-turn-dispatch", daemon=True,
        )
        self._dispatch_thread.start()
        return True

    def _dispatch_flip(self) -> None:
        completed = False
        try:
            if self._closing or self._stop.is_set():
                return
            self._event(
                "command", "Page-turn request started; waiting for MCU sequence completion.",
            )
            result = self._send_command("flip right")
            if self._closing or self._stop.is_set():
                return
            if result is False:
                self._event("command", "Page-turn preview; no serial command was sent.")
                return
            if result is not True:
                raise ValueError("Serial sender did not confirm MCU sequence completion")
            completed = True
            self._event("command", "MCU acknowledged page-turn sequence completion.")
            if self.camera_index is not None:
                job = self._request(f"{self.base_url}/v1/scan-jobs", "POST", {
                    "source": "automatic", "trigger_id": uuid4().hex,
                    "eye_camera_index": self.eye_camera_index, "camera_index": self.camera_index,
                })
                self._event("auto_scan", f"Page capture requested: {job['job_id']}.")
        except (OSError, ValueError, KeyError) as error:
            outcome = "completed; capture failed" if completed else "failed"
            self._event("command", f"Page-turn command {outcome}: {error}")
        finally:
            with self._lock:
                self._dispatching = False

    def begin_shutdown(self) -> None:
        with self._lock:
            self._closing = True
        if self._cancel_command is not None:
            self._cancel_command()

    def close(self) -> None:
        self.begin_shutdown()
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=3)
        with self._lock:
            released = self._eye_camera_state == "released"
        if released and self._poll_thread is not None and self._poll_thread.is_alive():
            self._released_acknowledged.clear()
            self._poll_wakeup.set()
            if not self._released_acknowledged.wait(timeout=2):
                self._event("tracker", "Eye camera released; API acknowledgement unavailable.")
        self._stop.set()
        self._poll_wakeup.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3)

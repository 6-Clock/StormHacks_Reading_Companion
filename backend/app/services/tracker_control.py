"""Standard-library bridge for the standalone tracker; networking stays off-frame."""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

POLL_SECONDS = 0.5
CONTROL_STALE_SECONDS = 2.5
TERMINAL_STATES = {"idle", "accepted", "rejected", "unchanged", "cancelled", "failed", "timed_out"}


@dataclass(frozen=True)
class TrackerControlSnapshot:
    blink_only: bool
    revision: str | None
    turns_blocked: bool
    connected: bool


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
        settle_seconds: float, send_command: Callable[[str], None],
        request: Callable = request_json, clock: Callable = time.monotonic,
    ) -> None:
        # Keep the existing --auto-scan-api flag compatible with its full endpoint.
        self.base_url = api_url.split("/v1/", 1)[0].rstrip("/")
        self.eye_camera_index = eye_camera_index
        self.camera_index = camera_index
        self.settle_seconds = settle_seconds
        self.session_id = uuid4().hex
        self._request = request
        self._send_command = send_command
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._events: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._poll_thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._desired_blink_only = False
        self._desired_revision: str | None = None
        self._received_at: float | None = None
        self._applied: tuple[str, bool, float] | None = None
        self._acknowledged_revision: str | None = None
        self._backend_busy = True
        self._dispatching = False
        self._guard_job_id: str | None = None
        self._last_failure = -float("inf")
        self._failed = False

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

    def _snapshot_locked(self, *, ignore_own_dispatch: bool = False) -> TrackerControlSnapshot:
        fresh = (
            self._received_at is not None
            and 0 <= self._clock() - self._received_at <= CONTROL_STALE_SECONDS
        )
        connected = fresh and self._acknowledged_revision == self._desired_revision
        return TrackerControlSnapshot(
            blink_only=self._desired_blink_only if fresh else False,
            revision=self._desired_revision if fresh else None,
            turns_blocked=(
                not connected or self._backend_busy
                or (self._dispatching and not ignore_own_dispatch)
                or self._guard_job_id is not None or self._stop.is_set()
            ),
            connected=connected,
        )

    def snapshot(self) -> TrackerControlSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def applied(self, revision: str | None, blink_only: bool) -> None:
        """Called only after the camera loop has applied a desired revision."""
        with self._lock:
            self._applied = None if revision is None else (revision, blink_only, self._clock())

    def poll_once(self) -> None:
        try:
            desired = self._request(f"{self.base_url}/v1/tracker-settings")
            latest = self._request(f"{self.base_url}/v1/scan-jobs/latest")
            if not isinstance(desired.get("blink_only"), bool) or not desired.get("revision"):
                raise ValueError("Invalid tracker settings response")
            if not isinstance(latest.get("status"), str):
                raise ValueError("Invalid scan status response")
            with self._lock:
                guard_job = self._guard_job_id
            guarded = latest
            if guard_job and latest.get("job_id") != guard_job:
                try:
                    guarded = self._request(f"{self.base_url}/v1/scan-jobs/{guard_job}")
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                    # The API restarted and no longer owns the old reservation.
                    guarded = {"status": "cancelled"}
            with self._lock:
                self._desired_blink_only = desired["blink_only"]
                self._desired_revision = desired["revision"]
                self._received_at = self._clock()
                self._backend_busy = latest["status"] not in TERMINAL_STATES
                if guard_job == self._guard_job_id and guarded["status"] in TERMINAL_STATES:
                    self._guard_job_id = None
                applied = self._applied
            # The UI's "applied" indication comes from the actual video loop,
            # never merely from this network worker receiving the desired value.
            if (
                applied is not None and applied[:2] == (desired["revision"], desired["blink_only"])
                and self._clock() - applied[2] <= CONTROL_STALE_SECONDS
            ):
                self._request(f"{self.base_url}/v1/tracker-settings/ack", "POST", {
                    "revision": applied[0], "blink_only": applied[1],
                    "tracker_session_id": self.session_id,
                    "eye_camera_index": self.eye_camera_index,
                })
                with self._lock:
                    self._acknowledged_revision = applied[0]
            else:
                with self._lock:
                    self._acknowledged_revision = None
            if self._failed:
                self._event("tracker", "Tracker control API reconnected; fresh gestures required.")
            self._failed = False
        except (OSError, ValueError, KeyError) as error:
            with self._lock:
                self._received_at = None
                self._acknowledged_revision = None
            now = self._clock()
            if not self._failed or now - self._last_failure >= 10:
                self._event("tracker", f"Tracker controls unavailable; page turns paused: {error}")
                self._last_failure = now
            self._failed = True

    def _poll_forever(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(POLL_SECONDS)

    def dispatch_flip(self) -> bool:
        """Try this gesture once; busy gestures are discarded, never deferred."""
        with self._lock:
            # Admission and marking the sole dispatch must be atomic with a
            # busy status arriving from the polling worker.
            if self._snapshot_locked().turns_blocked:
                return False
            self._dispatching = True
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_flip, name="page-turn-dispatch", daemon=True,
        )
        self._dispatch_thread.start()
        return True

    def _cancel_reservation(self, job_id: str) -> None:
        try:
            self._request(f"{self.base_url}/v1/scan-jobs/{job_id}/cancel", "POST", {})
        except (OSError, ValueError) as error:
            self._event("command", f"Could not cancel reservation {job_id}: {error}")

    def _dispatch_flip(self) -> None:
        job_id = None
        sent = False
        try:
            with self._lock:
                became_busy = self._snapshot_locked(ignore_own_dispatch=True).turns_blocked
            if became_busy:
                self._event(
                    "command", "Page turn skipped: scanner or tracker controls became busy.",
                )
                return
            try:
                reserved = self._request(f"{self.base_url}/v1/page-turns/reserve", "POST", {
                    "trigger_id": uuid4().hex, "eye_camera_index": self.eye_camera_index,
                    "camera_index": self.camera_index, "settle_seconds": self.settle_seconds,
                })
            except urllib.error.HTTPError as error:
                if error.code != 409:
                    raise
                # A scan can start after the last poll. The coordinator is the
                # final arbiter; this is a skip, with no retry or saved gesture.
                self._event(
                    "command", "Page turn skipped: another scan or page turn is active. "
                    "Blink again after it finishes.",
                )
                return
            job_id = reserved.get("job_id")
            if not job_id or reserved.get("status") != "reserved":
                raise ValueError("Page-turn reservation was not granted")
            with self._lock:
                self._guard_job_id = job_id
            if self._stop.is_set():
                self._cancel_reservation(job_id)
                return
            expiry = reserved.get("reservation_expires_at")
            # Serial write_timeout is one second. Reserve another 0.5 seconds
            # for scheduling; never send on a missing or nearly expired lease.
            if (
                isinstance(expiry, bool) or not isinstance(expiry, (float, int))
                or not math.isfinite(expiry) or expiry - time.time() < 1.5
            ):
                raise ValueError("Page-turn reservation is too short for a safe serial send")
            # No camera-loop callback writes serial until reserve succeeds.
            self._send_command("flip right")
            sent = True
            self._event("command", "Reserved page turn: flip right dispatched.")
            commit_url = f"{self.base_url}/v1/page-turns/{job_id}/commit"
            try:
                committed = self._request(commit_url, "POST", {})
            except (OSError, ValueError):
                # A lost response can mean the first commit succeeded. Retrying
                # the idempotent commit never sends a second hardware command.
                committed = self._request(commit_url, "POST", {})
            if committed.get("status") in {"cancelling", "cancelled", "failed", "timed_out"}:
                raise ValueError(f"Coordinator did not start OCR: {committed['status']}")
            next_stage = "capture" if self.camera_index is not None else "rearming"
            self._event("auto_scan", f"Page turn {job_id}: settling before {next_stage}.")
        except (OSError, ValueError) as error:
            outcome = "sent; coordination failed" if sent else "blocked"
            self._event("command", f"Page turn {outcome}: {error}")
            if job_id and not sent:
                self._cancel_reservation(job_id)
        finally:
            with self._lock:
                self._dispatching = False
                # Force a fresh coordinator poll before accepting another blink
                # sequence, even if reserve failed before its response arrived.
                self._received_at = None

    def close(self) -> None:
        self._stop.set()
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=6)
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5)

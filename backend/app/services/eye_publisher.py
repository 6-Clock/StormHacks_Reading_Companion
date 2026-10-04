"""Latest-only HTTP diagnostics, independent of the frame and control workers."""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import math
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.services.eye_telemetry import (
    EVENT_TYPES,
    MAX_EVENTS,
    MAX_SNAPSHOT_BYTES,
    PUBLISH_INTERVAL,
    STALE_SECONDS,
    sanitize_snapshot,
)

REQUEST_TIMEOUT = 0.75
MAX_BACKOFF = 2.0
FUTURE_TOLERANCE = 0.25
MAX_RESPONSE_BYTES = 4_096
logger = logging.getLogger(__name__)


def _local_endpoint(api_url: str) -> tuple[str, int, str]:
    parsed = urlsplit(api_url)
    host = parsed.hostname
    if parsed.scheme != "http" or host is None or parsed.username or parsed.password:
        raise ValueError("Eye diagnostics needs a local http URL without credentials.")
    if host == "localhost":
        host = "127.0.0.1"
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
        port = parsed.port or 80
    except ValueError as error:
        raise ValueError("Eye diagnostics supports localhost, 127.0.0.1 or ::1 only.") from error
    path = parsed.path.split("/v1/", 1)[0].rstrip("/") + "/v1/diagnostics/eyes"
    return host, port, path


def post_snapshot(api_url: str, payload: bytes, *, timeout: float = REQUEST_TIMEOUT) -> None:
    """Bound the entire local request, including a slow response body."""
    host, port, path = _local_endpoint(api_url)
    timeout = min(timeout, REQUEST_TIMEOUT)
    if timeout <= 0:
        raise TimeoutError("Eye diagnostics request deadline expired")
    deadline = time.monotonic() + timeout
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    watchdog: threading.Timer | None = None
    try:
        # The host is numeric loopback: this cannot wait for remote DNS.
        connection.connect()
        transport = connection.sock
        remaining = deadline - time.monotonic()
        if transport is None or remaining <= 0:
            raise TimeoutError("Eye diagnostics connection deadline expired")
        transport.settimeout(remaining)

        def abort_request() -> None:
            # Keep the original socket reference: HTTPResponse may detach it
            # from HTTPConnection when the peer declares Connection: close.
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            transport.close()

        watchdog = threading.Timer(remaining, abort_request)
        watchdog.daemon = True
        watchdog.start()
        connection.request("POST", path, body=payload, headers={
            "Content-Type": "application/json", "Connection": "close",
        })
        with connection.getresponse() as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if time.monotonic() >= deadline:
                raise TimeoutError("Eye diagnostics response deadline expired")
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Eye diagnostics response exceeded the size limit")
            if not 200 <= response.status < 300:
                detail = body.decode("utf-8", errors="replace")[:200]
                raise OSError(f"Eye diagnostics API returned {response.status}: {detail}")
    finally:
        if watchdog is not None:
            watchdog.cancel()
        connection.close()


@dataclass(frozen=True)
class _PendingSnapshot:
    sequence: int
    captured_at: float
    captured_monotonic: float
    payload: bytes


class EyeTelemetryPublisher:
    """Copy frame measurements immediately; upload at most five times a second."""

    def __init__(
        self, api_url: str, session_id: str, *, request: Callable = post_snapshot,
        monotonic: Callable = time.monotonic, wallclock: Callable = time.time,
        autostart: bool = True,
    ) -> None:
        _local_endpoint(api_url)
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 100:
            raise ValueError("A tracker session ID of 1 to 100 characters is required.")
        self.api_url = api_url
        self.session_id = session_id
        self._request = request
        self._monotonic = monotonic
        self._wallclock = wallclock
        self._condition = threading.Condition()
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._event_sequence = 0
        self._sequence = 0
        self._pending: _PendingSnapshot | None = None
        self._last_camera_index: int | None = None
        self._thread: threading.Thread | None = None
        self._next_attempt = -math.inf
        self._inflight = False
        self._failures = 0
        self._outage = False
        self._last_warning = -math.inf
        self._closing = False
        self._closed = False
        self._close_deadline: float | None = None
        if autostart:
            self.start()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None or self._closed:
                return
            self._thread = threading.Thread(
                target=self._run, name="eye-diagnostics-publisher", daemon=True,
            )
            self._thread.start()

    def event(self, event_type: str, message: str) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown eye telemetry event type: {event_type}")
        with self._condition:
            if self._closing or self._closed:
                return
            self._record_event(event_type, message)

    def _record_event(self, event_type: str, message: str) -> None:
        self._event_sequence += 1
        self._events.append({
            "id": f"{self.session_id[:48]}-{self._event_sequence}",
            "time": self._wallclock(), "type": event_type, "message": message[:200],
        })

    def _enqueue(self, measurements: dict[str, Any], captured_at: float) -> None:
        self._sequence += 1
        snapshot = sanitize_snapshot({
            **measurements, "updated_at": captured_at, "events": list(self._events),
        }, now=captured_at)
        envelope = {
            "tracker_session_id": self.session_id, "sequence": self._sequence,
            "snapshot": snapshot,
        }
        # Escaping Unicode can expand 200-character event messages beyond the
        # wire limit. Retain the newest events that actually fit in the body.
        while True:
            payload = json.dumps(envelope, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(payload) <= MAX_SNAPSHOT_BYTES:
                break
            if not snapshot["events"]:
                raise ValueError("Eye diagnostics snapshot exceeds the size limit")
            snapshot["events"].pop(0)
        now = self._monotonic()
        self._pending = _PendingSnapshot(
            sequence=self._sequence, captured_at=captured_at,
            captured_monotonic=now - max(0.0, self._wallclock() - captured_at),
            payload=payload,
        )
        self._last_camera_index = snapshot["camera_index"]
        self._condition.notify_all()

    def publish(
        self, measurements: dict[str, Any], *, captured_at: float, force: bool = False,
    ) -> bool:
        """Enqueue immutable data without any filesystem or network operation.

        Every frame replaces the pending frame, including forced snapshots.
        Sending is throttled by the worker so force cannot congest the API.
        """
        if (
            isinstance(captured_at, bool) or not isinstance(captured_at, (float, int))
            or not math.isfinite(captured_at)
            or not -FUTURE_TOLERANCE <= self._wallclock() - captured_at < STALE_SECONDS
        ):
            return False
        with self._condition:
            if self._closing or self._closed:
                return False
            self._enqueue(measurements, captured_at)
            return True

    def _fresh(self, packet: _PendingSnapshot) -> bool:
        age = self._wallclock() - packet.captured_at
        return (
            -FUTURE_TOLERANCE <= age < STALE_SECONDS
            and self._monotonic() - packet.captured_monotonic < STALE_SECONDS
        )

    def _send_once(self) -> bool:
        """Attempt one eligible upload; separate method permits deterministic tests."""
        with self._condition:
            if self._closed or self._inflight:
                return False
            packet = self._pending
            if packet is None:
                return False
            if not self._fresh(packet):
                self._pending = None
                return False
            started = self._monotonic()
            if started < self._next_attempt:
                return False
            timeout = REQUEST_TIMEOUT
            if self._close_deadline is not None:
                timeout = min(timeout, self._close_deadline - time.monotonic())
                if timeout <= 0:
                    self._pending = None
                    return False
            self._pending = None
            self._inflight = True
        failure: Exception | None = None
        recovered = warn = False
        try:
            self._request(self.api_url, packet.payload, timeout=timeout)
        except (OSError, ValueError, http.client.HTTPException) as error:
            failure = error
        finally:
            with self._condition:
                self._inflight = False
                now = self._monotonic()
                if failure is None:
                    self._failures = 0
                    self._next_attempt = started + PUBLISH_INTERVAL
                    recovered = self._outage
                    self._outage = False
                else:
                    self._failures = min(self._failures + 1, 5)
                    delay = min(PUBLISH_INTERVAL * 2 ** (self._failures - 1), MAX_BACKOFF)
                    self._next_attempt = (
                        started + PUBLISH_INTERVAL if self._closing else now + delay
                    )
                    # One pending slot: retry only when a newer frame hasn't
                    # already replaced this one. Retries retain their identity.
                    if self._pending is None and not self._closing and self._fresh(packet):
                        self._pending = packet
                    if not self._outage or now - self._last_warning >= 30:
                        warn = True
                        self._last_warning = now
                    self._outage = True
                self._condition.notify_all()
        # Logging is worker-only and outside the queue lock, so slow console
        # handlers cannot delay the camera thread's next publish call.
        if recovered:
            logger.info("Eye diagnostics connection recovered.")
        if warn:
            logger.warning(
                "Eye diagnostics API unavailable; camera tracking continues: %s",
                str(failure)[:250],
            )
        return True

    def _run(self) -> None:
        while True:
            if self._send_once():
                continue
            with self._condition:
                if self._closed or (self._closing and self._pending is None):
                    return
                remaining = self._next_attempt - self._monotonic()
                wait = 0.2 if self._pending is None else min(0.2, max(0.01, remaining))
                self._condition.wait(timeout=wait)

    def close(self) -> None:
        """Best-effort final disconnect; never hold camera cleanup past one second."""
        with self._condition:
            if self._closed or self._closing:
                return
            self._closing = True
            self._close_deadline = time.monotonic() + 0.9
            self._record_event("tracker", "Eye tracker stopped")
            self._enqueue({"connected": False, "camera_index": self._last_camera_index},
                          self._wallclock())
            self._next_attempt = min(self._next_attempt, self._monotonic() + PUBLISH_INTERVAL)
        self.start()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.95)
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()

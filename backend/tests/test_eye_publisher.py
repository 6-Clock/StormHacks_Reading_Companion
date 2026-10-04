import json
import logging
import socket
import threading
import time

import pytest

from app.services.eye_publisher import (
    MAX_SNAPSHOT_BYTES,
    EyeTelemetryPublisher,
    post_snapshot,
)


class Clock:
    def __init__(self):
        self.tick = 0.0

    def monotonic(self):
        return self.tick

    def wallclock(self):
        return 1_000.0 + self.tick

    def advance(self, seconds):
        self.tick += seconds


def measurements():
    return {
        "connected": True, "camera_index": 2, "mode": "READ", "eyes_visible": True,
        "gaze": {"x": 0.25, "y": 0.5}, "openness": {"left": 1.0, "right": 0.9},
        "phase": "OPEN", "blink_count": 1, "look_progress": 0,
        "capture_fps": 30, "inference_fps": 25, "frame_age_ms": 20,
        "calibrated": True, "turns_blocked": False, "blink_only": True,
    }


def make_publisher(request, clock=None):
    clock = clock or Clock()
    publisher = EyeTelemetryPublisher(
        "http://127.0.0.1:8001/v1/auto-scans", "session-one", request=request,
        monotonic=clock.monotonic, wallclock=clock.wallclock, autostart=False,
    )
    return publisher, clock


def test_publish_is_copy_only_then_worker_preserves_original_capture_timestamp():
    calls = []
    publisher, clock = make_publisher(lambda url, body, **kwargs: calls.append(json.loads(body)))
    original = measurements()
    publisher.event("blink", "Blink one")
    assert publisher.publish(original, captured_at=clock.wallclock() - 0.1)
    assert not calls
    original["gaze"]["x"] = 0.9
    publisher.event("blink", "Blink two")
    clock.advance(0.4)
    assert publisher._send_once()
    assert calls[0]["snapshot"]["gaze"]["x"] == 0.25
    assert calls[0]["snapshot"]["updated_at"] == 999.9
    assert len(calls[0]["snapshot"]["events"]) == 1
    assert calls[0]["tracker_session_id"] == "session-one"
    assert calls[0]["sequence"] == 1


def test_one_inflight_request_and_one_latest_slot_without_blocking_capture():
    calls = []
    entered, release = threading.Event(), threading.Event()

    def slow_request(url, body, **kwargs):
        calls.append(json.loads(body))
        entered.set()
        assert release.wait(timeout=2)

    publisher, clock = make_publisher(slow_request)
    publisher.publish(measurements(), captured_at=clock.wallclock())
    sending = threading.Thread(target=publisher._send_once)
    sending.start()
    assert entered.wait(timeout=1)
    started = time.monotonic()
    for blink_count in (2, 3, 0):
        publisher.publish({**measurements(), "blink_count": blink_count},
                          captured_at=clock.wallclock())
    assert time.monotonic() - started < 0.1
    assert not publisher._send_once()
    assert len(calls) == 1
    release.set()
    sending.join(timeout=1)
    assert not sending.is_alive()
    clock.advance(0.2)
    assert publisher._send_once()
    assert len(calls) == 2
    assert calls[1]["snapshot"]["blink_count"] == 0
    assert calls[1]["sequence"] == 4
    assert publisher._pending is None


def test_five_hertz_limit_applies_even_to_forced_fresh_snapshots():
    sent = []
    publisher, clock = make_publisher(lambda *args, **kwargs: sent.append(clock.tick))
    for _ in range(101):
        publisher.publish(measurements(), captured_at=clock.wallclock(), force=True)
        publisher._send_once()
        clock.advance(0.01)
    assert len(sent) <= 6  # Including an upload at both ends of a one-second interval.
    assert all(second - first >= 0.2 for first, second in zip(sent, sent[1:], strict=False))


def test_frozen_frame_retries_do_not_change_identity_or_keep_it_live():
    calls = []

    def unavailable(url, body, **kwargs):
        calls.append(json.loads(body))
        raise ConnectionRefusedError("API is down")

    publisher, clock = make_publisher(unavailable)
    publisher.publish(measurements(), captured_at=clock.wallclock())
    assert publisher._send_once()
    clock.advance(0.2)
    assert publisher._send_once()
    assert calls[0] == calls[1]
    clock.advance(1.8)
    assert not publisher._send_once()
    assert publisher._pending is None
    assert len(calls) == 2
    assert not publisher.publish(measurements(), captured_at=1_000)
    assert not publisher.publish(measurements(), captured_at=float("nan"))
    assert not publisher.publish(measurements(), captured_at=clock.wallclock() + 0.3)


def test_outage_backoff_is_bounded_newest_frame_recovers_and_logs_once(caplog):
    caplog.set_level(logging.INFO, logger="app.services.eye_publisher")
    sent = []
    unavailable = [True]

    def request(url, body, **kwargs):
        sent.append(json.loads(body))
        if unavailable[0]:
            raise OSError("API offline")

    publisher, clock = make_publisher(request)
    for _ in range(6):
        publisher.publish(measurements(), captured_at=clock.wallclock())
        assert publisher._send_once()
        delay = publisher._next_attempt - clock.monotonic()
        assert 0.2 <= delay <= 2.0
        clock.advance(delay + 0.01)
    assert sum("unavailable" in item.message for item in caplog.records) == 1
    unavailable[0] = False
    fresh_time = clock.wallclock()
    publisher.publish({**measurements(), "blink_count": 2}, captured_at=fresh_time)
    assert publisher._send_once()
    assert sent[-1]["snapshot"]["updated_at"] == fresh_time
    assert sent[-1]["snapshot"]["blink_count"] == 2
    clock.advance(0.2)
    publisher.publish(measurements(), captured_at=clock.wallclock())
    publisher._send_once()
    assert sum("recovered" in item.message for item in caplog.records) == 1


def test_wire_size_event_count_and_message_length_are_bounded():
    sent = []
    publisher, clock = make_publisher(lambda url, body, **kwargs: sent.append(body))
    for number in range(40):
        publisher.event("blink", f"{number}:" + "\U0001f440" * 300)
    publisher.publish(measurements(), captured_at=clock.wallclock())
    publisher._send_once()
    assert len(sent[0]) <= MAX_SNAPSHOT_BYTES
    events = json.loads(sent[0])["snapshot"]["events"]
    assert 0 < len(events) <= 32
    assert all(len(item["message"]) <= 200 for item in events)
    assert events[-1]["message"].startswith("39:")


def test_wallclock_rewind_does_not_rejuvenate_queued_samples():
    clock = Clock()
    wall = [1_000.0]
    calls = []
    publisher = EyeTelemetryPublisher(
        "http://localhost:8001", "session", request=lambda *args, **kwargs: calls.append(args),
        monotonic=clock.monotonic, wallclock=lambda: wall[0], autostart=False,
    )
    publisher.publish(measurements(), captured_at=1_000.0)
    clock.advance(2.1)
    # Wall time appears unchanged; monotonic age still proves the sample stale.
    assert not publisher._send_once()
    assert not calls


def test_close_sends_newest_disconnected_sequence_and_is_bounded():
    calls = []
    done = threading.Event()

    def request(url, body, **kwargs):
        calls.append(json.loads(body))
        done.set()

    publisher = EyeTelemetryPublisher("http://localhost:8001", "session", request=request)
    publisher.publish(measurements(), captured_at=time.time())
    assert done.wait(timeout=1)
    started = time.monotonic()
    publisher.close()
    assert time.monotonic() - started < 1.0
    assert len(calls) == 2
    assert calls[-1]["sequence"] > calls[0]["sequence"]
    assert calls[-1]["snapshot"]["connected"] is False
    assert calls[-1]["snapshot"]["gaze"] is None
    assert calls[-1]["snapshot"]["events"][-1]["message"] == "Eye tracker stopped"
    assert not publisher.publish(measurements(), captured_at=time.time())
    publisher.close()


def test_close_never_waits_for_a_misbehaving_injected_transport():
    entered, release = threading.Event(), threading.Event()

    def request(*args, **kwargs):
        entered.set()
        release.wait(timeout=2)

    publisher = EyeTelemetryPublisher("http://localhost:8001", "session", request=request)
    publisher.publish(measurements(), captured_at=time.time())
    assert entered.wait(timeout=1)
    started = time.monotonic()
    publisher.close()
    assert time.monotonic() - started < 1.1
    release.set()
    publisher._thread.join(timeout=1)
    assert not publisher._thread.is_alive()


@pytest.mark.parametrize("url", ["https://localhost:8001", "http://remote.test:8001", "ftp://127.0.0.1"])
def test_nonlocal_or_unsupported_endpoint_is_rejected_clearly(url):
    with pytest.raises(ValueError, match="local|localhost"):
        EyeTelemetryPublisher(url, "session", autostart=False)


def test_transport_deadline_includes_a_trickling_response_body():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    port = listener.getsockname()[1]

    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4_096)
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n")
                for _ in range(40):
                    connection.sendall(b"x")
                    time.sleep(0.02)
        except OSError:
            pass
        finally:
            listener.close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    started = time.monotonic()
    with pytest.raises((OSError, TimeoutError)):
        post_snapshot(f"http://127.0.0.1:{port}", b"{}", timeout=0.12)
    assert time.monotonic() - started < 0.5
    server.join(timeout=1)
    assert not server.is_alive()

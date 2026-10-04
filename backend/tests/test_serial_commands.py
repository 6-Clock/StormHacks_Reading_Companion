import threading
import time

import pytest

from app.services.serial_commands import SerialCommandSender


class FakeSerial:
    def __init__(self, *, replies=None, write_count=None, **kwargs):
        self.is_open = True
        self.writes = []
        self.replies = replies
        self.write_count = write_count
        self.chunks = []
        self.read_entered = threading.Event()
        self.options = kwargs

    def reset_input_buffer(self):
        self.chunks.clear()

    def write(self, payload):
        self.writes.append(payload)
        self.request_id = payload.decode().split()[1]
        if self.replies:
            self.chunks.extend(self.replies(self.request_id))
        return len(payload) if self.write_count is None else self.write_count

    def read(self, size):
        self.read_entered.set()
        if self.chunks:
            chunk = self.chunks.pop(0)
            if isinstance(chunk, BaseException):
                raise chunk
            return chunk
        time.sleep(0.005)
        return b""

    def close(self):
        self.is_open = False


def sender_with(replies=None, *, timeout=0.15, write_count=None):
    connection = FakeSerial(replies=replies, write_count=write_count)
    sender = SerialCommandSender(
        "COM3", 115200, completion_timeout=timeout,
        serial_factory=lambda **kwargs: connection,
    )
    return sender, connection


def test_waits_for_matching_ack_then_done_amid_fragmented_telemetry():
    sender, connection = sender_with(lambda rid: [
        b"offline\r\n1281\r\nACK 0\r\nDONE 0\r\n",
        f"DONE {rid}\r\nA".encode(),
        f"CK {rid}\r".encode(),
        b"\n1340\r\nD",
        f"ONE {rid}\r\n".encode(),
    ])
    assert sender.send("flip right") is True
    assert len(connection.writes) == 1
    assert connection.writes[0] == f"50 {connection.request_id}\n".encode()
    assert 0 < int(connection.request_id) <= 0xFFFFFFFF


@pytest.mark.parametrize("response", ["ACK {id}", "DONE {id}", "ACK 0\r\nDONE 0", ""])
def test_missing_correlated_completion_times_out_without_retry(response):
    sender, connection = sender_with(
        lambda rid: [response.format(id=rid).encode() + b"\r\n"], timeout=0.025,
    )
    with pytest.raises(TimeoutError, match="Command was not retried"):
        sender.send("flip right")
    assert len(connection.writes) == 1


@pytest.mark.parametrize("status", [
    "BUSY", "ERROR START_REJECTED", "ERROR FEEDBACK_LOST", "ERROR TIMEOUT", "ERROR SERVICE_LATE",
])
def test_rejected_request_raises_without_retry(status):
    parts = status.split(maxsplit=1)
    sender, connection = sender_with(
        lambda rid: [f"{parts[0]} {rid} {' '.join(parts[1:])}\r\n".encode()],
    )
    with pytest.raises(OSError, match="MCU rejected page turn"):
        sender.send("flip right")
    assert len(connection.writes) == 1


def test_disconnect_raises_without_retry():
    sender, connection = sender_with(lambda rid: [
        f"ACK {rid}\r\n".encode(), OSError("USB disconnected"),
    ])
    with pytest.raises(OSError, match="USB disconnected"):
        sender.send("flip right")
    assert len(connection.writes) == 1


def test_partial_write_is_not_retried():
    sender, connection = sender_with(write_count=2)
    with pytest.raises(OSError, match="Incomplete page-turn command write"):
        sender.send("flip right")
    assert len(connection.writes) == 1


def test_preview_never_claims_mcu_completion():
    sender = SerialCommandSender(None, 115200)
    assert sender.send("flip right") is False
    sender.close()


def test_cancel_exits_wait_and_concurrent_send_is_blocked():
    sender, connection = sender_with(timeout=10)
    outcomes = []

    def send():
        try:
            sender.send("flip right")
        except OSError as error:
            outcomes.append(str(error))

    worker = threading.Thread(target=send)
    worker.start()
    assert connection.read_entered.wait(timeout=1)
    with pytest.raises(OSError, match="already awaiting"):
        sender.send("flip right")
    sender.cancel()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert len(outcomes) == 1 and "cancelled" in outcomes[0]
    assert len(connection.writes) == 1
    sender.close()
    assert not connection.is_open


def test_long_corrupt_line_cannot_disguise_a_done_reply():
    sender, _ = sender_with(lambda rid: [
        b"x" * 200 + f"DONE {rid}\r\nACK {rid}\r\nDONE {rid}\r\n".encode(),
    ])
    assert sender.send("flip right") is True


def test_request_id_changes_between_commands(monkeypatch):
    monkeypatch.setattr("app.services.serial_commands.secrets.randbelow", lambda maximum: 7)
    sender, connection = sender_with(lambda rid: [f"ACK {rid}\r\nDONE {rid}\r\n".encode()])
    assert sender.send("flip right") is True
    assert sender.send("flip right") is True
    assert connection.writes == [b"50 8\n", b"50 9\n"]


def test_stale_nonzero_transaction_cannot_confirm_current_request(monkeypatch):
    monkeypatch.setattr("app.services.serial_commands.secrets.randbelow", lambda maximum: 7)
    sender, connection = sender_with(
        lambda rid: [b"ACK 6\r\nDONE 6\r\nERROR 6 FEEDBACK_LOST\r\n"], timeout=0.025,
    )
    with pytest.raises(TimeoutError, match="waiting for MCU ACK"):
        sender.send("flip right")
    assert connection.writes == [b"50 8\n"]


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError, match="positive finite"):
        SerialCommandSender(None, 115200, completion_timeout=timeout)

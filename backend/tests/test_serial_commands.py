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
        self.timeout = kwargs.get("timeout", 0.1)

    def reset_input_buffer(self):
        self.chunks.clear()

    def write(self, payload):
        self.writes.append(payload)
        self.request_id = payload.decode().splitlines()[-1].split()[1]
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
    assert connection.writes[0] == f"!\n50 {connection.request_id}\n".encode()
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


class McuSerial(FakeSerial):
    def __init__(self, *, write_error=False, **kwargs):
        super().__init__(**kwargs)
        self.write_error = write_error
        self.pending = bytearray()
        self.executed = []

    def write(self, payload):
        written = super().write(payload)
        self.pending.extend(payload[:written])
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            fields = bytes(line).split(b" ")
            if fields == [b"50"]:
                request_id = b"0"
            elif len(fields) == 2 and fields[0] == b"50" and fields[1].isdigit():
                request_id = fields[1]
            else:
                continue
            self.executed.append(request_id)
            self.chunks.append(b"ACK " + request_id + b"\r\nDONE " + request_id + b"\r\n")
        if self.write_error:
            raise OSError("write timeout")
        return written


@pytest.mark.parametrize("written", range(7))
@pytest.mark.parametrize("write_error", [False, True])
def test_next_request_discards_partial_write_without_executing_it(
    monkeypatch, written, write_error,
):
    monkeypatch.setattr("app.services.serial_commands.secrets.randbelow", lambda maximum: 7)
    connection = McuSerial(write_count=written, write_error=write_error)
    sender = SerialCommandSender("COM3", 115200, serial_factory=lambda **kwargs: connection)
    message = "write timeout" if write_error else "Incomplete page-turn command write"
    with pytest.raises(OSError, match=message):
        sender.send("flip right")
    assert connection.executed == []
    assert len(connection.writes) == 1
    connection.write_count = None
    connection.write_error = False
    assert sender.send("flip right") is True
    assert connection.executed == [b"9"]
    assert len(connection.writes) == 2


@pytest.mark.parametrize("fragment", [b"50", b"50 123", b"50 ", b"garbage"])
def test_new_sender_invalidates_prior_host_fragment_without_motion(fragment):
    connection = McuSerial()
    connection.pending.extend(fragment)
    sender = SerialCommandSender("COM3", 115200, serial_factory=lambda **kwargs: connection)
    assert sender.send("flip right") is True
    assert connection.executed == [connection.request_id.encode()]


def test_frame_reset_does_not_repeat_completed_motion(monkeypatch):
    monkeypatch.setattr("app.services.serial_commands.secrets.randbelow", lambda maximum: 7)
    connection = McuSerial()
    sender = SerialCommandSender("COM3", 115200, serial_factory=lambda **kwargs: connection)
    assert sender.send("flip right") is True
    assert sender.send("flip right") is True
    assert connection.executed == [b"8", b"9"]


class TimedSerial(FakeSerial):
    def __init__(self, *, completion_at):
        super().__init__()
        self.now = 0.0
        self.completion_at = completion_at
        self.read_timeouts = []

    def write(self, payload):
        written = super().write(payload)
        self.events = [
            (0.001, f"ACK {self.request_id}\r\n".encode()),
            (self.completion_at, f"DONE {self.request_id}\r\n".encode()),
        ]
        return written

    def read(self, size):
        self.read_timeouts.append(self.timeout)
        self.now = round(self.now + self.timeout, 12)
        result = b""
        while self.events and self.events[0][0] <= self.now:
            result += self.events.pop(0)[1]
        assert len(result) < size
        return result


@pytest.mark.parametrize("timeout,completion_at", [(0.025, 0.015), (4.05, 4.01)])
def test_completion_received_before_deadline_survives_final_timed_read(
    monkeypatch, timeout, completion_at,
):
    connection = TimedSerial(completion_at=completion_at)
    monkeypatch.setattr("app.services.serial_commands.time.monotonic", lambda: connection.now)
    sender = SerialCommandSender(
        "COM3", 115200, completion_timeout=timeout, serial_factory=lambda **kwargs: connection,
    )
    assert sender.send("flip right") is True
    assert connection.now == pytest.approx(timeout)
    assert 0 < connection.read_timeouts[-1] < 0.1
    assert len(connection.writes) == 1


def test_completion_after_deadline_is_not_accepted_or_retried(monkeypatch):
    connection = TimedSerial(completion_at=4.06)
    monkeypatch.setattr("app.services.serial_commands.time.monotonic", lambda: connection.now)
    sender = SerialCommandSender(
        "COM3", 115200, completion_timeout=4.05, serial_factory=lambda **kwargs: connection,
    )
    with pytest.raises(TimeoutError, match="DONE after ACK"):
        sender.send("flip right")
    assert connection.now == pytest.approx(4.05)
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
    assert connection.writes == [b"!\n50 8\n", b"!\n50 9\n"]


def test_stale_nonzero_transaction_cannot_confirm_current_request(monkeypatch):
    monkeypatch.setattr("app.services.serial_commands.secrets.randbelow", lambda maximum: 7)
    sender, connection = sender_with(
        lambda rid: [b"ACK 6\r\nDONE 6\r\nERROR 6 FEEDBACK_LOST\r\n"], timeout=0.025,
    )
    with pytest.raises(TimeoutError, match="waiting for MCU ACK"):
        sender.send("flip right")
    assert connection.writes == [b"!\n50 8\n"]


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError, match="positive finite"):
        SerialCommandSender(None, 115200, completion_timeout=timeout)

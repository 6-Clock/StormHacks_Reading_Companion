"""Correlate page-turn requests with MCU sequence acknowledgements."""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Callable

UART_COMMANDS = {"flip right": "50"}
DEFAULT_COMPLETION_TIMEOUT = 10.0


class SerialCommandSender:
    def __init__(
        self, port: str | None, baud: int, *,
        completion_timeout: float = DEFAULT_COMPLETION_TIMEOUT,
        serial_factory: Callable | None = None,
    ) -> None:
        if not math.isfinite(completion_timeout) or completion_timeout <= 0:
            raise ValueError("Serial completion timeout must be a positive finite number")
        self.completion_timeout = completion_timeout
        self._cancelled = threading.Event()
        self._send_lock = threading.Lock()
        self._last_request_id = 0
        self.connection = None
        if port:
            if serial_factory is None:
                import serial

                serial_factory = serial.Serial
            self.connection = serial_factory(
                port=port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                timeout=0.1, write_timeout=min(1.0, completion_timeout),
                xonxoff=False, rtscts=False, dsrdtr=False,
            )
            print(f"UART connected: {port} at {baud} baud (8N1, no flow control)")
        else:
            print("UART preview mode: use --port COM3 when the USB-to-TTL adapter is connected.")

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise OSError("Page-turn wait cancelled; MCU motion may still be in progress")

    def send(self, command: str) -> bool:
        """Return True only after ACK and DONE for this request; never retry motion."""
        try:
            uart_command = UART_COMMANDS[command]
        except KeyError as error:
            raise ValueError(f"Unsupported UART command: {command!r}") from error
        if not self._send_lock.acquire(blocking=False):
            raise OSError("A page-turn command is already awaiting MCU completion")
        try:
            self._check_cancelled()
            request_id = secrets.randbelow(0xFFFFFFFF) + 1
            if request_id == self._last_request_id:
                request_id = request_id % 0xFFFFFFFF + 1
            self._last_request_id = request_id
            # A bare newline could execute a buffered partial motion command.
            payload = f"!\n{uart_command} {request_id}\n".encode("ascii")
            if self.connection is None:
                print(f"[UART PREVIEW] {command!r} -> {payload!r}")
                return False
            deadline = time.monotonic() + self.completion_timeout
            self.connection.reset_input_buffer()
            written = self.connection.write(payload)
            if written != len(payload):
                raise OSError("Incomplete page-turn command write; motion outcome is unknown")
            print(f"[UART TX] {command!r} -> {payload!r}")
            acknowledged = False
            pending = bytearray()
            discard_line = False
            while True:
                self._check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if not self.connection.is_open:
                    raise OSError("Serial connection closed; page-turn outcome is unknown")
                self.connection.timeout = min(0.1, remaining)
                chunk = self.connection.read(256)
                self._check_cancelled()
                # A timed read can return the final frame as the deadline expires.
                for byte in chunk:
                    if byte != 10:
                        if not discard_line:
                            pending.append(byte)
                            if len(pending) > 128:
                                pending.clear()
                                discard_line = True
                        continue
                    if discard_line:
                        discard_line = False
                        continue
                    fields = bytes(pending).split()
                    pending.clear()
                    if len(fields) < 2 or fields[1] != str(request_id).encode("ascii"):
                        continue
                    status = fields[0]
                    if status == b"ACK" and len(fields) == 2:
                        acknowledged = True
                    elif status == b"DONE" and len(fields) == 2 and acknowledged:
                        self._check_cancelled()
                        print(f"[UART RX] MCU page-turn sequence completed: {request_id}")
                        return True
                    elif status in {b"BUSY", b"ERROR"}:
                        reason = b" ".join(fields[2:]).decode("ascii", errors="replace")
                        raise OSError(
                            f"MCU rejected page turn {request_id}: "
                            f"{status.decode('ascii')} {reason}".rstrip()
                        )
            phase = "DONE after ACK" if acknowledged else "ACK"
            raise TimeoutError(
                f"Timed out waiting for MCU {phase}; page-turn outcome is unknown. "
                "Command was not retried."
            )
        finally:
            self._send_lock.release()

    def cancel(self) -> None:
        """Cancel host waiting during shutdown without claiming to stop the MCU."""
        self._cancelled.set()

    def close(self) -> None:
        self.cancel()
        with self._send_lock:
            if self.connection is not None and self.connection.is_open:
                self.connection.close()

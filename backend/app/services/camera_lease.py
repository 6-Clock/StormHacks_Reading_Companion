"""Cross-process ownership of LOOB's cameras, released by the operating system.

The process holding an open camera owns this lock until its camera is closed.
It has no timeout or heartbeat expiry: a stalled camera is still an open camera.
"""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from typing import BinaryIO

CAMERA_LOCK_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "cameras.lock"


class CameraLease:
    def __init__(self, path: Path = CAMERA_LOCK_PATH) -> None:
        self._path = path
        self._file: BinaryIO | None = None
        self._mutex = threading.Lock()

    def acquire(self) -> bool:
        """Acquire immediately, or return False while another owner holds it."""
        with self._mutex:
            if self._file is not None:
                return True
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
            handle = os.fdopen(fd, "r+b", buffering=0)
            try:
                # Windows byte-range locks may extend beyond EOF, so there is
                # no need to modify a file that another process already owns.
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                handle.close()
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    return False
                raise
            self._file = handle
            return True

    def release(self) -> None:
        """Call only after camera capture and release have both finished."""
        with self._mutex:
            handle = self._file
            if handle is None:
                return
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
                self._file = None

"""Crash-safe local state and cooperative locks for the Linux/macOS control host."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()
_held = threading.local()


@contextmanager
def file_lock(path: Path):
    """Serialize threads and processes; nested calls on the same thread are reentrant."""
    key = str(path.resolve())
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def write_json(path: Path, value) -> None:
    """Replace a complete JSON document atomically; never expose a truncated state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

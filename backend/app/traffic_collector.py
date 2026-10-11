"""Incremental, bounded Caddy access-log collection over the existing node SSH helper."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from app import nodes
from app.nodes import Node
from app.traffic import TrafficMonitor, normalize_caddy_log

log = logging.getLogger(__name__)
DEFAULT_LOG_FILE = "$HOME/caddy/access-logs/access.json"
_ANCHOR_BYTES = 128


def _default_lock_path() -> Path:
    app_path = str(Path(__file__).resolve().parents[1]).encode()
    app_hash = hashlib.sha256(app_path).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"cloudmorph-traffic-{os.getuid()}-{app_hash}.lock"


@dataclass
class Cursor:
    inode: str | None = None
    offset: int = 0
    anchor_hash: str | None = None
    initialized: bool = False
    pending: bytes = b""
    discarding_oversized_line: bool = False


@dataclass(frozen=True)
class PollResult:
    read_bytes: int = 0
    ingested: int = 0
    rejected: int = 0
    gap: bool = False
    error: str | None = None


def _read_command(
    log_file: str,
    *,
    expected_inode: str | None,
    offset: int,
    read_data: bool,
    max_bytes: int,
) -> str:
    if not log_file.startswith("$HOME/") or any(c in log_file for c in "\n\r"):
        raise ValueError("log_file must be a path below the SSH user's home directory")
    file_expr = f'"$HOME"/{shlex.quote(log_file[len("$HOME/") :])}'
    expected = shlex.quote(expected_inode or "")
    baseline = not read_data and not expected_inode
    if baseline:
        start_command = 'start_offset="$size"'
    else:
        start_command = (
            f'if [ {expected} != "$inode" ] || [ {offset} -gt "$size" ]; '
            f"then start_offset=0; else start_offset={offset}; fi"
        )
    data_command = ""
    if read_data:
        data_command = (
            'if [ "$read_size" -gt 0 ]; then '
            'tail -c +$((start_offset + 1)) /proc/self/fd/3 | head -c "$read_size"; '
            "fi"
        )
    return (
        f"file={file_expr}; "
        'if [ ! -f "$file" ]; then printf "MISSING 0 0\\n"; exit 0; fi; '
        'exec 3< "$file" || exit 1; '
        'meta=$(stat -Lc "%i %s" /proc/self/fd/3) || exit 1; '
        "set -- $meta; inode=$1; size=$2; "
        f"{start_command}; "
        "read_size=$((size - start_offset)); "
        f'if [ "$read_size" -gt {max_bytes} ]; then read_size={max_bytes}; fi; '
        f"before_start=$((start_offset - {_ANCHOR_BYTES})); "
        'if [ "$before_start" -lt 0 ]; then before_start=0; fi; '
        "before_length=$((start_offset - before_start)); "
        "before_hash=$(tail -c +$((before_start + 1)) /proc/self/fd/3 "
        '| head -c "$before_length" | sha256sum | cut -d " " -f 1) || exit 1; '
        "end_offset=$((start_offset + read_size)); "
        f"after_start=$((end_offset - {_ANCHOR_BYTES})); "
        'if [ "$after_start" -lt 0 ]; then after_start=0; fi; '
        "after_length=$((end_offset - after_start)); "
        "after_hash=$(tail -c +$((after_start + 1)) /proc/self/fd/3 "
        '| head -c "$after_length" | sha256sum | cut -d " " -f 1) || exit 1; '
        'printf "%s %s %s %s %s %s\\n" "$inode" "$size" "$start_offset" '
        '"$read_size" "$before_hash" "$after_hash"; '
        f"{data_command}"
    )


class CaddyLogCollector:
    """Poll one router's rotated access log and feed complete JSON lines to TrafficMonitor."""

    def __init__(
        self,
        router: Node,
        monitor: TrafficMonitor,
        *,
        interval_seconds: float = 10.0,
        log_file: str = DEFAULT_LOG_FILE,
        max_batch_bytes: int = 256 * 1024,
        max_line_bytes: int = 65_536,
        lock_path: str | Path | None = None,
    ) -> None:
        if interval_seconds <= 0 or max_batch_bytes < 1 or max_line_bytes < 1:
            raise ValueError("interval_seconds, max_batch_bytes, and max_line_bytes must be positive")
        if max_line_bytes > monitor.max_line_bytes:
            raise ValueError("max_line_bytes cannot exceed TrafficMonitor.max_line_bytes")
        if not log_file.startswith("$HOME/") or any(c in log_file for c in "\n\r"):
            raise ValueError("log_file must be a path below the SSH user's home directory")
        self.router = router
        self.monitor = monitor
        self.interval_seconds = interval_seconds
        self.log_file = log_file
        self.max_batch_bytes = max_batch_bytes
        self.max_line_bytes = max_line_bytes
        self.lock_path = Path(lock_path) if lock_path is not None else _default_lock_path()
        self.cursor = Cursor()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._lock_fd: int | None = None
        self._start_attempted = False
        self.start_error: str | None = None
        self.last_result = PollResult()

    @property
    def thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_running(self) -> bool:
        return self.thread_alive and not self._stop.is_set()

    def _acquire_singleton_lock(self) -> tuple[int | None, str | None]:
        try:
            import fcntl
        except ImportError:
            return None, "process_lock_unsupported"

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.lock_path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                os.close(fd)
                return None, "process_lock_unsafe"
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None, "collector_already_running"
        except OSError:
            if "fd" in locals():
                os.close(fd)
            return None, "process_lock_unavailable"
        return fd, None

    def _release_singleton_lock(self) -> None:
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _gap(self, *, error: str | None = None) -> PollResult:
        self.monitor.note_gap()
        self.last_result = PollResult(gap=True, error=error)
        return self.last_result

    def _command(self) -> str:
        return _read_command(
            self.log_file,
            expected_inode=self.cursor.inode if self.cursor.initialized else None,
            offset=self.cursor.offset,
            read_data=self.cursor.initialized,
            max_bytes=self.max_batch_bytes,
        )

    def poll_once(self) -> PollResult:
        """Read at most one bounded chunk; retry-safe because cursor state advances only after success."""
        with self._lock:
            try:
                result = nodes.ssh(self.router, self._command(), timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                return self._gap(error="ssh_unavailable")
            if result.returncode != 0:
                return self._gap(error="ssh_command_failed")

            output = result.stdout
            header, separator, data = output.partition(b"\n")
            if not separator:
                return self._gap(error="invalid_response")
            if header == b"MISSING 0 0":
                had_file = self.cursor.inode is not None
                self.cursor.inode = None
                self.cursor.offset = 0
                self.cursor.pending = b""
                self.cursor.discarding_oversized_line = False
                self.cursor.initialized = True
                return self._gap(error="log_file_missing" if had_file else None)
            try:
                inode, size_text, start_text, read_text, before_hash, after_hash = header.decode(
                    "ascii"
                ).split()
                size, start_offset, read_size = int(size_text), int(start_text), int(read_text)
            except (UnicodeDecodeError, ValueError):
                return self._gap(error="invalid_response")

            if size < 0 or start_offset < 0 or start_offset > size or read_size < 0:
                return self._gap(error="invalid_response")
            expected_read_size = min(size - start_offset, self.max_batch_bytes)
            if read_size != expected_read_size or len(data) != read_size:
                self.cursor = Cursor(initialized=False)
                return self._gap(error="file_changed_during_read")

            if not self.cursor.initialized:
                self.cursor.inode = inode
                self.cursor.offset = start_offset
                self.cursor.anchor_hash = after_hash
                self.cursor.initialized = True
                return self._gap()

            changed = self.cursor.inode != inode
            truncated = size < self.cursor.offset
            if (
                not changed
                and not truncated
                and start_offset == self.cursor.offset
                and self.cursor.anchor_hash is not None
                and before_hash != self.cursor.anchor_hash
            ):
                # Detect copy-truncate followed by rapid regrowth when the bytes at
                # the previous cursor boundary have changed. Drop this batch and
                # keep the monitor uncertain instead of classifying partial data.
                self.cursor.inode = inode
                self.cursor.offset = start_offset + len(data)
                self.cursor.anchor_hash = after_hash
                self.cursor.pending = b""
                self.cursor.discarding_oversized_line = False
                self.monitor.note_gap()
                self.last_result = PollResult(read_bytes=len(data), gap=True, error="log_rewrite_detected")
                return self.last_result

            if changed or truncated:
                self.cursor.pending = b""
                self.cursor.discarding_oversized_line = False
                self.cursor.inode = inode
                self.cursor.offset = 0
                if start_offset != 0:
                    return self._gap(error="invalid_response")
                gap = True
            else:
                gap = False
                if start_offset != self.cursor.offset:
                    return self._gap(error="invalid_response")

            if len(data) > self.max_batch_bytes:
                return self._gap(error="oversized_batch")
            self.cursor.inode = inode
            self.cursor.offset = start_offset + len(data)
            self.cursor.anchor_hash = after_hash
            ingested, rejected, oversized = self._consume(data)
            if oversized:
                self.monitor.note_gap()
                gap = True
            self.last_result = PollResult(read_bytes=len(data), ingested=ingested, rejected=rejected, gap=gap)
            return self.last_result

    def _consume(self, data: bytes) -> tuple[int, int, bool]:
        combined = self.cursor.pending + data
        self.cursor.pending = b""
        oversized = False

        if self.cursor.discarding_oversized_line:
            delimiter = combined.find(b"\n")
            if delimiter < 0:
                return 0, 0, False
            combined = combined[delimiter + 1 :]
            self.cursor.discarding_oversized_line = False

        lines = combined.split(b"\n")
        complete, remainder = lines[:-1], lines[-1]
        ingested = rejected = 0
        for line in complete:
            line = line.removesuffix(b"\r")
            if len(line) > self.max_line_bytes:
                oversized = True
                rejected += 1
                self.monitor.note_invalid("oversized")
                continue
            if line:
                record = normalize_caddy_log(
                    line,
                    max_future_skew_seconds=self.monitor.max_future_skew_seconds,
                    max_age_seconds=self.monitor.retention_seconds,
                    max_line_bytes=self.max_line_bytes,
                )
                if record is None:
                    try:
                        json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                        error_kind = "malformed"
                    else:
                        error_kind = "invalid"
                    self.monitor.note_invalid(error_kind)
                    rejected += 1
                elif self.monitor.ingest(record):
                    ingested += 1
                else:
                    rejected += 1

        if len(remainder) > self.max_line_bytes:
            self.cursor.discarding_oversized_line = True
            oversized = True
            rejected += 1
        elif not self.cursor.discarding_oversized_line:
            self.cursor.pending = remainder
        return ingested, rejected, oversized

    def start(self) -> bool:
        """Start only if this process can hold the host-wide collector lock."""
        if self.thread_alive:
            return self.is_running
        if self._start_attempted:
            self.start_error = "collector_start_already_attempted"
            return False
        self._start_attempted = True
        fd, error = self._acquire_singleton_lock()
        if fd is None:
            self.start_error = error
            return False
        self._lock_fd = fd
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="caddy-log-collector", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            self._release_singleton_lock()
            self.start_error = "collector_thread_start_failed"
            return False
        return True

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        if self.thread_alive:
            self._thread.join(timeout=max(timeout, 0))
        stopped = not self.thread_alive
        if stopped:
            self._release_singleton_lock()
        return stopped

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception:
                    log.exception("unexpected Caddy access-log collector failure")
                    self._gap(error="collector_error")
                self._stop.wait(self.interval_seconds)
        finally:
            self._release_singleton_lock()

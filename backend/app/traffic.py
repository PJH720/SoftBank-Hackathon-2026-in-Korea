"""Bounded, privacy-conscious traffic aggregation and rule-based anomaly detection.

This module accepts Caddy JSON access-log entries but performs no I/O or enforcement.
It deliberately has no dependency on the API, Fleet, deployment targets, or AI clients.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

_APP_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z")
_STATIC_PATH_SEGMENTS = frozenset(
    {
        "admin",
        "api",
        "assets",
        "auth",
        "callback",
        "docs",
        "events",
        "files",
        "health",
        "items",
        "login",
        "logout",
        "metrics",
        "oauth",
        "openapi.json",
        "orders",
        "products",
        "ready",
        "register",
        "search",
        "signup",
        "static",
        "status",
        "users",
        "v1",
        "v2",
        "v3",
        "webhook",
    }
)
_MAX_PATH_SEGMENTS = 8
_MAX_PATH_LENGTH = 4096


class AnomalyStatus(str, Enum):
    NORMAL_INCREASE = "normal_increase"
    ATTACK_SUSPECTED = "attack_suspected"
    UNCERTAIN = "uncertain"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class TrafficRecord:
    """The only per-request data retained after a Caddy log is normalized."""

    timestamp: float
    app_id: str
    status_code: int
    duration_ms: float
    path_category: str


@dataclass(frozen=True, slots=True)
class WindowMetrics:
    app_id: str
    window_seconds: int
    request_count: int
    rps: float
    status_code_counts: dict[int, int]
    status_code_ratios: dict[int, float]
    error_ratio: float
    auth_failure_ratio: float
    top_path_category: str | None
    path_concentration: float
    repeated_pattern_ratio: float
    p95_latency_ms: float | None


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    min_samples: int = 20
    traffic_growth_factor: float = 2.0
    high_error_ratio: float = 0.5
    high_auth_failure_ratio: float = 0.25
    high_path_concentration: float = 0.75
    high_repeated_pattern_ratio: float = 0.75

    def __post_init__(self) -> None:
        if isinstance(self.min_samples, bool) or self.min_samples < 1:
            raise ValueError("min_samples must be a positive integer")
        if not math.isfinite(self.traffic_growth_factor) or self.traffic_growth_factor <= 1:
            raise ValueError("traffic_growth_factor must be finite and greater than 1")
        for ratio in (
            self.high_error_ratio,
            self.high_auth_failure_ratio,
            self.high_path_concentration,
            self.high_repeated_pattern_ratio,
        ):
            if not math.isfinite(ratio) or not 0 <= ratio <= 1:
                raise ValueError("detection ratios must be finite values between 0 and 1")


@dataclass(frozen=True, slots=True)
class TrafficAnalysis:
    app_id: str
    generated_at: float
    status: AnomalyStatus
    current_1m: WindowMetrics
    current_5m: WindowMetrics
    previous_1m: WindowMetrics
    traffic_increased: bool
    signals: tuple[str, ...]
    reason: str
    data_quality: str


def _path_category(uri: str) -> str | None:
    if len(uri) > _MAX_PATH_LENGTH:
        return None
    try:
        path = urlsplit(uri).path
    except ValueError:
        return None

    segments = [segment.lower() for segment in path.split("/") if segment]
    if len(segments) > _MAX_PATH_SEGMENTS:
        segments = segments[:_MAX_PATH_SEGMENTS] + [":more"]
    # Keep known route vocabulary, but collapse arbitrary values and identifiers so
    # user names, tokens, and high-cardinality path components are not retained.
    safe_segments = [segment if segment in _STATIC_PATH_SEGMENTS else ":param" for segment in segments]
    return "/" + "/".join(safe_segments) if safe_segments else "/"


def _app_id(payload: dict[str, Any], request: dict[str, Any]) -> str | None:
    explicit = payload.get("app_id") or payload.get("app")
    host = explicit or request.get("host")
    if not isinstance(host, str) or len(host) > 255:
        return None
    if explicit:
        candidate = host.lower()
    else:
        try:
            hostname = urlsplit(f"//{host}").hostname
        except ValueError:
            return None
        if not hostname:
            return None
        candidate = hostname.lower().split(".", 1)[0]
    return candidate if _APP_ID.fullmatch(candidate) else None


def normalize_caddy_log(
    payload: str | bytes | dict[str, Any],
    *,
    now: float | None = None,
    max_future_skew_seconds: float = 60.0,
    max_age_seconds: float | None = None,
    max_line_bytes: int = 65_536,
) -> TrafficRecord | None:
    """Parse and minimize one Caddy JSON access log, returning ``None`` if invalid.

    Only ``ts``, ``request.host``, ``request.uri``, ``status``, and ``duration``
    are read. In particular, request headers, remote addresses, and query strings
    are never copied into the retained record.
    """
    if isinstance(payload, bytes):
        if len(payload) > max_line_bytes:
            return None
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        try:
            if len(payload.encode("utf-8")) > max_line_bytes:
                return None
        except UnicodeEncodeError:
            return None
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, RecursionError):
            return None
    if not isinstance(payload, dict):
        return None

    request = payload.get("request")
    if not isinstance(request, dict):
        request = {}

    try:
        timestamp = float(payload["ts"])
        duration = float(payload["duration"])
        status = payload["status"]
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    current_time = time.time() if now is None else now
    if (
        not math.isfinite(current_time)
        or not math.isfinite(max_future_skew_seconds)
        or max_future_skew_seconds < 0
        or (max_age_seconds is not None and (not math.isfinite(max_age_seconds) or max_age_seconds < 0))
    ):
        return None
    if (
        isinstance(payload.get("ts"), bool)
        or not math.isfinite(timestamp)
        or timestamp < 0
        or timestamp > current_time + max_future_skew_seconds
        or (max_age_seconds is not None and timestamp < current_time - max_age_seconds)
    ):
        return None
    if (
        isinstance(status, bool)
        or not isinstance(status, int)
        or not 100 <= status <= 599
        or not math.isfinite(duration)
        or not 0 <= duration <= 3600
    ):
        return None

    app_id = _app_id(payload, request)
    uri = request.get("uri")
    if app_id is None or not isinstance(uri, str):
        return None
    path_category = _path_category(uri)
    if path_category is None:
        return None

    return TrafficRecord(
        timestamp=timestamp,
        app_id=app_id,
        status_code=status,
        duration_ms=round(duration * 1000, 3),
        path_category=path_category,
    )


def aggregate_window(
    records: list[TrafficRecord] | tuple[TrafficRecord, ...],
    app_id: str,
    *,
    start: float,
    end: float,
    window_seconds: int,
) -> WindowMetrics:
    selected = [record for record in records if record.app_id == app_id and start <= record.timestamp <= end]
    total = len(selected)
    statuses = Counter(record.status_code for record in selected)
    paths = Counter(record.path_category for record in selected)
    repeated_patterns = Counter((record.path_category, record.status_code) for record in selected)
    top_path, top_path_count = paths.most_common(1)[0] if paths else (None, 0)
    repeated_count = repeated_patterns.most_common(1)[0][1] if repeated_patterns else 0
    latencies = sorted(record.duration_ms for record in selected)
    p95 = latencies[max(0, math.ceil(0.95 * total) - 1)] if total else None
    errors = sum(count for code, count in statuses.items() if code >= 400)
    auth_failures = sum(count for code, count in statuses.items() if code in (401, 403))

    return WindowMetrics(
        app_id=app_id,
        window_seconds=window_seconds,
        request_count=total,
        rps=round(total / window_seconds, 4),
        status_code_counts=dict(sorted(statuses.items())),
        status_code_ratios={code: round(count / total, 4) for code, count in sorted(statuses.items())}
        if total
        else {},
        error_ratio=round(errors / total, 4) if total else 0.0,
        auth_failure_ratio=round(auth_failures / total, 4) if total else 0.0,
        top_path_category=top_path,
        path_concentration=round(top_path_count / total, 4) if total else 0.0,
        repeated_pattern_ratio=round(repeated_count / total, 4) if total else 0.0,
        p95_latency_ms=round(p95, 3) if p95 is not None else None,
    )


class TrafficMonitor:
    """In-memory, bounded rolling buffer and deterministic, observation-only detector."""

    def __init__(
        self,
        *,
        max_events: int = 10_000,
        retention_seconds: int = 600,
        max_line_bytes: int = 65_536,
        max_future_skew_seconds: float = 60.0,
        config: DetectionConfig | None = None,
    ) -> None:
        if (
            isinstance(max_events, bool)
            or isinstance(retention_seconds, bool)
            or isinstance(max_line_bytes, bool)
            or max_events < 1
            or retention_seconds < 300
            or max_line_bytes < 1
            or not math.isfinite(max_future_skew_seconds)
            or max_future_skew_seconds < 0
        ):
            raise ValueError("max_events and max_line_bytes must be positive; retention must be >= 300s")
        self.max_events = max_events
        self.retention_seconds = retention_seconds
        self.max_line_bytes = max_line_bytes
        self.max_future_skew_seconds = max_future_skew_seconds
        self.config = config or DetectionConfig()
        self._records: deque[TrafficRecord] = deque(maxlen=max_events)
        self._input_errors: deque[tuple[float, str]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self.accepted_count = 0
        self.malformed_count = 0
        self.rejected_count = 0

    def _prune(self, now: float) -> None:
        cutoff = now - self.retention_seconds
        self._records = deque(
            (record for record in self._records if record.timestamp >= cutoff),
            maxlen=self.max_events,
        )
        self._input_errors = deque(
            (error for error in self._input_errors if error[0] >= cutoff),
            maxlen=self.max_events,
        )

    def note_invalid(self, kind: str = "invalid", *, now: float | None = None) -> None:
        """Record only an input error category, never the rejected line itself."""
        if kind not in {"invalid", "malformed", "oversized"}:
            raise ValueError("kind must be invalid, malformed, or oversized")
        current_time = time.time() if now is None else now
        with self._lock:
            self._prune(current_time)
            self.rejected_count += 1
            if kind == "malformed":
                self.malformed_count += 1
            self._input_errors.append((current_time, kind))

    def ingest(
        self,
        line: str | bytes | dict[str, Any] | TrafficRecord,
        *,
        now: float | None = None,
    ) -> bool:
        """Ingest one JSON log line. Invalid, stale, oversized, or malformed input is discarded."""
        current_time = time.time() if now is None else now
        if isinstance(line, TrafficRecord):
            line = {
                "ts": line.timestamp,
                "app_id": line.app_id,
                "status": line.status_code,
                "duration": line.duration_ms / 1000,
                "request": {"uri": line.path_category},
            }
        if isinstance(line, (str, bytes)):
            try:
                byte_length = len(line) if isinstance(line, bytes) else len(line.encode("utf-8"))
            except UnicodeEncodeError:
                byte_length = self.max_line_bytes + 1
            if byte_length > self.max_line_bytes:
                with self._lock:
                    self.rejected_count += 1
                    self._input_errors.append((current_time, "oversized"))
                    self._prune(current_time)
                return False

        record = normalize_caddy_log(
            line,
            now=current_time,
            max_future_skew_seconds=self.max_future_skew_seconds,
            max_age_seconds=self.retention_seconds,
            max_line_bytes=self.max_line_bytes,
        )
        with self._lock:
            self._prune(current_time)
            if record is None:
                if isinstance(line, (str, bytes)):
                    try:
                        json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, RecursionError):
                        error_kind = "malformed"
                    else:
                        error_kind = "invalid"
                else:
                    error_kind = "invalid"
                self.rejected_count += 1
                if error_kind == "malformed":
                    self.malformed_count += 1
                self._input_errors.append((current_time, error_kind))
                return False
            self._records.append(record)
            self.accepted_count += 1
            return True

    def note_gap(self, *, now: float | None = None) -> None:
        """Mark an upstream collector gap so a snapshot will not overstate confidence."""
        current_time = time.time() if now is None else now
        with self._lock:
            self._prune(current_time)
            self._input_errors.append((current_time, "gap"))

    def app_ids(self, *, now: float | None = None) -> tuple[str, ...]:
        """Return identifiers with retained normalized records, never raw request data."""
        current_time = time.time() if now is None else now
        with self._lock:
            self._prune(current_time)
            return tuple(sorted({record.app_id for record in self._records}))

    def analyze(self, app_id: str, *, now: float | None = None) -> TrafficAnalysis:
        current_time = time.time() if now is None else now
        if not math.isfinite(current_time):
            raise ValueError("now must be a finite timestamp")
        if not isinstance(app_id, str) or not _APP_ID.fullmatch(app_id):
            raise ValueError("app_id must match [a-z0-9][a-z0-9-]{0,39}")
        with self._lock:
            self._prune(current_time)
            records = tuple(self._records)
            has_recent_input_errors = any(
                current_time - 300 <= timestamp <= current_time for timestamp, _ in self._input_errors
            )
            error_kinds = {
                kind
                for timestamp, kind in self._input_errors
                if current_time - 300 <= timestamp <= current_time
            }

        current_1m = aggregate_window(
            records, app_id, start=current_time - 60, end=current_time, window_seconds=60
        )
        current_5m = aggregate_window(
            records, app_id, start=current_time - 300, end=current_time, window_seconds=300
        )
        previous_1m = aggregate_window(
            records,
            app_id,
            start=current_time - 120,
            end=current_time - 60 - 1e-9,
            window_seconds=60,
        )
        traffic_increased = (
            current_1m.request_count >= self.config.min_samples
            and previous_1m.request_count > 0
            and current_1m.rps >= previous_1m.rps * self.config.traffic_growth_factor
        )
        cfg = self.config
        auth_signal = current_1m.auth_failure_ratio >= cfg.high_auth_failure_ratio
        error_signal = current_1m.error_ratio >= cfg.high_error_ratio
        path_signal = current_1m.path_concentration >= cfg.high_path_concentration
        repeat_signal = current_1m.repeated_pattern_ratio >= cfg.high_repeated_pattern_ratio
        signals = tuple(
            name
            for enabled, name in (
                (traffic_increased, "traffic_increase"),
                (error_signal, "high_error_ratio"),
                (auth_signal, "high_auth_failure_ratio"),
                (path_signal, "high_path_concentration"),
                (repeat_signal, "repeated_path_status_pattern"),
            )
            if enabled
        )

        if has_recent_input_errors:
            status = AnomalyStatus.UNCERTAIN
            reason = "최근 로그에 누락·오류가 있어 이상 여부를 확정할 수 없습니다."
        elif current_1m.request_count < cfg.min_samples:
            status = AnomalyStatus.INSUFFICIENT_DATA
            reason = f"최근 1분 표본이 부족합니다 ({current_1m.request_count}/{cfg.min_samples})."
        elif (auth_signal and (path_signal or repeat_signal)) or (
            error_signal and path_signal and repeat_signal
        ):
            status = AnomalyStatus.ATTACK_SUSPECTED
            reason = "오류/인증 실패와 경로 집중 또는 반복 패턴이 함께 관측됐습니다."
        elif traffic_increased and not error_signal and not auth_signal:
            status = AnomalyStatus.NORMAL_INCREASE
            reason = "요청량이 증가했지만 오류·인증 실패 신호는 높지 않습니다."
        else:
            status = AnomalyStatus.UNCERTAIN
            reason = "관측된 신호만으로 정상 증가 또는 공격 의심을 구분하기 어렵습니다."

        quality = "degraded" if has_recent_input_errors else "complete"
        if "gap" in error_kinds:
            quality = "gap_reported"

        return TrafficAnalysis(
            app_id=app_id,
            generated_at=current_time,
            status=status,
            current_1m=current_1m,
            current_5m=current_5m,
            previous_1m=previous_1m,
            traffic_increased=traffic_increased,
            signals=signals,
            reason=reason,
            data_quality=quality,
        )

    def snapshot(self, app_id: str, *, now: float | None = None) -> dict[str, Any]:
        """Return a JSON-serializable, aggregate-only snapshot without request-level data."""
        analysis = self.analyze(app_id, now=now)
        result = asdict(analysis)
        result["status"] = analysis.status.value
        return result

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

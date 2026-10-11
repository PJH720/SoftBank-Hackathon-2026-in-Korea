"""Observation-only comparison between Fleet actions and traffic-informed recommendations."""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
from collections import deque
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from app import fleet
from app.traffic import AnomalyStatus, TrafficAnalysis, TrafficMonitor

_APP_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z")


class ShadowDecision(str, Enum):
    SCALE_OUT_CANDIDATE = "scale_out_candidate"
    SCALE_IN_CANDIDATE = "scale_in_candidate"
    HOLD = "hold"
    INSUFFICIENT_DATA = "insufficient_data"


class _WindowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_count: StrictInt = Field(ge=0, le=10_000)
    rps: StrictFloat = Field(ge=0, le=10_000)
    error_ratio: StrictFloat = Field(ge=0, le=1)
    auth_failure_ratio: StrictFloat = Field(ge=0, le=1)
    path_concentration: StrictFloat = Field(ge=0, le=1)
    p95_latency_ms: StrictFloat | None = Field(default=None, ge=0, le=3_600_000)


class TrafficShadowRecord(BaseModel):
    """Sanitized, bounded comparison record; no request-, host-, or node-level identifiers."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: StrictStr = Field(pattern=r"^[a-f0-9]{32}$")
    observed_at: StrictFloat = Field(ge=0)
    app_id: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    current_replica_count: StrictInt = Field(ge=0, le=100)
    cpu_percentages: tuple[StrictFloat | None, ...] = Field(max_length=100)
    node_load_per_core: tuple[StrictFloat, ...] = Field(max_length=100)
    node_load_hot: StrictBool | None
    current_1m: _WindowSummary
    current_5m: _WindowSummary
    rule_status: Literal["normal_increase", "attack_suspected", "uncertain", "insufficient_data"]
    rule_signals: tuple[
        Literal[
            "traffic_increase",
            "high_error_ratio",
            "high_auth_failure_ratio",
            "high_path_concentration",
            "repeated_path_status_pattern",
        ],
        ...,
    ] = Field(max_length=5)
    data_quality: Literal["complete", "degraded", "gap_reported"]
    actual_fleet_decision: Literal["scale_out", "scale_in", "hold", "blocked"]
    shadow_decision: ShadowDecision
    decisions_match: StrictBool | None
    node_load_probe_complete: StrictBool
    rationale: tuple[
        Literal[
            "traffic_increase_with_resource_pressure",
            "low_absolute_demand_and_low_resource_use",
            "attack_suspected_requires_security_review",
            "uncertain_requires_more_observation",
            "insufficient_recent_sample",
            "input_data_gap",
            "no_shadow_action_threshold_met",
        ],
        ...,
    ] = Field(max_length=3)
    recommendation: Literal["none", "security_review", "additional_observation"]
    auth_failure_measurement: Literal["http_401_403_status_proxy"]


class TrafficShadowConfig:
    def __init__(
        self,
        *,
        min_samples: int = 20,
        cpu_hot_percent: float = fleet.CPU_HOT,
        cpu_cold_percent: float = fleet.CPU_COLD,
        scale_out_latency_ms: float = 1000.0,
        scale_in_rps_threshold: float = 0.5,
        scale_in_latency_ms: float = 250.0,
    ) -> None:
        if min_samples < 1 or min_samples > 10_000:
            raise ValueError("min_samples must be between 1 and 10000")
        thresholds = (
            cpu_hot_percent,
            cpu_cold_percent,
            scale_out_latency_ms,
            scale_in_rps_threshold,
            scale_in_latency_ms,
        )
        if any(not math.isfinite(value) or value < 0 for value in thresholds):
            raise ValueError("shadow thresholds must be finite and non-negative")
        self.min_samples = min_samples
        self.cpu_hot_percent = cpu_hot_percent
        self.cpu_cold_percent = cpu_cold_percent
        self.scale_out_latency_ms = scale_out_latency_ms
        self.scale_in_rps_threshold = scale_in_rps_threshold
        self.scale_in_latency_ms = scale_in_latency_ms


class TrafficShadowStore:
    def __init__(
        self,
        *,
        max_events: int = 1000,
        max_events_per_app: int = 100,
        retention_seconds: int = 3600,
        clock=time.time,
    ) -> None:
        if min(max_events, max_events_per_app, retention_seconds) < 1:
            raise ValueError("shadow store limits must be positive")
        self.max_events = max_events
        self.max_events_per_app = max_events_per_app
        self.retention_seconds = retention_seconds
        self._clock = clock
        self._events: deque[TrafficShadowRecord] = deque()
        self._dedupe: dict[tuple[str, float], str] = {}
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.retention_seconds
        retained = [event for event in self._events if event.observed_at >= cutoff]
        retained = retained[-self.max_events :]
        self._events = deque(retained)
        retained_ids = {event.event_id for event in retained}
        self._dedupe = {key: event_id for key, event_id in self._dedupe.items() if event_id in retained_ids}

    def add(self, record: TrafficShadowRecord) -> bool:
        now = self._clock()
        key = (record.app_id, record.observed_at)
        with self._lock:
            self._prune_locked(now)
            if key in self._dedupe:
                return False
            app_records = [event for event in self._events if event.app_id == record.app_id]
            if len(app_records) >= self.max_events_per_app:
                oldest = app_records[0]
                self._events.remove(oldest)
                self._dedupe.pop((oldest.app_id, oldest.observed_at), None)
            self._events.append(record.model_copy(deep=True))
            while len(self._events) > self.max_events:
                oldest = self._events.popleft()
                self._dedupe.pop((oldest.app_id, oldest.observed_at), None)
            self._dedupe[key] = record.event_id
            return True

    def list(self, *, limit: int = 100) -> list[TrafficShadowRecord]:
        if limit < 1 or limit > self.max_events:
            raise ValueError(f"limit must be between 1 and {self.max_events}")
        with self._lock:
            self._prune_locked(self._clock())
            return [event.model_copy(deep=True) for event in reversed(self._events)][:limit]


class TrafficShadowEvaluator:
    """Evaluate sanitized metrics only; this class never invokes Fleet mutation functions."""

    def __init__(
        self,
        store: TrafficShadowStore,
        *,
        config: TrafficShadowConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or TrafficShadowConfig()

    @staticmethod
    def _window(metrics) -> _WindowSummary:
        return _WindowSummary(
            request_count=metrics.request_count,
            rps=float(metrics.rps),
            error_ratio=float(metrics.error_ratio),
            auth_failure_ratio=float(metrics.auth_failure_ratio),
            path_concentration=float(metrics.path_concentration),
            p95_latency_ms=(float(metrics.p95_latency_ms) if metrics.p95_latency_ms is not None else None),
        )

    @staticmethod
    def _actual_decision(action: object) -> Literal["scale_out", "scale_in", "hold", "blocked"]:
        if not isinstance(action, str):
            return "hold"
        if action.startswith("scale_out ->"):
            return "scale_out"
        if action == "scale_in":
            return "scale_in"
        if action == "scale_out blocked":
            return "blocked"
        return "hold"

    def evaluate(
        self,
        fleet_row: dict,
        monitor: TrafficMonitor,
        *,
        observed_at: float | None = None,
    ) -> TrafficShadowRecord:
        timestamp = time.time() if observed_at is None else observed_at
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("observed_at must be a finite non-negative timestamp")
        app_id = fleet_row.get("app")
        if not isinstance(app_id, str) or not _APP_ID.fullmatch(app_id):
            raise ValueError("Fleet row has an invalid app identifier")
        app_ids = monitor.app_ids(now=timestamp)
        if app_id not in app_ids:
            hostname = fleet_row.get("hostname")
            hostname_app = hostname.split(".", 1)[0].lower() if isinstance(hostname, str) else ""
            if hostname_app in app_ids:
                app_id = hostname_app

        analysis: TrafficAnalysis = monitor.analyze(app_id, now=timestamp)
        raw_replicas = fleet_row.get("replicas")
        replicas = raw_replicas if isinstance(raw_replicas, list) else []
        raw_cpus = fleet_row.get("sampled_cpu_percentages")
        if not isinstance(raw_cpus, list):
            raw_cpus = [replica.get("cpu") if isinstance(replica, dict) else None for replica in replicas]
        cpus: list[float | None] = []
        for raw_cpu in raw_cpus[:100]:
            if isinstance(raw_cpu, (int, float)) and not isinstance(raw_cpu, bool):
                value = float(raw_cpu)
                cpus.append(value if math.isfinite(value) and 0 <= value <= 10_000 else None)
            else:
                cpus.append(None)

        node_hot = fleet_row.get("node_hot")
        if not isinstance(node_hot, bool):
            node_hot = None
        node_load_probe_complete = fleet_row.get("node_load_probe_complete") is True
        raw_node_loads = fleet_row.get("node_load_per_core")
        node_loads = (
            tuple(
                float(value)
                for value in raw_node_loads[:100]
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and 0 <= value <= 10_000
            )
            if isinstance(raw_node_loads, list)
            else ()
        )
        current = analysis.current_1m
        latency = current.p95_latency_ms
        cpu_known = [cpu for cpu in cpus if cpu is not None]
        cpu_hot = any(cpu >= self.config.cpu_hot_percent for cpu in cpu_known)
        all_cpu_cold = bool(cpu_known) and all(cpu < self.config.cpu_cold_percent for cpu in cpu_known)

        reasons: list[str] = []
        recommendation: Literal["none", "security_review", "additional_observation"] = "none"
        if analysis.data_quality != "complete":
            decision = ShadowDecision.INSUFFICIENT_DATA
            reasons.append("input_data_gap")
            recommendation = "additional_observation"
        elif current.request_count < self.config.min_samples:
            decision = ShadowDecision.INSUFFICIENT_DATA
            reasons.append("insufficient_recent_sample")
            recommendation = "additional_observation"
        elif analysis.status is AnomalyStatus.ATTACK_SUSPECTED:
            decision = ShadowDecision.HOLD
            reasons.append("attack_suspected_requires_security_review")
            recommendation = "security_review"
        elif analysis.status is AnomalyStatus.UNCERTAIN:
            decision = ShadowDecision.HOLD
            reasons.append("uncertain_requires_more_observation")
            recommendation = "additional_observation"
        elif analysis.status is AnomalyStatus.NORMAL_INCREASE:
            if analysis.traffic_increased and (
                cpu_hot
                or node_hot is True
                or (latency is not None and latency >= self.config.scale_out_latency_ms)
            ):
                decision = ShadowDecision.SCALE_OUT_CANDIDATE
                reasons.append("traffic_increase_with_resource_pressure")
            elif (
                len(replicas) > 1
                and all_cpu_cold
                and node_hot is False
                and node_load_probe_complete
                and current.rps <= self.config.scale_in_rps_threshold
                and latency is not None
                and latency <= self.config.scale_in_latency_ms
            ):
                decision = ShadowDecision.SCALE_IN_CANDIDATE
                reasons.append("low_absolute_demand_and_low_resource_use")
            else:
                decision = ShadowDecision.HOLD
                reasons.append("no_shadow_action_threshold_met")
        else:
            decision = ShadowDecision.INSUFFICIENT_DATA
            reasons.append("insufficient_recent_sample")
            recommendation = "additional_observation"

        actual = self._actual_decision(fleet_row.get("action"))
        observed_replica_count = fleet_row.get("replica_count_before_action")
        if (
            isinstance(observed_replica_count, bool)
            or not isinstance(observed_replica_count, int)
            or observed_replica_count < 0
        ):
            observed_replica_count = len(replicas)
        return TrafficShadowRecord(
            event_id=uuid.uuid4().hex,
            observed_at=float(timestamp),
            app_id=app_id,
            current_replica_count=min(observed_replica_count, 100),
            cpu_percentages=tuple(cpus),
            node_load_per_core=node_loads,
            node_load_hot=node_hot,
            current_1m=self._window(analysis.current_1m),
            current_5m=self._window(analysis.current_5m),
            rule_status=analysis.status.value,
            rule_signals=tuple(analysis.signals),
            data_quality=analysis.data_quality,
            actual_fleet_decision=actual,
            shadow_decision=decision,
            decisions_match=(
                None
                if decision is ShadowDecision.INSUFFICIENT_DATA
                else actual == decision.value.removesuffix("_candidate")
            ),
            node_load_probe_complete=node_load_probe_complete,
            rationale=tuple(reasons),
            recommendation=recommendation,
            auth_failure_measurement="http_401_403_status_proxy",
        )

    def observe(
        self,
        fleet_row: dict,
        monitor: TrafficMonitor,
        *,
        observed_at: float | None = None,
    ) -> bool:
        return self.store.add(self.evaluate(fleet_row, monitor, observed_at=observed_at))

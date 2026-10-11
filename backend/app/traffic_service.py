"""Periodic, bounded bridge from rule detection to asynchronous AI explanations."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from app.traffic import AnomalyStatus, TrafficAnalysis, TrafficMonitor
from app.traffic_ai import (
    TrafficAIAnalyzer,
    TrafficAIResult,
    TrafficClassification,
    TrafficEvidence,
    TrafficRecommendation,
)

logger = logging.getLogger(__name__)


class IncidentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_count_1m: StrictInt = Field(ge=0, le=10_000)
    rps_1m: StrictFloat = Field(ge=0, le=10_000)
    error_ratio_1m: StrictFloat = Field(ge=0, le=1)
    auth_failure_ratio_1m: StrictFloat = Field(ge=0, le=1)
    path_concentration_1m: StrictFloat = Field(ge=0, le=1)
    repeated_pattern_ratio_1m: StrictFloat = Field(ge=0, le=1)
    p95_latency_ms_1m: StrictFloat | None = Field(default=None, ge=0, le=3_600_000)
    request_count_5m: StrictInt = Field(ge=0, le=10_000)
    rps_5m: StrictFloat = Field(ge=0, le=10_000)
    error_ratio_5m: StrictFloat = Field(ge=0, le=1)
    auth_failure_ratio_5m: StrictFloat = Field(ge=0, le=1)
    path_concentration_5m: StrictFloat = Field(ge=0, le=1)
    repeated_pattern_ratio_5m: StrictFloat = Field(ge=0, le=1)
    p95_latency_ms_5m: StrictFloat | None = Field(default=None, ge=0, le=3_600_000)


class TrafficIncident(BaseModel):
    """Sanitized in-memory incident and non-enforcing AI state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    app_id: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    occurred_at: StrictFloat
    rule_classification: Literal["attack_suspected", "uncertain"]
    rule_signals: tuple[TrafficEvidence, ...]
    data_quality: Literal["complete", "degraded", "gap_reported"]
    metrics: IncidentMetrics
    analysis_status: Literal["pending", "analyzed", "failed", "skipped"]
    analysis_reason: (
        Literal[
            "normal_status",
            "unsupported_status",
            "client_unavailable",
            "cooldown",
            "rate_limited",
            "concurrency_limited",
            "timeout",
            "api_error",
            "invalid_response",
            "invalid_input",
        ]
        | None
    ) = None
    ai_classification: TrafficClassification | None = None
    ai_explanation: str | None = Field(default=None, max_length=600)
    ai_evidence: tuple[TrafficEvidence, ...] = ()
    recommendations: tuple[TrafficRecommendation, ...] = ()


class TrafficIncidentStore:
    def __init__(
        self,
        *,
        max_events: int = 200,
        retention_seconds: int = 3600,
        clock=time.time,
    ) -> None:
        if max_events < 1 or retention_seconds < 1:
            raise ValueError("max_events and retention_seconds must be positive")
        self.max_events = max_events
        self.retention_seconds = retention_seconds
        self._clock = clock
        self._events: deque[TrafficIncident] = deque()
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.retention_seconds
        self._events = deque(
            (event for event in self._events if event.occurred_at >= cutoff),
            maxlen=self.max_events,
        )

    def add(self, incident: TrafficIncident) -> None:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            self._events.append(incident)

    def update(
        self,
        event_id: str,
        *,
        status: Literal["analyzed", "failed", "skipped"],
        reason: str | None = None,
        result: TrafficAIResult | None = None,
    ) -> bool:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            for index, event in enumerate(self._events):
                if event.event_id == event_id:
                    self._events[index] = event.model_copy(
                        update={
                            "analysis_status": status,
                            "analysis_reason": reason,
                            "ai_classification": result.classification if result else None,
                            "ai_explanation": result.explanation if result else None,
                            "ai_evidence": tuple(result.evidence) if result else (),
                            "recommendations": tuple(result.recommendations) if result else (),
                        }
                    )
                    return True
        return False

    def list(self, *, limit: int = 100) -> list[TrafficIncident]:
        if limit < 1 or limit > self.max_events:
            raise ValueError(f"limit must be between 1 and {self.max_events}")
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            return [event.model_copy(deep=True) for event in reversed(self._events)][:limit]

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return len(self._events)


def _incident_metrics(analysis: TrafficAnalysis) -> IncidentMetrics:
    one = analysis.current_1m
    five = analysis.current_5m
    return IncidentMetrics(
        request_count_1m=one.request_count,
        rps_1m=one.rps,
        error_ratio_1m=one.error_ratio,
        auth_failure_ratio_1m=one.auth_failure_ratio,
        path_concentration_1m=one.path_concentration,
        repeated_pattern_ratio_1m=one.repeated_pattern_ratio,
        p95_latency_ms_1m=one.p95_latency_ms,
        request_count_5m=five.request_count,
        rps_5m=five.rps,
        error_ratio_5m=five.error_ratio,
        auth_failure_ratio_5m=five.auth_failure_ratio,
        path_concentration_5m=five.path_concentration,
        repeated_pattern_ratio_5m=five.repeated_pattern_ratio,
        p95_latency_ms_5m=five.p95_latency_ms,
    )


class TrafficAnalysisService:
    """Periodically detect events and run AI calls off request/collector threads."""

    def __init__(
        self,
        monitor: TrafficMonitor,
        analyzer: TrafficAIAnalyzer,
        store: TrafficIncidentStore | None = None,
        *,
        interval_seconds: float = 30.0,
        max_pending: int = 2,
        clock=time.time,
    ) -> None:
        if interval_seconds <= 0 or max_pending < 1:
            raise ValueError("interval_seconds and max_pending must be positive")
        self.monitor = monitor
        self.analyzer = analyzer
        self.store = store if store is not None else TrafficIncidentStore(clock=clock)
        self.interval_seconds = interval_seconds
        self._clock = clock
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._inflight = threading.BoundedSemaphore(max_pending)
        self._workers: set[threading.Thread] = set()
        self._active_events: dict[str, str] = {}
        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.is_alive() and not self._stop.is_set()

    @property
    def worker_count(self) -> int:
        with self._lock:
            return sum(worker.is_alive() for worker in self._workers)

    def _signature(self, analysis: TrafficAnalysis) -> str:
        stable = {
            "app": analysis.app_id,
            "status": analysis.status.value,
            "signals": sorted(analysis.signals),
            "quality": analysis.data_quality,
        }
        return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def scan_once(self, *, now: float | None = None) -> int:
        """Schedule at most one analysis for each active rule signature."""
        timestamp = self._clock() if now is None else now
        active: dict[str, str] = {}
        scheduled = 0
        for app_id in self.monitor.app_ids(now=timestamp):
            try:
                analysis = self.monitor.analyze(app_id, now=timestamp)
            except (TypeError, ValueError):
                self.last_error = "invalid_rule_analysis"
                logger.warning("traffic analysis scheduler skipped invalid rule result")
                continue
            if analysis.status not in {AnomalyStatus.ATTACK_SUSPECTED, AnomalyStatus.UNCERTAIN}:
                continue
            signature = self._signature(analysis)
            if self._active_events.get(app_id) == signature:
                active[app_id] = signature
                continue
            if self._schedule(analysis, signature):
                active[app_id] = signature
                scheduled += 1
        self._active_events = active
        return scheduled

    def _schedule(self, analysis: TrafficAnalysis, signature: str) -> bool:
        if not self._inflight.acquire(blocking=False):
            self.last_error = "analysis_queue_full"
            return False

        event_id = uuid.uuid4().hex
        incident = TrafficIncident(
            event_id=event_id,
            app_id=analysis.app_id,
            occurred_at=analysis.generated_at,
            rule_classification=analysis.status.value,
            rule_signals=tuple(TrafficEvidence(signal) for signal in analysis.signals),
            data_quality=analysis.data_quality,
            metrics=_incident_metrics(analysis),
            analysis_status="pending",
        )
        self.store.add(incident)
        worker = threading.Thread(
            target=self._run_analysis,
            args=(event_id, analysis),
            name=f"traffic-ai-{event_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._workers.add(worker)
        try:
            worker.start()
        except RuntimeError:
            with self._lock:
                self._workers.discard(worker)
            self.store.update(event_id, status="failed", reason="api_error")
            self._inflight.release()
            self.last_error = "analysis_worker_start_failed"
            logger.error("traffic AI worker could not start")
            return False
        return True

    def _run_analysis(self, event_id: str, analysis: TrafficAnalysis) -> None:
        try:
            outcome = self.analyzer.analyze(analysis)
            self.store.update(
                event_id,
                status=outcome.state,
                reason=outcome.reason,
                result=outcome.analysis,
            )
        except Exception as exc:  # noqa: BLE001 — contain failures at the AI boundary
            # The rules and incident record remain authoritative if the AI helper fails.
            logger.warning("unexpected traffic AI worker failure (%s)", type(exc).__name__)
            self.store.update(event_id, status="failed", reason="api_error")
        finally:
            with self._lock:
                self._workers.discard(threading.current_thread())
            self._inflight.release()

    def start(self) -> bool:
        with self._lifecycle_lock:
            if self._scheduler is not None:
                return self.is_running
            self._stop.clear()
            self._scheduler = threading.Thread(
                target=self._run,
                name="traffic-analysis-scheduler",
                daemon=True,
            )
            try:
                self._scheduler.start()
            except RuntimeError:
                self._scheduler = None
                self.last_error = "scheduler_start_failed"
                return False
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        with self._lifecycle_lock:
            self._stop.set()
            scheduler = self._scheduler
        deadline = time.monotonic() + max(timeout, 0)
        if scheduler is not None and scheduler.is_alive():
            scheduler.join(timeout=max(0, deadline - time.monotonic()))
        with self._lock:
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        return not (
            (scheduler is not None and scheduler.is_alive()) or any(worker.is_alive() for worker in workers)
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as exc:  # noqa: BLE001 — keep scheduler alive after one bad scan
                self.last_error = "scheduler_error"
                logger.warning("traffic analysis scheduler failed (%s)", type(exc).__name__)
            self._stop.wait(self.interval_seconds)

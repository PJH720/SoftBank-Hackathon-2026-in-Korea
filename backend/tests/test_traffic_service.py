"""Tests for scheduled anomaly analysis and bounded incident storage."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app import main
from app.traffic import AnomalyStatus, TrafficMonitor
from app.traffic_ai import (
    TrafficAIOutcome,
    TrafficAIResult,
    TrafficClassification,
    TrafficEvidence,
    TrafficLimitation,
    TrafficRecommendation,
)
from app.traffic_service import (
    IncidentMetrics,
    TrafficAnalysisService,
    TrafficIncident,
    TrafficIncidentStore,
)


def _attack_analysis(app_id: str = "shop"):
    now = time.time()
    monitor = TrafficMonitor()
    for index in range(25):
        monitor.ingest(
            {
                "ts": now - 20 + index * 0.5,
                "app_id": app_id,
                "status": 401,
                "duration": 0.02,
                "request": {
                    "uri": "/account/secret-user?token=private-query",
                    "remote_ip": "203.0.113.9",
                    "headers": {
                        "Authorization": ["sensitive-auth-value"],
                        "Cookie": ["session=private-cookie"],
                    },
                    "body": "private-body",
                },
            },
            now=now,
        )
    return monitor, monitor.analyze(app_id, now=now)


class _StaticMonitor:
    def __init__(self, analysis):
        self.analysis = analysis

    def app_ids(self, *, now=None):
        return (self.analysis.app_id,)

    def analyze(self, app_id, *, now=None):
        assert app_id == self.analysis.app_id
        if now is None:
            return self.analysis
        return replace(self.analysis, generated_at=now)


class _FakeAnalyzer:
    def __init__(self, outcome=None, *, entered=None, release=None):
        self.outcome = outcome or TrafficAIOutcome(
            state="analyzed",
            analysis=TrafficAIResult(
                classification=TrafficClassification.ATTACK_SUSPECTED,
                explanation="오류 비율과 인증 실패 신호가 함께 증가했습니다.",
                evidence=[TrafficEvidence.HIGH_AUTH_FAILURE_RATIO],
                recommendations=[TrafficRecommendation.HUMAN_REVIEW],
                limitations=[
                    TrafficLimitation.NO_CLIENT_IDENTITY,
                    TrafficLimitation.AGGREGATES_CANNOT_CONFIRM_INTENT,
                ],
            ),
        )
        self.calls = []
        self.entered = entered
        self.release = release

    def analyze(self, analysis):
        self.calls.append(analysis)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=2)
        return self.outcome


def _wait_until(check, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.005)
    return check()


def test_detected_attack_is_scheduled_and_ai_result_is_stored():
    _, analysis = _attack_analysis()
    analyzer = _FakeAnalyzer()
    store = TrafficIncidentStore()
    service = TrafficAnalysisService(_StaticMonitor(analysis), analyzer, store)

    assert service.scan_once(now=analysis.generated_at) == 1
    assert _wait_until(lambda: store.list()[0].analysis_status == "analyzed")

    [incident] = store.list()
    assert incident.rule_classification == "attack_suspected"
    assert incident.ai_classification is TrafficClassification.ATTACK_SUSPECTED
    assert incident.recommendations == (TrafficRecommendation.HUMAN_REVIEW,)
    assert incident.event_id
    assert incident.occurred_at == analysis.generated_at


def test_normal_rule_result_does_not_schedule_ai():
    _, attack = _attack_analysis()
    normal = replace(attack, status=AnomalyStatus.NORMAL_INCREASE)
    analyzer = _FakeAnalyzer()
    service = TrafficAnalysisService(_StaticMonitor(normal), analyzer)

    assert service.scan_once(now=normal.generated_at) == 0
    assert analyzer.calls == []
    assert len(service.store) == 0


def test_uncertain_rule_result_is_scheduled():
    _, attack = _attack_analysis()
    uncertain = replace(attack, status=AnomalyStatus.UNCERTAIN)
    analyzer = _FakeAnalyzer()
    service = TrafficAnalysisService(_StaticMonitor(uncertain), analyzer)

    assert service.scan_once(now=uncertain.generated_at) == 1
    assert _wait_until(lambda: len(service.store) == 1)
    assert service.store.list()[0].rule_classification == "uncertain"


def test_repeated_scans_of_same_active_event_schedule_once():
    _, analysis = _attack_analysis()
    analyzer = _FakeAnalyzer()
    service = TrafficAnalysisService(_StaticMonitor(analysis), analyzer)

    assert service.scan_once(now=analysis.generated_at) == 1
    assert service.scan_once(now=analysis.generated_at + 1) == 0
    assert _wait_until(lambda: len(analyzer.calls) == 1)
    assert len(service.store) == 1


def test_ai_failure_leaves_rule_incident_and_classification_intact():
    _, analysis = _attack_analysis()
    analyzer = _FakeAnalyzer(TrafficAIOutcome(state="failed", reason="timeout"))
    service = TrafficAnalysisService(_StaticMonitor(analysis), analyzer)

    service.scan_once(now=analysis.generated_at)
    assert _wait_until(lambda: service.store.list()[0].analysis_status == "failed")

    incident = service.store.list()[0]
    assert incident.rule_classification == "attack_suspected"
    assert incident.analysis_reason == "timeout"
    assert incident.ai_classification is None


def test_store_is_bounded_and_expires_records():
    now = [1000.0]
    store = TrafficIncidentStore(max_events=2, retention_seconds=10, clock=lambda: now[0])
    metrics = IncidentMetrics(
        request_count_1m=25,
        rps_1m=0.4,
        error_ratio_1m=1.0,
        auth_failure_ratio_1m=1.0,
        path_concentration_1m=1.0,
        repeated_pattern_ratio_1m=1.0,
        p95_latency_ms_1m=20.0,
        request_count_5m=25,
        rps_5m=0.08,
        error_ratio_5m=1.0,
        auth_failure_ratio_5m=1.0,
        path_concentration_5m=1.0,
        repeated_pattern_ratio_5m=1.0,
        p95_latency_ms_5m=20.0,
    )

    for index in range(3):
        store.add(
            TrafficIncident(
                event_id=f"{index:032x}",
                app_id="shop",
                occurred_at=now[0],
                rule_classification="attack_suspected",
                rule_signals=(TrafficEvidence.HIGH_AUTH_FAILURE_RATIO,),
                data_quality="complete",
                metrics=metrics,
                analysis_status="pending",
            )
        )
    assert len(store) == 2
    assert [item.event_id for item in store.list(limit=2)] == [f"{2:032x}", f"{1:032x}"]

    now[0] += 11
    assert len(store) == 0


def test_incident_memory_and_api_output_exclude_sensitive_request_data():
    _, analysis = _attack_analysis("private-app-identifier")
    service = TrafficAnalysisService(_StaticMonitor(analysis), _FakeAnalyzer())
    service.scan_once(now=analysis.generated_at)
    assert _wait_until(lambda: service.store.list()[0].analysis_status == "analyzed")

    serialized = json.dumps(
        [incident.model_dump(mode="json") for incident in service.store.list()],
        ensure_ascii=False,
    )
    for secret in (
        "203.0.113.9",
        "private-query",
        "sensitive-auth-value",
        "private-cookie",
        "private-body",
        "/account",
    ):
        assert secret not in serialized
    assert service.store.list()[0].app_id == "private-app-identifier"


def test_scheduler_runs_separately_and_stop_waits_for_workers():
    _, analysis = _attack_analysis()
    entered = threading.Event()
    release = threading.Event()
    service = TrafficAnalysisService(
        _StaticMonitor(analysis),
        _FakeAnalyzer(entered=entered, release=release),
        interval_seconds=60,
    )

    assert service.start()
    assert entered.wait(timeout=1)
    assert service.worker_count == 1
    assert not service.stop(timeout=0.01)

    release.set()
    assert _wait_until(lambda: service.worker_count == 0)
    assert service.stop(timeout=1)
    assert not service.is_running


def test_scheduler_start_is_idempotent_and_analyzer_does_not_block_scan():
    _, analysis = _attack_analysis()
    entered = threading.Event()
    release = threading.Event()
    analyzer = _FakeAnalyzer(entered=entered, release=release)
    service = TrafficAnalysisService(
        _StaticMonitor(analysis),
        analyzer,
        interval_seconds=60,
    )

    assert service.start()
    assert entered.wait(timeout=1)
    assert service.start()
    assert service.worker_count == 1
    release.set()
    assert service.stop(timeout=2)
    assert len(analyzer.calls) == 1


def test_main_opt_in_startup_and_shutdown_lifecycle(monkeypatch):
    started = []
    stopped = []

    class StubCollector:
        def __init__(self, router, monitor, *, interval_seconds):
            self.is_running = False

        def start(self):
            self.is_running = True
            return True

        def stop(self):
            self.is_running = False
            stopped.append("collector")
            return True

    class StubService:
        def __init__(self, monitor, analyzer, store, *, interval_seconds):
            started.append((monitor, store, interval_seconds))
            self.is_running = False
            self.last_error = None

        def start(self):
            self.is_running = True
            started.append("start")
            return True

        def stop(self):
            self.is_running = False
            stopped.append("stop")
            return True

    monkeypatch.setattr(main, "TrafficAnalysisService", StubService)
    monkeypatch.setattr(main, "TrafficAIAnalyzer", lambda: object())
    monkeypatch.setattr(main, "CaddyLogCollector", StubCollector)
    monkeypatch.setattr(main.nodepool, "load_registry", lambda: type("Registry", (), {"router": object()})())
    monkeypatch.delenv("CLOUDMORPH_FLEET_WATCH", raising=False)
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_INTERVAL", "10")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_AI", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION_SINGLE_INSTANCE", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_AI_INTERVAL", "17")
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )
    shutdown = next(
        handler for handler in app.router.on_shutdown if handler.__name__ == "stop_traffic_collection"
    )

    asyncio.run(startup())
    asyncio.run(startup())

    assert started[0] == (app.state.traffic_monitor, app.state.traffic_incidents, 17.0)
    assert started.count("start") == 1
    asyncio.run(shutdown())
    assert stopped == ["stop", "collector"]


def test_main_keeps_ai_disabled_without_explicit_single_instance_opt_in(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_AI", "1")
    monkeypatch.delenv("CLOUDMORPH_TRAFFIC_COLLECTION_SINGLE_INSTANCE", raising=False)

    def fail_if_enabled():
        raise AssertionError("analyzer must not be created")

    monkeypatch.setattr(main, "TrafficAIAnalyzer", fail_if_enabled)
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )

    asyncio.run(startup())

    assert app.state.traffic_incidents.list() == []


def test_main_does_not_start_ai_when_collector_lock_is_unavailable(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_AI", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION_SINGLE_INSTANCE", "1")

    class LockedCollector:
        is_running = False
        start_error = "already_locked"

        def __init__(self, router, monitor, *, interval_seconds):
            pass

        def start(self):
            return False

    def fail_if_enabled():
        raise AssertionError("AI must not run without the collector singleton")

    monkeypatch.setattr(main, "CaddyLogCollector", LockedCollector)
    monkeypatch.setattr(main, "TrafficAIAnalyzer", fail_if_enabled)
    monkeypatch.setattr(
        main.nodepool,
        "load_registry",
        lambda: type("Registry", (), {"router": object()})(),
    )
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )

    asyncio.run(startup())

    assert app.state.traffic_incidents.list() == []


def test_traffic_incident_api_is_token_protected(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_API_TOKEN", "test-token")
    client = TestClient(main.create_app())

    denied = client.get("/traffic/incidents")
    allowed = client.get("/traffic/incidents", headers={"X-API-Token": "test-token"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {"incidents": []}

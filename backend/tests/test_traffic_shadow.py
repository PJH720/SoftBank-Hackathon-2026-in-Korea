"""Synthetic, observation-only Fleet shadow decision tests."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import fleet, main
from app.nodes import Node, Registry, Router
from app.traffic import AnomalyStatus, TrafficMonitor
from app.traffic_ai import TrafficEvidence
from app.traffic_service import TrafficIncident, _incident_metrics
from app.traffic_shadow import (
    ShadowDecision,
    TrafficShadowConfig,
    TrafficShadowEvaluator,
    TrafficShadowStore,
)


def _monitor(
    *,
    current_count: int = 40,
    previous_count: int = 10,
    status: int = 200,
    app_id: str = "shop",
    now: float | None = None,
) -> TrafficMonitor:
    timestamp = time.time() if now is None else now
    monitor = TrafficMonitor()
    for index in range(previous_count):
        monitor.ingest(
            {
                "ts": timestamp - 115 + index * (50 / max(previous_count, 1)),
                "app_id": app_id,
                "status": 200,
                "duration": 0.04,
                "request": {"uri": "/products"},
            },
            now=timestamp,
        )
    for index in range(current_count):
        monitor.ingest(
            {
                "ts": timestamp - 50 + index * (45 / max(current_count, 1)),
                "app_id": app_id,
                "status": status,
                "duration": 0.04,
                "request": {
                    "uri": "/login/private-user?token=private-query-marker",
                    "remote_ip": "203.0.113.99",
                    "headers": {
                        "Authorization": ["private-auth-marker"],
                        "Cookie": ["session=private-cookie-marker"],
                    },
                    "body": "private-body-marker",
                },
            },
            now=timestamp,
        )
    return monitor


def _row(
    *,
    cpu: float | None = 5,
    replicas: int = 1,
    node_hot: bool = False,
    node_load_probe_complete: bool = True,
    action=None,
    app_id: str = "shop",
):
    return {
        "app": app_id,
        "replicas": [{"node": "private-node", "upstream": "10.0.0.9:8080", "cpu": cpu}] * replicas,
        "node_hot": node_hot,
        "node_load_per_core": [0.1],
        "node_load_probe_complete": node_load_probe_complete,
        "action": action,
    }


def test_normal_traffic_increase_with_resource_pressure_is_shadow_scale_out_candidate():
    now = time.time()
    monitor = _monitor(now=now)
    assert monitor.analyze("shop", now=now).status is AnomalyStatus.NORMAL_INCREASE

    record = TrafficShadowEvaluator(TrafficShadowStore()).evaluate(_row(cpu=80), monitor, observed_at=now)

    assert record.shadow_decision is ShadowDecision.SCALE_OUT_CANDIDATE
    assert record.actual_fleet_decision == "hold"
    assert record.decisions_match is False
    assert "traffic_increase_with_resource_pressure" in record.rationale


def test_low_absolute_demand_and_cold_cpu_can_be_a_shadow_scale_in_candidate():
    now = time.time()
    monitor = _monitor(current_count=20, previous_count=5, now=now)
    assert monitor.analyze("shop", now=now).status is AnomalyStatus.NORMAL_INCREASE

    record = TrafficShadowEvaluator(TrafficShadowStore()).evaluate(
        _row(cpu=5, replicas=2), monitor, observed_at=now
    )

    assert record.shadow_decision is ShadowDecision.SCALE_IN_CANDIDATE
    assert record.current_replica_count == 2
    assert "low_absolute_demand_and_low_resource_use" in record.rationale


def test_attack_suspected_recommends_security_review_without_scale_action():
    now = time.time()
    monitor = _monitor(current_count=25, previous_count=10, status=401, now=now)
    assert monitor.analyze("shop", now=now).status is AnomalyStatus.ATTACK_SUSPECTED

    record = TrafficShadowEvaluator(TrafficShadowStore()).evaluate(
        _row(cpu=95, node_hot=True), monitor, observed_at=now
    )

    assert record.shadow_decision is ShadowDecision.HOLD
    assert record.recommendation == "security_review"
    assert "attack_suspected_requires_security_review" in record.rationale
    assert record.auth_failure_measurement == "http_401_403_status_proxy"


def test_uncertain_and_insufficient_data_are_not_action_candidates():
    now = time.time()
    evaluator = TrafficShadowEvaluator(TrafficShadowStore())
    uncertain_monitor = _monitor(current_count=25, previous_count=0, now=now)
    assert uncertain_monitor.analyze("shop", now=now).status is AnomalyStatus.UNCERTAIN
    uncertain = evaluator.evaluate(_row(cpu=90), uncertain_monitor, observed_at=now)
    assert uncertain.shadow_decision is ShadowDecision.HOLD
    assert uncertain.recommendation == "additional_observation"

    insufficient_monitor = _monitor(current_count=3, previous_count=0, now=now)
    insufficient = evaluator.evaluate(_row(cpu=90), insufficient_monitor, observed_at=now)
    assert insufficient.shadow_decision is ShadowDecision.INSUFFICIENT_DATA
    assert insufficient.decisions_match is None


def test_incomplete_node_load_probe_never_becomes_shadow_scale_in_candidate():
    now = time.time()
    monitor = _monitor(current_count=20, previous_count=5, now=now)
    record = TrafficShadowEvaluator(TrafficShadowStore()).evaluate(
        _row(cpu=5, replicas=2, node_hot=False, node_load_probe_complete=False),
        monitor,
        observed_at=now,
    )

    assert record.shadow_decision is ShadowDecision.HOLD
    assert record.node_load_probe_complete is False
    assert "no_shadow_action_threshold_met" in record.rationale


def test_collector_gap_forces_insufficient_shadow_decision():
    now = time.time()
    monitor = _monitor(now=now)
    monitor.note_gap(now=now)

    record = TrafficShadowEvaluator(TrafficShadowStore()).evaluate(_row(cpu=90), monitor, observed_at=now)

    assert record.data_quality == "gap_reported"
    assert record.shadow_decision is ShadowDecision.INSUFFICIENT_DATA


def test_existing_cpu_scale_out_decision_is_observed_without_running_fleet_actions(monkeypatch):
    now = time.time()
    monitor = _monitor(now=now)
    registry = Registry(
        nodes=[Node(name="node-a", host="10.0.0.1"), Node(name="node-b", host="10.0.0.2")],
        router=Router(name="router", host="10.0.0.3"),
    )
    app = fleet.App(
        name="shop",
        image="shop:latest",
        container_port=8080,
        hostname="shop.test",
        replicas=[fleet.Replica("node-a", "shop-a", "10.0.0.1", 8080)],
    )
    state = fleet.State(apps={"shop": app})
    before_state = json.dumps(
        {
            "replicas": [replica.upstream for replica in app.replicas],
            "hot_streak": app.hot_streak,
        },
        sort_keys=True,
    )
    before_config = fleet.render_caddy_config(registry, state)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Shadow evaluation must never invoke Fleet mutation operations")

    monkeypatch.setattr(fleet, "scale_out", forbidden)
    monkeypatch.setattr(fleet, "scale_in", forbidden)
    monkeypatch.setattr(fleet, "push_config", forbidden)
    record = TrafficShadowEvaluator(TrafficShadowStore()).evaluate(
        _row(cpu=80, action="scale_out -> node-b"), monitor, observed_at=now
    )

    assert record.actual_fleet_decision == "scale_out"
    assert record.shadow_decision is ShadowDecision.SCALE_OUT_CANDIDATE
    assert record.decisions_match is True
    assert (
        json.dumps(
            {
                "replicas": [replica.upstream for replica in app.replicas],
                "hot_streak": app.hot_streak,
            },
            sort_keys=True,
        )
        == before_state
    )
    assert fleet.render_caddy_config(registry, state) == before_config


def test_store_deduplicates_same_app_and_timestamp_and_enforces_limits():
    clock = [1000.0]
    store = TrafficShadowStore(
        max_events=2, max_events_per_app=2, retention_seconds=10, clock=lambda: clock[0]
    )
    now = clock[0]
    evaluator = TrafficShadowEvaluator(store)
    monitor = _monitor(now=now)

    assert evaluator.observe(_row(), monitor, observed_at=now)
    assert not evaluator.observe(_row(), monitor, observed_at=now)
    assert evaluator.observe(_row(), monitor, observed_at=now + 1)
    assert evaluator.observe(_row(), monitor, observed_at=now + 2)
    assert len(store.list(limit=2)) == 2
    assert [record.observed_at for record in store.list(limit=2)] == [now + 2, now + 1]

    clock[0] = now + 20
    assert store.list(limit=2) == []


def test_shadow_record_does_not_retain_private_request_data():
    now = time.time()
    monitor = _monitor(status=401, now=now)
    record = TrafficShadowEvaluator(TrafficShadowStore()).evaluate(_row(), monitor, observed_at=now)
    serialized = record.model_dump_json()

    for marker in (
        "203.0.113.99",
        "private-node",
        "10.0.0.9",
        "private-user",
        "private-query-marker",
        "private-auth-marker",
        "private-cookie-marker",
        "private-body-marker",
    ):
        assert marker not in serialized


def test_shadow_endpoint_uses_existing_traffic_token_guard(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_API_TOKEN", "test-token")
    client = TestClient(main.create_app(), client=("203.0.113.20", 50000))

    assert client.get("/traffic/shadow").status_code == 401
    response = client.get("/traffic/shadow", headers={"X-API-Token": "test-token"})

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "comparisons": []}
    assert client.get("/deploy/missing/events").status_code == 401


def _incident(app_id: str, event_id: str) -> TrafficIncident:
    monitor = _monitor(app_id=app_id)
    analysis = monitor.analyze(app_id)
    return TrafficIncident(
        event_id=event_id,
        app_id=app_id,
        occurred_at=analysis.generated_at,
        rule_classification="attack_suspected",
        rule_signals=(TrafficEvidence.HIGH_AUTH_FAILURE_RATIO,),
        data_quality="complete",
        metrics=_incident_metrics(analysis),
        analysis_status="pending",
    )


def test_traffic_apis_isolate_users_and_follow_current_project_ownership(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_API_TOKEN", "review-admin-token")
    monkeypatch.setenv("CLOUDMORPH_USERS", "alice:review-alice-token,bob:review-bob-token")
    projects = {
        "shop": {"name": "shop", "owner": "alice"},
        "other": {"name": "other", "owner": "bob"},
    }
    monkeypatch.setattr(main, "_projects", projects)
    app = main.create_app()

    incidents = (
        _incident("shop", "a" * 32),
        _incident("other", "b" * 32),
        _incident("orphan", "c" * 32),
    )
    for incident in incidents:
        app.state.traffic_incidents.add(incident)

    evaluator = TrafficShadowEvaluator(app.state.traffic_shadow)
    for app_id, record_id in (("shop", "d" * 32), ("other", "e" * 32)):
        monitor = _monitor(app_id=app_id)
        record = evaluator.evaluate(_row(app_id=app_id), monitor)
        app.state.traffic_shadow.add(record.model_copy(update={"event_id": record_id}))
    orphan_shadow = evaluator.evaluate(_row(), _monitor())
    app.state.traffic_shadow.add(orphan_shadow.model_copy(update={"app_id": "orphan", "event_id": "f" * 32}))

    alice = TestClient(app, client=("203.0.113.10", 50000))
    bob = TestClient(app, client=("203.0.113.11", 50000))
    admin = TestClient(app, client=("203.0.113.12", 50000))

    for endpoint in ("/traffic/incidents", "/traffic/shadow"):
        assert alice.get(endpoint).status_code == 401
        assert bob.get(endpoint).status_code == 401

    alice_incidents = alice.get("/traffic/incidents", headers={"X-API-Token": "review-alice-token"}).json()[
        "incidents"
    ]
    alice_shadow = alice.get("/traffic/shadow", headers={"X-API-Token": "review-alice-token"}).json()[
        "comparisons"
    ]
    bob_incidents = bob.get("/traffic/incidents", headers={"X-API-Token": "review-bob-token"}).json()[
        "incidents"
    ]
    bob_shadow = bob.get("/traffic/shadow", headers={"X-API-Token": "review-bob-token"}).json()["comparisons"]

    assert [record["app_id"] for record in alice_incidents] == ["shop"]
    assert [record["app_id"] for record in alice_shadow] == ["shop"]
    assert [record["app_id"] for record in bob_incidents] == ["other"]
    assert [record["app_id"] for record in bob_shadow] == ["other"]
    assert "b" * 32 not in json.dumps(alice_incidents)
    assert "e" * 32 not in json.dumps(alice_shadow)
    assert "a" * 32 not in json.dumps(bob_incidents)
    assert "d" * 32 not in json.dumps(bob_shadow)
    assert "orphan" not in json.dumps(alice_incidents + alice_shadow)
    assert "orphan" not in json.dumps(bob_incidents + bob_shadow)

    projects["shop"]["owner"] = "bob"
    assert (
        alice.get("/traffic/incidents", headers={"X-API-Token": "review-alice-token"}).json()["incidents"]
        == []
    )
    assert [
        record["app_id"]
        for record in bob.get("/traffic/shadow", headers={"X-API-Token": "review-bob-token"}).json()[
            "comparisons"
        ]
    ] == ["other", "shop"]

    admin_incidents = admin.get("/traffic/incidents", headers={"X-API-Token": "review-admin-token"}).json()[
        "incidents"
    ]
    assert {record["app_id"] for record in admin_incidents} == {"shop", "other"}
    assert "orphan" not in json.dumps(admin_incidents)


def test_shadow_is_opt_in_and_attached_to_existing_fleet_watcher(monkeypatch):
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_SHADOW", "1")
    monkeypatch.setenv("CLOUDMORPH_FLEET_WATCH", "1")
    monkeypatch.delenv("CLOUDMORPH_TRAFFIC_COLLECTION", raising=False)
    captured = {}
    registry = Registry(nodes=[Node(name="node-a", host="127.0.0.1")])
    monkeypatch.setattr(main.nodepool, "load_registry", lambda: registry)

    def start_watcher(_interval, *, emit):
        captured["emit"] = emit

    monkeypatch.setattr(main.fleetmod, "start_background_watcher", start_watcher)
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )

    asyncio.run(startup())
    captured["emit"](
        {
            "app": "shop",
            "replicas": [{"node": "private-node", "upstream": "10.0.0.9:8080", "cpu": None}],
            "hot_streak": 0,
            "node_hot": False,
            "node_load_per_core": [0.1],
            "action": None,
        }
    )

    records = app.state.traffic_shadow.list()
    assert len(records) == 1
    assert records[0].shadow_decision is ShadowDecision.INSUFFICIENT_DATA


def test_shadow_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CLOUDMORPH_TRAFFIC_SHADOW", raising=False)
    monkeypatch.setenv("CLOUDMORPH_FLEET_WATCH", "1")
    monkeypatch.delenv("CLOUDMORPH_TRAFFIC_COLLECTION", raising=False)
    captured = {}
    registry = Registry(nodes=[Node(name="node-a", host="127.0.0.1")])
    monkeypatch.setattr(main.nodepool, "load_registry", lambda: registry)
    monkeypatch.setattr(
        main.fleetmod,
        "start_background_watcher",
        lambda _interval, *, emit: captured.setdefault("emit", emit),
    )
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )

    asyncio.run(startup())
    captured["emit"](
        {"app": "shop", "replicas": [], "node_hot": False, "node_load_per_core": [], "action": None}
    )

    assert app.state.traffic_shadow.list() == []


def test_store_rejects_invalid_limits_and_query_limit():
    with pytest.raises(ValueError):
        TrafficShadowStore(max_events=0)
    with pytest.raises(ValueError):
        TrafficShadowStore(max_events_per_app=0)
    with pytest.raises(ValueError):
        TrafficShadowStore().list(limit=0)
    with pytest.raises(ValueError):
        TrafficShadowConfig(cpu_hot_percent=-1)

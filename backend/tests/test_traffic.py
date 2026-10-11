"""Synthetic Caddy access-log tests; no external services are called."""

from __future__ import annotations

import json

import pytest

from app.traffic import (
    AnomalyStatus,
    DetectionConfig,
    TrafficMonitor,
    TrafficRecord,
    aggregate_window,
    normalize_caddy_log,
)

NOW = 1_800_000_000.0


def _log(
    timestamp: float,
    *,
    status: int = 200,
    uri: str = "/products",
    duration: float = 0.1,
    host: str = "shop.example.test",
) -> str:
    return json.dumps(
        {
            "level": "info",
            "ts": timestamp,
            "logger": "http.log.access",
            "msg": "handled request",
            "request": {
                "remote_ip": "203.0.113.99",
                "host": host,
                "uri": uri,
                "headers": {
                    "Authorization": ["Bearer private-token"],
                    "Cookie": ["session=private-cookie"],
                },
            },
            "status": status,
            "duration": duration,
            "size": 123,
        }
    )


def _fill(
    monitor: TrafficMonitor,
    count: int,
    *,
    start: float,
    interval: float = 0.5,
    status: int = 200,
    uri: str = "/products",
    duration: float = 0.1,
) -> None:
    for index in range(count):
        assert monitor.ingest(
            _log(start + index * interval, status=status, uri=uri, duration=duration), now=NOW
        )


def test_normalizes_caddy_json_without_retaining_secrets_or_query_string():
    record = normalize_caddy_log(
        _log(
            NOW - 1,
            uri="/users/alice@example.test?token=query-secret",
            host="Shop.example.test:443",
        ),
        now=NOW,
    )

    assert record == TrafficRecord(
        timestamp=NOW - 1,
        app_id="shop",
        status_code=200,
        duration_ms=100,
        path_category="/users/:param",
    )
    serialized = json.dumps(
        record.__dict__
        if hasattr(record, "__dict__")
        else {
            "timestamp": record.timestamp,
            "app_id": record.app_id,
            "status_code": record.status_code,
            "duration_ms": record.duration_ms,
            "path_category": record.path_category,
        }
    )
    for secret in ("203.0.113.99", "private-token", "private-cookie", "query-secret", "alice@example.test"):
        assert secret not in serialized


def test_aggregate_calculates_rps_status_path_concentration_and_p95():
    records = [
        TrafficRecord(NOW - 20 + index, "shop", 200 if index < 9 else 404, 10.0 + index, "/items")
        for index in range(10)
    ]

    metrics = aggregate_window(records, "shop", start=NOW - 60, end=NOW, window_seconds=60)

    assert metrics.request_count == 10
    assert metrics.rps == pytest.approx(10 / 60, abs=0.0001)
    assert metrics.status_code_ratios == {200: 0.9, 404: 0.1}
    assert metrics.error_ratio == 0.1
    assert metrics.path_concentration == 1.0
    assert metrics.repeated_pattern_ratio == 0.9
    assert metrics.p95_latency_ms == 19.0


def test_normal_user_traffic_increase_is_not_flagged_as_attack():
    monitor = TrafficMonitor()
    _fill(monitor, 10, start=NOW - 110, interval=1.0)
    _fill(monitor, 40, start=NOW - 40, interval=0.9, uri="/products")
    analysis = monitor.analyze("shop", now=NOW)

    assert analysis.traffic_increased
    assert analysis.status is AnomalyStatus.NORMAL_INCREASE
    assert "traffic_increase" in analysis.signals


def test_concentrated_repeated_error_requests_are_attack_suspected():
    monitor = TrafficMonitor()
    _fill(monitor, 30, start=NOW - 30, interval=0.5, status=404, uri="/api/unknown-route")

    analysis = monitor.analyze("shop", now=NOW)

    assert analysis.status is AnomalyStatus.ATTACK_SUSPECTED
    assert {"high_error_ratio", "high_path_concentration", "repeated_path_status_pattern"} <= set(
        analysis.signals
    )


def test_authentication_failure_rate_combines_with_path_concentration():
    monitor = TrafficMonitor()
    _fill(monitor, 25, start=NOW - 25, interval=0.5, status=401, uri="/login")

    analysis = monitor.analyze("shop", now=NOW)

    assert analysis.status is AnomalyStatus.ATTACK_SUSPECTED
    assert analysis.current_1m.auth_failure_ratio == 1.0


def test_low_sample_count_is_insufficient_data():
    monitor = TrafficMonitor()
    _fill(monitor, 5, start=NOW - 5)

    assert monitor.analyze("shop", now=NOW).status is AnomalyStatus.INSUFFICIENT_DATA


def test_malformed_missing_and_inaccurate_logs_are_discarded_and_mark_uncertain():
    monitor = TrafficMonitor()
    assert not monitor.ingest("{not-json", now=NOW)
    assert not monitor.ingest(_log(NOW + 300), now=NOW)
    assert not monitor.ingest(_log(NOW - 700), now=NOW)
    assert not monitor.ingest(json.dumps({"ts": NOW}), now=NOW)
    _fill(monitor, 25, start=NOW - 25, interval=0.5)

    analysis = monitor.analyze("shop", now=NOW)

    assert len(monitor) == 25
    assert monitor.malformed_count == 1
    assert monitor.rejected_count == 4
    assert analysis.status is AnomalyStatus.UNCERTAIN
    assert analysis.data_quality == "degraded"


def test_rps_spike_alone_is_not_an_attack_even_when_path_is_concentrated():
    monitor = TrafficMonitor()
    _fill(monitor, 10, start=NOW - 110, interval=1.0)
    _fill(monitor, 40, start=NOW - 40, interval=0.9, uri="/login")

    analysis = monitor.analyze("shop", now=NOW)

    assert analysis.traffic_increased
    assert analysis.current_1m.path_concentration == 1.0
    assert analysis.status is AnomalyStatus.NORMAL_INCREASE


def test_snapshot_contains_only_aggregates_and_no_sensitive_log_fields():
    monitor = TrafficMonitor()
    for index in range(25):
        assert monitor.ingest(
            _log(
                NOW - 25 + index * 0.5,
                status=401,
                uri="/login?password=secret-in-query",
            ),
            now=NOW,
        )

    snapshot = json.dumps(monitor.snapshot("shop", now=NOW))

    for secret in (
        "203.0.113.99",
        "private-token",
        "private-cookie",
        "secret-in-query",
        "headers",
        "remote_ip",
    ):
        assert secret not in snapshot


def test_retention_and_maximum_event_count_bound_memory():
    monitor = TrafficMonitor(max_events=3, retention_seconds=300)
    _fill(monitor, 5, start=NOW - 5, interval=0.5)
    assert len(monitor) == 3

    monitor.ingest(_log(NOW - 301), now=NOW)
    assert len(monitor) == 3
    assert monitor.accepted_count == 5
    assert monitor.rejected_count == 1


def test_gap_is_reported_and_never_triggers_enforcement():
    monitor = TrafficMonitor()
    _fill(monitor, 25, start=NOW - 25, interval=0.5, status=401, uri="/login")
    monitor.note_gap(now=NOW)

    analysis = monitor.analyze("shop", now=NOW)

    assert analysis.status is AnomalyStatus.UNCERTAIN
    assert analysis.data_quality == "gap_reported"
    assert not hasattr(analysis, "block_ip")
    assert not hasattr(analysis, "scale")


def test_detection_configuration_can_raise_minimum_sample_requirement():
    monitor = TrafficMonitor(config=DetectionConfig(min_samples=30))
    _fill(monitor, 25, start=NOW - 25, interval=0.5)

    assert monitor.analyze("shop", now=NOW).status is AnomalyStatus.INSUFFICIENT_DATA

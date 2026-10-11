"""Mocked incremental access-log collection tests."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time

from app import fleet, nodes
from app.nodes import Node, Registry, Router
from app.traffic import TrafficMonitor
from app.traffic_collector import CaddyLogCollector

NOW = time.time()


def _log(timestamp: float, *, uri: str = "/products", status: int = 200) -> bytes:
    return (
        json.dumps(
            {
                "ts": timestamp,
                "request": {
                    "host": "shop.example.test",
                    "uri": uri,
                    "remote_ip": "198.51.100.5",
                    "headers": {
                        "Authorization": ["Bearer hidden"],
                        "Cookie": ["session=hidden"],
                    },
                },
                "status": status,
                "duration": 0.02,
            }
        )
        + "\n"
    ).encode()


class _RemoteLog:
    def __init__(self, content: bytes = b"", inode: str = "101") -> None:
        self.content = content
        self.inode = inode
        self.missing = False
        self.failures = 0
        self.commands: list[str] = []

    def ssh(self, _node, command: str, *, timeout: int = 60, stdin=None):
        del timeout, stdin
        self.commands.append(command)
        if self.failures:
            self.failures -= 1
            raise subprocess.TimeoutExpired(command, 20)
        if self.missing:
            return subprocess.CompletedProcess(command, 0, b"MISSING 0 0\n", b"")

        expected_match = re.search(r"if \[ ([0-9]+|'') != \"\$inode\" \]", command)
        requested_match = re.search(r"else start_offset=(\d+); fi;", command)
        limit_match = re.search(r"if \[ \"\$read_size\" -gt (\d+) \]", command)
        expected_inode = expected_match.group(1).strip("'") if expected_match else ""
        requested_offset = int(requested_match.group(1)) if requested_match else 0
        if 'start_offset="$size"' in command:
            offset = len(self.content)
        elif expected_inode != self.inode or requested_offset > len(self.content):
            offset = 0
        else:
            offset = requested_offset
        limit = int(limit_match.group(1)) if limit_match else 0
        read_size = min(len(self.content) - offset, limit) if 'head -c "$read_size"' in command else 0
        end = offset + read_size
        before = self.content[max(0, offset - 128) : offset]
        after = self.content[max(0, end - 128) : end]
        header = (
            f"{self.inode} {len(self.content)} {offset} {read_size} "
            f"{hashlib.sha256(before).hexdigest()} {hashlib.sha256(after).hexdigest()}\n"
        ).encode()
        body = self.content[offset:end]
        return subprocess.CompletedProcess(command, 0, header + body, b"")


def _collector(
    monkeypatch,
    remote: _RemoteLog,
    *,
    max_batch_bytes: int = 256 * 1024,
    lock_path=None,
):
    router = Router(name="router", host="192.0.2.1")
    monitor = TrafficMonitor()
    monkeypatch.setattr(nodes, "ssh", remote.ssh)
    return CaddyLogCollector(
        router,
        monitor,
        interval_seconds=10,
        max_batch_bytes=max_batch_bytes,
        lock_path=lock_path,
    ), monitor


def test_collector_skips_existing_log_then_ingests_only_new_records(monkeypatch):
    remote = _RemoteLog(_log(NOW - 2))
    collector, monitor = _collector(monkeypatch, remote)

    initial = collector.poll_once()
    assert initial.gap
    assert initial.ingested == 0
    assert len(monitor) == 0

    remote.content += _log(NOW - 1, uri="/products?secret=value")
    result = collector.poll_once()
    assert result.ingested == 1
    assert result.read_bytes == len(_log(NOW - 1, uri="/products?secret=value"))
    assert len(monitor) == 1
    assert collector.poll_once().read_bytes == 0
    assert len(monitor) == 1


def test_collector_buffers_incomplete_json_until_newline(monkeypatch):
    remote = _RemoteLog()
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()

    line = _log(NOW - 1)
    remote.content += line[:-1]
    first = collector.poll_once()
    assert first.ingested == 0
    assert len(collector.cursor.pending) == len(line) - 1

    remote.content += line[-1:]
    second = collector.poll_once()
    assert second.ingested == 1
    assert collector.cursor.pending == b""
    assert len(monitor) == 1


def test_collector_resets_offset_on_rotation_and_marks_a_gap(monkeypatch):
    remote = _RemoteLog(_log(NOW - 2))
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()

    remote.inode = "202"
    remote.content = _log(NOW - 1, uri="/login", status=401)
    result = collector.poll_once()

    assert result.gap
    assert result.ingested == 1
    assert collector.cursor.inode == "202"
    assert collector.cursor.offset == len(remote.content)
    assert monitor.analyze("shop", now=time.time()).data_quality == "gap_reported"


def test_collector_reports_log_loss_on_truncation_and_missing_file(monkeypatch):
    remote = _RemoteLog(_log(NOW - 2) * 2)
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()

    remote.content = _log(NOW - 1)
    truncated = collector.poll_once()
    assert truncated.gap
    assert truncated.ingested == 1

    remote.missing = True
    missing = collector.poll_once()
    assert missing.gap
    assert missing.error == "log_file_missing"
    assert monitor.analyze("shop", now=time.time()).status.value == "uncertain"
    assert monitor.analyze("shop", now=time.time()).data_quality == "gap_reported"


def test_ssh_failure_marks_gap_without_advancing_cursor(monkeypatch):
    remote = _RemoteLog()
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()
    previous_offset = collector.cursor.offset

    remote.content += _log(NOW - 1)
    remote.failures = 1
    failed = collector.poll_once()
    assert failed.error == "ssh_unavailable"
    assert collector.cursor.offset == previous_offset

    recovered = collector.poll_once()
    assert recovered.ingested == 1
    assert monitor.analyze("shop", now=time.time()).data_quality == "gap_reported"


def test_copy_truncate_with_fast_regrowth_is_detected_and_marks_uncertain(monkeypatch):
    remote = _RemoteLog()
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()
    remote.content = b"".join(_log(NOW - 25 + index * 0.5, uri="/login", status=401) for index in range(25))
    first = collector.poll_once()
    assert first.ingested == 25
    assert monitor.analyze("shop", now=time.time()).status.value == "uncertain"

    previous_size = len(remote.content)
    remote.content = b"".join(_log(NOW - 25 + index * 0.5, uri="/products") for index in range(32))
    assert len(remote.content) > previous_size

    rewritten = collector.poll_once()

    assert rewritten.error == "log_rewrite_detected"
    assert rewritten.gap
    assert rewritten.ingested == 0
    assert monitor.analyze("shop", now=time.time()).status.value == "uncertain"


def test_collector_singleton_lock_prevents_two_worker_threads(monkeypatch, tmp_path):
    remote = _RemoteLog()
    lock_path = tmp_path / "collector.lock"
    first, _ = _collector(monkeypatch, remote, lock_path=lock_path)
    second, _ = _collector(monkeypatch, remote, lock_path=lock_path)

    assert first.start()
    assert not second.start()
    assert second.start_error == "collector_already_running"
    assert first.stop(timeout=2)
    assert not first.thread_alive


def test_stop_timeout_keeps_lock_until_thread_exits(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    lock_path = tmp_path / "collector.lock"

    def blocking_ssh(_node, command, *, timeout=60, stdin=None):
        del timeout, stdin
        entered.set()
        release.wait(timeout=2)
        return subprocess.CompletedProcess(command, 0, b"MISSING 0 0\n", b"")

    monkeypatch.setattr(nodes, "ssh", blocking_ssh)
    router = Router(name="router", host="192.0.2.1")
    first = CaddyLogCollector(router, TrafficMonitor(), lock_path=lock_path)
    second = CaddyLogCollector(router, TrafficMonitor(), lock_path=lock_path)

    assert first.start()
    assert entered.wait(timeout=1)
    assert not first.stop(timeout=0.01)
    assert first.thread_alive
    assert not second.start()
    assert second.start_error == "collector_already_running"

    release.set()
    first._thread.join(timeout=2)
    assert not first.thread_alive


def test_thread_can_reacquire_singleton_only_after_previous_shutdown(monkeypatch, tmp_path):
    remote = _RemoteLog()
    lock_path = tmp_path / "collector.lock"
    first, _ = _collector(monkeypatch, remote, lock_path=lock_path)
    second, _ = _collector(monkeypatch, remote, lock_path=lock_path)

    assert first.start()
    assert first.stop(timeout=2)
    assert second.start()
    assert second.stop(timeout=2)


def test_rotated_logs_are_deduplicated_by_inode_and_byte_offset(monkeypatch):
    remote = _RemoteLog()
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()
    record = _log(NOW - 1)
    remote.content += record

    first = collector.poll_once()
    duplicate_poll = collector.poll_once()

    assert first.ingested == 1
    assert duplicate_poll.read_bytes == 0
    assert len(monitor) == 1


def test_oversized_incomplete_line_is_bounded_and_discarded(monkeypatch):
    remote = _RemoteLog()
    collector, monitor = _collector(monkeypatch, remote, max_batch_bytes=64)
    collector.max_line_bytes = 48
    collector.poll_once()

    remote.content += b"x" * 64
    result = collector.poll_once()

    assert result.gap
    assert result.rejected == 1
    assert len(collector.cursor.pending) == 0
    assert len(monitor) == 0


def test_ingested_record_and_monitor_snapshot_do_not_expose_request_secrets(monkeypatch):
    remote = _RemoteLog()
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()
    ingested_records = []
    original_ingest = monitor.ingest

    def capture_ingest(record, **kwargs):
        ingested_records.append(record)
        return original_ingest(record, **kwargs)

    monkeypatch.setattr(monitor, "ingest", capture_ingest)
    remote.content += _log(NOW - 1, uri="/users/person@example.test?token=secret-value")
    collector.poll_once()

    assert len(ingested_records) == 1
    assert not isinstance(ingested_records[0], (str, bytes, dict))
    snapshot = json.dumps(monitor.snapshot("shop", now=NOW))
    for value in (
        "198.51.100.5",
        "Authorization",
        "Bearer hidden",
        "Cookie",
        "session=hidden",
        "secret-value",
    ):
        assert value not in snapshot
    assert ":param" in snapshot


def test_malformed_json_is_counted_without_passing_raw_input_to_monitor(monkeypatch):
    remote = _RemoteLog()
    collector, monitor = _collector(monkeypatch, remote)
    collector.poll_once()
    raw_log = b'{"request":{"uri":"/secret?token=hidden"}'
    remote.content += raw_log + b"\n"

    result = collector.poll_once()

    assert result.rejected == 1
    assert monitor.malformed_count == 1
    assert len(monitor) == 0


def test_router_initialization_creates_private_log_directory_without_replacing_router(monkeypatch):
    reg = Registry(
        router=Router(name="router", host="192.0.2.1"),
    )
    commands = []

    def fake_ssh_text(node, command, *, timeout=60):
        commands.append(command)
        return ""

    def fake_ssh(node, command, *, timeout=60, stdin=None):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(nodes, "ssh_text", fake_ssh_text)
    monkeypatch.setattr(nodes, "ssh", fake_ssh)
    monkeypatch.setattr(fleet, "push_config", lambda registry, state: None)
    monkeypatch.setattr(fleet, "load_state", lambda: fleet.State())

    fleet.init_router(reg)

    assert commands[0] == 'mkdir -p "$HOME/caddy/access-logs" && chmod 700 "$HOME/caddy/access-logs"'
    assert any("docker ps -q -f name=^cloudmorph-router$" in command for command in commands)
    assert not any("docker rm" in command for command in commands)


def test_rendered_caddy_config_logs_user_app_routes_only_as_json():
    reg = Registry(
        nodes=[Node(name="worker", host="192.0.2.2")],
        router=Router(name="router", host="192.0.2.1"),
    )
    app = fleet.App(
        name="shop",
        image="shop:v1",
        container_port=8080,
        hostname="shop.192.0.2.1.nip.io",
        replicas=[fleet.Replica("worker", "shop-1", "192.0.2.2", 8000)],
    )

    config = fleet.render_caddy_config(reg, fleet.State(apps={"shop": app}))
    server = config["apps"]["http"]["servers"]["public"]
    logger = config["logging"]["logs"]["traffic_access"]

    assert server["logs"]["logger_names"] == {app.hostname: "traffic_access"}
    assert logger["include"] == ["http.log.access"]
    assert logger["encoder"] == {"format": "json"}
    assert logger["writer"]["filename"] == "/etc/caddy/access-logs/access.json"
    assert logger["writer"]["mode"] == "0644"
    assert logger["writer"]["dir_mode"] == "0700"
    assert logger["writer"]["roll_size_mb"] == 10
    assert logger["writer"]["roll_keep"] == 3
    assert logger["writer"]["roll_keep_days"] == 1
    assert server["routes"][0]["handle"][0]["upstreams"][0]["dial"] == "192.0.2.2:8000"


def test_startup_traffic_collection_is_opt_in_and_stops_on_shutdown(monkeypatch):
    from app import main

    started = []
    stopped = []

    class StubCollector:
        def __init__(self, router, monitor, *, interval_seconds):
            started.append((router, monitor, interval_seconds))
            self.is_running = False

        def start(self):
            started.append("start")
            self.is_running = True
            return True

        def stop(self):
            stopped.append("stop")
            self.is_running = False
            return True

    reg = Registry(
        nodes=[Node(name="worker", host="192.0.2.2")],
        router=Router(name="router", host="192.0.2.1"),
    )
    monkeypatch.setattr(main, "CaddyLogCollector", StubCollector)
    monkeypatch.setattr(main.nodepool, "load_registry", lambda: reg)
    monkeypatch.delenv("CLOUDMORPH_FLEET_WATCH", raising=False)
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION_SINGLE_INSTANCE", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_INTERVAL", "7")

    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )
    shutdown = next(
        handler for handler in app.router.on_shutdown if handler.__name__ == "stop_traffic_collection"
    )

    import asyncio

    asyncio.run(startup())
    assert started[0] == (reg.router, app.state.traffic_monitor, 7.0)
    assert started[1] == "start"
    asyncio.run(startup())
    assert started.count("start") == 1
    asyncio.run(shutdown())
    assert stopped == ["stop"]


def test_startup_disables_traffic_collection_when_another_worker_holds_lock(monkeypatch, caplog):
    from app import main

    instances = []

    class LockedCollector:
        def __init__(self, router, monitor, *, interval_seconds):
            self.is_running = False
            self.thread_alive = False
            self.start_error = None
            self.starts = 0
            instances.append(self)

        def start(self):
            self.starts += 1
            self.start_error = "collector_already_running"
            return False

        def stop(self):
            return True

    reg = Registry(router=Router(name="router", host="192.0.2.1"))
    monkeypatch.setattr(main, "CaddyLogCollector", LockedCollector)
    monkeypatch.setattr(main.nodepool, "load_registry", lambda: reg)
    monkeypatch.delenv("CLOUDMORPH_FLEET_WATCH", raising=False)
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION", "1")
    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION_SINGLE_INSTANCE", "1")
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )

    import asyncio

    asyncio.run(startup())
    asyncio.run(startup())

    assert len(instances) == 1
    assert instances[0].starts == 1
    assert "could not acquire its single-process lock" in caplog.text


def test_startup_requires_single_instance_deployment_attestation(monkeypatch, caplog):
    from app import main

    monkeypatch.setenv("CLOUDMORPH_TRAFFIC_COLLECTION", "1")
    monkeypatch.delenv("CLOUDMORPH_TRAFFIC_COLLECTION_SINGLE_INSTANCE", raising=False)
    monkeypatch.delenv("CLOUDMORPH_FLEET_WATCH", raising=False)
    monkeypatch.setattr(
        main.nodepool,
        "load_registry",
        lambda: (_ for _ in ()).throw(AssertionError("registry must not be loaded")),
    )
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )

    import asyncio

    asyncio.run(startup())

    assert "backend deployment is constrained to one instance" in caplog.text


def test_traffic_collection_is_disabled_by_default(monkeypatch):
    from app import main

    monkeypatch.delenv("CLOUDMORPH_TRAFFIC_COLLECTION", raising=False)
    monkeypatch.delenv("CLOUDMORPH_TRAFFIC_COLLECTION_SINGLE_INSTANCE", raising=False)
    monkeypatch.delenv("CLOUDMORPH_FLEET_WATCH", raising=False)
    monkeypatch.setattr(
        main.nodepool,
        "load_registry",
        lambda: (_ for _ in ()).throw(AssertionError("registry must not be loaded")),
    )
    app = main.create_app()
    startup = next(
        handler for handler in app.router.on_startup if handler.__name__ == "start_background_services"
    )

    import asyncio

    asyncio.run(startup())
    assert app.state.traffic_monitor.accepted_count == 0

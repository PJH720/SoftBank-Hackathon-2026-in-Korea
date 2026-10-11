"""Multi-cloud load balancing and isolation without real machines (SSH and HTTP are mocked)."""

from __future__ import annotations

import subprocess

import pytest

from app import accounts, fleet, main, nodes
from app.nodes import Metrics, Node, Registry, Router


def _pool():
    return Registry(
        nodes=[
            Node(name="gcp-1", host="10.0.0.1", provider="gcp"),
            Node(name="gcp-2", host="10.0.0.2", provider="gcp"),
            Node(name="aws-1", host="10.0.1.1", provider="aws"),
            Node(name="oci-1", host="10.0.2.1", provider="oci"),
        ],
        router=Router(name="router", host="10.9.9.9"),
    )


def _probe(load):
    return lambda n: Metrics(
        node=n.name,
        ok=load.get(n.name) is not None,
        load1=load.get(n.name) or 0,
        cpus=1,
        mem_total_mb=1,
        mem_avail_mb=1,
        error="down",
    )


def test_select_node_spreads_to_a_cloud_the_app_is_not_on(monkeypatch):
    monkeypatch.setattr(nodes, "probe", _probe({"gcp-1": 0.9, "gcp-2": 0.0, "aws-1": 0.5, "oci-1": 0.7}))
    best, _ = nodes.select_node(_pool(), exclude={"gcp-1"}, spread_from={"gcp"})
    assert best.name == "aws-1"  # idler gcp-2 loses: same cloud as the replica we already have
    best, _ = nodes.select_node(_pool(), exclude={"gcp-1", "aws-1"}, spread_from={"gcp", "aws"})
    assert best.name == "oci-1"


def test_select_node_falls_back_to_same_cloud_when_others_are_down(monkeypatch):
    monkeypatch.setattr(nodes, "probe", _probe({"gcp-1": 0.1, "gcp-2": 0.2, "aws-1": None, "oci-1": None}))
    best, _ = nodes.select_node(_pool(), exclude={"gcp-1"}, spread_from={"gcp"})
    assert best.name == "gcp-2"


@pytest.fixture
def fleet_env(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "fleet.json")
    reg = _pool()
    monkeypatch.setattr(nodes, "load_registry", lambda path=None: reg)
    monkeypatch.setattr(nodes, "probe", _probe({"gcp-1": 0.1, "gcp-2": 0.0, "aws-1": 0.3, "oci-1": 0.4}))
    monkeypatch.setattr(nodes, "relay_image", lambda src, dst, image: 0.0)
    monkeypatch.setattr(nodes, "free_port_on", lambda node: 8001)
    monkeypatch.setattr(fleet, "push_config", lambda reg, state: None)
    monkeypatch.setattr(fleet, "READY_INTERVAL", 0)
    runs, removed = [], []
    monkeypatch.setattr(
        nodes,
        "run_container",
        lambda node, image, name, hp, cp, env=None, **kw: runs.append((node.name, env, kw)),
    )
    monkeypatch.setattr(
        nodes, "remove_container", lambda node, name: removed.append((node.name, name)) or True
    )
    monkeypatch.setattr(
        nodes, "container_env", lambda node, name: {"API_KEY": "k", "DATABASE_URL": "postgres://x"}
    )
    app = fleet.App(
        name="shop",
        image="shop:v1",
        container_port=8080,
        hostname="shop.10.9.9.9.nip.io",
        replicas=[fleet.Replica("gcp-1", "shop-v1", "10.0.0.1", 8000, provider="gcp")],
        limits=["--memory", "512m"],
    )
    fleet.save_state(fleet.State(apps={"shop": app}))
    return reg, runs, removed


def test_scale_out_copies_env_uses_app_network_and_lands_on_another_cloud(fleet_env, monkeypatch):
    _, runs, _ = fleet_env
    monkeypatch.setattr(fleet, "_wait_ready", lambda rep: True)
    app = fleet.spread("shop")
    assert [r.provider for r in app.replicas] == ["gcp", "aws", "oci"]
    node, env, kw = runs[0]
    assert node == "aws-1"
    assert env == {"API_KEY": "k", "DATABASE_URL": "postgres://x"}  # the replica gets the primary's env
    assert kw == {"limits": ["--memory", "512m"], "network": "cm-shop"}


def test_scale_out_never_routes_a_replica_that_does_not_come_up(fleet_env, monkeypatch):
    _, _, removed = fleet_env
    monkeypatch.setattr(fleet, "READY_TRIES", 2)

    def refuse(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(fleet.urllib.request, "urlopen", refuse)
    app = fleet.scale("shop", 2)
    assert len(app.replicas) == 1  # still only the healthy original behind the router
    assert len(removed) == 1 and removed[0][0] == "aws-1" and removed[0][1].startswith("shop-r2-")
    assert fleet.load_state().apps["shop"].events[-1]["type"] == "scale_out_failed"


def test_manual_scale_of_a_stateful_app_is_refused(fleet_env):
    state = fleet.load_state()
    state.apps["shop"].stateful = True
    fleet.save_state(state)
    app = fleet.scale("shop", 3)
    assert len(app.replicas) == 1
    assert fleet.load_state().apps["shop"].events[-1]["type"] == "scale_out_blocked"


def test_container_env_drops_what_the_image_bakes_in(monkeypatch):
    out = (
        "PATH=/usr/bin\nPORT=8080\nAPI_KEY=secret=with=equals\nLANG=C.UTF-8\n"
        f"{nodes._ENV_SPLIT}\nPATH=/usr/bin\nLANG=C.UTF-8\n"
    )
    monkeypatch.setattr(
        nodes, "ssh", lambda node, cmd, timeout=60: subprocess.CompletedProcess([], 0, out.encode(), b"")
    )
    assert nodes.container_env(Node(name="a", host="1"), "c") == {"API_KEY": "secret=with=equals"}


def test_each_app_and_its_sidecar_share_a_network_of_their_own(monkeypatch):
    script = nodes.sidecar_script(nodes.SIDECARS["postgres"], "todo", "pw")
    assert "--network cm-todo" in script and "--network cloudmorph" not in script
    assert "docker network connect cm-todo todo-db" in script  # a pre-existing sidecar joins it too
    cmds = []
    monkeypatch.setattr(
        nodes,
        "ssh",
        lambda node, cmd, timeout=60: cmds.append(cmd) or subprocess.CompletedProcess([], 0, b"id", b""),
    )
    nodes.run_container(
        Node(name="a", host="1"), "img", "todo-v2", 8000, 8080, network=nodes.app_network("todo")
    )
    assert "--network cm-todo" in cmds[0]


@pytest.mark.parametrize(
    ("source", "admin", "allowed"),
    [
        ("https://github.com/acme/shop", False, True),
        ("sample-apps/guestbook", False, True),
        ("sample-apps/../backend", False, False),
        ("/etc", False, False),
        (str(main.REPO_ROOT / "backend"), False, False),
        (str(main.REPO_ROOT / "backend"), True, True),
        ("sample-apps/does-not-exist", False, False),
    ],
)
def test_only_admins_deploy_arbitrary_server_paths(source, admin, allowed):
    assert main._source_allowed(source, accounts.User("u", admin=admin)) is allowed


def test_removing_an_app_drops_its_replicas_and_networks_on_every_node(fleet_env, monkeypatch):
    _, _, removed = fleet_env
    state = fleet.load_state()
    state.apps["shop"].replicas.append(fleet.Replica("aws-1", "shop-r2", "10.0.1.1", 8001, provider="aws"))
    fleet.save_state(state)
    networks = []
    monkeypatch.setattr(nodes, "remove_network", lambda node, net: networks.append((node.name, net)) or True)
    fleet.remove("shop")
    assert removed == [("gcp-1", "shop-v1"), ("aws-1", "shop-r2")]
    assert networks == [("gcp-1", "cm-shop"), ("aws-1", "cm-shop")]
    assert "shop" not in fleet.load_state().apps


def test_replicas_recorded_before_providers_take_it_from_the_registry():
    old = fleet.Replica("aws-1", "c", "10.0.1.1", 8000)
    assert old.provider == "unknown" and fleet.provider_of(_pool(), old) == "aws"
    assert fleet.provider_of(_pool(), fleet.Replica("gone", "c", "h", 1)) == "unknown"

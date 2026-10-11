"""Incident guard (app/incident.py): probes, Jev's verdict, guarded actions, drills. No Docker, no network."""

import subprocess

import pytest

from app import incident, systemone
from app.incident import Guard, Probe


class FakeJev(systemone.Endpoint):
    def __init__(self, cause="app_crashed", action="restart", severity=3.2):
        object.__setattr__(self, "verdict", (cause, action, severity))
        object.__setattr__(self, "states", [])
        super().__init__("Jev", "https://fake", "jev-latest", api_key="k")

    def ask(self, state, questions, timeout=15.0):
        self.states.append((state, questions))
        cause, action, severity = self.verdict
        assert action in questions["action"]["criteria"], "only allowed actions are offered"
        answers = {
            "severity": {"type": "score", "score": severity, "probabilities": {"3": 0.8, "4": 0.2}},
            "cause": {"type": "choice", "choice": cause, "probabilities": {cause: 0.9}, "confidence": 0.7},
            "action": {"type": "choice", "choice": action, "probabilities": {action: 0.8}, "confidence": 0.6},
        }
        return systemone.Answers(answers, "jev-1.13.0", 420.0, 600)


LOCAL = {"container_name": "gb-local-1", "local_url": "http://127.0.0.1:5000", "db_container": "gb-local-db"}


def project(live=None, **extra):
    return {
        "name": "gb",
        "source": "sample-apps/guestbook",
        "last_status": "completed",
        "live": {"local": LOCAL} if live is None else live,
        **extra,
    }


@pytest.fixture
def world(monkeypatch):
    """Scripted probes and docker commands. `probes` is consumed one per probe; the last one repeats."""
    state = {"probes": [], "docker": [], "jev": FakeJev()}

    def fake_probe(app, target, handles, url):
        script = state["probes"]
        return script.pop(0) if len(script) > 1 else script[0]

    def fake_run(cmd, **kwargs):
        state["docker"].append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(incident, "probe_target", fake_probe)
    monkeypatch.setattr(incident.subprocess, "run", fake_run)
    monkeypatch.setattr(incident, "docker_logs", lambda name, tail=12: ["Killed"])
    monkeypatch.setattr(incident, "decider", lambda: state["jev"])
    monkeypatch.setattr(incident.time, "sleep", lambda s: None)
    return state


def guard(projects, **kw):
    g = Guard(
        projects=lambda: projects, busy=lambda name: False, spawn=lambda fn, *a: fn(*a), interval=0.01, **kw
    )
    g.recover_wait = 0.05
    return g


def ok():
    return Probe("gb", "local", True, http=200, ms=12, container="running", db="running")


def test_logs_sent_to_jev_are_redacted(world, monkeypatch):
    monkeypatch.setattr(
        incident, "docker_logs", lambda name, tail=12: ["connect postgres://app:s3cr3tpw@db:5432/app failed"]
    )
    crashed = Probe("gb", "local", False, container="exited", exit_code=1, db="running")
    world["probes"] = [crashed, crashed, ok()]
    guard([project()]).tick()
    state, _ = world["jev"].states[0]
    assert "s3cr3tpw" not in state and "app:***@db" in state


def test_stopped_container_is_restarted_after_one_jev_call(world):
    crashed = Probe("gb", "local", False, container="exited", exit_code=137, db="running")
    world["probes"] = [crashed, crashed, ok()]
    g = guard([project()])
    g.tick()
    (i,) = g.incidents
    assert i.status == "resolved" and i.target == "local"
    assert world["docker"] == [["docker", "restart", "-t", "2", "gb-local-1"]]
    (verdict,) = i.verdicts
    assert (verdict["engine"], verdict["cause"], verdict["action"], verdict["ms"]) == (
        "Jev",
        "app_crashed",
        "restart",
        420,
    )
    texts = [t["text"] for t in i.timeline]
    assert (
        texts[0] == "gb 앱(내 컴퓨터)에 문제가 생겼어요: 앱 컨테이너가 꺼졌어요 (상태 exited, 종료 코드 137)"
    )
    assert texts[1] == "Jev가 0.4초 만에 판단했어요: 앱이 멈췄어요 (90%) → 앱을 다시 켤게요"
    assert texts[-1].startswith("다시 정상이에요. 문제를 알아챈 뒤")
    state, questions = world["jev"].states[0]
    assert '"recent_logs": ["Killed"]' in state and set(questions) == {"severity", "cause", "action"}
    assert list(questions["action"]["criteria"]) == ["restart", "start_database", "wait"]


def test_a_failed_http_check_must_repeat_before_paging(world):
    down = Probe(
        "gb", "local", False, http=None, error="ConnectionRefusedError", container="running", db="running"
    )
    world["probes"] = [down, down, down, ok()]
    g = guard([project()])
    g.tick()
    assert not g.incidents and g.health["gb"]["local"]["ok"] is False
    g.tick()
    assert len(g.incidents) == 1 and g.incidents[0].status == "resolved"


def test_without_any_model_rules_cover_a_stopped_database(world):
    world["jev"] = None
    db_down = Probe("gb", "local", False, http=500, container="running", db="exited")
    world["probes"] = [db_down, db_down, ok()]
    g = guard([project()])
    g.tick()
    (i,) = g.incidents
    assert i.verdicts[0]["engine"] == "규칙 (Jev 없음)" and i.verdicts[0]["action"] == "start_database"
    assert world["docker"][0] == ["docker", "start", "gb-local-db"] and i.status == "resolved"


def test_outage_drill_fails_over_and_mutes_the_old_place(world):
    world["jev"] = FakeJev("platform_outage", "failover", 3.9)
    world["probes"] = [ok()]
    started = []
    g = guard(
        [project()],
        failover=lambda p, target: started.append(target) or "dep42",
        job=lambda d: {
            "completed": True,
            "targets": {"cloudrun": {"success": True, "url": "https://gb.run.app"}},
        },
        can_failover=lambda p, target: "cloudrun",
    )
    assert g.drill(project(), "outage") == {
        "app": "gb",
        "target": "local",
        "kind": "outage",
        "at": pytest.approx(0, abs=1e12),
    }
    g.tick()
    (i,) = g.incidents
    assert started == ["cloudrun"] and i.status == "resolved" and i.drill == "outage"
    assert i.timeline[0]["text"].endswith("내 컴퓨터 쪽에 연결이 안 돼요 (장애 훈련)")
    assert i.timeline[-1]["text"] == "cloudrun에서 서비스를 이어가요. https://gb.run.app"
    g.tick()  # the old place is still "down" (drill), but muted: no second incident
    assert len(g.incidents) == 1


def test_gives_up_and_asks_a_person_after_three_tries(world):
    world["probes"] = [Probe("gb", "local", False, container="exited", exit_code=1, db="running")]
    g = guard([project()])
    g.tick()
    (i,) = g.incidents
    assert i.status == "escalated" and len(i.verdicts) == incident.MAX_ATTEMPTS
    assert i.timeline[-1]["text"] == "3번 시도했지만 돌아오지 않았어요. 직접 확인해 주세요."


def test_busy_or_stopped_apps_are_left_alone(world):
    world["probes"] = [Probe("gb", "local", False, container="exited")]
    g = guard([project(last_status="running")])
    g.tick()
    assert not g.incidents  # a deploy is replacing the container
    g = guard([project(live={})])
    g.tick()
    assert not g.incidents and g.health == {}


def test_drills_need_something_to_break(world):
    g = guard([])
    with pytest.raises(ValueError, match="local"):
        g.drill(project(live={"cloudrun": {"service": "gb"}}), "crash")
    with pytest.raises(ValueError, match="모르는 훈련"):
        g.drill(project(), "meteor")
    assert g.drill(project(), "crash")["target"] == "local"
    assert world["docker"] == [["docker", "kill", "gb-local-1"]]


def test_snapshot_names_the_engine(world, monkeypatch):
    g = guard([])
    assert g.snapshot()["guard"]["engine"] == "Jev"
    world["jev"] = None
    assert g.snapshot()["guard"]["engine"] == "규칙 (Jev 없음)"


def test_decider_prefers_jev_then_local_clef(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    monkeypatch.setenv("CLEF_BASE_URL", "http://127.0.0.1:11436")
    assert incident.decider().name == "Jev"
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setattr(systemone, "ready", lambda ep, ttl=30.0: True)
    assert incident.decider().name == "clef-flash (로컬)"
    monkeypatch.setattr(systemone, "ready", lambda ep, ttl=30.0: False)
    assert incident.decider() is None


def test_outage_with_nowhere_to_go_is_escalated_at_once(world):
    world["jev"] = FakeJev("platform_outage", "restart", 3.9)
    world["probes"] = [ok()]
    g = guard([project()])
    g.drill(project(), "outage")
    g.tick()
    (i,) = g.incidents
    assert i.status == "escalated" and len(i.verdicts) == 1 and world["docker"] == []  # no futile restarts
    assert "옮길 곳" in i.timeline[-1]["text"]


def test_failover_deploy_keeps_the_apps_own_targets(monkeypatch, tmp_path):
    """A guard failover runs one target; the next push must still go to every target the user chose."""
    import asyncio
    import contextlib

    from app import main
    from app.schemas import AnalysisResult

    @contextlib.contextmanager
    def source(*_):
        yield str(tmp_path)

    monkeypatch.setattr(main, "prepare_source", source)
    monkeypatch.setattr(main.analyzer, "quick_decision", lambda _: None)
    analysis = AnalysisResult(target="local", language="python", dockerfile="FROM x\n")
    monkeypatch.setattr(main.analyzer, "analyze", lambda _: analysis)
    monkeypatch.setattr(main, "run_pipeline", lambda *a, **kw: False)  # every target fails fast, nothing runs
    app = main.create_app()

    async def deploy(targets, trigger):
        dep = app.state.launch("https://github.com/acme/gb.git", targets, name="gb", trigger=trigger)
        right_after = list(main._projects["gb"]["targets"])
        while main._jobs[dep].state in ("queued", "running"):
            await asyncio.sleep(0.01)
        return right_after, main._projects["gb"]["targets"]

    async def run():
        everywhere = ["local", "cloudrun", "node"]
        assert await deploy(everywhere, "github-push") == (everywhere, everywhere)
        assert await deploy(["cloudrun"], "guard") == (everywhere, everywhere)
        # a user's move does change them
        assert await deploy(["cloudrun"], "move") == (["cloudrun"], ["cloudrun"])

    asyncio.run(run())

"""Troubleshooting scenarios: the bundled packs, validation (incl. names that
would give a fault away), Chaos's fault selection and naming, a full round
with the devices stubbed, the learner's view / hints / debrief, the Chaos
model's leak guard, and the routes."""
import copy
import random
from pathlib import Path

import pytest

from app import config
from app.models.scenario import Scenario, ScenarioAttempt
from app.models.topology import Topology
from app.services import chaos_ai, lesson_checks, troubleshooting as ts

ROOT = Path(__file__).resolve().parents[2] / "curriculum" / "troubleshooting"
SLUGS = sorted(p.name for p in ROOT.iterdir() if (p / "scenario.yaml").exists())


@pytest.fixture
def scenario(db):
    s = ts.save_scenario(db, ts.read_dir(ROOT / "sp-core-isis-bgp"))
    db.commit()
    return s


@pytest.mark.parametrize("slug", SLUGS)
def test_bundled_packs_are_valid(slug):
    pack = ts.read_dir(ROOT / slug)
    assert ts.validate(pack) == []
    kinds = {(f["protocol"], f["category"]) for f in pack["faults"]}
    for proto in ts.normalize_meta(pack["meta"])["protocols"]:      # every protocol: adjacency and advertisement
        assert {(proto, "adjacency"), (proto, "advertisement")} <= kinds
    assert any(c.get("scope") == "service" for c in pack["health"])


def test_validate_rejects_giveaways():
    pack = ts.read_dir(ROOT / "sp-core-isis-bgp")
    bad = copy.deepcopy(pack)
    v = bad["faults"][0]["variants"][0]
    v["inject"] = {"R1": "route-policy CHAOS-DROP\n  drop\nend-policy\ninterface Loopback9\n description broken by chaos\n"}
    errs = ts.validate(bad)
    assert any("CHAOS-DROP" in e for e in errs) and any("descriptions" in e for e in errs)
    bad = copy.deepcopy(pack)
    bad["faults"][0]["variants"][0]["inject"] = {"R9": "x"}
    assert any("R9" in e for e in ts.validate(bad))


def test_choose_faults_disjoint_and_spread(scenario):
    for seed in range(30):
        picked = ts.choose_faults(scenario, 4, random.Random(seed))
        assert len(picked) == 4
        assert len({f["id"] for f, _v in picked}) == 4
        scopes = [s for _f, v in picked for s in v.get("scope") or []]
        assert len(scopes) == len(set(scopes))
        assert {f["protocol"] for f, _v in picked} == {"isis", "bgp"}      # spread over protocols
        assert len({(f["protocol"], f["category"]) for f, _v in picked}) == 4


def test_names_are_neutral_and_unique(scenario):
    f = next(f for f in scenario.faults if f["id"] == "bgp-no-export")
    v = f["variants"][0]
    taken = set()
    names = ts.make_names(v, random.Random(1), taken)
    assert set(names) == {"POLICY1"} and names["POLICY1"] in ts.NAME_POOLS["POLICY"]
    cfg = ts.render(v["inject"]["R1"], names)
    assert "{POLICY1}" not in cfg and names["POLICY1"] in cfg
    for pool in ts.NAME_POOLS.values():
        for n in pool:
            assert not any(w in n.upper() for w in ("DENY", "BLOCK", "DROP", "BAD", "BREAK", "CHAOS", "FAULT", "EXPORT-NO"))
    k = next(f for f in scenario.faults if f["id"] == "isis-auth-mismatch")["variants"][1]
    n2 = ts.make_names(k, random.Random(2), taken)
    assert n2["KEYCHAIN1"] in ts.NAME_POOLS["KEYCHAIN"] and len(n2["SECRET1"]) == 10


def _attempt(db, s, difficulty="standard", count=2):
    a, created = ts.start_attempt(db, s, "learner", difficulty, count)
    db.commit()
    return a


def test_start_attempt_builds_a_lab_without_design(db, scenario):
    a = _attempt(db, scenario)
    t = db.get(Topology, a.topology_id)
    assert t.lesson_role == "troubleshoot" and t.scenario_id == scenario.id and t.annotations == []
    assert ts.start_attempt(db, scenario, "learner", "expert", 3)[1] is False       # resume, settings updated
    assert a.difficulty == "expert" and a.fault_count == 3
    with pytest.raises(ts.ScenarioError):
        ts.start_attempt(db, scenario, "learner", "expert", 9)


@pytest.fixture
def stub_devices(monkeypatch):
    """Fake network: every check passes unless one of its injected faults'
    symptom checks is still 'broken'. Configs are recorded."""
    state = {"broken": set(), "applied": []}

    def apply(db, tid, configs):
        state["applied"].append(configs)
        return []
    monkeypatch.setattr(ts, "apply_configs", apply)
    monkeypatch.setattr(ts, "_devices_up", lambda db, tid: (True, ""))
    monkeypatch.setattr(ts.time, "sleep", lambda s: None)

    def run_checks(db, tid, checks):
        return [{"ok": lesson_checks.describe(c) not in state["broken"], "describe": lesson_checks.describe(c),
                 "detail": "", "output": ""} for c in checks]
    monkeypatch.setattr(lesson_checks, "run_checks", run_checks)
    monkeypatch.setattr(chaos_ai, "write_ticket", lambda f, v, d: None)
    return state


def test_full_round(db, scenario, stub_devices, monkeypatch):
    a = _attempt(db, scenario, count=2)
    # Breaking: once injected, the symptom checks of the chosen faults fail.
    real_apply = ts.apply_configs

    def apply(db_, tid, configs):
        real_apply(db_, tid, configs)
        r = a.round
        for fr in r.get("faults") or []:
            _f, v = ts._find(scenario, fr["fault"], fr["variant"])
            if any(ts.render(c, fr["names"]) == configs.get(d) for d, c in v["inject"].items()):
                stub_devices["broken"] |= {lesson_checks.describe(c) for c in v["symptoms"]}
        return []
    monkeypatch.setattr(ts, "apply_configs", apply)
    ts._run(db, scenario, a)
    assert a.state == "active" and len(a.round["faults"]) == 2
    view = ts.view(db, a)
    assert len(view["tickets"]) == 2 and "debrief" not in view["tickets"][0] and view["docs_md"] == ""
    assert all(not f["silent"] for f in a.round["faults"])
    # Still broken: a check doesn't solve it.
    v = ts.check(db, a)
    assert a.state == "active" and v["checks_run"] == 1 and not any(t["resolved"] for t in v["tickets"])
    assert v["health"] is not None and all(h["scope"] == "service" for h in v["health"])      # standard: service only
    v = ts.reveal_hint(db, a, 0)
    assert len(v["tickets"][0]["hints"]) == 1 and v["hints_used"] == 1
    # The learner fixes everything.
    stub_devices["broken"].clear()
    v = ts.check(db, a)
    assert a.state == "solved" and all(t["resolved"] for t in v["tickets"])
    d = v["tickets"][0]["debrief"]
    assert d["title"] and d["changed"] and d["fix"]
    assert a.history[-1]["outcome"] == "solved" and a.history[-1]["hints"] == 1
    # Next round: the previous faults are reverted first.
    stub_devices["applied"].clear()
    a.round = {"pending_revert": a.round}
    ts._run(db, scenario, a)
    reverted = [cfg for cfg in stub_devices["applied"][:2]]
    assert reverted and a.state == "active"


def test_round_refuses_an_unhealthy_network(db, scenario, stub_devices, monkeypatch):
    a = _attempt(db, scenario, count=1)
    stub_devices["broken"].add(lesson_checks.describe(scenario.health[0]))
    monkeypatch.setattr(ts, "HEALTH_TIMEOUT", 0)
    with pytest.raises(ts.ScenarioError, match="isn't healthy"):
        ts._run(db, scenario, a)
    assert stub_devices["applied"] == []                      # nothing injected


def test_difficulty_rules(db, scenario, stub_devices):
    a = _attempt(db, scenario, difficulty="expert", count=1)
    ts._run(db, scenario, a)
    with pytest.raises(ts.ScenarioError, match="Expert"):
        ts.reveal_hint(db, a, 0)
    fr = a.round["faults"][0]
    stub_devices["broken"] |= {lesson_checks.describe(c) for c in ts._find(scenario, fr["fault"], fr["variant"])[1]["symptoms"]}
    v = ts.check(db, a)
    assert a.state == "active"
    assert v["health"] is None and v["tickets"][0]["hints_left"] == 0
    ts.give_up(db, a)
    assert a.state == "revealed" and "debrief" in ts.view(db, a)["tickets"][0]
    g = _attempt(db, ts.save_scenario(db, {**ts.read_dir(ROOT / "sp-core-isis-bgp"),
                                           "meta": {**ts.read_dir(ROOT / "sp-core-isis-bgp")["meta"], "slug": "g-copy"}}),
                 difficulty="guided", count=1)
    assert ts.view(db, g)["docs_md"].startswith("## Network overview")


def test_chaos_ticket_leak_guard(monkeypatch):
    f = {"leak_terms": ["password"]}
    v = {"ticket": "Our IPv4 session (10.3.16.2 ↔ 10.3.16.1) is down.", "reporter": "Upstream B"}
    monkeypatch.setattr(chaos_ai, "available", lambda: True)
    monkeypatch.setattr(chaos_ai, "_ask", lambda *a, **k: "Hi, our session 10.3.16.2 to 10.3.16.1 dropped — maybe a password?")
    assert chaos_ai.write_ticket(f, v, "standard") is None                 # leaks the cause
    monkeypatch.setattr(chaos_ai, "_ask", lambda *a, **k: "Hi team, our session to you is down since 09:00.")
    assert chaos_ai.write_ticket(f, v, "standard") is None                 # lost the addresses
    monkeypatch.setattr(chaos_ai, "_ask", lambda *a, **k: "Hi team — 10.3.16.2 ↔ 10.3.16.1 is down since 09:00, please check.")
    assert chaos_ai.write_ticket(f, v, "expert").startswith("Hi team")
    assert chaos_ai.CHAOS_MODEL == "claude-sonnet-5-5"


def test_routes(client, db, scenario, monkeypatch):
    lst = client.get("/api/troubleshooting").json()
    assert lst[0]["slug"] == "sp-core-isis-bgp" and lst[0]["coverage"]["isis"]["adjacency"] > 0
    d = client.get(f"/api/troubleshooting/{scenario.id}").json()
    assert "faults" not in d and all(n["startup_config"] is None for n in d["topology"]["nodes"])
    assert d["topology"]["annotations"] == []
    assert "faults" in client.get(f"/api/troubleshooting/{scenario.id}?full=true").json()     # admin
    r = client.post(f"/api/troubleshooting/{scenario.id}/start", json={"difficulty": "guided", "faults": 2}).json()
    aid = r["attempt"]["id"]
    assert r["created"] and r["attempt"]["state"] == "idle" and r["attempt"]["docs_md"]
    started = []
    monkeypatch.setattr(ts.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda self: started.append(args)})())
    v = client.post(f"/api/troubleshoot-attempts/{aid}/round", json={"faults": 3}).json()
    assert v["state"] == "preparing" and v["fault_count"] == 3 and started == [(aid,)]
    assert client.post(f"/api/troubleshoot-attempts/{aid}/check").status_code == 400      # no round yet
    tid = r["attempt"]["topology_id"]
    assert client.get(f"/api/troubleshoot-attempts/by-topology/{tid}").json()["attempt"]["id"] == aid
    t = next(x for x in client.get("/api/topologies/").json() if x["id"] == tid)
    assert t["lesson_role"] == "troubleshoot" and t["scenario_id"] == scenario.id and t["lesson_ai_policy"] == "hints"
    ts._RUNNING.discard(aid)
    monkeypatch.setattr(config, "LEARNING_ENABLED", False)
    assert client.get("/api/troubleshooting").status_code == 404

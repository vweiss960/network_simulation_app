"""Troubleshooting scenarios: the bundled packs, the template engine
(targets × templates × sides → candidates), validation (incl. names that
would give a fault away), Chaos's fault selection (the AI plan first, then
the rules) and naming, a full round with the devices stubbed, rebuild as a
fix, the config diff, the Chaos model's leak guard, and the routes."""
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
    cands, errs = ts.expand(pack["faults"], ts._families(pack["topology_yaml"]))
    assert errs == [] and len(cands) >= 60
    kinds = {(c["protocol"], c["category"]) for c in cands}
    for proto in ts.normalize_meta(pack["meta"])["protocols"]:      # every protocol: adjacency and advertisement
        assert {(proto, "adjacency"), (proto, "advertisement")} <= kinds
    assert any(c.get("scope") == "service" for c in pack["health"])
    meta = ts.normalize_meta(pack["meta"])
    assert meta["managed"] and meta["parties"]
    for c in cands:                                   # every placeholder filled, every candidate has a scope
        assert c["scope"] and c["symptoms"] and c["inject"] and c["revert"]
        assert not ts._VAR.search(str(c["symptoms"]) + " ".join(c["inject"].values()))


def test_expand_sides_and_device_override(scenario):
    by_id = {c["id"]: c for c in ts.candidates(scenario)}
    # One link template, both ends: each lands on its own router, the symptom on the other.
    a, b = by_id["isis-auth@R1:GigabitEthernet0/0/0/0"], by_id["isis-auth@R2:GigabitEthernet0/0/0/0"]
    assert list(a["inject"]) == ["R1"] and list(b["inject"]) == ["R2"]
    assert a["symptoms"][0]["device"] == "R2" and a["symptoms"][0]["via"] == "10.0.12.1"
    assert b["symptoms"][0]["device"] == "R1" and b["symptoms"][0]["via"] == "10.0.12.2"
    # The OS picks the config: XE's key chain on R3, XR's hello-password on R1.
    assert "key chain {KEYCHAIN1}" in by_id["isis-auth@R3:GigabitEthernet2"]["inject"]["R3"]
    assert a["family"] == "iosxr" and by_id["isis-auth@R3:GigabitEthernet2"]["family"] == "iosxe"
    # `device: "{rr}"`: the fault goes on the reflector, the text still names the client.
    rr = by_id["ibgp-md5-rr@R3:IPv4"]
    assert list(rr["inject"]) == ["R2"] and "neighbor 3.3.3.3" in rr["inject"]["R2"] and "R3" in rr["label"]
    # XR-only templates skip XE routers; `requires` filters targets.
    assert not any(c["template"] == "ebgp-no-policy-in" and c["family"] == "iosxe" for c in by_id.values())
    assert {c["id"] for c in by_id.values() if c["template"] == "ebgp-max-prefix"} == {
        "ebgp-max-prefix@R1:10.3.16.2", "ebgp-max-prefix@R1:2001:db8:3:16::2",
        "ebgp-max-prefix@R3:10.2.35.2", "ebgp-max-prefix@R3:2001:DB8:2:35::2"}
    # Multi-device targets: the aggregate's anchor goes away on both edges.
    assert set(by_id["aggregate-anchor@IPv4"]["inject"]) == {"R1", "R3"}
    assert "65110" in by_id["ebgp-remote-as@R1:10.1.14.2"]["leak_terms"]     # leak terms are filled too


def test_expand_reports_problems():
    cat = {"targets": {"x": [{"dev": "R1"}]},
           "templates": [{"id": "t", "target": "x", "config": {"iosxr": {"inject": "{nope}", "revert": "x"}}},
                         {"id": "u", "target": "missing"}]}
    cands, errs = ts.expand(cat, {"R1": "iosxr"})
    assert cands == [] and any("{nope}" in e for e in errs) and any("unknown target" in e for e in errs)


def test_validate_rejects_giveaways():
    pack = ts.read_dir(ROOT / "sp-core-isis-bgp")
    bad = copy.deepcopy(pack)
    t = next(t for t in bad["faults"]["templates"] if t["id"] == "isis-auth")
    t["config"]["iosxr"]["inject"] = "route-policy CHAOS-DROP\n  drop\nend-policy\ninterface Loopback9\n description broken by chaos\n"
    errs = ts.validate(bad)
    assert any("CHAOS-DROP" in e for e in errs) and any("descriptions" in e for e in errs)
    bad = copy.deepcopy(pack)
    bad["faults"]["targets"]["isis_router"][0]["dev"] = "R9"
    assert any("R9" in e for e in ts.validate(bad))
    bad = copy.deepcopy(pack)
    bad["meta"]["managed"] = ["R1", "R42"]
    assert any("R42" in e for e in ts.validate(bad))


def test_choose_faults_disjoint_and_spread(scenario):
    for seed in range(30):
        picked = ts.choose_faults(scenario, 4, random.Random(seed))
        assert len(picked) == 4
        assert len({c["template"] for c in picked}) == 4
        scopes = [s for c in picked for s in c["scope"]]
        assert len(scopes) == len(set(scopes))
        assert {c["protocol"] for c in picked} == {"isis", "bgp"}      # spread over protocols
        assert len({(c["protocol"], c["category"]) for c in picked}) == 4
    # Different every time: many distinct rounds from many seeds.
    rounds = {tuple(sorted(c["id"] for c in ts.choose_faults(scenario, 2, random.Random(s)))) for s in range(60)}
    assert len(rounds) > 40


def test_choose_faults_follows_the_plan_within_the_rules(scenario):
    plan = ["isis-mtu@R2:GigabitEthernet0/0/0/1", "isis-auth@R3:GigabitEthernet2",     # same link: 2nd refused
            "no-such-fault", "ebgp-md5@R3:10.2.35.2"]
    picked = ts.choose_faults(scenario, 3, random.Random(1), preferred=plan)
    ids = [c["id"] for c in picked]
    assert ids[:2] == ["isis-mtu@R2:GigabitEthernet0/0/0/1", "ebgp-md5@R3:10.2.35.2"] and len(ids) == 3
    assert "isis-auth@R3:GigabitEthernet2" not in ids


def test_names_are_neutral_and_unique(scenario):
    c = ts.get_candidate(scenario, "ebgp-no-export@R1:10.3.16.2")
    taken = set()
    names = ts.make_names(c, random.Random(1), taken)
    assert set(names) == {"POLICY1"} and names["POLICY1"] in ts.NAME_POOLS["POLICY"]
    cfg = ts.render(c["inject"]["R1"], names)
    assert "{POLICY1}" not in cfg and names["POLICY1"] in cfg and "neighbor 10.3.16.2" in cfg
    for pool in ts.NAME_POOLS.values():
        for n in pool:
            assert not any(w in n.upper() for w in ("DENY", "BLOCK", "DROP", "BAD", "BREAK", "CHAOS", "FAULT", "EXPORT-NO"))
    k = ts.get_candidate(scenario, "isis-auth@R3:GigabitEthernet3")
    n2 = ts.make_names(k, random.Random(2), taken)
    assert n2["KEYCHAIN1"] in ts.NAME_POOLS["KEYCHAIN"] and len(n2["SECRET1"]) == 10


def test_config_diff():
    before = {"R1": "hostname R1\nrouter bgp 65000\n neighbor 10.1.14.2\n  password clear x\n", "R2": "a\nb", "R3": ""}
    after = {"R1": "hostname R1\nrouter bgp 65000\n neighbor 10.1.14.2\n", "R2": "a\nb", "R3": "x"}
    d = ts.config_diff(before, after)
    assert set(d) == {"R1"} and "-  password clear x" in d["R1"]          # unchanged and unreadable devices skipped


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
    symptom checks is still 'broken'. Configs are recorded; the Chaos model
    is off unless a test turns it on."""
    state = {"broken": set(), "applied": [], "configs": {}}

    def apply(db, tid, configs):
        state["applied"].append(configs)
        return []
    monkeypatch.setattr(ts, "apply_configs", apply)
    monkeypatch.setattr(ts, "_devices_up", lambda db, tid: (True, ""))
    monkeypatch.setattr(ts.time, "sleep", lambda s: None)
    monkeypatch.setattr(ts, "snapshot", lambda db, tid, devs: {d: state["configs"].get(d, f"hostname {d}") for d in devs})

    def run_checks(db, tid, checks):
        return [{"ok": lesson_checks.describe(c) not in state["broken"], "describe": lesson_checks.describe(c),
                 "detail": "", "output": ""} for c in checks]
    monkeypatch.setattr(lesson_checks, "run_checks", run_checks)
    monkeypatch.setattr(chaos_ai, "available", lambda: False)
    return state


def _breaks_on_inject(monkeypatch, stub_devices, scenario, a):
    """Once a fault's inject is applied, its symptom checks fail."""
    def apply(db_, tid, configs):
        stub_devices["applied"].append(configs)
        for fr in a.round.get("faults") or []:
            c = ts.get_candidate(scenario, fr["fault"])
            if any(ts.render(cfg, fr["names"]) == configs.get(d) for d, cfg in c["inject"].items()):
                stub_devices["broken"] |= {lesson_checks.describe(x) for x in c["symptoms"]}
        return []
    monkeypatch.setattr(ts, "apply_configs", apply)


def test_full_round(db, scenario, stub_devices, monkeypatch):
    a = _attempt(db, scenario, count=2)
    _breaks_on_inject(monkeypatch, stub_devices, scenario, a)
    ts._run(db, scenario, a)
    assert a.state == "active" and len(a.round["faults"]) == 2 and a.round["planned_by"] == "random"
    assert a.round["managed"] == ["R1", "R2", "R3"] and set(a.round["after_chaos"]) == {"R1", "R2", "R3"}
    view = ts.view(db, a)
    assert len(view["tickets"]) == 2 and "debrief" not in view["tickets"][0] and view["docs_md"] == ""
    assert view["story"] is None and view["changes"] is None                       # nothing given away mid-round
    assert all(not f["silent"] for f in a.round["faults"])
    # Still broken: a check doesn't solve it.
    v = ts.check(db, a)
    assert a.state == "active" and v["checks_run"] == 1 and not any(t["resolved"] for t in v["tickets"])
    assert v["health"] is not None and all(h["scope"] == "service" for h in v["health"])      # standard: service only
    v = ts.reveal_hint(db, a, 0)
    assert len(v["tickets"][0]["hints"]) == 1 and v["hints_used"] == 1
    # The learner fixes everything (and the config diff records how).
    stub_devices["broken"].clear()
    stub_devices["configs"]["R1"] = "hostname R1\nrouter bgp 65000"
    v = ts.check(db, a)
    assert a.state == "solved" and all(t["resolved"] for t in v["tickets"])
    d = v["tickets"][0]["debrief"]
    assert d["title"] and d["changed"] and d["fix"] and d["where"]
    assert set(v["changes"]) == {"R1"} and "+router bgp 65000" in v["changes"]["R1"]
    assert a.history[-1]["outcome"] == "solved" and a.history[-1]["hints"] == 1
    # Next round: the previous faults are reverted first.
    stub_devices["applied"].clear()
    a.round = {"pending_revert": a.round}
    ts._run(db, scenario, a)
    assert stub_devices["applied"] and a.state == "active"


def test_round_follows_the_ai_plan_and_tickets(db, scenario, stub_devices, monkeypatch):
    a = _attempt(db, scenario, count=2)
    _breaks_on_inject(monkeypatch, stub_devices, scenario, a)
    seen = {}

    def plan(s, cands, n, difficulty, history):
        seen["n"], seen["menu"] = n, len(cands)
        return {"picks": ["isis-loopback@R3", "ebgp-max-prefix@R1:10.3.16.2"], "theme": "A maintenance window",
                "story": "Someone cleaned up R3's IS-IS and tightened a limit."}

    def tickets(s, difficulty, story, items, broken):
        seen["observed"] = [it["observed"] for it in items]
        return [{"reporter": "Upstream B peering desk (AS 65300)", "ticket": "Our session dropped."}, None]
    monkeypatch.setattr(chaos_ai, "plan_round", plan)
    monkeypatch.setattr(chaos_ai, "write_tickets", tickets)
    ts._run(db, scenario, a)
    assert [f["fault"] for f in a.round["faults"]] == ["isis-loopback@R3", "ebgp-max-prefix@R1:10.3.16.2"]
    assert seen["n"] == 2 and seen["menu"] == len(ts.candidates(scenario)) and all(seen["observed"])
    assert a.round["planned_by"] == "ai" and a.round["theme"] == "A maintenance window"
    v = ts.view(db, a)
    assert v["tickets"][0]["ticket"] == "Our session dropped."
    assert v["tickets"][1]["ticket"] == ts.get_candidate(scenario, "ebgp-max-prefix@R1:10.3.16.2")["ticket"]
    ts.give_up(db, a)
    assert ts.view(db, a)["story"].startswith("Someone")


def test_rebuild_during_a_round_counts_as_a_fix(db, scenario, stub_devices, monkeypatch):
    from app.services import lab_service
    monkeypatch.setattr(lab_service, "destroy_lab", lambda db, tid: None)
    a = _attempt(db, scenario, count=1)
    _breaks_on_inject(monkeypatch, stub_devices, scenario, a)
    ts._run(db, scenario, a)
    ts.rebuild_lab(db, a)
    assert a.state == "active" and a.round["rebuilt"] and a.round["reverted"]
    stub_devices["broken"].clear()                    # the fresh baseline is healthy
    v = ts.check(db, a)
    assert a.state == "solved" and v["rebuilt"] and not v["changes"]
    assert a.history[-1]["rebuilt"] is True
    # The next round doesn't revert faults that went away with the old devices.
    assert ts.view(db, a)["rebuilt"]
    monkeypatch.setattr(ts.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda self: None})())
    ts.start_round(db, a)
    assert "pending_revert" not in a.round
    ts._RUNNING.discard(a.id)


def test_round_refuses_an_unhealthy_network(db, scenario, stub_devices, monkeypatch):
    a = _attempt(db, scenario, count=1)
    stub_devices["broken"].add(lesson_checks.describe(scenario.health[0]))
    monkeypatch.setattr(ts, "HEALTH_TIMEOUT", 0)
    with pytest.raises(ts.ScenarioError, match="isn't healthy"):
        ts._run(db, scenario, a)
    assert stub_devices["applied"] == []                      # nothing injected


def test_a_fault_the_network_heals_is_replaced(db, scenario, stub_devices, monkeypatch):
    """The first pick breaks things, then the network heals during the
    settle (e.g. a session re-forms from the other side): Chaos reverts it,
    marks it ineffective, injects another, and tickets come from what's
    still broken."""
    a = _attempt(db, scenario, count=1)
    _breaks_on_inject(monkeypatch, stub_devices, scenario, a)
    flaky = "ibgp-update-source@R3:IPv6"
    monkeypatch.setattr(chaos_ai, "plan_round", lambda s, cands, n, d, h: {"picks": [flaky], "theme": "t", "story": "s"}
                        if any(c["id"] == flaky for c in cands) else None)
    seen = {}

    def tickets(s, difficulty, story, items, broken):
        seen["items"], seen["broken"] = items, broken
        return None
    monkeypatch.setattr(chaos_ai, "write_tickets", tickets)
    flaky_sym = {lesson_checks.describe(x) for x in ts.get_candidate(scenario, flaky)["symptoms"]}

    def sleep(sec):
        if sec == ts.SETTLE_SECONDS:
            stub_devices["broken"] -= flaky_sym           # healed by itself
    monkeypatch.setattr(ts.time, "sleep", sleep)
    ts._run(db, scenario, a)
    assert a.state == "active" and [f["fault"] for f in a.round["faults"]] != [flaky]
    assert flaky in scenario.ineffective and flaky not in {c["id"] for c in ts.live_candidates(scenario)}
    assert len(a.round["faults"]) == 1 and seen["items"][0]["observed"]
    assert a.round["planned_by"] == "ai+replaced" and a.round["story"] == ""       # story re-told (AI off → none)
    # The flaky fault was reverted on the device.
    c = ts.get_candidate(scenario, flaky)
    assert any(cfg == ts.render(c["revert"]["R3"], {}) for cfg in (x.get("R3") for x in stub_devices["applied"]))


def test_difficulty_rules(db, scenario, stub_devices, monkeypatch):
    a = _attempt(db, scenario, difficulty="expert", count=1)
    _breaks_on_inject(monkeypatch, stub_devices, scenario, a)
    ts._run(db, scenario, a)
    with pytest.raises(ts.ScenarioError, match="Expert"):
        ts.reveal_hint(db, a, 0)
    fr = a.round["faults"][0]
    stub_devices["broken"] |= {lesson_checks.describe(c) for c in ts.get_candidate(scenario, fr["fault"])["symptoms"]}
    v = ts.check(db, a)
    assert a.state == "active"
    assert v["health"] is None and v["tickets"][0]["hints_left"] == 0
    ts.give_up(db, a)
    assert a.state == "revealed" and "debrief" in ts.view(db, a)["tickets"][0]
    g = _attempt(db, ts.save_scenario(db, {**ts.read_dir(ROOT / "sp-core-isis-bgp"),
                                           "meta": {**ts.read_dir(ROOT / "sp-core-isis-bgp")["meta"], "slug": "g-copy"}}),
                 difficulty="guided", count=1)
    assert ts.view(db, g)["docs_md"].startswith("## Network overview")


def test_chaos_plan_and_ticket_guards(scenario, monkeypatch):
    monkeypatch.setattr(chaos_ai, "available", lambda: True)
    cands = ts.candidates(scenario)
    monkeypatch.setattr(chaos_ai, "_ask", lambda *a, **k: {"picks": ["made-up", cands[0]["id"]], "theme": "t", "story": "s"})
    assert chaos_ai.plan_round(scenario, cands, 2, "standard", [])["picks"] == [cands[0]["id"]]     # menu ids only
    monkeypatch.setattr(chaos_ai, "_ask", lambda *a, **k: {"picks": ["made-up"], "theme": "", "story": ""})
    assert chaos_ai.plan_round(scenario, cands, 1, "standard", []) is None                        # → random draw
    fault = {"leak_terms": ["password"], "reporter": "AS 65000 monitoring"}
    items = [{"observed": ["R6: 100.64.0.0/16 via 10.3.16.1"], "fault": fault}] * 3
    monkeypatch.setattr(chaos_ai, "_ask", lambda *a, **k: {"tickets": [
        {"reporter": "Upstream B peering desk (AS 65300)", "ticket": "Session down — maybe a password?"},   # leaks
        {"reporter": "Someone invented", "ticket": "We lost 100.64.0.0/16 from you at 10.3.16.1."},       # bad reporter
        {"reporter": "Upstream B peering desk (AS 65300)", "ticket": "Your aggregate is gone since 09:00."}]})
    out = chaos_ai.write_tickets(scenario, "standard", "", items, [])
    assert out[0] is None
    assert out[1]["reporter"] == "AS 65000 monitoring" and "100.64.0.0/16" in out[1]["ticket"]
    assert out[2]["reporter"] == "Upstream B peering desk (AS 65300)"
    monkeypatch.setattr(chaos_ai, "available", lambda: False)
    assert chaos_ai.plan_round(scenario, cands, 1, "standard", []) is None
    assert chaos_ai.CHAOS_MODEL == "claude-sonnet-5-5"


def test_routes(client, db, scenario, monkeypatch):
    lst = client.get("/api/troubleshooting").json()
    assert lst[0]["slug"] == "sp-core-isis-bgp" and lst[0]["coverage"]["isis"]["adjacency"] > 0
    d = client.get(f"/api/troubleshooting/{scenario.id}").json()
    assert "catalog" not in d and "candidates" not in d and all(n["startup_config"] is None for n in d["topology"]["nodes"])
    assert d["topology"]["annotations"] == []
    full = client.get(f"/api/troubleshooting/{scenario.id}?full=true").json()     # admin
    assert full["catalog"]["templates"] and len(full["candidates"]) == len(ts.candidates(scenario))
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

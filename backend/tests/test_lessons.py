"""Lessons: the bundled OSPF pack, pack round trip / validation / import
modes, attempts (start, resume, check, reset), the learner's view, authoring
capture and the AI policy."""
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.models.lesson import Lesson, LessonAttempt
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.topology import Topology
from app.services import lab_agent, lab_service, lesson_checks, lessons as svc, topology_io

PACK = Path(__file__).resolve().parents[2] / "curriculum" / "lessons" / "ospf-xr-multiarea-area-types"


def _pack():
    return svc.read_dir(PACK)


@pytest.fixture
def ospf(db):
    meta, topo, tasks, content = _pack()
    l = svc.save_lesson(db, meta, topo, tasks, content)
    db.commit()
    return l


def test_bundled_ospf_pack_is_valid(db):
    meta, topo, tasks, content = _pack()
    assert svc.validate(svc.normalize_meta(meta), topo, tasks, content) == []
    assert svc.seed_bundled(db, PACK.parents[1]) == ["ospf-xr-multiarea-area-types"]
    assert svc.seed_bundled(db, PACK.parents[1]) == []              # same version: left alone
    l = db.query(Lesson).one()
    assert l.track == "service_provider" and l.level == "intermediate" and "ospf" in l.tags
    assert svc.topology_summary(l.topology_yaml) == "5 routers · 2 hosts"
    assert [t["id"] for t in l.tasks][:2] == ["backbone-v2", "backbone-v3"]
    assert {r["kind"] for r in svc.requirements(l)} == {"cisco_xrd", "cisco_csr1000v", "linux"}
    titles = [s["title"] for s in svc.content_sections(l.content_md)]
    assert titles[0] == "Why split OSPF into areas?" and len(titles) == 10


def test_seed_updates_newer_version_unless_edited(db, tmp_path):
    root = tmp_path / "curriculum"
    d = root / "lessons" / "x"
    d.mkdir(parents=True)
    (d / "lesson.yaml").write_text("slug: x-lesson\ntitle: X\nversion: '1.0'\n")
    (d / "topology.yaml").write_text("name: x\nnodes: []\nlinks: []\n")
    svc.seed_bundled(db, root)
    (d / "lesson.yaml").write_text("slug: x-lesson\ntitle: X two\nversion: '1.1'\n")
    assert svc.seed_bundled(db, root) == ["x-lesson"]
    assert db.query(Lesson).one().title == "X two"
    db.query(Lesson).one().edited_in_app = 1
    db.commit()
    (d / "lesson.yaml").write_text("slug: x-lesson\ntitle: X three\nversion: '1.2'\n")
    assert svc.seed_bundled(db, root) == []


def test_pack_round_trip(db, ospf):
    meta, topo, tasks, content = svc.read_zip(svc.export_zip(ospf))
    assert svc.normalize_meta(meta)["slug"] == ospf.slug and meta["requires"]
    assert svc.normalize_tasks(tasks) == ospf.tasks
    assert yaml.safe_load(topo) == yaml.safe_load(ospf.topology_yaml)
    assert content == ospf.content_md


def test_validation_errors():
    topo = "name: t\nnodes:\n  - {name: R1, kind: cisco_xrd, image: x}\nlinks: []\n"
    meta = svc.normalize_meta({"title": "T", "level": "expert"})
    tasks = [{"id": "a", "title": "A", "checks": [{"type": "cli", "device": "R9", "command": "show x",
                                                 "expect": [{"regex": "("}]}]},
             {"id": "a", "title": "", "solution": {"R7": "x"}, "checks": [{"type": "teleport"}]}]
    errs = " | ".join(svc.validate(meta, topo, tasks))
    for frag in ("level must be one of", "device 'R9' isn't in the lesson topology", "bad regex",
                 "duplicate id", "a title is required", "solution for unknown device 'R7'", "unknown check type"):
        assert frag in errs, frag
    assert any("topology" in e for e in svc.validate(svc.normalize_meta({"title": "T"}),
                                                      "nodes:\n  - {name: A, kind: nope}\n", []))


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(f"pack/{k}", v)
    return buf.getvalue()


def test_import_modes(client, db):
    files = {p.name: p.read_text() for p in PACK.iterdir()}
    up = lambda mode="new": client.post(f"/api/lessons/import?mode={mode}",  # noqa: E731
                                        files={"file": ("l.zip", _zip(files), "application/zip")})
    r = up()
    assert r.status_code == 200, r.text
    assert up().status_code == 409
    r = up("copy")
    assert r.status_code == 200 and r.json()["slug"] == "ospf-xr-multiarea-area-types-2"
    assert up("replace").status_code == 200
    assert db.query(Lesson).count() == 2
    bad = client.post("/api/lessons/import", files={"file": ("l.zip", b"not a zip", "application/zip")})
    assert bad.status_code == 400
    lid = r.json()["id"]
    ex = client.get(f"/api/lessons/{lid}/export")
    assert ex.status_code == 200 and ex.headers["content-type"] == "application/zip"
    assert svc.read_zip(ex.content)[0]["slug"] == "ospf-xr-multiarea-area-types-2"


def test_attempt_start_resume_check_reset(db, ospf, monkeypatch):
    a, created = svc.start_attempt(db, ospf, "alice")
    assert created
    t = db.get(Topology, a.topology_id)
    assert t.lesson_id == ospf.id and t.lesson_role == "attempt"
    nodes = db.query(NodeInstance).filter_by(topology_id=t.id).all()
    assert len(nodes) == 7
    assert db.query(Link).filter_by(topology_id=t.id, label="area 0").count() == 2
    assert t.annotations[0]["text"].startswith("AREA 1")
    assert svc.start_attempt(db, ospf, "alice") == (a, False)              # resume
    assert svc.start_attempt(db, ospf, "bob")[1] is True                    # each learner their own lab

    monkeypatch.setattr(lesson_checks, "run_checks",
                        lambda db_, tid, checks: [{"ok": True, "describe": "x", "detail": "", "output": ""} for _ in checks])
    svc.check_attempt(db, a, "backbone-v2")
    assert a.results["backbone-v2"]["passed"] and a.completed_at is None
    svc.check_attempt(db, a)
    assert a.completed_at is not None

    # A later change breaks an earlier task's checks: it stays passed.
    monkeypatch.setattr(lesson_checks, "run_checks",
                        lambda db_, tid, checks: [{"ok": False, "describe": "x", "detail": "gone", "output": ""} for _ in checks])
    r = svc.check_attempt(db, a, "external")["external"]
    assert r["passed"] and r["changed_since"] and r["passed_at"]
    assert svc.check_attempt(db, a) == {}                  # Check all: nothing left to run

    destroyed = []
    monkeypatch.setattr(lab_service, "destroy_lab", lambda db_, tid: destroyed.append(tid))
    db.query(NodeInstance).filter_by(topology_id=t.id, name="H1").one().startup_config = "changed"
    svc.reset_attempt(db, a)
    assert destroyed == [t.id] and a.results == {} and a.completed_at is None
    h1 = db.query(NodeInstance).filter_by(topology_id=t.id, name="H1").one()
    assert "10.1.4.10/24" in h1.startup_config
    assert db.query(NodeInstance).filter_by(topology_id=t.id).count() == 7


def test_learner_view_and_hints(client, db, ospf, monkeypatch):
    from app.main import app
    from app.auth.dependencies import get_current_user
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2, username="learner", is_admin=False)
    d = client.get(f"/api/lessons/{ospf.id}").json()
    assert "solution" not in d["tasks"][0] and d["tasks"][0]["hint_count"] == 4
    assert d["tasks"][0]["checks"][0]["describe"].startswith("R2 (XR): show ospf neighbor")
    assert client.get(f"/api/lessons/{ospf.id}?full=true").json()["tasks"][0].get("solution") is None
    assert client.post("/api/lessons", json={"meta": {"title": "x"}}).status_code == 403

    r = client.post(f"/api/lessons/{ospf.id}/start").json()
    aid = r["attempt"]["id"]
    assert r["created"] and r["attempt"]["hints"]["backbone-v2"] == []          # on request: none shown yet
    h = client.post(f"/api/lesson-attempts/{aid}/hint?task=backbone-v2").json()
    assert len(h["hints"]["backbone-v2"]) == 1
    sol = client.get(f"/api/lesson-attempts/{aid}/solution?task=backbone-v2").json()
    assert "router ospf 1" in sol["solution"]["R1"]
    panel = client.get(f"/api/lesson-attempts/by-topology/{r['attempt']['topology_id']}").json()
    assert panel["lesson"]["title"] == ospf.title and panel["mine"]

    # Someone else's attempt is invisible.
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=3, username="eve", is_admin=False)
    assert client.post(f"/api/lesson-attempts/{aid}/check").status_code == 404


def test_hints_mode_none_hides_hints_and_solution(client, db, ospf):
    ospf.hints_mode = "none"
    db.commit()
    from app.main import app
    from app.auth.dependencies import get_current_user
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2, username="learner", is_admin=False)
    aid = client.post(f"/api/lessons/{ospf.id}/start").json()["attempt"]["id"]
    assert client.post(f"/api/lesson-attempts/{aid}/hint?task=backbone-v2").status_code == 400
    assert client.get(f"/api/lesson-attempts/{aid}/solution?task=backbone-v2").status_code == 403


def test_authoring_open_save_and_capture(db, ospf, monkeypatch):
    t = svc.open_authoring(db, ospf)
    assert t.lesson_role == "authoring" and svc.open_authoring(db, ospf).id == t.id
    r1 = db.query(NodeInstance).filter_by(topology_id=t.id, name="R1").one()
    r1.x = 999.0
    from app.services import device_exec
    monkeypatch.setattr(lab_service, "get_deployment_state", lambda db_, tid: {
        "nodes": {n.id: {"state": "running" if n.name == "R1" else "absent"}
                  for n in db.query(NodeInstance).filter_by(topology_id=tid)}})
    monkeypatch.setattr(device_exec, "target_for", lambda db_, tid, nid: SimpleNamespace())

    async def fake_running(t, timeout=60):
        return ("hostname R1\nusername clab\n group root-lr\n!\ninterface MgmtEth0/RP0/CPU0/0\n ipv4 address 172.30.0.9 255.255.0.0\n!\n"
                "interface Loopback0\n ipv4 address 1.1.1.1 255.255.255.255\n!\nrouter static\n address-family ipv4 unicast\n"
                "  0.0.0.0/0 MgmtEth0/RP0/CPU0/0 172.30.0.1\n  10.9.0.0/16 Null0\n !\n!\nrouter ospf 1\n area 0\n!\nend\n")
    monkeypatch.setattr(device_exec, "running_config", fake_running)
    res = svc.save_authoring(db, ospf, capture_running=True)
    assert res["captured"] == ["R1"] and not res["failed"]
    spec = yaml.safe_load(ospf.topology_yaml)
    r1 = next(n for n in spec["nodes"] if n["name"] == "R1")
    assert r1["position"][0] == 999.0
    cfg = r1["startup_config"]
    assert "router ospf 1" in cfg and "10.9.0.0/16 Null0" in cfg
    for gone in ("MgmtEth", "username clab", "hostname", "172.30.0"):
        assert gone not in cfg, gone
    assert ospf.edited_in_app == 1


def test_ai_policy(client, db, ospf, monkeypatch):
    a, _ = svc.start_attempt(db, ospf, "tester")
    db.commit()
    assert lab_agent.lesson_policy(a.topology_id) == "hints"
    seen = {}
    monkeypatch.setattr(lab_agent.llm, "AgentConversation",
                        lambda model, tools, server: seen.setdefault("tools", [t["name"] for t in tools]))
    lab_agent._new_convo("claude-opus-5-5", read_only=True)
    assert "apply_config" not in seen["tools"] and "run_show" in seen["tools"]
    block = lab_agent.lesson_prompt(a.topology_id)
    assert "hint-giver" in block and "Bring up the OSPFv2 backbone" in block and "The learner is on:" in block

    ospf.ai_policy = "off"
    db.commit()
    r = client.post(f"/api/topologies/{a.topology_id}/agent/chat", json={"message": "hi"})
    assert r.status_code == 403
    assert lab_agent.lesson_policy(999999) is None


def test_topology_io_enabled_notes_label(db):
    text = ("name: t\nnodes:\n  - {name: A, kind: linux, image: plabs/host:latest, enabled: false, notes: hi}\n"
            "  - {name: B, kind: linux, image: plabs/host:latest}\nlinks:\n  - [A, eth1, B, eth1, uplink]\n")
    t = topology_io.import_topology(db, text)
    db.flush()
    a = db.query(NodeInstance).filter_by(topology_id=t.id, name="A").one()
    assert a.enabled == 0 and a.notes == "hi"
    spec = topology_io.export_topology(db, t.id)
    assert spec["nodes"][0]["enabled"] is False and spec["nodes"][0]["notes"] == "hi"
    assert "enabled" not in spec["nodes"][1]
    assert spec["links"] == [["A", "eth1", "B", "eth1", "uplink"]]


def test_linux_host_startup_runs_as_exec():
    from app.services.clab_yaml import linux_exec_commands
    cmds = linux_exec_commands("linux", "plabs/host:latest", "# addressing\nip addr add 10.0.0.1/24 dev eth1\n\nip route add 10.0.0.0/8 via 10.0.0.254\n")
    assert cmds == ["sh -c 'ip addr add 10.0.0.1/24 dev eth1'", "sh -c 'ip route add 10.0.0.0/8 via 10.0.0.254'"]
    assert linux_exec_commands("linux", "plabs/frr:latest", "x") is None
    assert linux_exec_commands("cisco_xrd", "xrd", "x") is None

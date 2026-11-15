"""Troubleshooting scenarios: catalogue, starting a lab, Chaos rounds, checks,
hints, give-up / debrief, pack import / export, and an admin test lab for
verifying a fault catalog."""
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.models.database import get_db
from app.models.scenario import Scenario, ScenarioAttempt
from app.models.topology import Topology
from app.services import lesson_checks, lessons as lesson_svc, troubleshooting as svc

router = APIRouter(prefix="/api", tags=["troubleshooting"])

DIFFICULTY_INFO = {
    "guided": "Network documentation, the health dashboard, hints, and an AI coach that asks the right questions.",
    "standard": "The topology and the tickets. Customer-facing monitoring only; hints are counted.",
    "expert": "The topology and the tickets. Nothing else — no hints, no AI.",
}


def _scenario(db: Session, sid: int, user) -> Scenario:
    s = db.get(Scenario, sid)
    if not s or (s.status == "draft" and not user.is_admin):
        raise HTTPException(404, "Scenario not found")
    return s


def _summary(db: Session, s: Scenario, user) -> dict:
    a = svc.current_attempt(db, s, user.username)
    return {"id": s.id, "slug": s.slug, "title": s.title, "summary": s.summary, "level": s.level, "track": s.track,
            "tags": s.tags, "protocols": s.protocols, "max_faults": s.max_faults, "version": s.version,
            "author": s.author, "status": s.status, "topology_summary": lesson_svc.topology_summary(s.topology_yaml),
            "coverage": svc.coverage(s),
            "attempt": {"id": a.id, "state": a.state, "topology_id": a.topology_id, "difficulty": a.difficulty,
                        "fault_count": a.fault_count, "rounds": len(a.history)} if a else None}


@router.get("/troubleshooting")
def list_scenarios(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return [_summary(db, s, user) for s in db.query(Scenario).order_by(Scenario.title)
            if s.status != "draft" or user.is_admin]


@router.get("/troubleshooting/{sid}")
def get_scenario(sid: int, full: bool = False, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from app.routes.lessons import _topology_view
    s = _scenario(db, sid, user)
    view = _topology_view(s.topology_yaml)
    view["annotations"] = []
    for n in view["nodes"]:
        n["notes"] = None
        n["startup_config"] = None           # the baseline configs are the answer key
    out = {**_summary(db, s, user), "topology": view, "difficulties": DIFFICULTY_INFO, "can_edit": bool(user.is_admin)}
    if full and user.is_admin:
        out.update(faults=s.faults, health=s.health, docs_md=s.docs_md, topology_yaml=s.topology_yaml)
    return out


class StartIn(BaseModel):
    difficulty: str = "standard"
    faults: int = 1


@router.post("/troubleshooting/{sid}/start")
def start(sid: int, body: StartIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _scenario(db, sid, user)
    try:
        a, created = svc.start_attempt(db, s, user.username, body.difficulty, body.faults)
    except svc.ScenarioError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"attempt": svc.view(db, a), "created": created}


def _attempt(db: Session, aid: int, user) -> ScenarioAttempt:
    a = db.get(ScenarioAttempt, aid)
    if not a or (a.username != user.username and not user.is_admin):
        raise HTTPException(404, "Attempt not found")
    return a


@router.get("/troubleshoot-attempts/by-topology/{tid}")
def attempt_for_topology(tid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = svc.attempt_for_topology(db, tid)
    if not a or (a.username != user.username and not user.is_admin):
        raise HTTPException(404, "This lab isn't a troubleshooting lab")
    s = db.get(Scenario, a.scenario_id)
    return {"scenario": {"id": s.id, "title": s.title, "summary": s.summary, "max_faults": s.max_faults,
                         "protocols": s.protocols},
            "difficulties": DIFFICULTY_INFO, "attempt": svc.view(db, a)}


@router.get("/troubleshoot-attempts/{aid}")
def get_attempt(aid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.view(db, _attempt(db, aid, user))


class RoundIn(BaseModel):
    difficulty: Optional[str] = None
    faults: Optional[int] = None


def _act(db: Session, fn):
    try:
        out = fn()
    except svc.ScenarioError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return out


@router.post("/troubleshoot-attempts/{aid}/round")
def new_round(aid: int, body: RoundIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Let Chaos loose: (revert the last round,) check health, inject faults."""
    a = _attempt(db, aid, user)
    _act(db, lambda: svc.start_round(db, a, body.difficulty, body.faults))
    return svc.view(db, a)


@router.post("/troubleshoot-attempts/{aid}/check")
def check(aid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = _attempt(db, aid, user)
    return _act(db, lambda: svc.check(db, a))


@router.post("/troubleshoot-attempts/{aid}/hint")
def hint(aid: int, ticket: int = Query(...), user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = _attempt(db, aid, user)
    return _act(db, lambda: svc.reveal_hint(db, a, ticket))


@router.post("/troubleshoot-attempts/{aid}/give-up")
def give_up(aid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = _attempt(db, aid, user)
    return _act(db, lambda: svc.give_up(db, a))


@router.post("/troubleshoot-attempts/{aid}/debrief")
def debrief(aid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The Chaos model's narrative debrief (null when AI is off)."""
    a = _attempt(db, aid, user)
    text = _act(db, lambda: svc.debrief_ai(db, a))
    return {"text": text}


@router.post("/troubleshoot-attempts/{aid}/rebuild")
def rebuild(aid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = _attempt(db, aid, user)
    if a.state == "preparing":
        raise HTTPException(409, "Chaos is at work — wait for it to finish")
    _act(db, lambda: svc.rebuild_lab(db, a))
    return svc.view(db, a)


# --- packs ---------------------------------------------------------------------------------------

@router.post("/troubleshooting/import")
async def import_scenario(file: UploadFile = File(...), mode: str = Query("new", pattern="^(new|replace|copy)$"),
                          user=Depends(require_admin), db: Session = Depends(get_db)):
    data = await file.read(svc.MAX_PACK_BYTES + 1)
    try:
        s = svc.import_pack(db, svc.read_zip(data), mode=mode)
    except svc.ScenarioExists as e:
        raise HTTPException(409, {"message": str(e), "slug": e.slug})
    except svc.ScenarioError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _summary(db, s, user)


@router.get("/troubleshooting/{sid}/export")
def export_scenario(sid: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    """Admins only: the pack contains the answer key (faults and baseline)."""
    s = _scenario(db, sid, user)
    return Response(svc.export_zip(s), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{s.slug}-{s.version}.zip"'})


@router.delete("/troubleshooting/{sid}")
def delete_scenario(sid: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    s = _scenario(db, sid, user)
    db.query(ScenarioAttempt).filter(ScenarioAttempt.scenario_id == s.id).delete(synchronize_session=False)
    db.query(Topology).filter(Topology.scenario_id == s.id).update(
        {Topology.scenario_id: None, Topology.lesson_role: None}, synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"ok": True}


# --- admin test lab: prove a catalog on real devices ----------------------------------------------

def _test_lab(db: Session, s: Scenario) -> Topology | None:
    return (db.query(Topology).filter(Topology.scenario_id == s.id, Topology.lesson_role == "troubleshoot_test")
            .order_by(Topology.id.desc()).first())


@router.post("/troubleshooting/{sid}/test-lab")
def open_test_lab(sid: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    s = _scenario(db, sid, user)
    t = _test_lab(db, s) or svc._build_lab(db, s, f"[Test] {s.title}", "troubleshoot_test")
    db.commit()
    return {"topology_id": t.id}


class TestChecksIn(BaseModel):
    checks: list = []
    health: bool = False


@router.post("/troubleshooting/{sid}/test-lab/checks")
def test_checks(sid: int, body: TestChecksIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    s = _scenario(db, sid, user)
    t = _test_lab(db, s)
    if not t:
        raise HTTPException(400, "Open the scenario's test lab first")
    return {"results": lesson_checks.run_checks(db, t.id, s.health if body.health else body.checks)}


class TestFaultIn(BaseModel):
    fault: str
    variant: str
    action: str = "inject"              # inject | revert
    names: dict = {}


@router.post("/troubleshooting/{sid}/test-lab/fault")
def test_fault(sid: int, body: TestFaultIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    """Inject or revert one catalog variant on the test lab (names: reuse the
    ones returned by inject when reverting)."""
    import random
    s = _scenario(db, sid, user)
    t = _test_lab(db, s)
    if not t:
        raise HTTPException(400, "Open the scenario's test lab first")
    try:
        f, v = svc._find(s, body.fault, body.variant)
    except StopIteration:
        raise HTTPException(404, "No such fault / variant")
    names = body.names or svc.make_names(v, random.Random(), set())
    part = v["inject"] if body.action == "inject" else v["revert"]
    errors = svc.apply_configs(db, t.id, {d: svc.render(c, names) for d, c in part.items()})
    return {"names": names, "errors": errors, "symptoms": v["symptoms"]}

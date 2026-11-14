"""Lessons: catalogue, detail, pack import/export, learner attempts (start,
check, hints, solution, reset) and authoring (edit the topology on a lab,
save it back, test the checks)."""
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.models.database import get_db
from app.models.lesson import Lesson, LessonAttempt
from app.models.topology import Topology
from app.services import lesson_checks, lessons as svc

router = APIRouter(prefix="/api", tags=["lessons"])


def _lesson(db: Session, lesson_id: int) -> Lesson:
    l = db.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(404, "Lesson not found")
    return l


def _attempt(db: Session, attempt_id: int, user) -> LessonAttempt:
    a = db.get(LessonAttempt, attempt_id)
    if not a or (a.username != user.username and not user.is_admin):
        raise HTTPException(404, "Attempt not found")
    return a


def _attempt_out(db: Session, a: LessonAttempt | None, lesson: Lesson) -> Optional[dict]:
    if not a:
        return None
    res = a.results
    hints = a.hints
    tasks = lesson.tasks
    return {
        "id": a.id, "topology_id": a.topology_id, "lesson_version": a.lesson_version,
        "results": res,
        "passed": sum(1 for t in tasks if res.get(t["id"], {}).get("passed")), "total": len(tasks),
        # Hints the learner may see: all of them when hints are open.
        "hints": {t["id"]: (t["hints"] if lesson.hints_mode == "open" else t["hints"][:int(hints.get(t["id"], 0))])
                  for t in tasks} if lesson.hints_mode != "none" else {},
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
    }


def _summary(db: Session, l: Lesson, attempt: LessonAttempt | None = None) -> dict:
    return {
        "id": l.id, "slug": l.slug, "title": l.title, "summary": l.summary or "", "level": l.level,
        "track": l.track, "topic": l.topic or "", "tags": l.tags, "minutes": l.minutes, "status": l.status,
        "ai_policy": l.ai_policy, "hints_mode": l.hints_mode, "version": l.version, "author": l.author or "",
        "topology_summary": svc.topology_summary(l.topology_yaml), "task_count": len(l.tasks),
        "updated_at": l.updated_at.isoformat() if l.updated_at else None,
        "attempt": _attempt_out(db, attempt, l) if attempt else None,
    }


def _public_task(t: dict) -> dict:
    """What a learner sees of a task before revealing anything."""
    return {"id": t["id"], "title": t["title"], "body": t["body"], "hint_count": len(t["hints"]),
            "has_solution": bool(t["solution"]),
            "checks": [{"type": c.get("type"), "describe": lesson_checks.describe(c)} for c in t["checks"]]}


@router.get("/lessons")
def list_lessons(q: str = "", level: str = "", track: str = "", topic: str = "", tag: str = "",
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    out = []
    for l in db.query(Lesson).order_by(Lesson.title):
        if l.status == "draft" and not user.is_admin:
            continue
        if level and l.level != level or track and l.track != track or topic and (l.topic or "") != topic:
            continue
        if tag and tag.lower() not in l.tags:
            continue
        if q and q.lower() not in " ".join([l.title, l.summary or "", l.topic or "", *l.tags]).lower():
            continue
        out.append(_summary(db, l, svc.current_attempt(db, l, user.username)))
    return out


@router.get("/lessons/{lesson_id}")
def get_lesson(lesson_id: int, full: bool = False, user=Depends(get_current_user), db: Session = Depends(get_db)):
    l = _lesson(db, lesson_id)
    if l.status == "draft" and not user.is_admin:
        raise HTTPException(404, "Lesson not found")
    from app.services import lab_service
    have = lab_service.local_docker_images()
    reqs = [{**r, "ready": (r["image"] in have) if r["image"] else False} for r in svc.requirements(l)]
    authoring = svc.authoring_topology(db, l) if user.is_admin else None
    out = {
        **_summary(db, l, svc.current_attempt(db, l, user.username)),
        "topology_yaml": l.topology_yaml or "",
        "topology": _topology_view(l.topology_yaml),
        "tasks": l.tasks if (full and user.is_admin) else [_public_task(t) for t in l.tasks],
        "content_md": l.content_md or "",
        "sections": svc.content_sections(l.content_md),
        "requires": reqs,
        "authoring_topology_id": authoring.id if authoring else None,
        "can_edit": bool(user.is_admin),
    }
    return out


def _topology_view(text: str) -> dict:
    """The starting topology as data, for the read-only preview."""
    from app.services import topology_io
    try:
        spec = topology_io.parse(text or "nodes: []\nlinks: []\n")
    except svc.TopologyError:
        return {"nodes": [], "links": [], "annotations": []}
    links = []
    for row in spec.get("links") or []:
        if isinstance(row, dict):
            row = [row.get("a"), row.get("a_interface"), row.get("b"), row.get("b_interface"), row.get("label", "")]
        links.append({"a": row[0], "a_interface": row[1], "b": row[2], "b_interface": row[3],
                      "label": row[4] if len(row) > 4 else ""})
    return {"nodes": [{k: n.get(k) for k in ("name", "kind", "image", "icon", "position", "enabled", "notes",
                                              "startup_config", "network")} for n in spec.get("nodes") or []],
            "links": links, "annotations": spec.get("annotations") or []}


class LessonIn(BaseModel):
    meta: dict
    topology_yaml: str = Field("", max_length=2_000_000)
    tasks: list = []
    content_md: str = Field("", max_length=500_000)


@router.post("/lessons")
def create_lesson(body: LessonIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    meta = {**body.meta, "author": body.meta.get("author") or getattr(user, "display_name", "") or user.username}
    try:
        l = svc.save_lesson(db, meta, body.topology_yaml or "name: lab\nnodes: []\nlinks: []\n", body.tasks,
                            body.content_md, edited_in_app=True)
    except svc.LessonError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _summary(db, l)


@router.put("/lessons/{lesson_id}")
def update_lesson(lesson_id: int, body: LessonIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    l = _lesson(db, lesson_id)
    try:
        svc.save_lesson(db, body.meta, body.topology_yaml, body.tasks, body.content_md, lesson=l, edited_in_app=True)
    except svc.LessonError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _summary(db, l)


@router.post("/lessons/validate")
def validate_lesson(body: LessonIn, user=Depends(require_admin)):
    return {"errors": svc.validate(svc.normalize_meta(body.meta), body.topology_yaml, body.tasks, body.content_md)}


@router.delete("/lessons/{lesson_id}")
def delete_lesson(lesson_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    l = _lesson(db, lesson_id)
    db.query(LessonAttempt).filter(LessonAttempt.lesson_id == l.id).delete(synchronize_session=False)
    # Its labs stay (they may be running); they're just no longer lesson labs.
    db.query(Topology).filter(Topology.lesson_id == l.id).update(
        {Topology.lesson_id: None, Topology.lesson_role: None}, synchronize_session=False)
    db.delete(l)
    db.commit()
    return {"ok": True}


@router.post("/lessons/import")
async def import_lesson(file: UploadFile = File(...), mode: str = Query("new", pattern="^(new|replace|copy)$"),
                        user=Depends(require_admin), db: Session = Depends(get_db)):
    data = await file.read(svc.MAX_PACK_BYTES + 1)
    try:
        meta, topo, tasks, content = svc.read_zip(data)
        l = svc.import_pack(db, meta, topo, tasks, content, mode=mode)
    except svc.LessonExists as e:
        raise HTTPException(409, {"message": str(e), "slug": e.slug})
    except svc.LessonError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _summary(db, l)


@router.get("/lessons/{lesson_id}/export")
def export_lesson(lesson_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    l = _lesson(db, lesson_id)
    if l.status == "draft" and not user.is_admin:
        raise HTTPException(404, "Lesson not found")
    return Response(svc.export_zip(l), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{l.slug}-{l.version}.zip"'})


# --- attempts -------------------------------------------------------------------------------

@router.post("/lessons/{lesson_id}/start")
def start_lesson(lesson_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    l = _lesson(db, lesson_id)
    if l.status == "draft" and not user.is_admin:
        raise HTTPException(404, "Lesson not found")
    try:
        a, created = svc.start_attempt(db, l, user.username)
    except (svc.LessonError, svc.TopologyError) as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"attempt": _attempt_out(db, a, l), "created": created}


@router.get("/lesson-attempts/by-topology/{topology_id}")
def attempt_by_topology(topology_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The lesson running in a lab: what the lesson panel shows."""
    a = svc.attempt_for_topology(db, topology_id)
    if not a:
        raise HTTPException(404, "Not a lesson lab")
    l = db.get(Lesson, a.lesson_id)
    if not l:
        raise HTTPException(404, "The lesson was deleted")
    return {"lesson": {**_summary(db, l), "tasks": [_public_task(t) for t in l.tasks],
                       "sections": svc.content_sections(l.content_md)},
            "attempt": _attempt_out(db, a, l), "mine": a.username == user.username}


@router.post("/lesson-attempts/{attempt_id}/check")
def check(attempt_id: int, task: Optional[str] = None, user=Depends(get_current_user),
          db: Session = Depends(get_db)):
    a = _attempt(db, attempt_id, user)
    try:
        ran = svc.check_attempt(db, a, task)
    except svc.LessonError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ran": ran, "attempt": _attempt_out(db, a, db.get(Lesson, a.lesson_id))}


@router.post("/lesson-attempts/{attempt_id}/hint")
def hint(attempt_id: int, task: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = _attempt(db, attempt_id, user)
    try:
        svc.reveal_hint(db, a, task)
    except svc.LessonError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _attempt_out(db, a, db.get(Lesson, a.lesson_id))


@router.get("/lesson-attempts/{attempt_id}/solution")
def solution(attempt_id: int, task: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = _attempt(db, attempt_id, user)
    l = db.get(Lesson, a.lesson_id)
    t = next((x for x in (l.tasks if l else []) if x["id"] == task), None)
    if not t:
        raise HTTPException(404, "No such task")
    if l.hints_mode == "none" and not user.is_admin:
        raise HTTPException(403, "This lesson doesn't show solutions")
    return {"task": task, "solution": t["solution"]}


@router.post("/lesson-attempts/{attempt_id}/reset")
def reset(attempt_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = _attempt(db, attempt_id, user)
    try:
        svc.reset_attempt(db, a)
    except svc.LessonError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _attempt_out(db, a, db.get(Lesson, a.lesson_id))


# --- authoring ---------------------------------------------------------------------------------

@router.post("/lessons/{lesson_id}/authoring")
def open_authoring(lesson_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    l = _lesson(db, lesson_id)
    try:
        t = svc.open_authoring(db, l)
    except (svc.LessonError, svc.TopologyError) as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"topology_id": t.id}


class SaveAuthoringIn(BaseModel):
    capture_running: bool = False


@router.post("/lessons/{lesson_id}/authoring/save")
def save_authoring(lesson_id: int, body: SaveAuthoringIn, user=Depends(require_admin),
                   db: Session = Depends(get_db)):
    l = _lesson(db, lesson_id)
    try:
        r = svc.save_authoring(db, l, capture_running=body.capture_running)
    except svc.LessonError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {**r, "topology_summary": svc.topology_summary(l.topology_yaml)}


@router.post("/lessons/{lesson_id}/authoring/test-checks")
def test_checks(lesson_id: int, task: Optional[str] = None, user=Depends(require_admin),
                db: Session = Depends(get_db)):
    """Run the lesson's checks against its (deployed) authoring lab."""
    l = _lesson(db, lesson_id)
    t = svc.authoring_topology(db, l)
    if not t:
        raise HTTPException(400, "Open the lesson's topology on the canvas and deploy it first")
    return {tk["id"]: lesson_checks.task_result(lesson_checks.run_checks(db, t.id, tk["checks"]))
            for tk in l.tasks if task is None or tk["id"] == task}

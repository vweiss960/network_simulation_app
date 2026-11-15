"""Module routes: catalogue, detail with the learner's progress, authoring
(admins) and pack import / export."""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.models.database import get_db
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.scenario import Scenario
from app.services import lessons as lesson_svc, modules as svc

router = APIRouter(prefix="/api", tags=["modules"])


def _module(db: Session, module_id: int, user) -> Module:
    m = db.get(Module, module_id)
    if not m or (m.status == "draft" and not user.is_admin):
        raise HTTPException(404, "Module not found")
    return m


def _summary(db: Session, m: Module, username: str) -> dict:
    p = svc.progress(db, m, username)
    return {
        "id": m.id, **svc.module_meta(m), "lesson_count": p["total"], "completed": p["completed"],
        "minutes": p["minutes"], "next_lesson_id": p["next_lesson_id"], "next_item": p["next_item"],
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


@router.get("/modules")
def list_modules(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return [_summary(db, m, user.username) for m in db.query(Module).order_by(Module.title)
            if m.status != "draft" or user.is_admin]


@router.get("/modules/{module_id}")
def get_module(module_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = _module(db, module_id, user)
    p = svc.progress(db, m, user.username)
    items = []
    for it in p["items"]:
        if it["kind"] == "scenario":
            sc = db.get(Scenario, it["scenario_id"]) if it["scenario_id"] else None
            if sc is not None and sc.status == "draft" and not user.is_admin:
                sc = None
                it = {**it, "scenario_id": None, "status": {"state": "not_installed"}}
            items.append({**it, "lesson_title": sc.title if sc else "", "lesson_summary": sc.summary if sc else "",
                          "level": sc.level if sc else None, "minutes": svc.SCENARIO_MINUTES if sc else None,
                          "task_count": 0, "protocols": sc.protocols if sc else [],
                          "fault_count": sum(len(f.get("variants") or []) for f in sc.faults) if sc else 0,
                          "topology_summary": lesson_svc.topology_summary(sc.topology_yaml) if sc else ""})
            continue
        l = db.get(Lesson, it["lesson_id"]) if it["lesson_id"] else None
        if l is not None and l.status == "draft" and not user.is_admin:
            l = None
            it = {**it, "lesson_id": None, "status": {"state": "not_installed"}}
        items.append({**it, "lesson_title": l.title if l else "", "lesson_summary": l.summary if l else "",
                      "level": l.level if l else None, "minutes": l.minutes if l else None,
                      "task_count": len(l.tasks) if l else 0,
                      "topology_summary": lesson_svc.topology_summary(l.topology_yaml) if l else ""})
    return {**_summary(db, m, user.username), "overview_md": m.overview_md or "",
            "sections": lesson_svc.content_sections(m.overview_md), "items": items, "can_edit": bool(user.is_admin)}


class ModuleIn(BaseModel):
    meta: dict
    items: list = []
    overview_md: str = Field("", max_length=500_000)


@router.post("/modules")
def create_module(body: ModuleIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    meta = {**body.meta, "author": body.meta.get("author") or getattr(user, "display_name", "") or user.username}
    try:
        m = svc.save_module(db, meta, body.items, body.overview_md, edited_in_app=True)
    except svc.ModuleError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _summary(db, m, user.username)


@router.put("/modules/{module_id}")
def update_module(module_id: int, body: ModuleIn, user=Depends(require_admin), db: Session = Depends(get_db)):
    m = _module(db, module_id, user)
    try:
        svc.save_module(db, body.meta, body.items, body.overview_md, module=m, edited_in_app=True)
    except svc.ModuleError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _summary(db, m, user.username)


@router.post("/modules/validate")
def validate_module(body: ModuleIn, user=Depends(require_admin)):
    return {"errors": svc.validate(svc.normalize_meta(body.meta), svc.normalize_items(body.items))}


@router.delete("/modules/{module_id}")
def delete_module(module_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    """Deletes the module only — its lessons stay."""
    db.delete(_module(db, module_id, user))
    db.commit()
    return {"ok": True}


@router.post("/modules/import")
async def import_module(file: UploadFile = File(...), mode: str = Query("new", pattern="^(new|replace|copy)$"),
                        user=Depends(require_admin), db: Session = Depends(get_db)):
    data = await file.read(svc.MAX_PACK_BYTES + 1)
    try:
        meta, items, overview, packs, spacks = svc.read_zip(data)
        m, report = svc.import_pack(db, meta, items, overview, packs, spacks, mode=mode)
    except svc.ModuleExists as e:
        db.rollback()
        raise HTTPException(409, {"message": str(e), "slug": e.slug})
    except svc.ModuleError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    return {**_summary(db, m, user.username), "lessons": report}


@router.get("/modules/{module_id}/export")
def export_module(module_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = _module(db, module_id, user)
    # Scenario packs carry their faults (the answer key): admins only.
    return Response(svc.export_zip(db, m, include_scenarios=bool(user.is_admin)), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{m.slug}-{m.version}.zip"'})

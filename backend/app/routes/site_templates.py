"""Site templates — reusable small sites (devices + links + patch panels)
that are dropped into a topology with their variables filled in.

Templates are global, like device templates (`router`, auth only). Adding one
to a topology writes nodes/links, so `instantiate_router` is mounted behind
the protected-topology edit-session guard. Logic lives in
services/site_templates.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.site_template import SiteTemplate
from app.services import site_templates as svc

router = APIRouter(prefix="/api/site-templates", tags=["site-templates"])
instantiate_router = APIRouter(prefix="/api/topologies/{topology_id}/site-templates",
                               tags=["site-templates"])


class SiteTemplateIn(BaseModel):
    name: str
    description: str = ""
    definition: dict


class InstantiateIn(BaseModel):
    variables: dict = {}
    x: float = 0.0
    y: float = 0.0


def _to_dict(t: SiteTemplate) -> dict:
    return {"id": t.id, "name": t.name, "description": t.description or "",
            "definition": t.definition,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None}


def _get(db: Session, template_id: int) -> SiteTemplate:
    t = db.query(SiteTemplate).filter(SiteTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Site template not found")
    return t


def _apply(db: Session, t: SiteTemplate, data: SiteTemplateIn) -> None:
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "A site template needs a name")
    clash = db.query(SiteTemplate).filter(SiteTemplate.name == name, SiteTemplate.id != (t.id or 0)).first()
    if clash:
        raise HTTPException(409, f"A site template named '{name}' already exists")
    try:
        definition = svc.normalize_definition(db, data.definition)
    except svc.SiteTemplateError as e:
        raise HTTPException(e.status, str(e))
    t.name, t.description, t.definition = name, data.description.strip(), definition


@router.get("/")
def list_site_templates(db: Session = Depends(get_db)):
    return [_to_dict(t) for t in db.query(SiteTemplate).order_by(SiteTemplate.name).all()]


@router.post("/")
def create_site_template(data: SiteTemplateIn, db: Session = Depends(get_db)):
    t = SiteTemplate()
    _apply(db, t, data)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _to_dict(t)


@router.put("/{template_id}")
def update_site_template(template_id: int, data: SiteTemplateIn, db: Session = Depends(get_db)):
    t = _get(db, template_id)
    _apply(db, t, data)
    db.commit()
    db.refresh(t)
    return _to_dict(t)


@router.delete("/{template_id}")
def delete_site_template(template_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, template_id))
    db.commit()
    return {"ok": True}


def _patch_panels_enabled() -> bool:
    from app.main import PATCH_PANELS_ENABLED  # lazy: main imports this module
    return PATCH_PANELS_ENABLED


@instantiate_router.post("/{template_id}/instantiate")
def instantiate_site_template(topology_id: int, template_id: int, data: InstantiateIn,
                              db: Session = Depends(get_db)):
    """Add the site to the topology: every node, link and patch panel in one
    transaction (nothing is written if any part fails)."""
    from app.routes.links import _link_to_dict, _link_sync_payload
    from app.routes.nodes import _node_to_dict

    t = _get(db, template_id)
    try:
        # Re-validate: device templates may have changed since it was saved.
        definition = svc.normalize_definition(db, t.definition)
        out = svc.instantiate(db, topology_id, definition, data.variables, data.x, data.y,
                              panels_enabled=_patch_panels_enabled())
    except svc.SiteTemplateError as e:
        db.rollback()
        raise HTTPException(e.status, f"{t.name}: {e}")
    try:
        from app.services.sync_worker import enqueue_sync
        for n in out["nodes"]:
            enqueue_sync(db, topology_id, "node", n.id, "create", _node_to_dict(n))
        for l in out["links"]:
            enqueue_sync(db, topology_id, "link", l.id, "create", _link_sync_payload(l, db))
    except Exception:
        pass
    return {"nodes": [_node_to_dict(n) for n in out["nodes"]],
            "links": [_link_to_dict(l) for l in out["links"]],
            "patch_panels": out["patch_panels"], "warnings": out["warnings"]}


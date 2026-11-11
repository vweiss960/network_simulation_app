"""Service module routes — CRUD on services / templates / variables, plus
render-preview, deploy (SSH push), and export/import.

Gated behind PHANERO_SERVICE_ENABLED (which requires PHANERO_LAB_ENABLED) at
registration time in main.py. Mirrors the Config Design routers: inline Pydantic
schemas, 409 on duplicate names, ordered members via a `position` column.
"""
import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.models.database import get_db
from app.models.service import Service
from app.models.service_template import ServiceTemplate
from app.models.service_variable import ServiceVariable
from app.services.config_renderer import validate_template
from app.services import service_deploy
from app.services.service_deploy import ServiceError


router = APIRouter(prefix="/api/services", tags=["service"])


# ---------------------- Schemas ----------------------

class ServiceCreate(BaseModel):
    name: str
    description: str = ""
    target_mode: str = "lab"          # "lab" | "mgmt_ip"


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_mode: Optional[str] = None


class TemplateCreate(BaseModel):
    name: str
    body: str = ""
    position: Optional[int] = None


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    body: Optional[str] = None
    position: Optional[int] = None


class VariableCreate(BaseModel):
    name: str
    required: bool = False
    default: Optional[Any] = None     # any JSON value; None = no default
    description: str = ""


class VariableUpdate(BaseModel):
    name: Optional[str] = None
    required: Optional[bool] = None
    default: Optional[Any] = None
    description: Optional[str] = None


class ValidateReq(BaseModel):
    body: str


class ReorderReq(BaseModel):
    template_ids: list[int]


class Deployment(BaseModel):
    device: str
    templates: Optional[list[str]] = None    # None → all templates, in order
    variables: dict = {}


class RenderReq(BaseModel):
    topology_id: int
    target_mode: Optional[str] = None        # default = service.target_mode
    deployments: list[Deployment]


class Credentials(BaseModel):
    username: str
    password: str = ""


class DeployReq(BaseModel):
    topology_id: int
    target_mode: Optional[str] = None
    credentials: Optional[Credentials] = None   # required for mgmt_ip mode
    dry_run: bool = False
    deployments: list[Deployment]


class ImportReq(BaseModel):
    service: dict
    templates: list[dict] = []
    variables: list[dict] = []


# ---------------------- Serializers ----------------------

def _var_default(v: ServiceVariable) -> Any:
    if v.default_json in (None, ""):
        return None
    try:
        return json.loads(v.default_json)
    except (json.JSONDecodeError, TypeError):
        return v.default_json


def _template_out(t: ServiceTemplate) -> dict:
    return {"id": t.id, "name": t.name, "body": t.body, "position": t.position}


def _variable_out(v: ServiceVariable) -> dict:
    return {"id": v.id, "name": v.name, "required": v.required,
            "default": _var_default(v), "description": v.description}


def _service_out(s: Service) -> dict:
    return {
        "id": s.id, "name": s.name, "description": s.description,
        "target_mode": s.target_mode,
        "templates": [_template_out(t) for t in s.templates],
        "variables": [_variable_out(v) for v in s.variables],
    }


def _load_service(db: Session, sid: int) -> Service:
    s = (db.query(Service)
         .options(selectinload(Service.templates), selectinload(Service.variables))
         .filter(Service.id == sid).first())
    if not s:
        raise HTTPException(404, "Service not found")
    return s


# ---------------------- Service CRUD ----------------------

@router.get("")
def list_services(db: Session = Depends(get_db)):
    rows = (db.query(Service)
            .options(selectinload(Service.templates), selectinload(Service.variables))
            .order_by(Service.name).all())
    return [_service_out(s) for s in rows]


@router.post("")
def create_service(body: ServiceCreate, db: Session = Depends(get_db)):
    if body.target_mode not in ("lab", "mgmt_ip"):
        raise HTTPException(400, "target_mode must be 'lab' or 'mgmt_ip'")
    if db.query(Service).filter(Service.name == body.name).first():
        raise HTTPException(409, f"A service named '{body.name}' already exists.")
    s = Service(name=body.name, description=body.description, target_mode=body.target_mode)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _service_out(s)


@router.get("/{sid}")
def get_service(sid: int, db: Session = Depends(get_db)):
    return _service_out(_load_service(db, sid))


@router.put("/{sid}")
def update_service(sid: int, body: ServiceUpdate, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    if body.name is not None and body.name != s.name:
        if db.query(Service).filter(Service.name == body.name, Service.id != sid).first():
            raise HTTPException(409, f"A service named '{body.name}' already exists.")
        s.name = body.name
    if body.description is not None:
        s.description = body.description
    if body.target_mode is not None:
        if body.target_mode not in ("lab", "mgmt_ip"):
            raise HTTPException(400, "target_mode must be 'lab' or 'mgmt_ip'")
        s.target_mode = body.target_mode
    db.commit()
    db.refresh(s)
    return _service_out(s)


@router.delete("/{sid}")
def delete_service(sid: int, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    db.delete(s)          # cascade removes templates + variables
    db.commit()
    return {"ok": True}


# ---------------------- Templates ----------------------

@router.get("/{sid}/templates")
def list_templates(sid: int, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    return [_template_out(t) for t in s.templates]


@router.post("/{sid}/templates")
def create_template(sid: int, body: TemplateCreate, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "template name is required")
    if any(t.name == name for t in s.templates):
        raise HTTPException(409, f"A template named '{name}' already exists in this service.")
    pos = body.position if body.position is not None else len(s.templates)
    t = ServiceTemplate(service_id=sid, name=name, body=body.body or "", position=pos)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _template_out(t)


def _get_template(db: Session, sid: int, tid: int) -> ServiceTemplate:
    t = db.query(ServiceTemplate).filter(
        ServiceTemplate.id == tid, ServiceTemplate.service_id == sid).first()
    if not t:
        raise HTTPException(404, "Template not found")
    return t


@router.put("/{sid}/templates/{tid}")
def update_template(sid: int, tid: int, body: TemplateUpdate, db: Session = Depends(get_db)):
    t = _get_template(db, sid, tid)
    if body.name is not None and body.name != t.name:
        if db.query(ServiceTemplate).filter(
                ServiceTemplate.service_id == sid, ServiceTemplate.name == body.name,
                ServiceTemplate.id != tid).first():
            raise HTTPException(409, f"A template named '{body.name}' already exists in this service.")
        t.name = body.name
    if body.body is not None:
        t.body = body.body
    if body.position is not None:
        t.position = body.position
    db.commit()
    db.refresh(t)
    return _template_out(t)


@router.delete("/{sid}/templates/{tid}")
def delete_template(sid: int, tid: int, db: Session = Depends(get_db)):
    t = _get_template(db, sid, tid)
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.put("/{sid}/templates/reorder")
def reorder_templates(sid: int, body: ReorderReq, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    order = {tid: i for i, tid in enumerate(body.template_ids)}
    # Listed templates get their index; any unlisted go to the end (stable).
    tail = len(order)
    for t in s.templates:
        t.position = order.get(t.id, tail)
        if t.id not in order:
            tail += 1
    db.commit()
    db.refresh(s)
    return [_template_out(t) for t in s.templates]


@router.post("/{sid}/templates/validate")
def validate_service_template(sid: int, body: ValidateReq, db: Session = Depends(get_db)):
    _load_service(db, sid)   # 404 if the service is gone
    return validate_template(body.body)


# ---------------------- Variables ----------------------

@router.get("/{sid}/variables")
def list_variables(sid: int, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    return [_variable_out(v) for v in s.variables]


@router.post("/{sid}/variables")
def create_variable(sid: int, body: VariableCreate, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "variable name is required")
    if any(v.name == name for v in s.variables):
        raise HTTPException(409, f"A variable named '{name}' already exists in this service.")
    default_json = "" if body.default is None else json.dumps(body.default)
    v = ServiceVariable(service_id=sid, name=name, required=body.required,
                        default_json=default_json, description=body.description)
    db.add(v)
    db.commit()
    db.refresh(v)
    return _variable_out(v)


def _get_variable(db: Session, sid: int, vid: int) -> ServiceVariable:
    v = db.query(ServiceVariable).filter(
        ServiceVariable.id == vid, ServiceVariable.service_id == sid).first()
    if not v:
        raise HTTPException(404, "Variable not found")
    return v


@router.put("/{sid}/variables/{vid}")
def update_variable(sid: int, vid: int, body: VariableUpdate, db: Session = Depends(get_db)):
    v = _get_variable(db, sid, vid)
    if body.name is not None and body.name != v.name:
        if db.query(ServiceVariable).filter(
                ServiceVariable.service_id == sid, ServiceVariable.name == body.name,
                ServiceVariable.id != vid).first():
            raise HTTPException(409, f"A variable named '{body.name}' already exists in this service.")
        v.name = body.name
    if body.required is not None:
        v.required = body.required
    if body.default is not None:
        v.default_json = json.dumps(body.default)
    if body.description is not None:
        v.description = body.description
    db.commit()
    db.refresh(v)
    return _variable_out(v)


@router.delete("/{sid}/variables/{vid}")
def delete_variable(sid: int, vid: int, db: Session = Depends(get_db)):
    v = _get_variable(db, sid, vid)
    db.delete(v)
    db.commit()
    return {"ok": True}


# ---------------------- Render (preview) + Deploy (push) ----------------------

@router.post("/{sid}/render")
def render_service(sid: int, body: RenderReq, db: Session = Depends(get_db)):
    """Dry-run: render every deployment, no SSH. Lets the caller see the config
    before pushing."""
    s = _load_service(db, sid)
    mode = body.target_mode or s.target_mode
    deployments = [d.dict() for d in body.deployments]
    try:
        results = service_deploy.preview_service(db, s, body.topology_id, mode, deployments)
    except ServiceError as e:
        raise HTTPException(400, str(e))
    return {"target_mode": mode, "results": results}


@router.post("/{sid}/deploy")
def deploy_service_route(sid: int, body: DeployReq, db: Session = Depends(get_db)):
    """Render + push each deployment over SSH (unless dry_run). Returns a
    per-device result with the rendered config and the device transcript."""
    s = _load_service(db, sid)
    mode = body.target_mode or s.target_mode
    deployments = [d.dict() for d in body.deployments]
    creds = body.credentials.dict() if body.credentials else None
    if body.dry_run:
        try:
            results = service_deploy.preview_service(db, s, body.topology_id, mode, deployments)
        except ServiceError as e:
            raise HTTPException(400, str(e))
        return {"target_mode": mode, "dry_run": True, "results": results}
    try:
        # deploy_service resolves via sync DB calls then awaits async SSH; run it
        # to completion here (this endpoint runs in FastAPI's threadpool).
        results = asyncio.run(
            service_deploy.deploy_service(db, s, body.topology_id, mode, deployments, creds))
    except ServiceError as e:
        raise HTTPException(409, str(e))
    return {"target_mode": mode, "dry_run": False, "results": results}


# ---------------------- Export / Import ----------------------

@router.get("/{sid}/export")
def export_service(sid: int, db: Session = Depends(get_db)):
    s = _load_service(db, sid)
    return {
        "service": {"name": s.name, "description": s.description, "target_mode": s.target_mode},
        "templates": [{"name": t.name, "body": t.body, "position": t.position} for t in s.templates],
        "variables": [{"name": v.name, "required": v.required,
                       "default": _var_default(v), "description": v.description} for v in s.variables],
    }


@router.post("/import")
def import_service(body: ImportReq, db: Session = Depends(get_db)):
    meta = body.service or {}
    base_name = (meta.get("name") or "imported-service").strip() or "imported-service"
    # Auto-suffix to avoid a name collision rather than failing the import.
    name = base_name
    n = 2
    while db.query(Service).filter(Service.name == name).first():
        name = f"{base_name} ({n})"
        n += 1
    s = Service(name=name, description=meta.get("description", ""),
                target_mode=meta.get("target_mode") if meta.get("target_mode") in ("lab", "mgmt_ip") else "lab")
    db.add(s)
    db.flush()   # get s.id
    for i, t in enumerate(body.templates or []):
        db.add(ServiceTemplate(service_id=s.id, name=t.get("name") or f"template-{i}",
                               body=t.get("body", ""),
                               position=t.get("position", i)))
    for v in body.variables or []:
        default = v.get("default")
        db.add(ServiceVariable(service_id=s.id, name=v.get("name") or "var",
                               required=bool(v.get("required", False)),
                               default_json="" if default is None else json.dumps(default),
                               description=v.get("description", "")))
    db.commit()
    db.refresh(s)
    return _service_out(s)

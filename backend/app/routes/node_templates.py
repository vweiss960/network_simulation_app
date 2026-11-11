"""Device templates — the canvas palette."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.node_template import NodeTemplate
from app.schemas.schemas import NodeTemplateCreate, NodeTemplateUpdate, NodeTemplateOut
from app.services.device_kinds import is_known_kind

router = APIRouter(prefix="/api/node-templates", tags=["node_templates"])


def _out(t: NodeTemplate, local_images: set[str] | None = None) -> dict:
    return {
        "id": t.id, "name": t.name, "vendor": t.vendor or "", "model": t.model or "",
        "kind": t.kind, "image_ref": t.image_ref or "", "icon": t.icon or "router",
        "interface_count": t.interface_count or 0,
        "min_memory_mb": t.min_memory_mb or 0, "min_cpu_cores": t.min_cpu_cores or 0,
        "requires_hugepages": bool(t.requires_hugepages),
        "startup_config": t.startup_config or "", "description": t.description or "",
        "created_by": t.created_by or "system",
        "image_available": (None if local_images is None
                            else bool(t.image_ref) and _image_present(t.image_ref, local_images)),
    }


def _image_present(ref: str, local: set[str]) -> bool:
    return ref in local or (":" not in ref and f"{ref}:latest" in local)


@router.get("/", response_model=list[NodeTemplateOut])
def list_templates(check_images: bool = False, db: Session = Depends(get_db)):
    local = None
    if check_images:
        from app.services.lab_service import local_docker_images
        local = local_docker_images()
    return [_out(t, local) for t in db.query(NodeTemplate).order_by(NodeTemplate.vendor, NodeTemplate.name)]


@router.post("/", response_model=NodeTemplateOut)
def create_template(data: NodeTemplateCreate, db: Session = Depends(get_db)):
    if not is_known_kind(data.kind):
        raise HTTPException(400, f"Unknown device kind '{data.kind}'")
    t = NodeTemplate(**{**data.model_dump(), "requires_hugepages": int(data.requires_hugepages)},
                     created_by="user")
    db.add(t)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.patch("/{template_id}", response_model=NodeTemplateOut)
def update_template(template_id: int, data: NodeTemplateUpdate, db: Session = Depends(get_db)):
    t = db.get(NodeTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    patch = data.model_dump(exclude_unset=True)
    if "kind" in patch and not is_known_kind(patch["kind"]):
        raise HTTPException(400, f"Unknown device kind '{patch['kind']}'")
    for k, v in patch.items():
        setattr(t, k, int(v) if k == "requires_hugepages" else v)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(NodeTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    db.delete(t)
    db.commit()
    return {"ok": True}

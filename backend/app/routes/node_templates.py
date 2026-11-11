import json
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.node_template import NodeTemplate
from app.schemas.schemas import NodeTemplateCreate, NodeTemplateOut

router = APIRouter(prefix="/api/node-templates", tags=["node_templates"])


@router.get("/", response_model=list[NodeTemplateOut])
def list_templates(db: Session = Depends(get_db)):
    templates = db.query(NodeTemplate).all()
    result = []
    for t in templates:
        d = {
            "id": t.id, "name": t.name, "vendor": t.vendor, "model": t.model,
            "platform": t.platform, "capacity_gbps": t.capacity_gbps,
            "default_mtbf_hours": t.default_mtbf_hours, "default_mttr_hours": t.default_mttr_hours,
            "default_interfaces": t.default_interfaces,
            "role": t.role or "router",
            "clab_kind": t.clab_kind or "",
            "image_ref": t.image_ref or "",
            "min_memory_mb": t.min_memory_mb or 0,
            "min_cpu_cores": t.min_cpu_cores or 0,
            "requires_hugepages": bool(t.requires_hugepages),
            "startup_config": t.startup_config or "",
        }
        result.append(d)
    return result


def _template_to_dict(t: NodeTemplate) -> dict:
    return {
        "id": t.id, "name": t.name, "vendor": t.vendor, "model": t.model,
        "platform": t.platform, "capacity_gbps": t.capacity_gbps,
        "default_mtbf_hours": t.default_mtbf_hours, "default_mttr_hours": t.default_mttr_hours,
        "default_interfaces": t.default_interfaces,
        "role": t.role or "router",
        "clab_kind": t.clab_kind or "",
        "image_ref": t.image_ref or "",
        "min_memory_mb": t.min_memory_mb or 0,
        "min_cpu_cores": t.min_cpu_cores or 0,
        "requires_hugepages": bool(t.requires_hugepages),
        "startup_config": t.startup_config or "",
    }


@router.post("/", response_model=NodeTemplateOut)
def create_template(data: NodeTemplateCreate, db: Session = Depends(get_db)):
    t = NodeTemplate(
        name=data.name, vendor=data.vendor, model=data.model,
        platform=data.platform, capacity_gbps=data.capacity_gbps,
        default_mtbf_hours=data.default_mtbf_hours, default_mttr_hours=data.default_mttr_hours,
        default_interfaces_json=json.dumps([i.model_dump() for i in data.default_interfaces]),
        role=data.role or "router",
        clab_kind=data.clab_kind, image_ref=data.image_ref,
        min_memory_mb=data.min_memory_mb, min_cpu_cores=data.min_cpu_cores,
        requires_hugepages=1 if data.requires_hugepages else 0,
        startup_config=data.startup_config,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _template_to_dict(t)


@router.patch("/{template_id}", response_model=NodeTemplateOut)
def update_template(template_id: int, data: dict, db: Session = Depends(get_db)):
    """Partial update for any template field. Useful for adding clab metadata
    to an existing template without re-supplying every column."""
    t = db.query(NodeTemplate).filter(NodeTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    allowed = {
        "name", "vendor", "model", "platform", "capacity_gbps",
        "default_mtbf_hours", "default_mttr_hours", "role",
        "clab_kind", "image_ref", "min_memory_mb", "min_cpu_cores",
        "startup_config",
    }
    for k, v in data.items():
        if k in allowed:
            setattr(t, k, v)
    if "requires_hugepages" in data:
        t.requires_hugepages = 1 if data["requires_hugepages"] else 0
    if "default_interfaces" in data and isinstance(data["default_interfaces"], list):
        t.default_interfaces_json = json.dumps(data["default_interfaces"])
    db.commit()
    db.refresh(t)
    return _template_to_dict(t)


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    t = db.query(NodeTemplate).filter(NodeTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    if t.role == "customer":
        raise HTTPException(400, "Built-in templates cannot be deleted")
    db.delete(t)
    db.commit()
    return {"ok": True}


def _parse_json(val):
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


@router.get("/export")
def export_templates(format: str = Query("json", regex="^(json|yaml)$"), db: Session = Depends(get_db)):
    """Export all device templates as JSON or YAML."""
    templates = db.query(NodeTemplate).filter(NodeTemplate.role != "customer").all()
    data = {
        "version": "1.0",
        "node_templates": [
            {
                "name": t.name, "vendor": t.vendor, "model": t.model,
                "platform": t.platform or "", "capacity_gbps": t.capacity_gbps,
                "default_mtbf_hours": t.default_mtbf_hours,
                "default_mttr_hours": t.default_mttr_hours,
                "default_interfaces": _parse_json(t.default_interfaces_json),
                "created_by": t.created_by or "",
            }
            for t in templates
        ],
    }
    if format == "yaml":
        content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return Response(
            content=content, media_type="application/x-yaml",
            headers={"Content-Disposition": 'attachment; filename="device_templates.yaml"'},
        )
    else:
        content = json.dumps(data, indent=2)
        return Response(
            content=content, media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="device_templates.json"'},
        )


@router.post("/import")
async def import_templates(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import device templates from a JSON or YAML file. Skips duplicates (same name+vendor+model)."""
    content = await file.read()
    text = content.decode("utf-8")

    filename = (file.filename or "").lower()
    try:
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except Exception as e:
        raise HTTPException(400, f"Failed to parse file: {e}")

    if not isinstance(data, dict):
        raise HTTPException(400, "Invalid format: expected a JSON/YAML object")

    templates_data = data.get("node_templates", [])
    if not templates_data:
        raise HTTPException(400, "No node_templates found in file")

    imported = 0
    skipped = 0
    for t_data in templates_data:
        # Skip built-in templates
        if t_data.get("role") == "customer":
            skipped += 1
            continue
        existing = db.query(NodeTemplate).filter(
            NodeTemplate.name == t_data.get("name"),
            NodeTemplate.vendor == t_data.get("vendor"),
            NodeTemplate.model == t_data.get("model"),
        ).first()
        if existing:
            skipped += 1
            continue
        tmpl = NodeTemplate(
            name=t_data["name"], vendor=t_data["vendor"], model=t_data["model"],
            platform=t_data.get("platform", ""),
            capacity_gbps=t_data.get("capacity_gbps"),
            default_mtbf_hours=t_data.get("default_mtbf_hours"),
            default_mttr_hours=t_data.get("default_mttr_hours"),
            default_interfaces_json=json.dumps(t_data.get("default_interfaces", [])),
            created_by=t_data.get("created_by", "import"),
        )
        db.add(tmpl)
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "total": len(templates_data)}

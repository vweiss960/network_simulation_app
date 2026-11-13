"""Topologies (each one is a lab)."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.lab_deployment import LabDeployment
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.topology import Topology
from app.schemas.schemas import Annotation, TopologyCreate, TopologyUpdate, TopologyOut
from app.services import lab_service, topology_io
from app.services.lab_view import safe_lab_name
from app.services.topology_ops import TopologyError

router = APIRouter(prefix="/api/topologies", tags=["topologies"])


def _out(db: Session, t: Topology) -> dict:
    dep = db.query(LabDeployment).filter(LabDeployment.topology_id == t.id).first()
    return {
        "id": t.id, "name": t.name, "description": t.description or "",
        "node_count": db.query(func.count(NodeInstance.id)).filter(NodeInstance.topology_id == t.id).scalar(),
        "link_count": db.query(func.count(Link.id)).filter(Link.topology_id == t.id).scalar(),
        "lab_status": dep.status if dep else "idle",
    }


def _get(db: Session, topology_id: int) -> Topology:
    t = db.get(Topology, topology_id)
    if not t:
        raise HTTPException(404, "Topology not found")
    return t


@router.get("/", response_model=list[TopologyOut])
def list_topologies(db: Session = Depends(get_db)):
    return [_out(db, t) for t in db.query(Topology).order_by(Topology.updated_at.desc(), Topology.id.desc())]


@router.post("/", response_model=TopologyOut)
def create_topology(data: TopologyCreate, db: Session = Depends(get_db)):
    t = Topology(name=data.name.strip() or "Untitled lab", description=data.description)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _out(db, t)


@router.post("/import", response_model=TopologyOut)
async def import_topology(file: UploadFile = File(...), db: Session = Depends(get_db)):
    text = (await file.read()).decode("utf-8", errors="replace")
    try:
        t = topology_io.import_topology(db, text)
    except TopologyError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    return _out(db, t)


@router.get("/{topology_id}", response_model=TopologyOut)
def get_topology(topology_id: int, db: Session = Depends(get_db)):
    return _out(db, _get(db, topology_id))


@router.patch("/{topology_id}", response_model=TopologyOut)
def update_topology(topology_id: int, data: TopologyUpdate, db: Session = Depends(get_db)):
    t = _get(db, topology_id)
    dep = db.query(LabDeployment).filter(LabDeployment.topology_id == t.id).first()
    if data.name is not None and data.name.strip() != t.name:
        if dep and dep.status not in ("idle",):
            raise HTTPException(409, "Destroy the lab before renaming it (the name is part of every container name).")
        t.name = data.name.strip() or t.name
    if data.description is not None:
        t.description = data.description
    db.commit()
    return _out(db, t)


@router.delete("/{topology_id}")
def delete_topology(topology_id: int, db: Session = Depends(get_db)):
    t = _get(db, topology_id)
    dep = db.query(LabDeployment).filter(LabDeployment.topology_id == t.id).first()
    lab_service.destroy_lab_by_name((dep.lab_name if dep and dep.lab_name else safe_lab_name(t)))
    db.query(Link).filter(Link.topology_id == t.id).delete(synchronize_session=False)
    db.query(NodeInstance).filter(NodeInstance.topology_id == t.id).delete(synchronize_session=False)
    if dep:
        db.delete(dep)
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.post("/{topology_id}/duplicate", response_model=TopologyOut)
def duplicate_topology(topology_id: int, db: Session = Depends(get_db)):
    src = _get(db, topology_id)
    spec = topology_io.export_topology(db, src.id)
    t = Topology(name=f"{src.name} (copy)", description=src.description)
    db.add(t)
    db.flush()
    topology_io.build_into(db, t, spec)
    db.commit()
    return _out(db, t)


@router.get("/{topology_id}/annotations", response_model=list[Annotation])
def get_annotations(topology_id: int, db: Session = Depends(get_db)):
    return _get(db, topology_id).annotations


@router.put("/{topology_id}/annotations", response_model=list[Annotation])
def put_annotations(topology_id: int, items: list[Annotation], db: Session = Depends(get_db)):
    """Replace the canvas annotations (they never touch the deployed lab)."""
    if len(items) > 500:
        raise HTTPException(400, "Too many annotations (max 500)")
    t = _get(db, topology_id)
    t.annotations = [a.model_dump() for a in items]
    db.commit()
    return t.annotations


@router.get("/{topology_id}/export", response_class=PlainTextResponse)
def export_topology(topology_id: int, db: Session = Depends(get_db)):
    _get(db, topology_id)
    return PlainTextResponse(topology_io.export_yaml(db, topology_id), media_type="application/x-yaml")

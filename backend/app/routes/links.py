"""Cables between device interfaces. Creating / deleting / re-pinning a link
on a deployed topology reconciles the running lab (veth added / removed)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.schemas.schemas import LinkCreate, LinkUpdate, LinkOut
from app.services import lab_service
from app.services.topology_ops import TopologyError, create_link, update_link_interfaces, used_interfaces

router = APIRouter(prefix="/api/topologies/{topology_id}/links", tags=["links"])


def _link(db: Session, topology_id: int, link_id: int) -> Link:
    l = db.get(Link, link_id)
    if not l or l.topology_id != topology_id:
        raise HTTPException(404, "Link not found")
    return l


@router.get("/", response_model=list[LinkOut])
def list_links(topology_id: int, db: Session = Depends(get_db)):
    return db.query(Link).filter(Link.topology_id == topology_id).order_by(Link.id).all()


@router.get("/free-interfaces/{node_id}")
def free_interfaces(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    n = db.get(NodeInstance, node_id)
    if not n or n.topology_id != topology_id:
        raise HTTPException(404, "Device not found")
    used = used_interfaces(db, n.id)
    return {"interfaces": [{**i, "used": i["name"] in used} for i in n.interfaces]}


@router.post("/", response_model=LinkOut)
def add_link(topology_id: int, data: LinkCreate, db: Session = Depends(get_db)):
    try:
        l = create_link(db, topology_id, **data.model_dump())
    except TopologyError as e:
        raise HTTPException(400, str(e))
    db.commit()
    lab_service.request_sync(db, topology_id)
    return l


@router.patch("/{link_id}", response_model=LinkOut)
def update_link(topology_id: int, link_id: int, data: LinkUpdate, db: Session = Depends(get_db)):
    l = _link(db, topology_id, link_id)
    try:
        update_link_interfaces(db, l, data.source_interface, data.target_interface)
    except TopologyError as e:
        raise HTTPException(400, str(e))
    if data.label is not None:
        l.label = data.label
    if data.color is not None:
        l.color = data.color
    db.commit()
    if data.source_interface or data.target_interface:
        lab_service.request_sync(db, topology_id)
    return l


@router.delete("/{link_id}")
def delete_link(topology_id: int, link_id: int, db: Session = Depends(get_db)):
    db.delete(_link(db, topology_id, link_id))
    db.commit()
    lab_service.request_sync(db, topology_id)
    return {"ok": True}

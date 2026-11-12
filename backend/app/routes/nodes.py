"""Devices on a topology. Mutations that change what's deployed trigger a
background reconcile of the running lab (lab_service.request_sync)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.topology import Topology
from app.schemas.schemas import NodeCreate, NodeUpdate, NodeOut, NodePositions
from app.services import lab_service
from app.services.device_kinds import is_bridge
from app.services.host_net import Segment
from app.services.topology_ops import TopologyError, create_node, set_interface_count, set_network

router = APIRouter(prefix="/api/topologies/{topology_id}/nodes", tags=["nodes"])

# Fields whose change alters the deployed device (→ reconcile). Position,
# colour and notes are canvas-only.
_DEPLOY_FIELDS = {"name", "image_ref", "interface_count", "startup_config", "username", "password", "network"}


def node_out(n: NodeInstance) -> dict:
    return {
        "id": n.id, "topology_id": n.topology_id, "template_id": n.template_id,
        "name": n.name, "kind": n.kind, "image_ref": n.image_ref or "", "icon": n.icon or "router",
        "x": n.x or 0.0, "y": n.y or 0.0, "interfaces": n.interfaces,
        "mgmt_ipv4": n.mgmt_ipv4 or "", "username": n.credentials.get("username", ""),
        "startup_config": n.startup_config or "",
        "min_memory_mb": n.min_memory_mb or 0, "min_cpu_cores": n.min_cpu_cores or 0,
        "color": n.color or "", "notes": n.notes or "",
        "network": n.network if is_bridge(n.kind) else None,
        "network_label": Segment.from_dict(n.network).describe() if is_bridge(n.kind) else "",
    }


def _topology(db: Session, topology_id: int) -> Topology:
    t = db.get(Topology, topology_id)
    if not t:
        raise HTTPException(404, "Topology not found")
    return t


def _node(db: Session, topology_id: int, node_id: int) -> NodeInstance:
    n = db.get(NodeInstance, node_id)
    if not n or n.topology_id != topology_id:
        raise HTTPException(404, "Device not found")
    return n


@router.get("/", response_model=list[NodeOut])
def list_nodes(topology_id: int, db: Session = Depends(get_db)):
    _topology(db, topology_id)
    q = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).order_by(NodeInstance.id)
    return [node_out(n) for n in q]


@router.post("/", response_model=NodeOut)
def add_node(topology_id: int, data: NodeCreate, db: Session = Depends(get_db)):
    _topology(db, topology_id)
    try:
        n = create_node(db, topology_id, **data.model_dump())
    except TopologyError as e:
        raise HTTPException(400, str(e))
    db.commit()
    lab_service.request_sync(db, topology_id)
    return node_out(n)


@router.put("/positions")
def save_positions(topology_id: int, data: NodePositions, db: Session = Depends(get_db)):
    """Bulk position save after a drag (canvas-only, no reconcile)."""
    nodes = {n.id: n for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id)}
    for nid, (x, y) in data.positions.items():
        if nid in nodes:
            nodes[nid].x, nodes[nid].y = x, y
    db.commit()
    return {"ok": True}


@router.get("/{node_id}", response_model=NodeOut)
def get_node(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    return node_out(_node(db, topology_id, node_id))


@router.patch("/{node_id}", response_model=NodeOut)
def update_node(topology_id: int, node_id: int, data: NodeUpdate, db: Session = Depends(get_db)):
    n = _node(db, topology_id, node_id)
    patch = data.model_dump(exclude_unset=True)
    if "name" in patch:
        name = (patch["name"] or "").strip()
        if not name:
            raise HTTPException(400, "Name can't be empty")
        clash = db.query(NodeInstance).filter(
            NodeInstance.topology_id == topology_id, NodeInstance.name == name, NodeInstance.id != n.id).first()
        if clash:
            raise HTTPException(400, f"A device named '{name}' already exists")
        n.name = name
    if patch.get("network") is not None:
        try:
            set_network(db, n, patch["network"])
        except TopologyError as e:
            raise HTTPException(400, str(e))
    if "interface_count" in patch and not is_bridge(n.kind):
        try:
            set_interface_count(db, n, patch["interface_count"])
        except TopologyError as e:
            raise HTTPException(400, str(e))
    if "username" in patch or "password" in patch:
        creds = n.credentials
        for k in ("username", "password"):
            if k in patch:
                if patch[k]:
                    creds[k] = patch[k]
                else:
                    creds.pop(k, None)
        n.credentials = creds
    for k in ("image_ref", "icon", "x", "y", "startup_config", "min_memory_mb", "min_cpu_cores", "color", "notes"):
        if k in patch:
            setattr(n, k, patch[k])
    db.commit()
    if _DEPLOY_FIELDS & patch.keys():
        lab_service.request_sync(db, topology_id)
    return node_out(n)


@router.delete("/{node_id}")
def delete_node(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    n = _node(db, topology_id, node_id)
    db.query(Link).filter((Link.source_node_id == n.id) | (Link.target_node_id == n.id)).delete(
        synchronize_session=False)
    db.delete(n)
    db.commit()
    lab_service.request_sync(db, topology_id)
    return {"ok": True}

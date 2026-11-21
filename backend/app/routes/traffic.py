"""Traffic generator nodes (services/traffic_gen.py): read and change a node's
configuration (applied live by the running container) and read its stats."""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.node_instance import NodeInstance
from app.services import lab_service, traffic_gen
from app.services.device_kinds import is_traffic_image
from app.services.lab_view import compute_lab_view, safe_node_name

router = APIRouter(prefix="/api/topologies/{topology_id}/nodes/{node_id}/traffic", tags=["traffic"])


class TrafficIn(BaseModel):
    config: dict


def _node(db: Session, topology_id: int, node_id: int) -> NodeInstance:
    n = db.get(NodeInstance, node_id)
    if not n or n.topology_id != topology_id:
        raise HTTPException(404, "Device not found")
    if not is_traffic_image(n.kind, n.image_ref):
        raise HTTPException(400, f"{n.name} isn't a traffic generator")
    return n


def _names(db: Session, topology_id: int, n: NodeInstance) -> tuple[str, str]:
    view = compute_lab_view(db, topology_id)
    clab = next((v["clab_name"] for v in view["nodes"] if v["id"] == n.id), None) or safe_node_name(n.name)
    return view["lab_name"], clab


def _state(db: Session, topology_id: int, n: NodeInstance) -> dict:
    lab_name, clab = _names(db, topology_id, n)
    cfg = n.traffic or traffic_gen.default_config()
    stats = traffic_gen.read_stats_file(lab_name, clab)
    dep = lab_service.get_deployment_state(db, topology_id)
    rt = (dep.get("nodes") or {}).get(n.id) or (dep.get("nodes") or {}).get(str(n.id)) or {}
    container_up = rt.get("state") == "running"
    fresh = bool(stats) and container_up and time.time() - float(stats.get("ts") or 0) < 5
    return {"node_id": n.id, "name": n.name, "config": cfg, "running": fresh,
            "container": rt.get("state") or "absent", "stats": stats if fresh else None,
            "dscp_names": traffic_gen.DSCP_NAMES}


@router.get("")
def get_traffic(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    return _state(db, topology_id, _node(db, topology_id, node_id))


@router.put("")
def put_traffic(topology_id: int, node_id: int, body: TrafficIn, db: Session = Depends(get_db)):
    n = _node(db, topology_id, node_id)
    try:
        cfg = traffic_gen.normalize(body.config)
    except traffic_gen.TrafficConfigError as e:
        raise HTTPException(400, str(e))
    n.traffic = cfg
    db.commit()
    lab_name, clab = _names(db, topology_id, n)
    traffic_gen.write_config(lab_name, clab, cfg)      # the running container picks it up within a second
    return _state(db, topology_id, n)

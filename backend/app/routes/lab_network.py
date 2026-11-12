"""Host NICs and the lab management network settings."""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin
from app.models.database import get_db
from app.models.lab_deployment import LabDeployment
from app.models.node_instance import NodeInstance
from app.models.topology import Topology
from app.services import host_net, lab_network
from app.services.device_kinds import BRIDGE_KIND
from app.services.host_net import HostNetError, Segment

router = APIRouter(prefix="/api/lab-network", tags=["lab-network"])


class SettingsIn(BaseModel):
    subnet: str = lab_network.DEFAULT_INTERNAL_SUBNET
    mtu: int = 0


def _usage(db: Session) -> list[dict]:
    """Which topologies' Network nodes use which NIC/VLAN."""
    out = []
    for n in db.query(NodeInstance).filter(NodeInstance.kind == BRIDGE_KIND):
        seg = Segment.from_dict(n.network)
        if seg.mode == "internal" or not seg.nic:
            continue
        t = db.get(Topology, n.topology_id)
        out.append({"topology_id": n.topology_id, "topology": t.name if t else "?", "node": n.name,
                    "mgmt": bool(n.network.get("mgmt")),
                    "nic": seg.nic, "vlan": seg.vlan if seg.mode == "trunk" else 0,
                    "bridge": seg.bridge(n.topology_id, n.id)})
    return out


@router.get("/")
def get_lab_network(db: Session = Depends(get_db)):
    return {
        "settings": asdict(lab_network.get_settings(db)),
        "status": lab_network.status(db),
        "nics": host_net.list_nics(),
        "bridges": host_net.list_plabs_bridges(),
        "usage": _usage(db),
    }


@router.get("/nics")
def nics():
    return {"nics": host_net.list_nics()}


@router.put("/", dependencies=[Depends(require_admin)])
def put_lab_network(data: SettingsIn, db: Session = Depends(get_db)):
    new = lab_network.MgmtSettings(**data.model_dump())
    try:
        lab_network.validate(new)
    except HostNetError as e:
        raise HTTPException(400, str(e))
    old = lab_network.get_settings(db)
    if old.subnet != new.subnet and db.query(LabDeployment).filter(
            LabDeployment.status.in_(("deploying", "running", "syncing", "destroying"))).count():
        raise HTTPException(409, "Destroy all running labs before changing the internal management subnet.")
    lab_network.save_settings(db, new)
    return {"settings": asdict(new)}

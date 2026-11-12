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


class MgmtIn(BaseModel):
    mode: str = "internal"
    nic: str = ""
    vlan: int = 0
    subnet: str = lab_network.DEFAULT_INTERNAL_SUBNET
    gateway: str = ""
    host_ip: str = ""
    pool_start: str = ""
    pool_end: str = ""
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
                    "nic": seg.nic, "vlan": seg.vlan if seg.mode == "trunk" else 0,
                    "bridge": seg.bridge(n.topology_id, n.id)})
    return out


@router.get("/")
def get_lab_network(db: Session = Depends(get_db)):
    s = lab_network.get_settings(db)
    return {
        "settings": asdict(s),
        "status": lab_network.status(s),
        "nics": host_net.list_nics(),
        "bridges": host_net.list_plabs_bridges(),
        "usage": _usage(db),
    }


@router.get("/nics")
def nics():
    return {"nics": host_net.list_nics()}


@router.put("/", dependencies=[Depends(require_admin)])
def put_lab_network(data: MgmtIn, db: Session = Depends(get_db)):
    new = lab_network.MgmtSettings(**data.model_dump())
    try:
        lab_network.validate(new)
    except HostNetError as e:
        raise HTTPException(400, str(e))
    old = lab_network.get_settings(db)
    changed = asdict(old) != asdict(new)
    if changed:
        active = db.query(LabDeployment).filter(
            LabDeployment.status.in_(("deploying", "running", "syncing", "destroying"))).count()
        if active:
            raise HTTPException(409, "Destroy all running labs before changing the management network — "
                                     "every device's management interface is attached to it.")
        if old.bridged:
            lab_network.teardown_mgmt(old)
        if old.pool_start != new.pool_start or old.pool_end != new.pool_end or old.subnet != new.subnet:
            # Addresses from the old pool are no longer valid; reassign on next deploy.
            db.query(NodeInstance).update({NodeInstance.mgmt_ipv4: ""})
    lab_network.save_settings(db, new)
    try:
        lab_network.ensure_mgmt(new)
    except HostNetError as e:
        raise HTTPException(400, f"Saved, but the network couldn't be created: {e}")
    return {"settings": asdict(new), "status": lab_network.status(new)}

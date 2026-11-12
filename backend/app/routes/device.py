"""Device access: CLI, configuration, running-config, NETCONF, RESTCONF,
gNMI and config snapshots for one lab device (by its management IP)."""
import asyncio
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.models.config_snapshot import ConfigSnapshot
from app.models.database import get_db
from app.models.node_instance import NodeInstance
from app.services import device_exec, lab_service
from app.services.device_exec import ExecError
from app.services.device_kinds import is_bridge

router = APIRouter(prefix="/api/topologies/{topology_id}", tags=["device"])


class CliIn(BaseModel):
    commands: list[str] = Field(..., min_length=1, max_length=50)
    timeout: float = 60


class ConfigIn(BaseModel):
    config: str
    replace: bool = False


class NetconfIn(BaseModel):
    op: str = "get-config"
    filter: str = ""
    config: str = ""
    datastore: str = ""
    commit: bool = True


class RestconfIn(BaseModel):
    method: str = "GET"
    path: str
    body: Optional[Any] = None


class GnmiIn(BaseModel):
    op: str = "get"
    paths: list[str] = []
    update: list = []
    replace: list = []
    delete: list[str] = []
    encoding: str = "json_ietf"


class SnapshotIn(BaseModel):
    label: str = ""


class RestoreIn(BaseModel):
    # "reboot": becomes the startup config and the device is recreated (any kind)
    # "live":   applied in place (IOS-XR commit replace / FRR)
    mode: str = "reboot"


def _target(db: Session, tid: int, nid: int) -> device_exec.Target:
    try:
        return device_exec.target_for(db, tid, nid)
    except ExecError as e:
        raise HTTPException(400, str(e))


async def _call(fn, *a, **kw):
    try:
        if asyncio.iscoroutinefunction(fn):
            return await fn(*a, **kw)
        return await asyncio.to_thread(fn, *a, **kw)
    except ExecError as e:
        raise HTTPException(502, str(e))


@router.post("/nodes/{node_id}/cli")
async def cli(topology_id: int, node_id: int, body: CliIn, db: Session = Depends(get_db)):
    t = _target(db, topology_id, node_id)
    return {"results": await _call(device_exec.run_cli, t, body.commands, body.timeout)}


@router.post("/nodes/{node_id}/configure")
async def configure(topology_id: int, node_id: int, body: ConfigIn, db: Session = Depends(get_db)):
    t = _target(db, topology_id, node_id)
    return await _call(device_exec.configure, t, body.config, replace=body.replace)


@router.get("/nodes/{node_id}/running-config")
async def running_config(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    t = _target(db, topology_id, node_id)
    return {"config": await _call(device_exec.running_config, t)}


@router.post("/nodes/{node_id}/netconf")
async def netconf(topology_id: int, node_id: int, body: NetconfIn, db: Session = Depends(get_db)):
    t = _target(db, topology_id, node_id)
    return await _call(device_exec.netconf, t, body.op, filter_xml=body.filter, config_xml=body.config,
                       datastore=body.datastore, commit=body.commit)


@router.post("/nodes/{node_id}/restconf")
async def restconf(topology_id: int, node_id: int, body: RestconfIn, db: Session = Depends(get_db)):
    t = _target(db, topology_id, node_id)
    return await _call(device_exec.restconf, t, body.method, body.path, body.body)


@router.post("/nodes/{node_id}/gnmi")
async def gnmi(topology_id: int, node_id: int, body: GnmiIn, db: Session = Depends(get_db)):
    t = _target(db, topology_id, node_id)
    return await _call(device_exec.gnmi, t, body.op, paths=body.paths, update=body.update,
                       replace=body.replace, delete=body.delete, encoding=body.encoding)


# --- snapshots ---------------------------------------------------------------

def _snap_out(s: ConfigSnapshot, node_name: str = "") -> dict:
    return {"id": s.id, "node_id": s.node_id, "node": node_name, "label": s.label, "family": s.family,
            "created_by": s.created_by, "created_at": s.created_at.isoformat() if s.created_at else None,
            "lines": s.config.count("\n")}


@router.get("/snapshots")
def list_snapshots(topology_id: int, node_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(ConfigSnapshot).filter(ConfigSnapshot.topology_id == topology_id)
    if node_id:
        q = q.filter(ConfigSnapshot.node_id == node_id)
    names = {n.id: n.name for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id)}
    return [_snap_out(s, names.get(s.node_id, "?")) for s in q.order_by(ConfigSnapshot.id.desc())]


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(topology_id: int, snapshot_id: int, db: Session = Depends(get_db)):
    s = db.get(ConfigSnapshot, snapshot_id)
    if not s or s.topology_id != topology_id:
        raise HTTPException(404, "Snapshot not found")
    return {**_snap_out(s), "config": s.config}


async def _take(db: Session, tid: int, nid: int, label: str, user: str) -> ConfigSnapshot:
    t = _target(db, tid, nid)
    cfg = await _call(device_exec.running_config, t)
    s = ConfigSnapshot(topology_id=tid, node_id=nid, label=label, family=t.family, config=cfg, created_by=user)
    db.add(s)
    db.commit()
    return s


def _label(body: SnapshotIn) -> str:
    return body.label.strip() or datetime.now().strftime("snapshot %Y-%m-%d %H:%M:%S")


@router.post("/nodes/{node_id}/snapshots")
async def snapshot_node(topology_id: int, node_id: int, body: SnapshotIn, db: Session = Depends(get_db),
                        user=Depends(get_current_user)):
    s = await _take(db, topology_id, node_id, _label(body), user.username)
    return _snap_out(s)


@router.post("/snapshots")
async def snapshot_lab(topology_id: int, body: SnapshotIn, db: Session = Depends(get_db),
                       user=Depends(get_current_user)):
    """Snapshot every running device that has a config (skips plain hosts)."""
    label = _label(body)
    state = lab_service.get_deployment_state(db, topology_id)
    taken, skipped = [], []
    for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).order_by(NodeInstance.id):
        if is_bridge(n.kind):
            continue
        if state["nodes"].get(n.id, {}).get("state") != "running":
            skipped.append({"node": n.name, "reason": "not running"})
            continue
        try:
            taken.append(_snap_out(await _take(db, topology_id, n.id, label, user.username), n.name))
        except HTTPException as e:
            skipped.append({"node": n.name, "reason": e.detail})
    return {"label": label, "taken": taken, "skipped": skipped}


@router.post("/snapshots/{snapshot_id}/restore")
async def restore_snapshot(topology_id: int, snapshot_id: int, body: RestoreIn, db: Session = Depends(get_db)):
    s = db.get(ConfigSnapshot, snapshot_id)
    if not s or s.topology_id != topology_id:
        raise HTTPException(404, "Snapshot not found")
    n = db.get(NodeInstance, s.node_id)
    if body.mode == "live":
        t = _target(db, topology_id, s.node_id)
        if t.family != "iosxr" and "frr" not in t.image.lower():
            raise HTTPException(400, "Live restore is supported on IOS-XR (commit replace) and FRR; use mode=reboot")
        return await _call(device_exec.configure, t, s.config, replace=(t.family == "iosxr"))
    if body.mode != "reboot":
        raise HTTPException(400, "mode must be 'reboot' or 'live'")
    n.startup_config = s.config
    db.commit()
    scheduled = lab_service.request_sync(db, topology_id)   # changed startup config → device recreated
    return {"ok": True, "mode": "reboot", "recreating": scheduled,
            "note": "Startup config replaced; the device is recreated with it" if scheduled
            else "Startup config replaced; it applies on the next deploy"}


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(topology_id: int, snapshot_id: int, db: Session = Depends(get_db)):
    s = db.get(ConfigSnapshot, snapshot_id)
    if not s or s.topology_id != topology_id:
        raise HTTPException(404, "Snapshot not found")
    db.delete(s)
    db.commit()
    return {"ok": True}

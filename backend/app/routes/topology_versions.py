"""Topology version (snapshot) management API routes."""

import copy
import difflib
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.topology_version import TopologyVersion
from app.models.topology import Topology
from app.models.user import User
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/topologies/{topology_id}/versions", tags=["topology_versions"])


# Entity lists in a snapshot and how to identify each item (so a change is
# attributed to a specific node/link/flow rather than a bare line). Layout
# extras (overrides/views/remap helper) are intentionally not diffed; node
# coordinates live inside `nodes` so position changes still show.
def _link_key(l: dict) -> str:
    return f'{l.get("source_node", "")}→{l.get("target_node", "")} ({l.get("source_interface", "")}/{l.get("target_interface", "")})'


_DIFF_SECTIONS = [
    ("nodes", "node", lambda n: n.get("name", "")),
    ("links", "link", _link_key),
    ("flows", "flow", lambda f: f.get("name", "")),
    ("customers", "customer", lambda c: c.get("name", "")),
    ("srlgs", "srlg", lambda s: s.get("name", "")),
    ("node_templates", "template", lambda t: t.get("name", "")),
]


def _block(obj: dict, prefix: str) -> list[str]:
    return [prefix + ln for ln in json.dumps(obj, indent=2, sort_keys=True).splitlines()]


def _index_by(items: list, keyfn) -> dict:
    """Map items by their natural key, disambiguating collisions (e.g. parallel
    links with identical endpoints) with a #N suffix so none is silently dropped."""
    out: dict = {}
    for x in items:
        k = keyfn(x)
        if k in out:
            i = 2
            while f"{k} #{i}" in out:
                i += 1
            k = f"{k} #{i}"
        out[k] = x
    return out


def _entity_diff(from_snap: dict, to_snap: dict) -> str:
    """Build an entity-level diff: each added/removed/changed item is shown as
    its WHOLE JSON block (changed items via a full-context unified diff so the
    entire object — including its name — is visible, with changed lines marked)."""
    out: list[str] = []

    fm, tm = from_snap.get("topology", {}), to_snap.get("topology", {})
    if fm != tm:
        out.append("@@ topology @@")
        for ln in difflib.unified_diff(
            json.dumps(fm, indent=2, sort_keys=True).splitlines(),
            json.dumps(tm, indent=2, sort_keys=True).splitlines(), n=10000, lineterm=""):
            if not (ln.startswith("---") or ln.startswith("+++") or ln.startswith("@@")):
                out.append(ln)
        out.append("")

    for key, label, keyfn in _DIFF_SECTIONS:
        fmap = _index_by(from_snap.get(key, []), keyfn)
        tmap = _index_by(to_snap.get(key, []), keyfn)
        for ident in sorted(set(fmap) | set(tmap)):
            fo, to_ = fmap.get(ident), tmap.get(ident)
            if fo == to_:
                continue
            state = "added" if fo is None else "removed" if to_ is None else "changed"
            out.append(f'@@ {label} "{ident}" — {state} @@')
            if fo is None:
                out += _block(to_, "+")
            elif to_ is None:
                out += _block(fo, "-")
            else:
                fl = json.dumps(fo, indent=2, sort_keys=True).splitlines()
                tl = json.dumps(to_, indent=2, sort_keys=True).splitlines()
                for ln in difflib.unified_diff(fl, tl, n=10000, lineterm=""):
                    if not (ln.startswith("---") or ln.startswith("+++") or ln.startswith("@@")):
                        out.append(ln)
            out.append("")
    return "\n".join(out).rstrip("\n")


def _version_to_dict(v: TopologyVersion) -> dict:
    return {
        "id": v.id,
        "topology_id": v.topology_id,
        "name": v.name,
        "description": v.description or "",
        "node_count": v.node_count,
        "link_count": v.link_count,
        "flow_count": v.flow_count,
        "created_by": v.created_by or "",
        "created_at": v.created_at.isoformat() if v.created_at else "",
    }


@router.get("/")
def list_versions(topology_id: int, db: Session = Depends(get_db)):
    """List saved versions for a topology, newest first (excludes transient
    pre-edit baselines used by protected-topology discard)."""
    versions = db.query(TopologyVersion).filter(
        TopologyVersion.topology_id == topology_id,
        (TopologyVersion.is_baseline == 0) | (TopologyVersion.is_baseline.is_(None)),
    ).order_by(TopologyVersion.created_at.desc()).all()
    return [_version_to_dict(v) for v in versions]


@router.get("/diff")
def diff_versions(
    topology_id: int,
    from_ref: str = Query(..., alias="from"),
    to_ref: str = Query(..., alias="to"),
    db: Session = Depends(get_db),
):
    """Unified diff between two versions' canonical JSON. Each ref is a version
    id or the literal 'current' (the live topology state)."""
    def _resolve(ref: str):
        if ref == "current":
            from app.routes.topologies import _serialize_topology
            return _serialize_topology(topology_id, db), "current state"
        try:
            vid = int(ref)
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid version reference")
        v = db.query(TopologyVersion).filter(
            TopologyVersion.id == vid, TopologyVersion.topology_id == topology_id).first()
        if not v:
            raise HTTPException(404, "Version not found")
        return json.loads(v.snapshot_json), v.name

    from_snap, from_label = _resolve(from_ref)
    to_snap, to_label = _resolve(to_ref)
    diff = _entity_diff(from_snap, to_snap)
    return {"from_label": from_label, "to_label": to_label, "diff": diff, "identical": diff == ""}


@router.post("/")
def save_version(topology_id: int, body: dict, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Save the current topology state as a named version."""
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Version name is required")

    from app.routes.topologies import _serialize_topology
    snapshot = _serialize_topology(topology_id, db)
    snapshot_json = json.dumps(snapshot)

    version = TopologyVersion(
        topology_id=topology_id,
        name=name,
        description=body.get("description", ""),
        snapshot_json=snapshot_json,
        node_count=len(snapshot.get("nodes", [])),
        link_count=len(snapshot.get("links", [])),
        flow_count=len(snapshot.get("flows", [])),
        created_by=current_user.username,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return _version_to_dict(version)


@router.post("/{version_id}/restore")
def restore_version(topology_id: int, version_id: int, db: Session = Depends(get_db)):
    """Restore a topology to a saved version. Auto-saves current state first."""
    version = db.query(TopologyVersion).filter(
        TopologyVersion.id == version_id,
        TopologyVersion.topology_id == topology_id,
    ).first()
    if not version:
        raise HTTPException(404, "Version not found")

    # Auto-save current state as a backup before restoring
    from app.routes.topologies import _serialize_topology, _restore_into_topology
    current_snapshot = _serialize_topology(topology_id, db)
    backup = TopologyVersion(
        topology_id=topology_id,
        name=f"Auto-backup before restoring '{version.name}'",
        description="Automatically saved before version restore",
        snapshot_json=json.dumps(current_snapshot),
        node_count=len(current_snapshot.get("nodes", [])),
        link_count=len(current_snapshot.get("links", [])),
        flow_count=len(current_snapshot.get("flows", [])),
    )
    db.add(backup)
    db.flush()

    # Restore the snapshot
    snapshot = json.loads(version.snapshot_json)
    _restore_into_topology(snapshot, topology_id, db)

    return {"ok": True, "restored_version": version.name, "backup_id": backup.id}


@router.delete("/{version_id}")
def delete_version(topology_id: int, version_id: int, db: Session = Depends(get_db)):
    """Delete a saved version."""
    version = db.query(TopologyVersion).filter(
        TopologyVersion.id == version_id,
        TopologyVersion.topology_id == topology_id,
    ).first()
    if not version:
        raise HTTPException(404, "Version not found")
    db.delete(version)
    db.commit()
    return {"ok": True}

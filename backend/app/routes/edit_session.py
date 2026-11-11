"""Protected-topology edit sessions (admin-only).

A protected topology can only be modified by an administrator who has begun an
edit session. The session is EXCLUSIVE (one editor at a time) and times out; an
expired/abandoned session auto-discards
(restores the pre-edit baseline). The session ends with commit (saves a new
version stamped with the username) or discard (restores the baseline).

`require_edit_session` is the dependency the content routers use to enforce this.
"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.topology import Topology
from app.models.topology_version import TopologyVersion
from app.models.user import User
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/topologies/{topology_id}/edit-session", tags=["edit-session"])

# Generous timeout; the frontend pings /heartbeat while the session is active.
EDIT_SESSION_MINUTES = 45
_BASELINE_NAME = "__pre-edit baseline__"


def _has_live_session(topo: Topology, now: datetime) -> bool:
    return bool(topo.edit_session_user_id and topo.edit_session_expires_at and topo.edit_session_expires_at > now)


def _baseline_for(topo: Topology, db: Session):
    """The session's pre-edit baseline version, scoped to this topology (the
    topology_id filter guards against ever restoring another topology's snapshot)."""
    if not topo.edit_session_baseline_version_id:
        return None
    return db.query(TopologyVersion).filter(
        TopologyVersion.id == topo.edit_session_baseline_version_id,
        TopologyVersion.topology_id == topo.id,
    ).first()


def _clear_session(topo: Topology, db: Session) -> None:
    """Drop the session + its pre-edit baseline snapshot (caller commits)."""
    bl = _baseline_for(topo, db)
    if bl:
        db.delete(bl)
    topo.edit_session_user_id = None
    topo.edit_session_username = ""
    topo.edit_session_expires_at = None
    topo.edit_session_baseline_version_id = None


def expire_if_stale(topo: Topology, db: Session) -> None:
    """Auto-discard an expired/abandoned session: restore the baseline and clear
    the lock. No-op if there's no session or it's still live. Lazy (runs on the
    guard + status paths) since there's no background scheduler."""
    now = datetime.utcnow()
    if not topo.edit_session_user_id or _has_live_session(topo, now):
        return
    from app.routes.topologies import _restore_into_topology
    bl = _baseline_for(topo, db)
    if bl:
        _restore_into_topology(json.loads(bl.snapshot_json), topo.id, db)
    _clear_session(topo, db)
    db.commit()


def _status(topo: Topology, user: User) -> dict:
    now = datetime.utcnow()
    live = _has_live_session(topo, now)
    return {
        "protected": bool(topo.is_protected),
        "active": live,
        "held_by_me": live and topo.edit_session_user_id == user.id,
        "username": topo.edit_session_username or "",
        "expires_at": topo.edit_session_expires_at.isoformat() if topo.edit_session_expires_at else None,
    }


def _get_topo(topology_id: int, db: Session) -> Topology:
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    return topo


@router.get("/")
def get_status(topology_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    topo = _get_topo(topology_id, db)
    expire_if_stale(topo, db)
    return _status(topo, current_user)


@router.post("/begin")
def begin(topology_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    topo = _get_topo(topology_id, db)
    if not topo.is_protected:
        raise HTTPException(400, "Topology is not protected; no edit session needed.")
    # Only administrators may edit a protected topology.
    if not current_user.is_admin:
        raise HTTPException(403, "Only an administrator can edit a protected topology.")
    expire_if_stale(topo, db)
    now = datetime.utcnow()

    # Already holding a live session — just extend it (don't re-snapshot, so
    # discard still reverts to the original entry point).
    if _has_live_session(topo, now) and topo.edit_session_user_id == current_user.id:
        topo.edit_session_expires_at = now + timedelta(minutes=EDIT_SESSION_MINUTES)
        db.commit()
        return _status(topo, current_user)

    # Exclusive lock: someone else holds a live session.
    if _has_live_session(topo, now) and topo.edit_session_user_id != current_user.id:
        raise HTTPException(409, f"Locked by {topo.edit_session_username} — being edited.")

    # Snapshot the pre-edit baseline (for discard / auto-discard).
    from app.routes.topologies import _serialize_topology
    snap = _serialize_topology(topology_id, db)
    baseline = TopologyVersion(
        topology_id=topology_id, name=_BASELINE_NAME,
        description="Pre-edit baseline", snapshot_json=json.dumps(snap),
        node_count=len(snap.get("nodes", [])), link_count=len(snap.get("links", [])),
        flow_count=len(snap.get("flows", [])), created_by=current_user.username, is_baseline=1,
    )
    db.add(baseline)
    db.flush()
    topo.edit_session_user_id = current_user.id
    topo.edit_session_username = current_user.username
    topo.edit_session_expires_at = now + timedelta(minutes=EDIT_SESSION_MINUTES)
    topo.edit_session_baseline_version_id = baseline.id
    db.commit()
    return _status(topo, current_user)


@router.post("/heartbeat")
def heartbeat(topology_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    topo = _get_topo(topology_id, db)
    now = datetime.utcnow()
    if not (_has_live_session(topo, now) and topo.edit_session_user_id == current_user.id):
        raise HTTPException(409, "No active edit session held by you.")
    topo.edit_session_expires_at = now + timedelta(minutes=EDIT_SESSION_MINUTES)
    db.commit()
    return _status(topo, current_user)


@router.post("/commit")
def commit(topology_id: int, body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    topo = _get_topo(topology_id, db)
    now = datetime.utcnow()
    if not (_has_live_session(topo, now) and topo.edit_session_user_id == current_user.id):
        raise HTTPException(403, "You do not hold an active edit session for this topology.")
    from app.routes.topologies import _serialize_topology
    snap = _serialize_topology(topology_id, db)
    # The version name is always "<user> — <timestamp>"; the commit message
    # (if any) is stored separately as the description.
    name = f"{current_user.username} — {now.strftime('%Y-%m-%d %H:%M UTC')}"
    message = (body.get("message") or "").strip()
    version = TopologyVersion(
        topology_id=topology_id, name=name, description=message,
        snapshot_json=json.dumps(snap),
        node_count=len(snap.get("nodes", [])), link_count=len(snap.get("links", [])),
        flow_count=len(snap.get("flows", [])), created_by=current_user.username, is_baseline=0,
    )
    db.add(version)
    _clear_session(topo, db)  # drops the baseline; keeps the committed version
    db.commit()
    db.refresh(version)
    return {"ok": True, "version_id": version.id, "name": version.name, "created_by": version.created_by}


@router.post("/discard")
def discard(topology_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    topo = _get_topo(topology_id, db)
    now = datetime.utcnow()
    if not (_has_live_session(topo, now) and topo.edit_session_user_id == current_user.id):
        raise HTTPException(403, "You do not hold an active edit session for this topology.")
    from app.routes.topologies import _restore_into_topology
    bl = _baseline_for(topo, db)
    if bl:
        _restore_into_topology(json.loads(bl.snapshot_json), topology_id, db)
    _clear_session(topo, db)
    db.commit()
    return {"ok": True}


# Read-only analytics that happen to be POSTs — these must work on a protected
# topology even when not in edit mode (compute-path doesn't persist;
# analyze-availability only caches the analysis result).
_READONLY_OPS = ("/compute-path", "/analyze-availability")


async def require_edit_session(
    request: Request, topology_id: int,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """Dependency: block mutating requests to a protected topology unless the
    caller holds a live edit session. Read methods + read-only analytics pass;
    non-protected pass. Fail-safe: anything not explicitly exempt is blocked."""
    if request.method in ("GET", "HEAD", "OPTIONS") or request.url.path.rstrip("/").endswith(_READONLY_OPS):
        return
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo or not topo.is_protected:
        return
    expire_if_stale(topo, db)
    db.refresh(topo)
    now = datetime.utcnow()
    if _has_live_session(topo, now) and topo.edit_session_user_id == current_user.id:
        return
    if _has_live_session(topo, now):
        raise HTTPException(409, f"Locked by {topo.edit_session_username} — being edited.")
    raise HTTPException(403, "This topology is protected. Enter edit mode to make changes.")

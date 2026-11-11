"""Saved sub-topology views — CRUD.

A view is a named subset of a topology's nodes. The frontend applies one as
a canvas filter: only its nodes + the links between them render. Only node
ids are stored; links are derived at view time.
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.topology_view import TopologyView
from app.models.topology import Topology

router = APIRouter(prefix="/api/topologies/{topology_id}/views", tags=["topology-views"])


class ViewCreate(BaseModel):
    name: str
    description: str = ""
    node_ids: list[int] = []
    color: str = ""


class DefaultViewUpdate(BaseModel):
    # Node ids removed from the Default view. The Default view renders every
    # node EXCEPT these, so new/inherited nodes appear automatically.
    excluded_node_ids: list[int] = []


class ViewUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    node_ids: Optional[list[int]] = None
    color: Optional[str] = None


def _view_to_dict(v: TopologyView) -> dict:
    return {
        "id": v.id,
        "topology_id": v.topology_id,
        "name": v.name,
        "description": v.description or "",
        "node_ids": v.node_ids,
        "color": v.color or "",
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }


@router.get("/")
def list_views(topology_id: int, db: Session = Depends(get_db)):
    views = (
        db.query(TopologyView)
        .filter(TopologyView.topology_id == topology_id)
        .order_by(TopologyView.name)
        .all()
    )
    return [_view_to_dict(v) for v in views]


@router.post("/")
def create_view(topology_id: int, data: ViewCreate, db: Session = Depends(get_db)):
    if not data.name.strip():
        raise HTTPException(400, "View name is required")
    view = TopologyView(
        topology_id=topology_id,
        name=data.name.strip(),
        description=data.description,
        node_ids_json=json.dumps(sorted(set(data.node_ids))),
        color=data.color or "",
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    return _view_to_dict(view)


# --- Default view (exclusion set). Declared before /{view_id} so the literal
# "default" path is never treated as a view id. ---
@router.get("/default")
def get_default_view(topology_id: int, db: Session = Depends(get_db)):
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    return {"excluded_node_ids": topo.default_view_excluded_node_ids}


@router.put("/default")
def set_default_view(topology_id: int, data: DefaultViewUpdate, db: Session = Depends(get_db)):
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    topo.default_view_excluded_node_ids = sorted(set(data.excluded_node_ids))
    db.commit()
    return {"excluded_node_ids": topo.default_view_excluded_node_ids}


@router.put("/{view_id}")
def update_view(topology_id: int, view_id: int, data: ViewUpdate, db: Session = Depends(get_db)):
    view = (
        db.query(TopologyView)
        .filter(TopologyView.id == view_id, TopologyView.topology_id == topology_id)
        .first()
    )
    if not view:
        raise HTTPException(404, "View not found")
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(400, "View name cannot be empty")
        view.name = data.name.strip()
    if data.description is not None:
        view.description = data.description
    if data.node_ids is not None:
        view.node_ids_json = json.dumps(sorted(set(data.node_ids)))
    if data.color is not None:
        view.color = data.color
    db.commit()
    db.refresh(view)
    return _view_to_dict(view)


@router.delete("/{view_id}")
def delete_view(topology_id: int, view_id: int, db: Session = Depends(get_db)):
    view = (
        db.query(TopologyView)
        .filter(TopologyView.id == view_id, TopologyView.topology_id == topology_id)
        .first()
    )
    if not view:
        raise HTTPException(404, "View not found")
    db.delete(view)
    db.commit()
    return {"ok": True}

"""Maintenance preset CRUD routes."""

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.maintenance_preset import MaintenancePreset

router = APIRouter(prefix="/api/topologies/{topology_id}/maintenance-presets", tags=["maintenance-presets"])


class PresetCreate(BaseModel):
    name: str
    description: str = ""
    node_ids: list[int] = []
    link_ids: list[int] = []


class PresetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    node_ids: Optional[list[int]] = None
    link_ids: Optional[list[int]] = None


def _preset_to_dict(p: MaintenancePreset) -> dict:
    return {
        "id": p.id,
        "topology_id": p.topology_id,
        "name": p.name,
        "description": p.description or "",
        "node_ids": p.node_ids,
        "link_ids": p.link_ids,
    }


@router.get("/")
def list_presets(topology_id: int, db: Session = Depends(get_db)):
    presets = db.query(MaintenancePreset).filter(MaintenancePreset.topology_id == topology_id).all()
    return [_preset_to_dict(p) for p in presets]


@router.post("/")
def create_preset(topology_id: int, data: PresetCreate, db: Session = Depends(get_db)):
    preset = MaintenancePreset(
        topology_id=topology_id,
        name=data.name,
        description=data.description,
        node_ids_json=json.dumps(data.node_ids),
        link_ids_json=json.dumps(data.link_ids),
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return _preset_to_dict(preset)


@router.put("/{preset_id}")
def update_preset(topology_id: int, preset_id: int, data: PresetUpdate, db: Session = Depends(get_db)):
    preset = db.query(MaintenancePreset).filter(
        MaintenancePreset.id == preset_id, MaintenancePreset.topology_id == topology_id
    ).first()
    if not preset:
        raise HTTPException(404, "Preset not found")
    if data.name is not None:
        preset.name = data.name
    if data.description is not None:
        preset.description = data.description
    if data.node_ids is not None:
        preset.node_ids_json = json.dumps(data.node_ids)
    if data.link_ids is not None:
        preset.link_ids_json = json.dumps(data.link_ids)
    db.commit()
    db.refresh(preset)
    return _preset_to_dict(preset)


@router.delete("/{preset_id}")
def delete_preset(topology_id: int, preset_id: int, db: Session = Depends(get_db)):
    preset = db.query(MaintenancePreset).filter(
        MaintenancePreset.id == preset_id, MaintenancePreset.topology_id == topology_id
    ).first()
    if not preset:
        raise HTTPException(404, "Preset not found")
    db.delete(preset)
    db.commit()
    return {"ok": True}

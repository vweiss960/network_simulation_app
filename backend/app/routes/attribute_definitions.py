"""CRUD for global attribute definitions.

DeviceAttributeDefinition  — see app.models.attribute_definitions
InterfaceAttributeDefinition — same

Mounted under /api/attribute-definitions/{device,interface} so the Settings
panel and the config renderer share one source of truth.
"""
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.attribute_definitions import (
    DeviceAttributeDefinition, InterfaceAttributeDefinition, VALID_DEVICE_ATTR_TYPES,
)


router = APIRouter(prefix="/api/attribute-definitions", tags=["attribute_definitions"])


# Keys are used directly as Jinja attribute paths (device.<key>), so we
# constrain them to valid Python identifiers — no spaces, no leading digit.
KEY_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


# ---------------------- Device attributes ----------------------

class DeviceAttrDefIn(BaseModel):
    key: str
    label: str
    description: str = ""
    type: str = "string"
    options: list[str] = []
    default_value: str = ""
    unique_per_topology: bool = False
    unique_start_value: int = 0

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not KEY_PATTERN.match(v):
            raise ValueError("key must match [a-zA-Z_][a-zA-Z0-9_]* (used as a Jinja attribute path)")
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in VALID_DEVICE_ATTR_TYPES:
            raise ValueError(f"type must be one of {sorted(VALID_DEVICE_ATTR_TYPES)}")
        return v


class DeviceAttrDefOut(BaseModel):
    id: int
    key: str
    label: str
    description: str
    type: str
    options: list[str]
    default_value: str
    unique_per_topology: bool = False
    unique_start_value: int = 0


@router.get("/device", response_model=list[DeviceAttrDefOut])
def list_device_defs(db: Session = Depends(get_db)):
    rows = db.query(DeviceAttributeDefinition).order_by(DeviceAttributeDefinition.label).all()
    return [
        DeviceAttrDefOut(
            id=r.id, key=r.key, label=r.label, description=r.description,
            type=r.type, options=r.options, default_value=r.default_value,
            unique_per_topology=bool(r.unique_per_topology),
            unique_start_value=r.unique_start_value or 0,
        )
        for r in rows
    ]


@router.post("/device", response_model=DeviceAttrDefOut)
def create_device_def(data: DeviceAttrDefIn, db: Session = Depends(get_db)):
    if db.query(DeviceAttributeDefinition).filter(DeviceAttributeDefinition.key == data.key).first():
        raise HTTPException(status_code=409, detail=f"Attribute '{data.key}' already exists")
    row = DeviceAttributeDefinition(
        key=data.key, label=data.label, description=data.description,
        type=data.type, default_value=data.default_value,
        unique_per_topology=1 if data.unique_per_topology else 0,
        unique_start_value=int(data.unique_start_value) if data.unique_start_value is not None else 0,
    )
    row.options = data.options
    db.add(row)
    db.commit()
    db.refresh(row)
    return DeviceAttrDefOut(
        id=row.id, key=row.key, label=row.label, description=row.description,
        type=row.type, options=row.options, default_value=row.default_value,
        unique_per_topology=bool(row.unique_per_topology),
        unique_start_value=row.unique_start_value or 0,
    )


@router.put("/device/{def_id}", response_model=DeviceAttrDefOut)
def update_device_def(def_id: int, data: DeviceAttrDefIn, db: Session = Depends(get_db)):
    row = db.query(DeviceAttributeDefinition).filter(DeviceAttributeDefinition.id == def_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Definition not found")
    if data.key != row.key and db.query(DeviceAttributeDefinition).filter(DeviceAttributeDefinition.key == data.key).first():
        raise HTTPException(status_code=409, detail=f"Attribute '{data.key}' already exists")
    row.key = data.key
    row.label = data.label
    row.description = data.description
    row.type = data.type
    row.options = data.options
    row.default_value = data.default_value
    row.unique_per_topology = 1 if data.unique_per_topology else 0
    row.unique_start_value = int(data.unique_start_value) if data.unique_start_value is not None else 0
    db.commit()
    db.refresh(row)
    return DeviceAttrDefOut(
        id=row.id, key=row.key, label=row.label, description=row.description,
        type=row.type, options=row.options, default_value=row.default_value,
        unique_per_topology=bool(row.unique_per_topology),
        unique_start_value=row.unique_start_value or 0,
    )


@router.get("/device/{def_id}/next-value")
def next_unique_value(def_id: int, topology_id: int, db: Session = Depends(get_db)):
    """Return the next available integer for a unique-flagged integer
    attribute, starting from `unique_start_value` and walking upward until an
    unused value is found. Considers the topology chain so inherited node
    values count as 'in use'.

    Errors if the attribute isn't an integer or isn't flagged unique — those
    constraints define what 'next available' means.
    """
    from app.models.node_instance import NodeInstance
    from app.services.topology_chain import get_topology_chain

    row = db.query(DeviceAttributeDefinition).filter(DeviceAttributeDefinition.id == def_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Definition not found")
    if row.type != "integer":
        raise HTTPException(status_code=400, detail="Auto-pick is only valid for integer attributes")
    if not row.unique_per_topology:
        raise HTTPException(status_code=400, detail="Auto-pick is only valid for attributes flagged unique")

    chain = get_topology_chain(db, topology_id)
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    used: set[int] = set()
    for n in nodes:
        v = n.custom_attrs.get(row.key)
        if v is None or v == "":
            continue
        try:
            used.add(int(v))
        except (TypeError, ValueError):
            continue

    start = int(row.unique_start_value or 0)
    candidate = start
    while candidate in used:
        candidate += 1
    return {"next_value": candidate, "start": start, "used_count": len(used)}


@router.delete("/device/{def_id}")
def delete_device_def(def_id: int, db: Session = Depends(get_db)):
    row = db.query(DeviceAttributeDefinition).filter(DeviceAttributeDefinition.id == def_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Definition not found")
    db.delete(row)
    db.commit()
    # Per-device values for this key remain in custom_attrs_json — they
    # become inert (template just sees them gone). If a future definition
    # re-uses the key, those old values will resurface as expected.
    return {"ok": True}


# ---------------------- Interface attributes ----------------------

class IfaceAttrDefIn(BaseModel):
    key: str
    label: str
    description: str = ""

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not KEY_PATTERN.match(v):
            raise ValueError("key must match [a-zA-Z_][a-zA-Z0-9_]*")
        return v


class IfaceAttrDefOut(BaseModel):
    id: int
    key: str
    label: str
    description: str


@router.get("/interface", response_model=list[IfaceAttrDefOut])
def list_iface_defs(db: Session = Depends(get_db)):
    rows = db.query(InterfaceAttributeDefinition).order_by(InterfaceAttributeDefinition.label).all()
    return rows


@router.post("/interface", response_model=IfaceAttrDefOut)
def create_iface_def(data: IfaceAttrDefIn, db: Session = Depends(get_db)):
    if db.query(InterfaceAttributeDefinition).filter(InterfaceAttributeDefinition.key == data.key).first():
        raise HTTPException(status_code=409, detail=f"Attribute '{data.key}' already exists")
    row = InterfaceAttributeDefinition(key=data.key, label=data.label, description=data.description)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/interface/{def_id}", response_model=IfaceAttrDefOut)
def update_iface_def(def_id: int, data: IfaceAttrDefIn, db: Session = Depends(get_db)):
    row = db.query(InterfaceAttributeDefinition).filter(InterfaceAttributeDefinition.id == def_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Definition not found")
    if data.key != row.key and db.query(InterfaceAttributeDefinition).filter(InterfaceAttributeDefinition.key == data.key).first():
        raise HTTPException(status_code=409, detail=f"Attribute '{data.key}' already exists")
    row.key = data.key
    row.label = data.label
    row.description = data.description
    db.commit()
    db.refresh(row)
    return row


@router.delete("/interface/{def_id}")
def delete_iface_def(def_id: int, db: Session = Depends(get_db)):
    row = db.query(InterfaceAttributeDefinition).filter(InterfaceAttributeDefinition.id == def_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Definition not found")
    db.delete(row)
    db.commit()
    return {"ok": True}

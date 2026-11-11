"""External integration management routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.external_system import ExternalSystem
from app.models.resource_mapping import ResourceMapping
from app.models.external_ref import ExternalRef
from app.models.sync_queue import SyncQueueItem
from app.services.external_sync_service import (
    ExternalSyncError, test_connection, list_tables, get_table_schema,
)

router = APIRouter(prefix="/api/topologies/{topology_id}/integrations", tags=["integrations"])


# --- Schemas ---

class ExternalSystemCreate(BaseModel):
    name: str
    base_url: str
    auth_type: str = "api_key"
    auth_header_name: str = "X-API-Key"
    auth_value: str = ""
    enabled: bool = False
    verify_ssl: bool = True


class ExternalSystemUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    auth_header_name: Optional[str] = None
    auth_value: Optional[str] = None
    enabled: Optional[bool] = None
    verify_ssl: Optional[bool] = None


class ResourceMappingCreate(BaseModel):
    phanero_resource: str  # "link" or "node"
    target_table: str
    primary_key_column: str = "id"
    field_map: dict = {}
    enabled: bool = True


class ResourceMappingUpdate(BaseModel):
    target_table: Optional[str] = None
    primary_key_column: Optional[str] = None
    field_map: Optional[dict] = None
    enabled: Optional[bool] = None


def _serialize_system(s: ExternalSystem) -> dict:
    return {
        "id": s.id,
        "topology_id": s.topology_id,
        "name": s.name,
        "base_url": s.base_url,
        "auth_type": s.auth_type,
        "auth_header_name": s.auth_header_name,
        "auth_value": "***" if s.auth_value else "",
        "enabled": s.enabled,
        "verify_ssl": bool(getattr(s, "verify_ssl", True)),
    }


def _serialize_mapping(m: ResourceMapping) -> dict:
    return {
        "id": m.id,
        "external_system_id": m.external_system_id,
        "phanero_resource": m.phanero_resource,
        "target_table": m.target_table,
        "primary_key_column": m.primary_key_column,
        "field_map": m.field_map,
        "enabled": m.enabled,
    }


# --- External System CRUD ---

@router.get("/")
def list_systems(topology_id: int, db: Session = Depends(get_db)):
    systems = db.query(ExternalSystem).filter(ExternalSystem.topology_id == topology_id).all()
    return [_serialize_system(s) for s in systems]


@router.post("/")
def create_system(topology_id: int, data: ExternalSystemCreate, db: Session = Depends(get_db)):
    system = ExternalSystem(topology_id=topology_id, **data.dict())
    db.add(system)
    db.commit()
    db.refresh(system)
    return _serialize_system(system)


@router.patch("/{system_id}")
def update_system(topology_id: int, system_id: int, data: ExternalSystemUpdate, db: Session = Depends(get_db)):
    system = db.query(ExternalSystem).filter(
        ExternalSystem.id == system_id, ExternalSystem.topology_id == topology_id
    ).first()
    if not system:
        raise HTTPException(404, "System not found")
    for key, val in data.dict(exclude_unset=True).items():
        # Don't overwrite auth_value if user sent the masked "***"
        if key == "auth_value" and val == "***":
            continue
        setattr(system, key, val)
    db.commit()
    db.refresh(system)
    return _serialize_system(system)


@router.delete("/{system_id}")
def delete_system(topology_id: int, system_id: int, db: Session = Depends(get_db)):
    system = db.query(ExternalSystem).filter(
        ExternalSystem.id == system_id, ExternalSystem.topology_id == topology_id
    ).first()
    if not system:
        raise HTTPException(404, "System not found")
    # Cascade: delete mappings, refs, and queue items
    db.query(ResourceMapping).filter(ResourceMapping.external_system_id == system_id).delete()
    db.query(ExternalRef).filter(ExternalRef.external_system_id == system_id).delete()
    db.query(SyncQueueItem).filter(SyncQueueItem.external_system_id == system_id).delete()
    db.delete(system)
    db.commit()
    return {"ok": True}


# --- Discovery ---

@router.post("/{system_id}/test")
def test_system(topology_id: int, system_id: int, db: Session = Depends(get_db)):
    system = db.query(ExternalSystem).filter(
        ExternalSystem.id == system_id, ExternalSystem.topology_id == topology_id
    ).first()
    if not system:
        raise HTTPException(404, "System not found")
    try:
        result = test_connection(system)
        return {"connected": True, "tables_count": len(result.get("tables", []))}
    except ExternalSyncError as e:
        raise HTTPException(400, detail={"error": str(e)})


@router.get("/{system_id}/tables")
def discover_tables(topology_id: int, system_id: int, db: Session = Depends(get_db)):
    system = db.query(ExternalSystem).filter(
        ExternalSystem.id == system_id, ExternalSystem.topology_id == topology_id
    ).first()
    if not system:
        raise HTTPException(404, "System not found")
    try:
        return {"tables": list_tables(system)}
    except ExternalSyncError as e:
        raise HTTPException(400, detail={"error": str(e)})


@router.get("/{system_id}/tables/{table_name}/schema")
def discover_schema(topology_id: int, system_id: int, table_name: str, db: Session = Depends(get_db)):
    system = db.query(ExternalSystem).filter(
        ExternalSystem.id == system_id, ExternalSystem.topology_id == topology_id
    ).first()
    if not system:
        raise HTTPException(404, "System not found")
    try:
        return get_table_schema(system, table_name)
    except ExternalSyncError as e:
        raise HTTPException(400, detail={"error": str(e)})


# --- Resource Mappings ---

@router.get("/{system_id}/mappings")
def list_mappings(topology_id: int, system_id: int, db: Session = Depends(get_db)):
    mappings = db.query(ResourceMapping).filter(ResourceMapping.external_system_id == system_id).all()
    return [_serialize_mapping(m) for m in mappings]


@router.post("/{system_id}/mappings")
def create_mapping(topology_id: int, system_id: int, data: ResourceMappingCreate, db: Session = Depends(get_db)):
    # Replace existing mapping for the same resource type
    existing = db.query(ResourceMapping).filter(
        ResourceMapping.external_system_id == system_id,
        ResourceMapping.phanero_resource == data.phanero_resource,
    ).first()
    if existing:
        existing.target_table = data.target_table
        existing.primary_key_column = data.primary_key_column
        existing.field_map = data.field_map
        existing.enabled = data.enabled
        db.commit()
        db.refresh(existing)
        return _serialize_mapping(existing)
    mapping = ResourceMapping(
        external_system_id=system_id,
        phanero_resource=data.phanero_resource,
        target_table=data.target_table,
        primary_key_column=data.primary_key_column,
        enabled=data.enabled,
    )
    mapping.field_map = data.field_map
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return _serialize_mapping(mapping)


@router.delete("/{system_id}/mappings/{mapping_id}")
def delete_mapping(topology_id: int, system_id: int, mapping_id: int, db: Session = Depends(get_db)):
    mapping = db.query(ResourceMapping).filter(
        ResourceMapping.id == mapping_id, ResourceMapping.external_system_id == system_id
    ).first()
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    db.delete(mapping)
    db.commit()
    return {"ok": True}


# --- Sync Queue Inspection ---

@router.get("/{system_id}/queue")
def list_queue_items(topology_id: int, system_id: int, status: str = "all", limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(SyncQueueItem).filter(SyncQueueItem.external_system_id == system_id)
    if status != "all":
        q = q.filter(SyncQueueItem.status == status)
    items = q.order_by(SyncQueueItem.created_at.desc()).limit(limit).all()
    return [{
        "id": i.id,
        "phanero_resource": i.phanero_resource,
        "phanero_id": i.phanero_id,
        "operation": i.operation,
        "status": i.status,
        "retry_count": i.retry_count,
        "last_error": i.last_error or "",
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "completed_at": i.completed_at.isoformat() if i.completed_at else None,
    } for i in items]


@router.post("/{system_id}/queue/{item_id}/retry")
def retry_queue_item(topology_id: int, system_id: int, item_id: int, db: Session = Depends(get_db)):
    item = db.query(SyncQueueItem).filter(
        SyncQueueItem.id == item_id, SyncQueueItem.external_system_id == system_id
    ).first()
    if not item:
        raise HTTPException(404, "Queue item not found")
    item.status = "pending"
    item.retry_count = 0
    item.next_attempt_at = None
    item.last_error = ""
    db.commit()
    return {"ok": True}


@router.delete("/{system_id}/queue")
def clear_queue(topology_id: int, system_id: int, status: str = "success", db: Session = Depends(get_db)):
    """Clear queue items by status. Default clears successful items only."""
    q = db.query(SyncQueueItem).filter(SyncQueueItem.external_system_id == system_id)
    if status != "all":
        q = q.filter(SyncQueueItem.status == status)
    count = q.delete()
    db.commit()
    return {"ok": True, "deleted": count}


# --- Bulk sync (initial population) ---

@router.post("/{system_id}/sync-all")
def sync_all(topology_id: int, system_id: int, db: Session = Depends(get_db)):
    """Enqueue sync events for all existing nodes and links against THIS system.

    Targets the specific system_id rather than fanning out across all enabled
    systems on the topology. Items are queued regardless of the system's
    enabled state — if disabled, the worker will cancel them on dequeue, so
    the user can still see what was attempted.
    """
    import json as _json
    from app.models.node_instance import NodeInstance
    from app.models.link import Link
    from app.models.sync_queue import SyncQueueItem

    system = db.query(ExternalSystem).filter(
        ExternalSystem.id == system_id, ExternalSystem.topology_id == topology_id
    ).first()
    if not system:
        raise HTTPException(404, "System not found")

    def node_to_dict(n: NodeInstance) -> dict:
        return {
            "id": n.id, "name": n.name, "vendor": n.vendor or "", "model": n.model or "",
            "platform": n.platform or "", "location": n.location or "",
            "latitude": n.latitude, "longitude": n.longitude,
            "mtbf_hours": n.mtbf_hours, "mttr_hours": n.mttr_hours,
            "capacity_gbps": n.capacity_gbps,
            "prometheus_id": n.prometheus_id or "", "mgmt_ip": n.mgmt_ip or "",
            "is_planned": bool(n.is_planned),
        }

    def link_to_dict(l: Link, nodes: dict) -> dict:
        return {
            "id": l.id,
            "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
            "source_node_name": nodes.get(l.source_node_id, ""),
            "target_node_name": nodes.get(l.target_node_id, ""),
            "source_interface": l.source_interface or "", "target_interface": l.target_interface or "",
            "bandwidth_gbps": l.bandwidth_gbps, "cost": l.cost, "latency_ms": l.latency_ms,
            "carrier": l.carrier or "", "mtbf_hours": l.mtbf_hours, "mttr_hours": l.mttr_hours,
            "circuit_cost": l.circuit_cost or 0, "circuit_id": l.circuit_id or "",
            "link_id_label": l.link_id_label or "",
            "source_ip": l.source_ip or "", "target_ip": l.target_ip or "",
            "is_planned": bool(l.is_planned),
        }

    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).all()
    node_name_map = {n.id: n.name for n in nodes}

    count = 0
    for n in nodes:
        db.add(SyncQueueItem(
            external_system_id=system_id, phanero_resource="node",
            phanero_id=n.id, operation="create",
            payload_json=_json.dumps(node_to_dict(n)), status="pending",
        ))
        count += 1
    for l in links:
        db.add(SyncQueueItem(
            external_system_id=system_id, phanero_resource="link",
            phanero_id=l.id, operation="create",
            payload_json=_json.dumps(link_to_dict(l, node_name_map)), status="pending",
        ))
        count += 1
    db.commit()
    return {"ok": True, "queued": count, "enabled": system.enabled}

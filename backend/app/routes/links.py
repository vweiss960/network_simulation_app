import json
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.schemas.schemas import LinkCreate, LinkUpdate, LinkOut
from app.services.latency import infer_latency_source, SOURCES as LATENCY_SOURCES
from app.services.satellite import validate_link_roles, SatelliteLinkError

router = APIRouter(prefix="/api/topologies/{topology_id}/links", tags=["links"])

# Reference bandwidth for auto-cost: cost = REFERENCE_BW / link_bw
# 400G so that a 400G link = cost 1, 100G = 4, 10G = 40, 1G = 400
REFERENCE_BANDWIDTH_GBPS = 400


def _link_to_dict(l: Link, is_inherited: bool = False) -> dict:
    return {
        "id": l.id, "topology_id": l.topology_id,
        "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
        "source_interface": l.source_interface, "target_interface": l.target_interface,
        "bandwidth_gbps": l.bandwidth_gbps, "cost": l.cost, "latency_ms": l.latency_ms or 0.0,
        "latency_source": l.latency_source or "unknown", "transport": l.transport or "terrestrial",
        "orbit": l.orbit or "",
        "carrier": l.carrier, "mtbf_hours": l.mtbf_hours, "mttr_hours": l.mttr_hours,
        "sr_attributes": l.sr_attributes,
        "circuit_cost": l.circuit_cost or 0,
        "circuit_id": l.circuit_id or "",
        "link_id_label": l.link_id_label or "",
        "source_ip": l.source_ip or "",
        "target_ip": l.target_ip or "",
        "source_ipv6": l.source_ipv6 or "",
        "target_ipv6": l.target_ipv6 or "",
        "is_planned": bool(l.is_planned),
        "color": l.color or "",
        "geo_path": json.loads(l.geo_path_json) if getattr(l, "geo_path_json", "") else None,
        "is_inherited": is_inherited,
    }


def _with_effective_latency(d: dict, link: Link, ctx) -> dict:
    """Add the effective latency (measured / estimated / unknown) to a link dict."""
    from app.services.latency import effective_latency
    info = effective_latency(link, ctx)
    d.update(latency_effective_ms=info.ms, latency_effective_source=info.source, latency_method=info.method)
    return d


def _latency_ctx(db: Session, topology_id: int):
    """Estimate context over every node visible to this topology."""
    from app.services.latency import build_context
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    return build_context(db, db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all())


def _link_sync_payload(l: Link, db: Session) -> dict:
    """Flat payload for external sync — includes resolved node names for templates."""
    src = db.query(NodeInstance).filter(NodeInstance.id == l.source_node_id).first()
    tgt = db.query(NodeInstance).filter(NodeInstance.id == l.target_node_id).first()
    return {
        "id": l.id,
        "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
        "source_node_name": src.name if src else "",
        "target_node_name": tgt.name if tgt else "",
        "source_interface": l.source_interface or "", "target_interface": l.target_interface or "",
        # Only known latencies leave the system — an estimate must never
        # overwrite another system's data. None = not known here.
        "bandwidth_gbps": l.bandwidth_gbps, "cost": l.cost,
        "latency_ms": l.latency_ms if l.latency_source == "measured" and (l.latency_ms or 0) > 0 else None,
        "carrier": l.carrier or "", "mtbf_hours": l.mtbf_hours, "mttr_hours": l.mttr_hours,
        "circuit_cost": l.circuit_cost or 0, "circuit_id": l.circuit_id or "",
        "link_id_label": l.link_id_label or "",
        "source_ip": l.source_ip or "", "target_ip": l.target_ip or "",
        "source_ipv6": l.source_ipv6 or "", "target_ipv6": l.target_ipv6 or "",
        "is_planned": bool(l.is_planned),
    }


def _get_used_interfaces(topology_id: int, node_id: int, db: Session, exclude_link_id: int | None = None) -> set[str]:
    """Get set of interface names already used by links, flows, or marked reserved on a node.

    Walks the inheritance chain (so inherited link/flow usage is counted) and
    also folds in usage from any locally-shadowed nodes whose name was claimed
    by this ancestor — their links/flows belong to the winning node now.
    """
    from app.services.topology_chain import get_topology_chain, merge_by_name
    chain = get_topology_chain(db, topology_id)

    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    _, node_remap = merge_by_name(all_nodes, chain)

    # All node IDs that, after remap, count as this same logical node
    effective_id = node_remap.get(node_id, node_id)
    equivalent_ids = {effective_id} | {local for local, ancestor in node_remap.items() if ancestor == effective_id}

    used = set()
    links = db.query(Link).filter(Link.topology_id.in_(chain)).all()
    for l in links:
        if exclude_link_id and l.id == exclude_link_id:
            continue
        # Links hidden because a higher topology absorbed them still hold
        # their interfaces: if the absorbing link is ever removed they come
        # back, and must not find their ports double-booked.
        if l.source_node_id in equivalent_ids and l.source_interface:
            used.add(l.source_interface)
        if l.target_node_id in equivalent_ids and l.target_interface:
            used.add(l.target_interface)
    # Also exclude interfaces assigned to flows across the chain (inherited flows count too)
    from app.models.flow import Flow
    flows = db.query(Flow).filter(Flow.topology_id.in_(chain)).all()
    for f in flows:
        if f.source_node_id in equivalent_ids and f.source_interface:
            used.add(f.source_interface)
        if f.dest_node_id in equivalent_ids and f.dest_interface:
            used.add(f.dest_interface)
    # Also exclude reserved interfaces (use the winning ancestor's interface list)
    node = next((n for n in all_nodes if n.id == effective_id), None)
    if node:
        for iface in node.interfaces:
            if iface.get("status") == "reserved":
                used.add(iface["name"])
    return used


def _get_available_interfaces(node: NodeInstance, used: set[str]) -> list[dict]:
    """Get available (unused, non-reserved) interfaces for a node."""
    return [iface for iface in node.interfaces if iface["name"] not in used and iface.get("status", "free") != "reserved"]


def _get_interface_speed(node: NodeInstance, iface_name: str) -> float | None:
    """Get speed of a specific interface on a node."""
    for iface in node.interfaces:
        if iface["name"] == iface_name:
            return iface["speed_gbps"]
    return None


def _validate_link_interfaces(
    data: LinkCreate | LinkUpdate,
    topology_id: int,
    db: Session,
    source_node: NodeInstance | None = None,
    target_node: NodeInstance | None = None,
    exclude_link_id: int | None = None,
) -> tuple[str, str, float]:
    """Validate interface selection. Returns (source_iface, target_iface, bandwidth)."""
    if not data.source_interface or not data.target_interface:
        raise HTTPException(400, "Both source_interface and target_interface are required")

    if not source_node or not target_node:
        raise HTTPException(400, "Source and target nodes required")

    # Verify interfaces exist on their respective nodes
    src_speed = _get_interface_speed(source_node, data.source_interface)
    tgt_speed = _get_interface_speed(target_node, data.target_interface)

    if src_speed is None:
        raise HTTPException(400, f"Interface '{data.source_interface}' not found on node '{source_node.name}'")
    if tgt_speed is None:
        raise HTTPException(400, f"Interface '{data.target_interface}' not found on node '{target_node.name}'")

    # Validate speed match
    if src_speed != tgt_speed:
        raise HTTPException(
            400,
            f"Interface speed mismatch: {data.source_interface} is {src_speed}G but {data.target_interface} is {tgt_speed}G. "
            f"Both interfaces must have the same speed."
        )

    # Check interfaces aren't already in use
    src_used = _get_used_interfaces(topology_id, source_node.id, db, exclude_link_id)
    tgt_used = _get_used_interfaces(topology_id, target_node.id, db, exclude_link_id)

    if data.source_interface in src_used:
        raise HTTPException(400, f"Interface '{data.source_interface}' on '{source_node.name}' is already in use by another link")
    if data.target_interface in tgt_used:
        raise HTTPException(400, f"Interface '{data.target_interface}' on '{target_node.name}' is already in use by another link")

    return data.source_interface, data.target_interface, src_speed


@router.get("/", response_model=list[LinkOut])
def list_links(topology_id: int, db: Session = Depends(get_db)):
    """List links for a topology, including those inherited from parent topologies.

    Two kinds of hiding apply:
      - Node-shadowing: a child link's source/target node ID is transparently
        remapped to the ancestor's node ID when the child's local node has
        been shadowed by a parent's same-named node.
      - Link nullification: child links between two nodes are hidden when an
        ancestor topology has any link between the same node pair. Planning
        links are silently absorbed once the parent commits an official link.
    """
    from app.services.topology_chain import get_topology_chain, merge_by_name, nullified_link_ids
    chain = get_topology_chain(db, topology_id)
    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    _, node_remap = merge_by_name(all_nodes, chain)
    links = db.query(Link).filter(Link.topology_id.in_(chain)).all()
    nullified_ids = nullified_link_ids(chain, links, node_remap)
    from app.services.latency import build_context
    ctx = build_context(db, all_nodes)
    out = []
    for l in links:
        if l.id in nullified_ids:
            continue
        d = _with_effective_latency(_link_to_dict(l, is_inherited=(l.topology_id != topology_id)), l, ctx)
        if d["source_node_id"] in node_remap:
            d["source_node_id"] = node_remap[d["source_node_id"]]
        if d["target_node_id"] in node_remap:
            d["target_node_id"] = node_remap[d["target_node_id"]]
        out.append(d)
    return out


@router.get("/available-interfaces/{node_id}")
def get_available_interfaces(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    """Get available (unused) interfaces for a node. Node may live in a parent topology.

    If the caller passes a now-shadowed local node ID, transparently redirect
    to the winning ancestor's node so the response reflects the merged view.
    """
    from app.services.topology_chain import get_topology_chain, merge_by_name
    chain = get_topology_chain(db, topology_id)
    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    _, node_remap = merge_by_name(all_nodes, chain)
    effective_id = node_remap.get(node_id, node_id)

    node = next((n for n in all_nodes if n.id == effective_id), None)
    if not node:
        raise HTTPException(404, "Node not found")

    used = _get_used_interfaces(topology_id, effective_id, db)
    available = _get_available_interfaces(node, used)
    return {"node_id": effective_id, "node_name": node.name, "available": available, "used": list(used)}


@router.post("/", response_model=LinkOut)
def create_link(topology_id: int, data: LinkCreate, db: Session = Depends(get_db)):
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    # Source/target may live in a parent topology — child can attach to inherited nodes
    source_node = db.query(NodeInstance).filter(NodeInstance.id == data.source_node_id, NodeInstance.topology_id.in_(chain)).first()
    target_node = db.query(NodeInstance).filter(NodeInstance.id == data.target_node_id, NodeInstance.topology_id.in_(chain)).first()

    if not source_node:
        raise HTTPException(404, f"Source node {data.source_node_id} not found")
    if not target_node:
        raise HTTPException(404, f"Target node {data.target_node_id} not found")

    try:
        transport = validate_link_roles(source_node.role, target_node.role, data.transport, data.orbit)
    except SatelliteLinkError as e:
        raise HTTPException(400, str(e))
    if transport == "satellite":
        # Satellite links need no interfaces: bandwidth is entered directly.
        if not data.bandwidth_gbps or data.bandwidth_gbps <= 0:
            raise HTTPException(400, "Bandwidth must be greater than 0")
        src_iface, tgt_iface, bandwidth = data.source_interface or "", data.target_interface or "", data.bandwidth_gbps
    else:
        src_iface, tgt_iface, speed = _validate_link_interfaces(data, topology_id, db, source_node, target_node)
        # Bandwidth is determined by interface speed
        bandwidth = speed
    # Auto-compute cost from reference bandwidth if not explicitly set
    cost = data.cost if data.cost != 1.0 else REFERENCE_BANDWIDTH_GBPS / bandwidth

    link = Link(
        topology_id=topology_id,
        source_node_id=data.source_node_id, target_node_id=data.target_node_id,
        source_interface=src_iface, target_interface=tgt_iface,
        bandwidth_gbps=bandwidth, cost=cost,
        latency_ms=data.latency_ms if data.latency_ms is not None else 0.0,
        latency_source=infer_latency_source(data.latency_ms, data.latency_source),
        transport=transport,
        orbit=data.orbit if transport == "satellite" else "",
        carrier=data.carrier, mtbf_hours=data.mtbf_hours, mttr_hours=data.mttr_hours,
        sr_attributes_json=json.dumps(data.sr_attributes),
        circuit_cost=data.circuit_cost,
        circuit_id=data.circuit_id,
        link_id_label=data.link_id_label,
        is_planned=1 if data.is_planned else 0,
        source_ip=data.source_ip,
        target_ip=data.target_ip,
        source_ipv6=data.source_ipv6,
        target_ipv6=data.target_ipv6,
        color=data.color or "",
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    try:
        from app.services.sync_worker import enqueue_sync
        enqueue_sync(db, topology_id, "link", link.id, "create", _link_sync_payload(link, db))
    except Exception:
        pass
    return _with_effective_latency(_link_to_dict(link), link, _latency_ctx(db, topology_id))


@router.put("/bulk-update")
def bulk_update_links(topology_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Update multiple links at once. Accepts link_ids and fields to set."""
    link_ids = body.get("link_ids", [])
    updates = body.get("updates", {})
    if not link_ids or not updates:
        raise HTTPException(400, "link_ids and updates are required")
    links = db.query(Link).filter(Link.topology_id == topology_id, Link.id.in_(link_ids)).all()
    allowed_fields = {"color", "carrier", "cost", "latency_ms", "latency_source", "circuit_cost", "circuit_id", "is_planned"}
    if "latency_source" in updates and updates["latency_source"] not in LATENCY_SOURCES:
        raise HTTPException(400, f"latency_source must be one of {list(LATENCY_SOURCES)}")
    if "latency_ms" in updates:
        v = updates["latency_ms"]
        if v is None:
            # Clearing the value = unknown (estimate it).
            updates = {**{k: x for k, x in updates.items() if k != "latency_ms"}, "latency_source": "unknown"}
        elif not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            raise HTTPException(400, "latency_ms must be a non-negative number (or null to estimate)")
    for link in links:
        for key, val in updates.items():
            if key in allowed_fields:
                if key == "is_planned":
                    link.is_planned = 1 if val else 0
                else:
                    setattr(link, key, val)
        # Setting a latency value without a source means it's known.
        if "latency_ms" in updates and "latency_source" not in updates:
            link.latency_source = "measured" if updates["latency_ms"] > 0 else "unknown"
    db.commit()
    ctx = _latency_ctx(db, topology_id)
    return [_with_effective_latency(_link_to_dict(l), l, ctx) for l in links]


@router.put("/{link_id}", response_model=LinkOut)
def update_link(topology_id: int, link_id: int, data: LinkUpdate, db: Session = Depends(get_db)):
    link = db.query(Link).filter(Link.id == link_id, Link.topology_id == topology_id).first()
    if not link:
        # Distinguish "doesn't exist anywhere" from "lives in parent topology" (read-only here)
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        inherited = db.query(Link).filter(Link.id == link_id, Link.topology_id.in_(chain)).first()
        if inherited:
            raise HTTPException(403, "This link is inherited from a parent topology and is read-only here. Edit it in the root topology.")
        raise HTTPException(404, "Link not found")
    # Nullified by parent? A parent has its own official link between these
    # endpoints, so this planning link is no longer visible/editable.
    from app.services.topology_chain import get_nullified_child_link_ids
    if link.id in get_nullified_child_link_ids(db, topology_id):
        raise HTTPException(
            403,
            "A parent topology has its own link between these nodes — this planning link has been absorbed and is no longer editable. "
            "Edit the parent's version, or delete this one to clean up."
        )

    # If endpoints (node or interface) are actually being CHANGED, validate them.
    # Compare to the current values so an edit form that re-sends unchanged
    # endpoints (incl. links that legitimately have blank interfaces) can still
    # update other fields without tripping the "both interfaces required" check.
    endpoints_changed = (
        (data.source_node_id is not None and data.source_node_id != link.source_node_id)
        or (data.target_node_id is not None and data.target_node_id != link.target_node_id)
        or (data.source_interface is not None and data.source_interface != link.source_interface)
        or (data.target_interface is not None and data.target_interface != link.target_interface)
    )
    # Satellite rules: every dish ↔ PoP link is a satellite link (orbit
    # required, no interfaces needed); dishes can't link to each other.
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)

    def _node_in_chain(node_id):
        return db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id.in_(chain)).first()
    new_src = _node_in_chain(data.source_node_id or link.source_node_id)
    new_tgt = _node_in_chain(data.target_node_id or link.target_node_id)
    if new_src is None or new_tgt is None:
        raise HTTPException(404, "Source or target node not found")
    new_orbit = data.orbit if data.orbit is not None else (link.orbit or "")
    try:
        transport = validate_link_roles(new_src.role if new_src else None, new_tgt.role if new_tgt else None,
                                        data.transport or link.transport, new_orbit)
    except SatelliteLinkError as e:
        raise HTTPException(400, str(e))
    if transport == "satellite":
        if endpoints_changed:
            if new_src is None or new_tgt is None or new_src.id == new_tgt.id:
                raise HTTPException(400, "A satellite link needs a dish and a PoP")
            link.source_node_id, link.target_node_id = new_src.id, new_tgt.id
            link.source_interface = data.source_interface if data.source_interface is not None else link.source_interface
            link.target_interface = data.target_interface if data.target_interface is not None else link.target_interface
            endpoints_changed = False  # no interface / speed checks for satellite links
        if data.bandwidth_gbps is not None:
            if data.bandwidth_gbps <= 0:
                raise HTTPException(400, "Bandwidth must be greater than 0")
            link.bandwidth_gbps = data.bandwidth_gbps
        link.orbit = new_orbit
    else:
        link.orbit = ""
    link.transport = transport

    if endpoints_changed:
        src_node_id = data.source_node_id or link.source_node_id
        tgt_node_id = data.target_node_id or link.target_node_id
        src_iface = data.source_interface or link.source_interface
        tgt_iface = data.target_interface or link.target_interface
        if src_node_id == tgt_node_id:
            raise HTTPException(400, "Source and target nodes must be different")
        # Either end may live in a parent / grandparent topology (as on create).
        source_node, target_node = new_src, new_tgt

        # Build a temporary object for validation
        class TempData:
            pass
        temp = TempData()
        temp.source_interface = src_iface
        temp.target_interface = tgt_iface

        _, _, speed = _validate_link_interfaces(temp, topology_id, db, source_node, target_node, exclude_link_id=link_id)
        link.source_node_id = src_node_id
        link.target_node_id = tgt_node_id
        link.source_interface = src_iface
        link.target_interface = tgt_iface
        link.bandwidth_gbps = speed
        link.cost = REFERENCE_BANDWIDTH_GBPS / speed  # Recompute cost

    if data.is_planned is not None:
        link.is_planned = 1 if data.is_planned else 0
    if data.color is not None:
        link.color = data.color
    if data.latency_source is not None:
        if data.latency_source not in LATENCY_SOURCES:
            raise HTTPException(400, f"latency_source must be one of {list(LATENCY_SOURCES)}")
        link.latency_source = data.latency_source
    elif data.latency_ms is not None:
        link.latency_source = "measured"  # a value without a source is known
    for field in ["cost", "latency_ms", "carrier", "mtbf_hours", "mttr_hours", "circuit_cost", "circuit_id", "link_id_label", "source_ip", "target_ip", "source_ipv6", "target_ipv6"]:
        val = getattr(data, field)
        if val is not None:
            setattr(link, field, val)
    if data.sr_attributes is not None:
        link.sr_attributes_json = json.dumps(data.sr_attributes)
    if data.geo_path is not None:  # geo-view fiber route; [] clears it
        link.geo_path_json = json.dumps(data.geo_path) if data.geo_path else ""

    db.commit()
    db.refresh(link)
    try:
        from app.services.sync_worker import enqueue_sync
        enqueue_sync(db, topology_id, "link", link.id, "update", _link_sync_payload(link, db))
    except Exception:
        pass
    return _with_effective_latency(_link_to_dict(link), link, _latency_ctx(db, topology_id))


@router.delete("/{link_id}")
def delete_link(topology_id: int, link_id: int, db: Session = Depends(get_db)):
    link = db.query(Link).filter(Link.id == link_id, Link.topology_id == topology_id).first()
    if not link:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        inherited = db.query(Link).filter(Link.id == link_id, Link.topology_id.in_(chain)).first()
        if inherited:
            raise HTTPException(403, "This link is inherited from a parent topology and cannot be deleted here. Delete it in the root topology.")
        raise HTTPException(404, "Link not found")
    # Clean up SRLG memberships
    from app.models.srlg import SrlgMember
    db.query(SrlgMember).filter(SrlgMember.member_type == "link", SrlgMember.member_id == link_id).delete()
    from app.routes.patch_panels import strip_patch_panel_references
    strip_patch_panel_references(db, link_ids=[link_id])
    db.delete(link)
    db.commit()
    try:
        from app.services.sync_worker import enqueue_sync
        enqueue_sync(db, topology_id, "link", link_id, "delete", {"id": link_id})
    except Exception:
        pass
    return {"ok": True}

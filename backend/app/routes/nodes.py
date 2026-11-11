import json
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.schemas.schemas import NodeInstanceCreate, NodeInstanceUpdate, NodeInstanceOut

router = APIRouter(prefix="/api/topologies/{topology_id}/nodes", tags=["nodes"])

# Projection constants for converting lat/lng to canvas x/y
# Uses equirectangular projection centered roughly on continental US
SCALE = 18  # pixels per degree
LNG_OFFSET = 130  # shift so US mainland starts near x=0
LAT_OFFSET = 55   # shift so US mainland starts near y=0


def lat_lng_to_xy(lat: float, lng: float) -> tuple[float, float]:
    x = (lng + LNG_OFFSET) * SCALE
    y = (LAT_OFFSET - lat) * SCALE
    return round(x, 1), round(y, 1)


def xy_to_lat_lng(x: float, y: float) -> tuple[float, float]:
    lng = (x / SCALE) - LNG_OFFSET
    lat = LAT_OFFSET - (y / SCALE)
    return round(lat, 4), round(lng, 4)


def _strip_rr_peer_references(db: Session, node_id: int) -> int:
    """Remove node_id from every node's rr_peer_ids, across ALL topologies
    (a child topology's nodes may peer with an inherited ancestor RR). Called
    when a device stops being a route reflector — or is deleted — so client
    devices don't keep a stale peer reference that the edit-form picker can no
    longer display but the peer count / rendered config still uses.
    Returns how many nodes were cleaned."""
    cleaned = 0
    rows = db.query(NodeInstance).filter(
        NodeInstance.rr_peer_ids_json.isnot(None),
        NodeInstance.rr_peer_ids_json != "[]",
        NodeInstance.rr_peer_ids_json != "",
        # Substring prefilter so only plausibly-referencing rows are loaded;
        # false positives (id 15 vs 157) fall out of the exact check below.
        NodeInstance.rr_peer_ids_json.like(f"%{node_id}%"),
    ).all()
    for n in rows:
        peers = n.rr_peer_ids
        if node_id in peers:
            n.rr_peer_ids = [pid for pid in peers if pid != node_id]
            cleaned += 1
    return cleaned


def _validate_unique_attrs(db: Session, topology_id: int, node_id: int, new_attrs: dict) -> None:
    """Raise 409 if any unique-flagged custom attribute would collide with
    another node's value in the same topology (or its inheritance chain).

    Empty/None values are exempt — multiple nodes can leave an attribute
    unset without colliding. The check runs across the full topology_chain
    so the constraint holds even for nodes inherited from a parent topology.
    """
    if not new_attrs:
        return
    # Skip definitions that aren't flagged unique. Config Design may not be
    # enabled, in which case the table won't exist — bail silently then.
    try:
        from app.models.attribute_definitions import DeviceAttributeDefinition
        unique_keys = {
            d.key for d in db.query(DeviceAttributeDefinition).filter(
                DeviceAttributeDefinition.unique_per_topology == 1
            ).all()
        }
    except Exception:
        return
    keys_to_check = unique_keys & set(new_attrs.keys())
    if not keys_to_check:
        return

    # Look across this topology AND any parents — uniqueness should hold for
    # everything visible from the user's perspective.
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    others = db.query(NodeInstance).filter(
        NodeInstance.topology_id.in_(chain),
        NodeInstance.id != node_id,
    ).all()

    for key in keys_to_check:
        new_val = new_attrs.get(key)
        if new_val is None or new_val == "":
            continue
        # Normalise: integer attrs may arrive as "100" or 100; compare strings.
        nv = str(new_val).strip()
        if not nv:
            continue
        for other in others:
            other_val = other.custom_attrs.get(key)
            if other_val is None or other_val == "":
                continue
            if str(other_val).strip() == nv:
                raise HTTPException(
                    status_code=409,
                    detail=f"Value '{nv}' for '{key}' is already used by '{other.name}' — this attribute must be unique across the topology.",
                )


def _node_to_dict(n: NodeInstance, is_inherited: bool = False) -> dict:
    # Read each *_json column with a single json.loads instead of going
    # through the @property getters on NodeInstance, which would re-parse
    # the same JSON every access. On a large topology this trims the cost
    # of building the response from four json.loads/node to one each.
    try:
        interfaces = json.loads(n.interfaces_json) if n.interfaces_json else []
    except (ValueError, TypeError):
        interfaces = []
    try:
        rr_peer_ids = json.loads(n.rr_peer_ids_json) if n.rr_peer_ids_json else []
    except (ValueError, TypeError):
        rr_peer_ids = []
    try:
        custom_attrs = json.loads(n.custom_attrs_json) if n.custom_attrs_json else {}
    except (ValueError, TypeError):
        custom_attrs = {}
    try:
        custom_iface_refs = json.loads(n.custom_iface_refs_json) if n.custom_iface_refs_json else {}
    except (ValueError, TypeError):
        custom_iface_refs = {}
    return {
        "id": n.id, "topology_id": n.topology_id, "template_id": n.template_id,
        "name": n.name, "vendor": n.vendor, "model": n.model, "platform": n.platform,
        "x": n.x, "y": n.y,
        "latitude": n.latitude, "longitude": n.longitude,
        "geo_latitude": n.geo_latitude, "geo_longitude": n.geo_longitude,
        "interfaces": interfaces,
        "mtbf_hours": n.mtbf_hours, "mttr_hours": n.mttr_hours,
        "capacity_gbps": n.capacity_gbps, "location": n.location or "",
        "prometheus_id": n.prometheus_id or "",
        "mgmt_ip": n.mgmt_ip or "",
        "is_planned": bool(n.is_planned),
        "role": n.role or "router",
        "customer_id": n.customer_id,
        "color": n.color or "",
        "is_inherited": is_inherited,
        "is_route_reflector": bool(n.is_route_reflector),
        "is_super_route_reflector": bool(getattr(n, "is_super_route_reflector", 0)),
        "rr_peer_ids": rr_peer_ids,
        "custom_attrs": custom_attrs,
        "custom_iface_refs": custom_iface_refs,
    }


@router.get("/", response_model=list[NodeInstanceOut])
def list_nodes(topology_id: int, db: Session = Depends(get_db)):
    """List nodes for a topology, including those inherited from parent topologies.

    When a child has a local node whose name was later claimed by a parent
    addition, the local one is silently shadowed — only the parent's version
    is returned, marked is_inherited=True.

    Inherited nodes also pick up any local position override the child has
    recorded for them, so children can lay out the canvas independently.
    """
    from app.services.topology_chain import get_topology_chain, merge_by_name
    from app.models.node_position_override import NodePositionOverride
    chain = get_topology_chain(db, topology_id)
    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    visible, _ = merge_by_name(all_nodes, chain)
    overrides = db.query(NodePositionOverride).filter(
        NodePositionOverride.topology_id == topology_id
    ).all()
    override_map = {o.node_id: o for o in overrides}
    result = []
    for n in visible:
        d = _node_to_dict(n, is_inherited=(n.topology_id != topology_id))
        o = override_map.get(n.id)
        if o is not None:
            # Logical layout is overridable per child (canvas independence).
            d["x"] = o.x
            d["y"] = o.y
            d["latitude"] = o.latitude
            d["longitude"] = o.longitude
            # Geographic coordinates are NOT overridden per child — an inherited
            # node's geo always reflects the parent (set in the parent topology).
        result.append(d)
    return result


@router.post("/", response_model=NodeInstanceOut)
def create_node(topology_id: int, data: NodeInstanceCreate, db: Session = Depends(get_db)):
    # Name must be unique across the inheritance chain (root + all ancestors), not just this topology
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    existing = db.query(NodeInstance).filter(
        NodeInstance.topology_id.in_(chain), NodeInstance.name == data.name
    ).first()
    if existing:
        if existing.topology_id == topology_id:
            raise HTTPException(409, f"A node named '{data.name}' already exists in this topology")
        raise HTTPException(409, f"A node named '{data.name}' is inherited from a parent topology — pick a different name")
    ifaces = data.interfaces
    vendor = data.vendor
    model = data.model
    platform = data.platform
    mtbf = data.mtbf_hours
    mttr = data.mttr_hours
    capacity = data.capacity_gbps

    if data.template_id:
        tmpl = db.query(NodeTemplate).filter(NodeTemplate.id == data.template_id).first()
        if tmpl:
            if not ifaces:
                ifaces = [{"name": i["name"], "speed_gbps": i["speed_gbps"], "description": i.get("description", "")} for i in tmpl.default_interfaces]
            vendor = vendor or tmpl.vendor
            model = model or tmpl.model
            platform = platform or tmpl.platform
            mtbf = mtbf if mtbf is not None else tmpl.default_mtbf_hours
            mttr = mttr if mttr is not None else tmpl.default_mttr_hours
            capacity = capacity if capacity is not None else tmpl.capacity_gbps
            if data.role == "router" and tmpl.role and tmpl.role != "router":
                data.role = tmpl.role

    # No template + no explicit capacity → fall back to the same default
    # the NodeInstance model would otherwise have applied (100 Gbps).
    if capacity is None:
        capacity = 100.0

    # Compute x/y from lat/lng if provided
    x, y = data.x, data.y
    lat, lng = data.latitude, data.longitude
    if lat is not None and lng is not None:
        x, y = lat_lng_to_xy(lat, lng)

    # Enforce uniqueness for any flagged custom attrs before creating the row.
    # node_id=0 here since the new node has no id yet — _validate_unique_attrs
    # treats the sentinel as "no node to exclude", so the check sees every
    # existing node in the chain.
    _validate_unique_attrs(db, topology_id, 0, data.custom_attrs or {})

    node = NodeInstance(
        topology_id=topology_id, template_id=data.template_id, name=data.name,
        vendor=vendor, model=model, platform=platform,
        x=x, y=y, latitude=lat, longitude=lng,
        geo_latitude=data.geo_latitude, geo_longitude=data.geo_longitude,
        interfaces_json=json.dumps([i.model_dump() if hasattr(i, 'model_dump') else i for i in ifaces]),
        mtbf_hours=mtbf, mttr_hours=mttr, capacity_gbps=capacity,
        location=data.location,
        prometheus_id=data.prometheus_id,
        mgmt_ip=data.mgmt_ip,
        is_planned=1 if data.is_planned else 0,
        role=data.role or "router",
        customer_id=data.customer_id,
        color=data.color or "",
        is_route_reflector=1 if (data.is_route_reflector or data.is_super_route_reflector) else 0,
        is_super_route_reflector=1 if data.is_super_route_reflector else 0,
        rr_peer_ids_json=json.dumps([int(pid) for pid in data.rr_peer_ids]),
        custom_attrs_json=json.dumps(data.custom_attrs or {}),
        custom_iface_refs_json=json.dumps(data.custom_iface_refs or {}),
    )
    # Customer nodes default to MTTR=0 (don't impact availability)
    if node.role == "customer" and node.mttr_hours is None:
        node.mttr_hours = 0
    db.add(node)
    db.commit()
    db.refresh(node)
    # Trigger external sync
    try:
        from app.services.sync_worker import enqueue_sync
        enqueue_sync(db, topology_id, "node", node.id, "create", _node_to_dict(node))
    except Exception:
        pass
    return _node_to_dict(node)


@router.put("/bulk-update")
def bulk_update_nodes(topology_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Update multiple nodes at once (e.g. bulk-color a selection). Accepts
    node_ids and a small set of fields to set. Only local nodes are touched —
    inherited nodes (different topology_id) won't match and are left alone."""
    node_ids = body.get("node_ids", [])
    updates = body.get("updates", {})
    if not node_ids or not updates:
        raise HTTPException(400, "node_ids and updates are required")
    nodes = db.query(NodeInstance).filter(
        NodeInstance.topology_id == topology_id, NodeInstance.id.in_(node_ids)
    ).all()
    allowed_fields = {"color", "is_planned"}
    for node in nodes:
        for key, val in updates.items():
            if key in allowed_fields:
                if key == "is_planned":
                    node.is_planned = 1 if val else 0
                else:
                    setattr(node, key, val)
    db.commit()
    return [_node_to_dict(n) for n in nodes]


def _check_role_change_keeps_links_valid(db: Session, topology_id: int, node: NodeInstance, new_role: str) -> None:
    """Refuse a role change that would leave the node's links breaking the
    satellite rules (e.g. a router↔router link turning into dish↔PoP with no
    orbit). The user removes or re-makes those links first."""
    from app.models.link import Link
    from app.services.satellite import validate_link_roles, SatelliteLinkError
    from app.services.topology_chain import get_topology_chain
    from app.services.topology_chain import get_nullified_child_link_ids
    chain = get_topology_chain(db, topology_id)
    links = db.query(Link).filter(Link.topology_id.in_(chain),
                                  (Link.source_node_id == node.id) | (Link.target_node_id == node.id)).all()
    hidden = get_nullified_child_link_ids(db, topology_id)  # absorbed links aren't shown, so don't block on them
    broken = []
    for l in links:
        if l.id in hidden:
            continue
        other_id = l.target_node_id if l.source_node_id == node.id else l.source_node_id
        other = db.get(NodeInstance, other_id)
        try:
            transport = validate_link_roles(new_role, other.role if other else None, l.transport, l.orbit)
        except SatelliteLinkError:
            broken.append(other.name if other else f"node {other_id}")
            continue
        if transport != (l.transport or "terrestrial"):
            broken.append(other.name if other else f"node {other_id}")
    if broken:
        raise HTTPException(400, f"Can't change {node.name} to '{new_role}': its link(s) to {', '.join(sorted(broken))} "
                                 "would no longer fit (satellite links must be Satellite Dish ↔ Satellite PoP). "
                                 "Delete or re-create those links first.")


@router.put("/{node_id}", response_model=NodeInstanceOut)
def update_node(topology_id: int, node_id: int, data: NodeInstanceUpdate, db: Session = Depends(get_db)):
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id == topology_id).first()
    if not node:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        inherited = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id.in_(chain)).first()
        if inherited:
            # Position-only updates on inherited nodes are allowed — they
            # become a per-child position override, decoupling layout from
            # the parent without modifying any other attribute.
            fields_set = data.model_dump(exclude_unset=True)
            position_keys = {"x", "y", "latitude", "longitude", "geo_latitude", "geo_longitude"}
            if fields_set and set(fields_set.keys()).issubset(position_keys):
                # Geographic coordinates of an inherited node are owned by the
                # parent topology — they can't be set/overridden from a child.
                # Logical (x/y/lat/long) layout overrides remain allowed so
                # children can still arrange the canvas independently.
                if "geo_latitude" in fields_set or "geo_longitude" in fields_set:
                    raise HTTPException(
                        403,
                        "Geographic coordinates of an inherited node are set in the parent topology. "
                        "Edit the node's geographic position there.",
                    )
                from app.models.node_position_override import NodePositionOverride
                override = db.query(NodePositionOverride).filter(
                    NodePositionOverride.topology_id == topology_id,
                    NodePositionOverride.node_id == node_id,
                ).first()
                if not override:
                    override = NodePositionOverride(topology_id=topology_id, node_id=node_id)
                    # Seed from the inherited node's current logical position so a
                    # partial update doesn't zero out the other axis.
                    override.x = inherited.x
                    override.y = inherited.y
                    override.latitude = inherited.latitude
                    override.longitude = inherited.longitude
                    db.add(override)
                if data.latitude is not None and data.longitude is not None:
                    x, y = lat_lng_to_xy(data.latitude, data.longitude)
                    override.latitude = data.latitude
                    override.longitude = data.longitude
                    override.x = x
                    override.y = y
                elif data.x is not None and data.y is not None:
                    lat, lng = xy_to_lat_lng(data.x, data.y)
                    override.x = data.x
                    override.y = data.y
                    override.latitude = lat
                    override.longitude = lng
                db.commit()
                db.refresh(override)
                d = _node_to_dict(inherited, is_inherited=True)
                d["x"] = override.x
                d["y"] = override.y
                d["latitude"] = override.latitude
                d["longitude"] = override.longitude
                # geo stays as the parent's (from _node_to_dict) — inherited geo
                # is parent-owned and never overridden per child.
                return d
            raise HTTPException(
                403,
                "This node is inherited from a parent topology and is read-only here. "
                "Only position changes (drag/move) are accepted, and are recorded as a local override."
            )
        raise HTTPException(404, "Node not found")
    # Shadowed-by-parent case: a parent later added a node with the same name.
    # The local copy is now read-only EXCEPT for renaming — let the user rename
    # it away from the conflict if they want to keep it as a separate node.
    from app.services.topology_chain import get_topology_chain, merge_by_name
    chain = get_topology_chain(db, topology_id)
    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    _, node_remap = merge_by_name(all_nodes, chain)
    if node.id in node_remap:
        fields_set = data.model_dump(exclude_unset=True)
        is_rename_only = (
            len(fields_set) == 1 and "name" in fields_set
            and data.name and data.name != node.name
        )
        if not is_rename_only:
            raise HTTPException(
                403,
                f"A node named '{node.name}' was added in a parent topology — this local copy has been absorbed and is now read-only. "
                f"Either edit the parent's version, or rename this one (name only) to keep it as a separate local node."
            )
    if data.name is not None and data.name != node.name:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        conflict = db.query(NodeInstance).filter(
            NodeInstance.topology_id.in_(chain),
            NodeInstance.name == data.name,
            NodeInstance.id != node_id,
        ).first()
        if conflict:
            if conflict.topology_id == topology_id:
                raise HTTPException(409, f"A node named '{data.name}' already exists in this topology")
            raise HTTPException(409, f"A node named '{data.name}' is inherited from a parent topology — pick a different name")

    # If lat/lng are being updated, recompute x/y
    if data.latitude is not None and data.longitude is not None:
        x, y = lat_lng_to_xy(data.latitude, data.longitude)
        node.latitude = data.latitude
        node.longitude = data.longitude
        node.x = x
        node.y = y
    # If x/y are being updated (drag), recompute lat/lng
    elif data.x is not None and data.y is not None:
        lat, lng = xy_to_lat_lng(data.x, data.y)
        node.x = data.x
        node.y = data.y
        node.latitude = lat
        node.longitude = lng

    # Geographic coordinates are standalone (map-view placement) — set them
    # directly with no projection to/from x/y or the logical lat/lng above.
    if data.geo_latitude is not None and data.geo_longitude is not None:
        node.geo_latitude = data.geo_latitude
        node.geo_longitude = data.geo_longitude

    for field in ["name", "vendor", "model", "platform", "mtbf_hours", "mttr_hours", "capacity_gbps", "location", "prometheus_id", "mgmt_ip", "color"]:
        val = getattr(data, field)
        if val is not None:
            setattr(node, field, val)
    if data.is_planned is not None:
        node.is_planned = 1 if data.is_planned else 0
    if data.role is not None:
        if data.role != (node.role or "router"):
            _check_role_change_keeps_links_valid(db, topology_id, node, data.role)
        node.role = data.role
    if data.customer_id is not None:
        # Flows touching this node follow it to the new customer (derived —
        # see services/customer_flows.py).
        node.customer_id = data.customer_id if data.customer_id != 0 else None
    if data.interfaces is not None:
        node.interfaces_json = json.dumps([i.model_dump() for i in data.interfaces])
    # Config Design — route reflection + custom attribute writes.
    was_rr = bool(node.is_route_reflector)
    if data.is_route_reflector is not None:
        node.is_route_reflector = 1 if data.is_route_reflector else 0
    if data.is_super_route_reflector is not None:
        node.is_super_route_reflector = 1 if data.is_super_route_reflector else 0
        # Super RR implies RR — even if the client didn't also send is_route_reflector.
        if data.is_super_route_reflector:
            node.is_route_reflector = 1
    # Demoted from RR → strip this node from every other device's peer list,
    # or clients keep a stale reference the picker can't even display.
    if was_rr and not node.is_route_reflector:
        _strip_rr_peer_references(db, node.id)
    if data.rr_peer_ids is not None:
        # Filter to ints, drop self-references.
        node.rr_peer_ids = [int(pid) for pid in data.rr_peer_ids if int(pid) != node.id]
    if data.custom_attrs is not None:
        # Enforce per-topology uniqueness on any attribute flagged for it.
        _validate_unique_attrs(db, topology_id, node.id, data.custom_attrs)
        node.custom_attrs = data.custom_attrs
    if data.custom_iface_refs is not None:
        # Drop entries whose interface_name no longer exists on this device.
        valid_names = {i.get("name") for i in node.interfaces}
        node.custom_iface_refs = {k: v for k, v in data.custom_iface_refs.items() if v in valid_names or v == ""}

    db.commit()
    db.refresh(node)

    # Auto-regenerate Prometheus configs if topology uses built-in mode and mgmt_ip changed
    if data.mgmt_ip is not None:
        from app.models.prometheus_config import PrometheusConfig
        pcfg = db.query(PrometheusConfig).filter(
            PrometheusConfig.topology_id == topology_id, PrometheusConfig.mode == "builtin"
        ).first()
        if pcfg:
            from app.services.prometheus_config_generator import regenerate_prometheus_configs
            try:
                regenerate_prometheus_configs(db)
            except Exception:
                pass

    # Trigger external sync
    try:
        from app.services.sync_worker import enqueue_sync
        enqueue_sync(db, topology_id, "node", node.id, "update", _node_to_dict(node))
    except Exception:
        pass

    return _node_to_dict(node)


@router.get("/{node_id}/interfaces")
def get_node_interfaces(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    """Get full interface details for a node: status, link connections, and flow associations.

    Walks the inheritance chain so interfaces used by inherited links/nodes are visible.
    """
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id.in_(chain)).first()
    if not node:
        raise HTTPException(404, "Node not found")

    from app.models.link import Link
    from app.models.flow import Flow

    # Build link map: interface_name → link info (walk chain for inherited links)
    links = db.query(Link).filter(Link.topology_id.in_(chain)).all()
    link_map = {}
    for l in links:
        if l.source_node_id == node_id and l.source_interface:
            peer = db.query(NodeInstance).filter(NodeInstance.id == l.target_node_id).first()
            link_map[l.source_interface] = {"link_id": l.id, "peer_node_id": l.target_node_id, "peer_name": peer.name if peer else None, "peer_interface": l.target_interface}
        if l.target_node_id == node_id and l.target_interface:
            peer = db.query(NodeInstance).filter(NodeInstance.id == l.source_node_id).first()
            link_map[l.target_interface] = {"link_id": l.id, "peer_node_id": l.source_node_id, "peer_name": peer.name if peer else None, "peer_interface": l.source_interface}

    # Build flow map: interface_name → list of flow info (walk chain for inherited flows)
    node_flows = db.query(Flow).filter(Flow.topology_id.in_(chain)).filter(
        (Flow.source_node_id == node_id) | (Flow.dest_node_id == node_id)
    ).all()
    flow_map: dict[str, list] = {}
    for f in node_flows:
        if f.source_node_id == node_id and f.source_interface:
            flow_map.setdefault(f.source_interface, []).append({
                "flow_id": f.id, "flow_name": f.name, "direction": "source",
                "other_node_id": f.dest_node_id,
            })
        if f.dest_node_id == node_id and f.dest_interface:
            flow_map.setdefault(f.dest_interface, []).append({
                "flow_id": f.id, "flow_name": f.name, "direction": "dest",
                "other_node_id": f.source_node_id,
            })

    result = []
    for iface in node.interfaces:
        name = iface["name"]
        result.append({
            "name": name,
            "speed_gbps": iface.get("speed_gbps", 10),
            "description": iface.get("description", ""),
            "status": iface.get("status", "free"),
            "link": link_map.get(name),
            "flows": flow_map.get(name, []),
        })
    return {"node_id": node_id, "node_name": node.name, "interfaces": result}


@router.patch("/{node_id}/interfaces/{interface_name}")
def update_node_interface(topology_id: int, node_id: int, interface_name: str, data: dict, db: Session = Depends(get_db)):
    """Update a single interface's properties (status, etc.) without replacing the full interface list."""
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id == topology_id).first()
    if not node:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        inherited = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id.in_(chain)).first()
        if inherited:
            raise HTTPException(403, "This node is inherited from a parent topology and is read-only here.")
        raise HTTPException(404, "Node not found")
    # Block interface edits on a shadowed local node
    from app.services.topology_chain import get_topology_chain, merge_by_name
    chain = get_topology_chain(db, topology_id)
    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    _, node_remap = merge_by_name(all_nodes, chain)
    if node.id in node_remap:
        raise HTTPException(403, f"A node named '{node.name}' was added in a parent topology — this local copy is now read-only.")

    interfaces = node.interfaces
    found = False
    for iface in interfaces:
        if iface["name"] == interface_name:
            for key in ["status", "description"]:
                if key in data:
                    iface[key] = data[key]
            found = True
            break
    if not found:
        raise HTTPException(404, f"Interface '{interface_name}' not found on node")

    node.interfaces_json = json.dumps(interfaces)
    db.commit()
    db.refresh(node)
    return {"ok": True, "interface": next(i for i in node.interfaces if i["name"] == interface_name)}


@router.delete("/{node_id}")
def delete_node(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id == topology_id).first()
    if not node:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        inherited = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id.in_(chain)).first()
        if inherited:
            raise HTTPException(403, "This node is inherited from a parent topology and cannot be deleted here. Delete it in the root topology.")
        raise HTTPException(404, "Node not found")
    # Clean up SRLG memberships and any per-child position overrides for the node
    from app.models.srlg import SrlgMember
    from app.models.node_position_override import NodePositionOverride
    db.query(SrlgMember).filter(SrlgMember.member_type == "node", SrlgMember.member_id == node_id).delete()
    db.query(NodePositionOverride).filter(NodePositionOverride.node_id == node_id).delete()
    # Strip this node from any device's RR peer list (stale ids otherwise
    # inflate peer counts and leak into rendered configs).
    _strip_rr_peer_references(db, node_id)
    # Delete all connected links (and their SRLG memberships) to free up interfaces on neighbor nodes
    from app.models.link import Link
    connected_links = db.query(Link).filter(
        Link.topology_id == topology_id,
        (Link.source_node_id == node_id) | (Link.target_node_id == node_id),
    ).all()
    deleted_link_ids = []
    for link in connected_links:
        db.query(SrlgMember).filter(SrlgMember.member_type == "link", SrlgMember.member_id == link.id).delete()
        deleted_link_ids.append(link.id)
        db.delete(link)
    # Patch-panel ports documenting this node/its links keep their status/notes
    # but lose the dead references.
    from app.routes.patch_panels import strip_patch_panel_references
    strip_patch_panel_references(db, node_ids=[node_id], link_ids=deleted_link_ids)
    db.delete(node)
    db.commit()
    # Trigger external sync (deletes for cascaded links and the node itself)
    try:
        from app.services.sync_worker import enqueue_sync
        for lid in deleted_link_ids:
            enqueue_sync(db, topology_id, "link", lid, "delete", {"id": lid})
        enqueue_sync(db, topology_id, "node", node_id, "delete", {"id": node_id})
    except Exception:
        pass
    return {"ok": True}

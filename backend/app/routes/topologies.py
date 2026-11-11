import html
import json
from typing import Optional
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.topology import Topology
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.models.link import Link
from app.models.flow import Flow
from app.services.customer_flows import node_customer_map, flow_customer_ids
from app.services.latency import infer_latency_source, latency_infos
from app.schemas.schemas import TopologyCreate, TopologyOut
from app.routes.edit_session import require_edit_session
from app.models.user import User
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/topologies", tags=["topologies"])


@router.get("/", response_model=list[TopologyOut])
def list_topologies(db: Session = Depends(get_db)):
    counts = dict(
        db.query(NodeInstance.topology_id, func.count(NodeInstance.id))
        .filter(or_(NodeInstance.role.is_(None), NodeInstance.role.notin_(("customer", "site"))))
        .group_by(NodeInstance.topology_id)
        .all()
    )
    out = []
    for t in db.query(Topology).all():
        item = TopologyOut.model_validate(t)
        item.device_count = counts.get(t.id, 0)
        out.append(item)
    return out


@router.post("/", response_model=TopologyOut)
def create_topology(data: TopologyCreate, db: Session = Depends(get_db)):
    if data.parent_topology_id is not None:
        parent = db.query(Topology).filter(Topology.id == data.parent_topology_id).first()
        if not parent:
            raise HTTPException(400, f"Parent topology {data.parent_topology_id} not found")
    topo = Topology(
        name=data.name, description=data.description,
        parent_topology_id=data.parent_topology_id,
    )
    db.add(topo)
    db.commit()
    db.refresh(topo)
    return topo


@router.get("/{topology_id}", response_model=TopologyOut)
def get_topology(topology_id: int, db: Session = Depends(get_db)):
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    return topo


@router.put("/{topology_id}", response_model=TopologyOut)
def update_topology(topology_id: int, data: TopologyCreate, db: Session = Depends(get_db),
                    _: None = Depends(require_edit_session)):
    from app.services.topology_chain import would_create_cycle
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    # Patch semantics: only update fields the client actually sent. The
    # rename path sends just {name, description}; without exclude_unset
    # Pydantic would default parent_topology_id to None and silently detach
    # the child from its parent.
    fields = data.model_dump(exclude_unset=True)
    if "name" in fields:
        topo.name = fields["name"]
    if "description" in fields:
        topo.description = fields["description"]
    if "parent_topology_id" in fields:
        new_parent = fields["parent_topology_id"]
        if new_parent is not None and new_parent != topo.parent_topology_id:
            parent = db.query(Topology).filter(Topology.id == new_parent).first()
            if not parent:
                raise HTTPException(400, f"Parent topology {new_parent} not found")
            if would_create_cycle(db, topology_id, new_parent):
                raise HTTPException(400, "Cannot set parent: would create a cycle in the topology chain")
        topo.parent_topology_id = new_parent
    db.commit()
    db.refresh(topo)
    return topo


@router.put("/{topology_id}/protection", response_model=TopologyOut)
def set_protection(topology_id: int, body: dict, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """Toggle a topology's protected flag. Admin-only, and deliberately NOT
    behind the edit-session guard — admins manage protection from the home
    screen without entering edit mode. Unprotecting ends any active session."""
    if not current_user.is_admin:
        raise HTTPException(403, "Only an administrator can change topology protection.")
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    next_val = 1 if body.get("is_protected") else 0
    if topo.is_protected and not next_val:
        from app.routes.edit_session import _clear_session
        _clear_session(topo, db)
    topo.is_protected = next_val
    db.commit()
    db.refresh(topo)
    return topo


@router.delete("/{topology_id}")
def delete_topology(topology_id: int, db: Session = Depends(get_db),
                    _: None = Depends(require_edit_session)):
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    # Protected topologies are never deleted, even in edit mode — an admin
    # must unprotect it first.
    if topo.is_protected:
        raise HTTPException(409, f"'{topo.name}' is protected — unprotect it before deleting.")
    # Prevent deletion if other topologies inherit from this one
    children = db.query(Topology).filter(Topology.parent_topology_id == topology_id).all()
    if children:
        names = ", ".join(c.name for c in children)
        raise HTTPException(
            400,
            f"Cannot delete: {len(children)} topology/topologies inherit from this one ({names}). "
            f"Re-parent or delete them first."
        )
    # Drop any local position overrides this topology had on inherited nodes
    # (SQLite-friendly explicit cleanup — doesn't rely on FK ON DELETE CASCADE).
    from app.models.node_position_override import NodePositionOverride
    db.query(NodePositionOverride).filter(NodePositionOverride.topology_id == topology_id).delete()
    from app.routes.patch_panels import delete_topology_panels
    delete_topology_panels(db, topology_id)
    from app.models.interface_metric import InterfaceMetric
    db.query(InterfaceMetric).filter(InterfaceMetric.topology_id == topology_id).delete()
    db.delete(topo)
    db.commit()
    return {"ok": True}


@router.post("/{topology_id}/duplicate", response_model=TopologyOut)
def duplicate_topology(topology_id: int, db: Session = Depends(get_db)):
    orig = db.query(Topology).filter(Topology.id == topology_id).first()
    if not orig:
        raise HTTPException(404, "Topology not found")

    # Round-trip through the complete, schema-drift-guarded serialize/import
    # (tests/test_roundtrip_coverage.py) so a duplicate carries EVERYTHING —
    # RR attributes, custom attributes, colors, geo coords, customers, SRLGs —
    # rather than a hand-picked subset that silently drops fields.
    snapshot = _serialize_topology(topology_id, db)
    snapshot["topology"]["name"] = f"{orig.name} (copy)"
    return _import_topology_data(snapshot, db)


def _parse_json_list(val):
    """Best-effort parse of a JSON-array column; [] on null/garbage."""
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_json_obj(val):
    """Best-effort parse of a JSON-object column; {} on null/garbage."""
    if not val:
        return {}
    try:
        out = json.loads(val)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _node_export_dict(n, template_name, customer_name, node_name_map=None) -> dict:
    """Serialize one node to the export shape (shared by raw + merged exports).

    Every persistent NodeInstance column is round-tripped (see the schema-drift
    guard in tests/test_roundtrip_coverage.py). Referential fields are emitted
    by NAME so they survive a delete-and-recreate restore: template_name,
    customer_name, and rr_peer_names (peer node ids -> names via node_name_map).
    """
    return {
        "id": n.id,
        "name": n.name,
        "template_name": template_name,
        "vendor": n.vendor or "", "model": n.model or "", "platform": n.platform or "",
        "x": n.x, "y": n.y,
        "latitude": n.latitude, "longitude": n.longitude,
        "geo_latitude": n.geo_latitude, "geo_longitude": n.geo_longitude,
        "location": n.location or "",
        "interfaces": _parse_json_list(n.interfaces_json),
        "mtbf_hours": n.mtbf_hours, "mttr_hours": n.mttr_hours,
        "capacity_gbps": n.capacity_gbps,
        "prometheus_id": n.prometheus_id or "",
        "mgmt_ip": n.mgmt_ip or "",
        "is_planned": bool(n.is_planned),
        "role": n.role or "router",
        "customer_name": customer_name,
        "color": n.color or "",
        "is_route_reflector": bool(n.is_route_reflector),
        "is_super_route_reflector": bool(n.is_super_route_reflector),
        # Peers by NAME (ids aren't stable across a rebuild). Unresolvable peers
        # are dropped from the list; the node itself is never affected.
        "rr_peer_names": ([node_name_map.get(pid) for pid in n.rr_peer_ids if node_name_map and node_name_map.get(pid)]
                          if node_name_map else []),
        "custom_attrs": _parse_json_obj(n.custom_attrs_json),
        "custom_iface_refs": _parse_json_obj(n.custom_iface_refs_json),
        # Config-template assignments reference global (non-topology) rows that a
        # restore doesn't delete, so raw ids stay valid within an instance.
        "config_template_id": n.config_template_id,
        "config_template_group_id": n.config_template_group_id,
    }


def _latency_display(info) -> str:
    """Offline-viewer latency text: "12 ms", "~14.2 ms (estimated)" or "unknown"."""
    if info.ms is None:
        return "unknown"
    ms = f"{info.ms:.3g}"
    return f"~{ms} ms (estimated)" if info.source == "estimated" else f"{ms} ms"


def _link_export_dict(l, node_name_map) -> dict:
    """Serialize one link to the export shape."""
    return {
        "id": l.id,
        "source_node": node_name_map.get(l.source_node_id, ""),
        "target_node": node_name_map.get(l.target_node_id, ""),
        "source_interface": l.source_interface or "",
        "target_interface": l.target_interface or "",
        "bandwidth_gbps": l.bandwidth_gbps,
        "cost": l.cost, "latency_ms": l.latency_ms,
        "latency_source": l.latency_source or "unknown",
        "transport": l.transport or "terrestrial",
        "orbit": l.orbit or "",
        "carrier": l.carrier or "",
        "mtbf_hours": l.mtbf_hours, "mttr_hours": l.mttr_hours,
        "sr_attributes": _parse_json_list(l.sr_attributes_json),
        "circuit_cost": l.circuit_cost or 0,
        "circuit_id": l.circuit_id or "",
        "link_id_label": l.link_id_label or "",
        "source_ip": l.source_ip or "",
        "target_ip": l.target_ip or "",
        "source_ipv6": l.source_ipv6 or "",
        "target_ipv6": l.target_ipv6 or "",
        "is_planned": bool(l.is_planned),
        "color": l.color or "",
        # Geographic fiber route (list of coordinate paths) — carried as-is.
        "geo_path": _parse_json_list(l.geo_path_json),
    }


def _flow_customer_name(f, node_customer, customer_name_map) -> Optional[str]:
    """Display name of the customer(s) owning a flow's endpoint nodes
    ("A / B" for a flow between two customers), or None."""
    if f is None:
        return None
    names = [customer_name_map[c] for c in flow_customer_ids(f, node_customer) if c in customer_name_map]
    return " / ".join(names) or None


def _flow_export_dict(f, node_name_map, customer_name, include_report=False, link_key_by_id=None) -> dict:
    """Serialize one flow to the export shape. include_report attaches the
    persisted per-flow Monte Carlo availability report (HTML export only)."""
    d = {
        "id": f.id,
        "name": f.name,
        "description": f.description or "",
        "source_node": node_name_map.get(f.source_node_id, ""),
        "dest_node": node_name_map.get(f.dest_node_id, ""),
        "source_interface": f.source_interface or "",
        "dest_interface": f.dest_interface or "",
        "bandwidth_gbps": f.bandwidth_gbps,
        "routing_behavior": f.routing_behavior or "shortest_path",
        "ecmp_enabled": bool(f.ecmp_enabled),
        "explicit_path_nodes": [node_name_map.get(nid, str(nid)) for nid in _parse_json_list(f.explicit_path_json)],
        # Pinned explicit links by identity key (endpoints+ifaces), so they
        # re-resolve to the recreated links on restore. Unresolvable keys drop.
        "explicit_link_keys": ([link_key_by_id[lid] for lid in _parse_json_list(f.explicit_link_ids_json)
                                if link_key_by_id and lid in link_key_by_id]
                               if link_key_by_id else []),
        "customer_name": customer_name,
    }
    if include_report:
        d["availability_report"] = f.availability_report
    return d


def _serialize_topology(topology_id: int, db: Session) -> dict:
    """Serialize a complete topology with all its data for export."""
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")

    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).all()
    flows = db.query(Flow).filter(Flow.topology_id == topology_id).all()

    from app.models.customer import Customer
    from app.models.srlg import SrlgGroup, SrlgMember
    from app.models.node_position_override import NodePositionOverride
    from app.models.topology_view import TopologyView
    customers = db.query(Customer).filter(Customer.topology_id == topology_id).all()
    srlg_groups = db.query(SrlgGroup).filter(SrlgGroup.topology_id == topology_id).all()
    overrides = db.query(NodePositionOverride).filter(NodePositionOverride.topology_id == topology_id).all()
    views = db.query(TopologyView).filter(TopologyView.topology_id == topology_id).all()
    from app.models.patch_panel import PatchPanel
    panels = db.query(PatchPanel).filter(PatchPanel.topology_id == topology_id).all()

    # Also export referenced node templates
    template_ids = {n.template_id for n in nodes if n.template_id}
    templates = db.query(NodeTemplate).filter(NodeTemplate.id.in_(template_ids)).all() if template_ids else []
    template_name_by_id = {t.id: t.name for t in templates}

    _parse_json = _parse_json_list
    # Resolve endpoint names across the FULL inheritance chain: a child's own
    # links/flows may reference inherited (ancestor) nodes, so a child-only map
    # would serialize those endpoints as "" and lose them on restore.
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    if len(chain) > 1:
        chain_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
        node_name_map = {n.id: n.name for n in chain_nodes}
    else:
        node_name_map = {n.id: n.name for n in nodes}
    customer_name_map = {c.id: c.name for c in customers}
    flow_node_customer = node_customer_map(nodes)
    # Build link label map for SRLG member resolution
    link_label_map = {}
    for l in links:
        src = node_name_map.get(l.source_node_id, "?")
        tgt = node_name_map.get(l.target_node_id, "?")
        link_label_map[l.id] = f"{src}||{tgt}||{l.source_interface}||{l.target_interface}"

    return {
        "version": "1.0",
        "topology": {
            "name": topo.name,
            "description": topo.description or "",
        },
        "node_templates": [
            {
                "name": t.name, "vendor": t.vendor, "model": t.model,
                "platform": t.platform or "", "capacity_gbps": t.capacity_gbps,
                "default_mtbf_hours": t.default_mtbf_hours,
                "default_mttr_hours": t.default_mttr_hours,
                "default_interfaces": _parse_json(t.default_interfaces_json),
                "created_by": t.created_by or "",
            }
            for t in templates
        ],
        "nodes": [
            _node_export_dict(
                n, template_name_by_id.get(n.template_id),
                customer_name_map.get(n.customer_id) if n.customer_id else None,
                node_name_map=node_name_map,
            )
            for n in nodes
        ],
        "links": [_link_export_dict(l, node_name_map) for l in links],
        "flows": [
            _flow_export_dict(f, node_name_map, _flow_customer_name(f, flow_node_customer, customer_name_map),
                              link_key_by_id=link_label_map)
            for f in flows
        ],
        "customers": [
            {
                "name": c.name, "contact_email": c.contact_email or "",
                "contact_phone": c.contact_phone or "", "notes": c.notes or "",
                "color": c.color or "",
            }
            for c in customers
        ],
        "srlgs": [
            {
                "name": sg.name, "description": sg.description or "",
                "srlg_type": sg.srlg_type or "custom",
                "members": [
                    {"member_type": m.member_type, "member_ref": node_name_map.get(m.member_id, "") if m.member_type == "node" else link_label_map.get(m.member_id, "")}
                    for m in sg.members
                ],
            }
            for sg in srlg_groups
        ],
        # Layout/view state for full-fidelity snapshots (used by protected-edit
        # commit/discard). Position overrides target inherited nodes (stable ids);
        # views/default-exclusion reference local node ids that change on restore,
        # so _local_node_names lets _restore_into_topology remap them by name.
        "position_overrides": [
            {"node_id": o.node_id, "x": o.x, "y": o.y, "latitude": o.latitude,
             "longitude": o.longitude, "geo_latitude": o.geo_latitude, "geo_longitude": o.geo_longitude}
            for o in overrides
        ],
        "topology_views": [
            {"name": v.name, "description": v.description or "", "color": v.color or "",
             "node_ids": v.node_ids,
             # Members by NAME (chain-aware) — the durable identity. Ids of
             # local nodes change on every restore; names re-resolve. Stale ids
             # (nodes that no longer exist) drop out here, so a snapshot/restore
             # cycle self-heals a damaged view instead of carrying dead ids.
             "node_names": [node_name_map[i] for i in v.node_ids if i in node_name_map]}
            for v in views
        ],
        "default_view_excluded_node_ids": topo.default_view_excluded_node_ids,
        "default_view_excluded_node_names": [
            node_name_map[i] for i in (topo.default_view_excluded_node_ids or []) if i in node_name_map
        ],
        # Patch panel documentation; port refs by NAME (node) and identity key
        # (link) so they re-resolve after a delete-and-recreate restore.
        "patch_panels": [_patch_panel_export_dict(p, node_name_map, link_label_map,
                                                  {pp.id: pp.name for pp in panels}) for p in panels],
        "_local_node_names": {str(n.id): n.name for n in nodes},
    }


def _patch_panel_export_dict(p, node_name_map, link_key_by_id, panel_name_by_id) -> dict:
    ports = {}
    for k, v in p.ports.items():
        ports[k] = {
            "label": v.get("label", ""), "status": v.get("status", "unused"),
            "node_name": node_name_map.get(v.get("node_id")) if v.get("node_id") else None,
            "interface": v.get("interface", ""),
            "link_key": link_key_by_id.get(v.get("link_id")) if v.get("link_id") else None,
            "notes": v.get("notes", ""),
        }
        if v.get("rear_port"):
            ports[k]["rear_port"] = v["rear_port"]
    return {"location": p.location, "name": p.name, "rack": p.rack or "",
            # Chain by NAME (unique per location); ids change on restore.
            "in_front_of": panel_name_by_id.get(p.in_front_of_id) if p.in_front_of_id else None,
            "start_u": p.start_u, "height_u": p.height_u or 1, "rows": p.rows,
            "ports_per_row": p.ports_per_row, "notes": p.notes or "", "ports": ports}


# Mirrors frontend/src/utils/colors.ts VIEW_LAYER_PALETTE + resolveViewColors, so
# exported node tints match the colors shown in the in-app Sub-topology Views menu.
VIEW_LAYER_PALETTE = ['#22d3ee', '#f59e0b', '#a855f7', '#10b981', '#ef4444', '#3b82f6', '#ec4899', '#84cc16']


def _resolve_view_colors(views: list) -> dict[int, str]:
    """view id -> effective color: the stored color if set, otherwise a
    collision-free palette color keyed by id (independent of list order)."""
    used = {v.color for v in views if (v.color or "").strip()}
    pal_idx = 0

    def next_color():
        nonlocal pal_idx
        while pal_idx < len(VIEW_LAYER_PALETTE) and VIEW_LAYER_PALETTE[pal_idx] in used:
            pal_idx += 1
        c = VIEW_LAYER_PALETTE[pal_idx % len(VIEW_LAYER_PALETTE)]
        pal_idx += 1
        used.add(c)
        return c

    colors: dict[int, str] = {}
    for v in sorted(views, key=lambda x: x.id):
        colors[v.id] = v.color if (v.color or "").strip() else next_color()
    return colors


def _html_export_data(topology_id: int, db: Session, view_ids: list[int],
                      include_planned: bool, show_paths: bool, include_reports: bool,
                      include_spof: bool = True, include_default: bool = True,
                      customer_ids: Optional[list[int]] = None,
                      include_unassigned: bool = True,
                      include_planned_parent: bool = True,
                      include_customer_nodes: bool = False,
                      strip_device_info: bool = False,
                      strip_interface_info: bool = False) -> dict:
    """Build the data payload for the offline HTML viewer from the MERGED
    inheritance view (so child topologies match the canvas), enriched with
    per-link utilization + per-demand contributions, demand paths, SPOF, the
    selected views, and persisted per-flow Monte Carlo reports."""
    from app.services.topology_chain import get_merged_topology_data, get_topology_chain
    from app.services.simulation import run_simulation
    from app.models.customer import Customer
    from app.models.topology_view import TopologyView

    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")

    m_nodes, m_links, m_flows, _ = get_merged_topology_data(db, topology_id)
    chain = get_topology_chain(db, topology_id)

    # Node/link/flow filtering, applied BEFORE serialization so excluded data
    # never reaches the exported file:
    # - planned items are toggled independently for THIS topology
    #   (include_planned) vs the inherited parent chain (include_planned_parent);
    # - customer (role="customer") nodes are excluded unless opted in.
    # Links touching a dropped node and flows terminating on one drop with it.
    def _keep_planned(item) -> bool:
        if not item.is_planned:
            return True
        return include_planned if item.topology_id == topology_id else include_planned_parent

    m_nodes = [n for n in m_nodes
               if _keep_planned(n) and (include_customer_nodes or (n.role or "") != "customer")]
    kept_ids = {n.id for n in m_nodes}
    m_links = [l for l in m_links
               if _keep_planned(l) and l.source_node_id in kept_ids and l.target_node_id in kept_ids]
    m_flows = [f for f in m_flows
               if f.source_node_id in kept_ids and f.dest_node_id in kept_ids]
    kept_flow_ids = {f.id for f in m_flows}

    id_to_name = {n.id: n.name for n in m_nodes}

    customers = db.query(Customer).filter(Customer.topology_id.in_(chain)).all()
    customer_name_map = {c.id: c.name for c in customers}
    node_customer = node_customer_map(m_nodes)
    template_ids = {n.template_id for n in m_nodes if n.template_id}
    templates = db.query(NodeTemplate).filter(NodeTemplate.id.in_(template_ids)).all() if template_ids else []
    template_name_by_id = {t.id: t.name for t in templates}
    lat_infos = latency_infos(db, m_nodes, m_links)

    data = {
        "version": "1.0",
        "topology": {"name": topo.name, "description": topo.description or ""},
        "nodes": [
            _node_export_dict(n, template_name_by_id.get(n.template_id),
                              customer_name_map.get(n.customer_id) if n.customer_id else None,
                              node_name_map=id_to_name)
            for n in m_nodes
        ],
        "links": [_link_export_dict(l, id_to_name) | {"latency_display": _latency_display(lat_infos[l.id])}
                  for l in m_links],
        "flows": [
            _flow_export_dict(f, id_to_name, _flow_customer_name(f, node_customer, customer_name_map),
                              include_report=include_reports)
            for f in m_flows
        ],
    }

    # CUI sanitization: blank device- and/or interface-identifying fields in
    # place (rather than filtering in the viewer) so redacted values are absent
    # from the self-contained file entirely.
    if strip_device_info:
        for nd in data["nodes"]:
            nd.update(template_name=None, vendor="", model="", platform="",
                      mgmt_ip="", prometheus_id="", custom_attrs={},
                      custom_iface_refs={}, config_template_id=None,
                      config_template_group_id=None)
    if strip_interface_info:
        for nd in data["nodes"]:
            nd["interfaces"] = []
            nd["custom_iface_refs"] = {}
        for ld in data["links"]:
            ld.update(source_interface="", target_interface="", source_ip="",
                      target_ip="", source_ipv6="", target_ipv6="")
        for fd in data["flows"]:
            fd.update(source_interface="", dest_interface="")

    # failure_analysis is the expensive per-node/per-link SPOF pass; skip it
    # entirely when SPOF isn't being included.
    try:
        sim = run_simulation(topology_id, db,
                             include_planned=include_planned and include_planned_parent,
                             failure_analysis=include_spof)
    except Exception:
        sim = {}

    flow_name_by_id = {f.id: f.name for f in m_flows}
    flow_by_id = {f.id: f for f in m_flows}
    customers_by_flow_id = {f.id: flow_customer_ids(f, node_customer) for f in m_flows}
    # Customer path filter: which flows' demand paths are visible. Utilization
    # below is NOT filtered — it always reflects all flows. customer_ids=None
    # means "no filter" (show every flow's paths).
    selected_customers = set(customer_ids) if customer_ids is not None else None

    def _flow_visible(flow_id) -> bool:
        if selected_customers is None:
            return True
        cids = customers_by_flow_id.get(flow_id)
        if not cids:
            return include_unassigned
        return any(cid in selected_customers for cid in cids)

    # Per-link per-demand contribution from flow paths (link_id -> {flow_name: gbps}).
    contrib: dict[int, dict[str, float]] = {}
    for fp in sim.get("flow_paths", []):
        if fp.get("flow_id") not in kept_flow_ids:
            continue  # flow was filtered out (customer/planned) — don't attribute it
        fname = fp.get("flow_name", "")
        for lids, bw in zip(fp.get("link_ids_per_path", []), fp.get("bandwidth_per_path_gbps", [])):
            for lid in lids:
                contrib.setdefault(lid, {})
                contrib[lid][fname] = contrib[lid].get(fname, 0) + bw

    link_by_id = {l["id"]: l for l in data["links"]}
    for lu in sim.get("link_utilizations", []):
        ld = link_by_id.get(lu["link_id"])
        if not ld:
            continue
        ld["_util"] = {
            "used_gbps": lu["used_gbps"],
            "utilization_pct": lu["utilization_pct"],
        }
        # Per-demand attribution ships only when demand paths are included, so
        # a paths-free export carries no flow->link mapping at all. Flows this
        # export filtered out (customer/planned) are never named: utilization
        # totals still count them, but attribution lists only kept flows.
        if show_paths:
            ld["_util"]["flow_names"] = [flow_name_by_id[fid] for fid in lu.get("flow_ids", [])
                                         if fid in flow_name_by_id]
            ld["_util"]["demand_contributions"] = sorted(
                [{"flow_name": fn, "contrib_gbps": round(g, 4)}
                 for fn, g in contrib.get(lu["link_id"], {}).items()],
                key=lambda x: -x["contrib_gbps"],
            )

    # Demand paths (node-id sequences translated to names so the viewer resolves
    # links by consecutive name pairs; ECMP yields multiple paths_named entries).
    if show_paths:
        data["_flow_paths"] = [
            {
                "flow_name": fp["flow_name"],
                "customer_name": _flow_customer_name(flow_by_id.get(fp.get("flow_id")), node_customer, customer_name_map),
                "paths_named": [[id_to_name.get(nid, str(nid)) for nid in path] for path in fp["paths"]],
                "bandwidth_per_path_gbps": fp.get("bandwidth_per_path_gbps", []),
                "latency_per_path_ms": fp.get("latency_per_path_ms", []),
            }
            for fp in sim.get("flow_paths", [])
            if fp.get("paths") and fp.get("flow_id") in kept_flow_ids
            and _flow_visible(fp.get("flow_id"))
        ]

    # SPOF (optional), sorted by number of affected flows (most impactful first).
    # Only present when included, so the viewer can hide the panel when it wasn't run.
    if include_spof:
        link_name_map = {l["id"]: f"{l['source_node']} ↔ {l['target_node']}" for l in data["links"]}
        spof_list = [
            {
                "type": s["type"],
                "name": s["name"] if s["type"] == "node" else link_name_map.get(s["id"], s["name"]),
                "affected_flows": s["affected_flows"],
                "isolated_count": s.get("isolated_count", 0),
            }
            for s in sim.get("single_points_of_failure", [])
            # The sim sees the full DB topology; drop SPOF entries that name a
            # node/link this export filtered out (planned/customer).
            if s["id"] in (id_to_name if s["type"] == "node" else link_name_map)
        ]
        spof_list.sort(key=lambda s: (-s["affected_flows"], -s["isolated_count"]))
        data["_spof"] = spof_list

    # Switchable views. A view's node_ids are resolved directly against the
    # merged (rendered) node ids — matching how the app filters/colors — and
    # planned nodes are always included in the combined view.
    all_names = [n["name"] for n in data["nodes"]]
    planned_names = {n["name"] for n in data["nodes"] if n["is_planned"]}

    def names_from_ids(ids):
        # No node_remap: the app filters/colors by the RENDERED node's own id
        # against the view's raw node_ids, so a view that references a shadowed
        # (non-visible) node id must NOT resolve to the visible winner.
        out = []
        for nid in ids or []:
            nm = id_to_name.get(nid)
            if nm:
                out.append(nm)
        return out

    # A single flattened view, the only view shown in the export: the Default
    # view (all nodes minus the default exclusion) combined with the selected
    # saved views, plus planned nodes (which are always included). Each node also
    # carries the color of the (non-default) selected view it belongs to, so the
    # viewer can tint it; a node in several selected views takes the last one's
    # color, and default-only nodes keep the default color.
    # The Default view (all nodes minus the exclusion set) is optional: when
    # include_default is off, the combined view is just the selected saved
    # views' nodes, so the export shows only those. Planned nodes ride along
    # with the default set (as before) and otherwise appear only if a selected
    # view contains them.
    excl = set(names_from_ids(topo.default_view_excluded_node_ids))
    combined: set = set()
    if include_default:
        combined = {nm for nm in all_names if nm not in excl} | planned_names
    # View colors, keyed by node ID (not name) so duplicate node names can't
    # bleed color onto unrelated nodes. Collect each selected view's member ids
    # in selection order; the last selected view wins on overlap (matches the
    # app's topmost-layer rule).
    node_color_by_id: dict[int, str] = {}
    colored_views: list = []  # (color, member_winner_ids) in selection order
    view_legend: list[dict] = []
    if view_ids:
        # Resolve colors over ALL of the topology's views (like the app) so
        # palette-assigned colors match; then pick out the selected ones.
        all_views = db.query(TopologyView).filter(TopologyView.topology_id == topology_id).all()
        color_by_id = _resolve_view_colors(all_views)
        by_id = {v.id: v for v in all_views}
        # Selected views in the app's stack order (the views list is ordered by
        # name), so "last wins" on overlap matches the app.
        selected = sorted((by_id[vid] for vid in view_ids if vid in by_id), key=lambda v: v.name)
        for v in selected:
            combined |= set(names_from_ids(v.node_ids))
            color = color_by_id.get(v.id, "")
            if not color:
                continue
            member_ids = set()
            for nid in v.node_ids:
                # Direct id match only (no remap) — matches the app's coloring.
                if nid in id_to_name:
                    member_ids.add(nid)
                    node_color_by_id[nid] = color  # node: last (by name) view wins
            colored_views.append((color, member_ids))
            view_legend.append({"name": v.name, "color": color})
    data["_export_views"] = [{
        "name": "Combined view", "color": "",
        "node_names": [nm for nm in all_names if nm in combined],
    }]
    # Node tint: actual members only.
    for nd in data["nodes"]:
        c = node_color_by_id.get(nd["id"])
        if c:
            nd["_view_color"] = c
    # Link tint (matches the app, TopologyEditor rfEdges): a link takes a view's
    # color when EITHER endpoint is in that view; the last selected view wins.
    # Links with neither endpoint in any selected view (default-view links) stay
    # uncolored.
    for color, member_ids in colored_views:
        for l in m_links:
            if l.source_node_id in member_ids or l.target_node_id in member_ids:
                ld = link_by_id.get(l.id)
                if ld:
                    ld["_view_color"] = color
    data["_view_legend"] = view_legend
    return data


@router.get("/{topology_id}/export")
def export_topology(
    topology_id: int,
    format: str = Query("json", regex="^(json|yaml|html)$"),
    view_ids: str = Query("", description="HTML only: comma-separated TopologyView ids to embed"),
    include_planned: int = Query(1, description="HTML only: include this topology's own planned nodes/links"),
    include_planned_parent: int = Query(1, description="HTML only: include planned nodes/links inherited from parent topologies"),
    show_paths: int = Query(1, description="HTML only: embed demand paths"),
    include_reports: int = Query(1, description="HTML only: embed per-flow availability reports"),
    include_spof: int = Query(1, description="HTML only: run + embed SPOF analysis"),
    include_default: int = Query(1, description="HTML only: include the Default view in the combined view"),
    customer_ids: Optional[str] = Query(None, description="HTML only: comma-separated customer ids to scope visible demand paths (omit = all)"),
    include_unassigned: int = Query(1, description="HTML only: include flows with no customer when filtering paths"),
    theme: str = Query("dark", regex="^(dark|light)$", description="HTML only: STARTING theme (the page itself has a Light/Dark toggle)"),
    title: Optional[str] = Query(None, max_length=120, description="HTML only: viewer title (top-left) and download filename; defaults to the topology name with spaces as underscores"),
    include_customer_nodes: int = Query(0, description="HTML only: include customer (role=customer) nodes and their links/flows"),
    cui: int = Query(0, description="HTML only: mark the document with a CUI banner"),
    strip_device_info: int = Query(0, description="HTML only: blank device-identifying fields (vendor/model/platform/mgmt IP/custom attrs)"),
    strip_interface_info: int = Query(0, description="HTML only: blank interface names and IP addressing"),
    db: Session = Depends(get_db),
):
    """Export a complete topology as JSON, YAML, or interactive HTML."""
    if format == "html":
        vids = [int(x) for x in view_ids.split(",") if x.strip().isdigit()]
        cids = None if customer_ids is None else [int(x) for x in customer_ids.split(",") if x.strip().isdigit()]
        data = _html_export_data(topology_id, db, vids, bool(include_planned), bool(show_paths),
                                 bool(include_reports), bool(include_spof),
                                 include_default=bool(include_default), customer_ids=cids,
                                 include_unassigned=bool(include_unassigned),
                                 include_planned_parent=bool(include_planned_parent),
                                 include_customer_nodes=bool(include_customer_nodes),
                                 strip_device_info=bool(strip_device_info),
                                 strip_interface_info=bool(strip_interface_info))
        viewer_title = (title or "").strip() or data["topology"]["name"].replace(" ", "_")
        # Header values are latin-1 (quotes/controls also break the header) —
        # keep the full title in the page, but restrict the filename.
        fname = "".join(c for c in viewer_title
                        if c != '"' and 32 <= ord(c) < 256) or "topology"
        return Response(
            content=_build_html_viewer(data, theme=theme, title=viewer_title, cui=bool(cui)),
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{fname}.html"'},
        )

    data = _serialize_topology(topology_id, db)
    # Internal remap helper used only by version restore — not for user exports.
    data.pop("_local_node_names", None)
    topo_name = data["topology"]["name"].replace(" ", "_")

    if format == "yaml":
        content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return Response(
            content=content,
            media_type="application/x-yaml",
            headers={"Content-Disposition": f'attachment; filename="{topo_name}.yaml"'},
        )
    content = json.dumps(data, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{topo_name}.json"'},
    )


def _build_html_viewer(data: dict, theme: str = "dark", title: Optional[str] = None,
                       cui: bool = False) -> str:
    """Build a self-contained interactive HTML topology viewer.

    Ships BOTH color themes: chrome colors live in CSS variables (:root = dark,
    body.light = light) plus a JS THEMES object for imperatively-set colors
    (node masking fill etc.). `theme` only picks the STARTING theme — the page
    header has a Light/Dark toggle."""
    topo_name = data["topology"]["name"]
    # User-supplied viewer title (free text) — escape it; default mirrors the
    # export dialog's suggestion (topology name, spaces -> underscores).
    page_title = html.escape(title or topo_name.replace(" ", "_"))
    topo_desc = data["topology"].get("description", "")
    # Escape </script> in JSON to prevent breaking the HTML parser
    json_data = json.dumps(data).replace("</", "<\\/")
    body_classes = " ".join(c for c in ("light" if theme == "light" else "", "cui" if cui else "") if c)
    body_cls = f' class="{body_classes}"' if body_classes else ""
    cui_banner = '<div id="cui-banner">CUI</div>' if cui else ""
    # The Reports button only ships when at least one flow actually carries a
    # persisted Monte Carlo report (include_reports off, or none stored => no button).
    has_reports = any(f.get("availability_report") for f in data["flows"])
    mc_btn = '  <button id="mc-btn" onclick="toggleMc()">&#128202; Reports</button>\n' if has_reports else ""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title} — Network Topology</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
/* Chrome palette. Data colors (view layers, utilization ramp, path hues) are
   intentionally NOT themed — they encode meaning and must match the app. */
:root{{--bg:#0f172a;--panel:#1e293b;--panel-t:#1e293bdd;--border:#334155;--chip:#334155;--text:#e2e8f0;--muted:#94a3b8;--muted-strong:#cbd5e1;--accent:#60a5fa;--accent-strong:#3b82f6;--badge-node:#3b82f622;--badge-link:#f59e0b22;--badge-mc:#8b5cf622;--badge-danger:#ef444422;--shadow:rgba(0,0,0,.4);--title:#f1f5f9}}
body.light{{--bg:#f1f5f9;--panel:#ffffff;--panel-t:#ffffffdd;--border:#cbd5e1;--chip:#e2e8f0;--text:#1e293b;--muted:#64748b;--muted-strong:#475569;--accent:#1d4ed8;--accent-strong:#2563eb;--badge-node:#2563eb22;--badge-link:#d9770622;--badge-mc:#7c3aed22;--badge-danger:#dc262622;--shadow:rgba(15,23,42,.15);--title:#0f172a}}
body{{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);overflow:hidden;height:100vh;color-scheme:dark}}
body.light{{color-scheme:light}}
.muted{{color:var(--muted)}}
#cui-banner{{height:22px;line-height:22px;background:#502b85;color:#ffffff;text-align:center;font-weight:700;font-size:12px;letter-spacing:0.25em}}
body.cui #canvas-wrap{{height:calc(100vh - 66px)}}
#header{{background:var(--panel);border-bottom:1px solid var(--border);padding:10px 20px;display:flex;align-items:center;gap:16px;z-index:10;position:relative}}
#header h1{{font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:0.2em;color:var(--title)}}
#header .desc{{font-size:11px;color:var(--muted)}}
#header .stats{{margin-left:auto;display:flex;gap:16px;font-size:11px;color:var(--muted)}}
#header .stats span{{color:var(--accent);font-weight:600}}
#canvas-wrap{{position:relative;width:100%;height:calc(100vh - 44px);overflow:hidden;cursor:grab}}
#canvas-wrap.dragging{{cursor:grabbing}}
svg{{position:absolute;top:0;left:0;width:100%;height:100%}}
.link-label{{font-size:9px;fill:var(--muted);text-anchor:middle;pointer-events:none}}
.node-label{{font-size:10px;fill:var(--text);text-anchor:middle;pointer-events:none;font-weight:500}}
#detail-panel{{position:absolute;top:54px;right:10px;width:320px;max-height:calc(100vh - 64px);background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;font-size:11px;overflow-y:auto;display:none;z-index:5;box-shadow:0 8px 24px var(--shadow)}}
#detail-panel.open{{display:block}}
#detail-panel h3{{color:var(--accent);font-size:13px;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:6px}}
#detail-panel .row{{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border)}}
#detail-panel .row .lbl{{color:var(--muted)}}
#detail-panel .row .val{{color:var(--text);text-align:right;max-width:180px;overflow:hidden;text-overflow:ellipsis}}
#detail-panel .section{{margin-top:10px;color:var(--muted);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.5px}}
#detail-panel .iface{{padding:2px 6px;margin:2px 0;background:var(--chip);border-radius:4px;display:flex;justify-content:space-between}}
#detail-panel .iface .spd{{color:var(--accent)}}
.close-btn{{position:absolute;top:8px;right:10px;background:none;border:none;color:var(--muted);cursor:pointer;font-size:16px}}
.close-btn:hover{{color:var(--text)}}
#spof-panel{{position:absolute;top:54px;left:220px;width:340px;max-height:calc(100vh - 64px);background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;font-size:11px;overflow-y:auto;display:none;z-index:6;box-shadow:0 8px 24px var(--shadow)}}
#spof-panel.open{{display:block}}
#spof-panel h3{{color:#ef4444;font-size:13px;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:6px}}
.spof-item{{padding:6px 8px;margin:3px 0;background:var(--chip);border-radius:4px;border-left:3px solid #ef4444}}
.spof-item .spof-name{{color:var(--text);font-weight:500}}
.spof-item .spof-meta{{color:var(--muted);font-size:10px;margin-top:2px}}
.spof-badge{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;font-weight:600;margin-right:4px}}
.spof-badge.node{{background:var(--badge-node);color:var(--accent)}}
.spof-badge.link{{background:var(--badge-link);color:#f59e0b}}
#spof-btn{{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:4px 10px;color:var(--muted);cursor:pointer;font-size:11px;margin-left:8px}}
#spof-btn:hover{{background:var(--chip);color:var(--text)}}
#spof-btn.active{{background:var(--badge-danger);color:#ef4444;border-color:#ef4444}}
#legend{{position:absolute;bottom:10px;left:10px;background:var(--panel-t);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:10px;color:var(--muted);z-index:5}}
#legend div{{display:flex;align-items:center;gap:6px;margin:2px 0}}
#legend .swatch{{width:12px;height:3px;border-radius:2px}}
#search{{position:absolute;top:10px;left:10px;z-index:5}}
#search input{{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:11px;width:200px;outline:none}}
#search input:focus{{border-color:var(--accent-strong)}}
#search .results{{background:var(--panel);border:1px solid var(--border);border-radius:6px;margin-top:4px;max-height:200px;overflow-y:auto;display:none}}
#search .results.open{{display:block}}
#search .results div{{padding:5px 10px;cursor:pointer;font-size:11px}}
#search .results div:hover{{background:var(--chip)}}
#zoom-ctrl{{position:absolute;bottom:10px;right:10px;display:flex;flex-direction:column;gap:4px;z-index:5}}
#zoom-ctrl button{{width:32px;height:32px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center}}
#zoom-ctrl button:hover{{background:var(--chip)}}
#ctrl-bar{{display:flex;align-items:center;gap:6px}}
#ctrl-bar select{{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:4px 8px;color:var(--text);font-size:11px;outline:none;max-width:180px}}
#ctrl-bar select:focus{{border-color:var(--accent-strong)}}
#ctrl-bar label{{font-size:10px;color:var(--muted)}}
#ctrl-bar .swatch{{width:12px;height:12px;border-radius:3px;border:1px solid var(--muted)}}
#mc-btn,#theme-btn{{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:4px 10px;color:var(--muted);cursor:pointer;font-size:11px;margin-left:8px}}
#mc-btn:hover,#theme-btn:hover{{background:var(--chip);color:var(--text)}}
#mc-btn.active{{background:var(--badge-mc);color:#a78bfa;border-color:#8b5cf6}}
#mc-panel{{position:absolute;top:54px;left:50%;transform:translateX(-50%);width:440px;max-height:calc(100vh - 64px);background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;font-size:11px;overflow-y:auto;display:none;z-index:6;box-shadow:0 8px 24px var(--shadow)}}
#mc-panel.open{{display:block}}
#mc-panel h3{{color:#a78bfa;font-size:13px;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:6px}}
.mc-flow{{margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border)}}
.mc-flow .mc-title{{color:var(--text);font-weight:600;margin-bottom:4px}}
.mc-hist{{display:flex;align-items:flex-end;gap:1px;height:64px;margin-top:8px;background:var(--bg);border-radius:4px;padding:4px}}
.mc-hist .bar{{flex:1;background:#8b5cf6;min-height:1px;border-radius:1px 1px 0 0}}
.mc-hist .bar:hover{{background:#a78bfa}}
#path-info{{margin-top:8px}}
#legend .views-hdr{{margin-top:4px;color:var(--muted-strong)}}
.path-row{{padding:4px 6px;margin:3px 0;background:var(--chip);border-radius:4px;border-left:3px solid #f472b6}}
</style>
</head>
<body{body_cls}>
{cui_banner}<div id="header">
  <h1>{page_title}</h1>
  <span class="desc">{topo_desc}</span>
  <div id="ctrl-bar">
    <label for="linkcolor-select">Links</label>
    <select id="linkcolor-select" title="Color links by utilization or by view color">
      <option value="util">Utilization</option>
      <option value="plain">Plain (gray)</option>
      <option value="view">View color</option>
    </select>
    <label for="path-select">Demand</label>
    <select id="path-select" title="Highlight a demand's path(s)"><option value="">— none —</option></select>
  </div>
  <div class="stats">
    <div>Nodes: <span id="stat-nodes">0</span></div>
    <div>Links: <span id="stat-links">0</span></div>
    <div>Flows: <span id="stat-flows">0</span></div>
  </div>
{mc_btn}  <button id="spof-btn" onclick="toggleSpof()">&#9888; SPOF</button>
  <button id="theme-btn" onclick="toggleTheme()" title="Switch between dark and light theme">&#9788; Light</button>
</div>
<div id="canvas-wrap">
  <svg id="topo-svg"></svg>
  <div id="search">
    <input type="text" placeholder="Search nodes..." id="search-input" autocomplete="off">
    <div class="results" id="search-results"></div>
  </div>
  <div id="detail-panel">
    <button class="close-btn" onclick="closePanel()">&times;</button>
    <div id="detail-content"></div>
  </div>
  <div id="spof-panel">
    <button class="close-btn" onclick="toggleSpof()">&times;</button>
    <div id="spof-content"></div>
  </div>
  <div id="mc-panel">
    <button class="close-btn" onclick="toggleMc()">&times;</button>
    <div id="mc-content"></div>
  </div>
  <div id="legend">
    <div><span class="swatch" style="background:#3b82f6"></span> No traffic (0%)</div>
    <div><span class="swatch" style="background:#22c55e"></span> Low (1-24%)</div>
    <div><span class="swatch" style="background:#84cc16"></span> Medium (25-49%)</div>
    <div><span class="swatch" style="background:#eab308"></span> High (50-74%)</div>
    <div><span class="swatch" style="background:#f97316"></span> Critical (75-89%)</div>
    <div><span class="swatch" style="background:#ef4444"></span> Overloaded (90%+)</div>
    <div><span class="swatch" style="background:#94a3b8;background-image:repeating-linear-gradient(90deg,#94a3b8 0 4px,transparent 4px 7px)"></span> Planned (dashed)</div>
    <div><span class="swatch" style="background:#f472b6"></span> Highlighted demand path</div>
  </div>
  <div id="zoom-ctrl">
    <button onclick="zoomBy(1.3)" title="Zoom in">+</button>
    <button onclick="zoomBy(0.7)" title="Zoom out">&minus;</button>
    <button onclick="fitAll()" title="Fit all" style="font-size:11px">Fit</button>
  </div>
</div>
<script id="topo-data" type="application/json">{json_data}</script>
<script>
const DATA = JSON.parse(document.getElementById("topo-data").textContent);
const nodes = DATA.nodes || [];
const links = DATA.links || [];
const flows = DATA.flows || [];
const viewLegend = DATA._view_legend || [];             /* {{name, color}} for selected views */
/* Imperatively-set chrome colors per theme. mask MUST equal the CSS --bg so
   node circles keep hiding the links that pass behind them. Data colors
   (view layers, utilization ramp, PATH_HUES) are not themed. */
const THEMES = {{
  dark:  {{ mask: "#0f172a", hover: "#334155", nodeStroke: "#3b82f6", glyph: "#60a5fa", neutralLink: "#64748b", plainLink: "#64748b" }},
  light: {{ mask: "#f1f5f9", hover: "#e2e8f0", nodeStroke: "#334155", glyph: "#475569", neutralLink: "#94a3b8", plainLink: "#94a3b8" }},
}};
let T = THEMES["{theme}"] || THEMES.dark;

document.getElementById("stat-nodes").textContent = nodes.length;
document.getElementById("stat-links").textContent = links.length;
document.getElementById("stat-flows").textContent = flows.length;

const spofData = DATA._spof || [];
const spofIncluded = ("_spof" in DATA);
const pathsIncluded = ("_flow_paths" in DATA);
if (!spofIncluded) {{ const _b = document.getElementById("spof-btn"); if (_b) _b.style.display = "none"; }}

const nodeMap = {{}};
nodes.forEach(n => {{ nodeMap[n.name] = n; }});

/* Utilization color function matching the app */
function utilColor(pct) {{
  if (pct <= 0) return "#3b82f6";
  if (pct < 25) return "#22c55e";
  if (pct < 50) return "#84cc16";
  if (pct < 75) return "#eab308";
  if (pct < 90) return "#f97316";
  return "#ef4444";
}}

/* --- SVG pan/zoom state --- */
const svg = document.getElementById("topo-svg");
const wrap = document.getElementById("canvas-wrap");
let vx = 0, vy = 0, vscale = 1;
let dragging = false, dragStartX, dragStartY, vxStart, vyStart;

function applyTransform() {{
  const g = document.getElementById("main-g");
  if (g) g.setAttribute("transform", `translate(${{vx}},${{vy}}) scale(${{vscale}})`);
}}

function zoomBy(factor) {{
  const cx = wrap.clientWidth / 2, cy = wrap.clientHeight / 2;
  vx = cx - factor * (cx - vx);
  vy = cy - factor * (cy - vy);
  vscale *= factor;
  applyTransform();
}}

function fitAll(visibleNames) {{
  const list = (visibleNames && visibleNames.size) ? nodes.filter(n => visibleNames.has(n.name)) : nodes;
  if (!list.length) return;
  const pad = 80;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  list.forEach(n => {{ if(n.x<minX) minX=n.x; if(n.x>maxX) maxX=n.x; if(n.y<minY) minY=n.y; if(n.y>maxY) maxY=n.y; }});
  const w = maxX - minX || 1, h = maxY - minY || 1;
  const cw = wrap.clientWidth - pad*2, ch = wrap.clientHeight - pad*2;
  vscale = Math.min(cw / w, ch / h, 2);
  vx = (wrap.clientWidth - w * vscale) / 2 - minX * vscale;
  vy = (wrap.clientHeight - h * vscale) / 2 - minY * vscale;
  applyTransform();
}}

wrap.addEventListener("mousedown", e => {{
  if (e.target.closest("#detail-panel") || e.target.closest("#search") || e.target.closest("#zoom-ctrl")) return;
  if (e.button === 0) {{ dragging = true; dragStartX = e.clientX; dragStartY = e.clientY; vxStart = vx; vyStart = vy; wrap.classList.add("dragging"); }}
}});
window.addEventListener("mousemove", e => {{
  if (!dragging) return;
  vx = vxStart + (e.clientX - dragStartX);
  vy = vyStart + (e.clientY - dragStartY);
  applyTransform();
}});
window.addEventListener("mouseup", () => {{ dragging = false; wrap.classList.remove("dragging"); }});
wrap.addEventListener("wheel", e => {{
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.1 : 0.9;
  const rect = wrap.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  vx = mx - factor * (mx - vx);
  vy = my - factor * (my - vy);
  vscale *= factor;
  applyTransform();
}}, {{ passive: false }});

/* --- Render --- */
const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
g.id = "main-g";
svg.appendChild(g);

/* Registries built during render so view-filtering and path-highlighting can
   show/hide and restyle without re-rendering the SVG. */
const linkEls = [];
const linkByPair = {{}};
const nodeEls = {{}};

/* Draw links with utilization colors */
links.forEach((l) => {{
  const a = nodeMap[l.source_node], b = nodeMap[l.target_node];
  if (!a || !b) return;
  const util = l._util || null;
  const pct = util ? util.utilization_pct : 0;
  const color = utilColor(pct);
  const line = document.createElementNS("http://www.w3.org/2000/svg","line");
  line.setAttribute("x1",a.x); line.setAttribute("y1",a.y);
  line.setAttribute("x2",b.x); line.setAttribute("y2",b.y);
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", "2.5");
  if (l.is_planned) line.setAttribute("stroke-dasharray", "6,4");
  /* Events go to the wide invisible hit line below, even when this line is
     re-appended on top for highlighting. */
  line.style.pointerEvents = "none";
  line.style.transition = "stroke-width .15s";
  /* View color for the link: set server-side when the link touches a selected
     view (either endpoint); null otherwise. */
  const linkViewColor = l._view_color || null;
  const entry = {{el: line, hitEl: null, labelEl: null, src: l.source_node, tgt: l.target_node,
                  baseColor: color, utilColor: color, viewColor: linkViewColor, highlighted: false}};
  g.appendChild(line);
  /* Invisible wide hit area: hover/click the link without pixel-precision. */
  const hit = document.createElementNS("http://www.w3.org/2000/svg","line");
  hit.setAttribute("x1",a.x); hit.setAttribute("y1",a.y);
  hit.setAttribute("x2",b.x); hit.setAttribute("y2",b.y);
  hit.setAttribute("stroke","transparent");
  hit.setAttribute("stroke-width","16");
  hit.style.cursor = "pointer";
  hit.addEventListener("mouseenter", () => line.setAttribute("stroke-width","4"));
  hit.addEventListener("mouseleave", () => line.setAttribute("stroke-width", entry.highlighted ? "5" : "2.5"));
  hit.addEventListener("click", () => showLinkDetail(l));
  g.appendChild(hit);
  entry.hitEl = hit;
  /* Label: show utilization % if available, otherwise bandwidth */
  const mx = (a.x+b.x)/2, my = (a.y+b.y)/2;
  const txt = document.createElementNS("http://www.w3.org/2000/svg","text");
  txt.setAttribute("x",mx); txt.setAttribute("y",my-6);
  txt.classList.add("link-label");
  txt.setAttribute("fill", color);
  txt.textContent = util ? Math.round(pct) + "%" : l.bandwidth_gbps + "G";
  g.appendChild(txt);
  entry.labelEl = txt;
  linkEls.push(entry);
  linkByPair[l.source_node + "||" + l.target_node] = entry;
  linkByPair[l.target_node + "||" + l.source_node] = entry;
}});

/* Draw nodes — router icon matching the app (circle with crosshair arrows) */
const NS = "http://www.w3.org/2000/svg";
nodes.forEach(n => {{
  const grp = document.createElementNS(NS,"g");
  grp.style.cursor = "pointer";
  /* When a node belongs to a selected view, tint the WHOLE glyph its color
     (ring + crosshair + arrows), matching how the node looks in the app.
     Default-only nodes stay blue. */
  const vColor = n._view_color || null;
  let baseStroke = vColor || T.nodeStroke;
  const glyphColor = vColor || T.glyph;
  const glyphEls = [];
  /* Background circle */
  const circ = document.createElementNS(NS,"circle");
  circ.setAttribute("cx", n.x); circ.setAttribute("cy", n.y);
  circ.setAttribute("r", "20");
  /* Fill with the page background so links behind the node aren't visible
     through it, while the interior still reads as empty. */
  circ.setAttribute("fill", T.mask); circ.setAttribute("stroke", baseStroke); circ.setAttribute("stroke-width", "2");
  if (n.is_planned) circ.setAttribute("stroke-dasharray", "5,3");
  grp.appendChild(circ);
  /* Crosshair lines */
  const vline = document.createElementNS(NS,"line");
  vline.setAttribute("x1",n.x); vline.setAttribute("y1",n.y-16);
  vline.setAttribute("x2",n.x); vline.setAttribute("y2",n.y+16);
  vline.setAttribute("stroke",glyphColor); vline.setAttribute("stroke-width","1.5");
  glyphEls.push(vline);
  vline.style.pointerEvents = "none";
  grp.appendChild(vline);
  const hline = document.createElementNS(NS,"line");
  hline.setAttribute("x1",n.x-16); hline.setAttribute("y1",n.y);
  hline.setAttribute("x2",n.x+16); hline.setAttribute("y2",n.y);
  hline.setAttribute("stroke",glyphColor); hline.setAttribute("stroke-width","1.5");
  glyphEls.push(hline);
  hline.style.pointerEvents = "none";
  grp.appendChild(hline);
  /* Arrow heads */
  const arrows = [
    `${{n.x-4}},${{n.y-10}} ${{n.x}},${{n.y-16}} ${{n.x+4}},${{n.y-10}}`,
    `${{n.x-4}},${{n.y+10}} ${{n.x}},${{n.y+16}} ${{n.x+4}},${{n.y+10}}`,
    `${{n.x-10}},${{n.y-4}} ${{n.x-16}},${{n.y}} ${{n.x-10}},${{n.y+4}}`,
    `${{n.x+10}},${{n.y-4}} ${{n.x+16}},${{n.y}} ${{n.x+10}},${{n.y+4}}`,
  ];
  arrows.forEach(pts => {{
    const pl = document.createElementNS(NS,"polyline");
    pl.setAttribute("points", pts);
    pl.setAttribute("fill","none"); pl.setAttribute("stroke",glyphColor);
    glyphEls.push(pl);
    pl.setAttribute("stroke-width","2"); pl.setAttribute("stroke-linecap","round"); pl.setAttribute("stroke-linejoin","round");
    pl.style.pointerEvents = "none";
    grp.appendChild(pl);
  }});
  /* Hover — brighten the (opaque) interior; back to the background otherwise */
  grp.addEventListener("mouseenter", () => circ.setAttribute("fill", T.hover));
  grp.addEventListener("mouseleave", () => circ.setAttribute("fill", T.mask));
  /* Label */
  const lbl = document.createElementNS(NS,"text");
  lbl.setAttribute("x", n.x); lbl.setAttribute("y", n.y+34);
  lbl.classList.add("node-label");
  lbl.textContent = n.name;
  grp.appendChild(lbl);
  grp.addEventListener("click", () => showNodeDetail(n));
  g.appendChild(grp);
  nodeEls[n.name] = {{grp, circ, glyphEls, vColored: !!vColor, setBaseStroke: (c) => {{ baseStroke = c; circ.setAttribute("stroke", c); }}}};
}});

fitAll();

/* --- Detail panel --- */
const panel = document.getElementById("detail-panel");
const content = document.getElementById("detail-content");

function closePanel() {{ panel.classList.remove("open"); }}

/* --- SPOF panel --- */
const spofPanel = document.getElementById("spof-panel");
const spofContent = document.getElementById("spof-content");
const spofBtn = document.getElementById("spof-btn");

function toggleSpof() {{
  const isOpen = spofPanel.classList.toggle("open");
  spofBtn.classList.toggle("active", isOpen);
  if (isOpen) renderSpof();
}}

function renderSpof() {{
  if (!spofData.length) {{
    spofContent.innerHTML = `<h3>\\u26a0 Single Points of Failure</h3><div style="color:#22c55e;padding:8px 0">No single points of failure detected.</div>`;
    return;
  }}
  let html = `<h3>\\u26a0 Single Points of Failure (${{spofData.length}})</h3>`;
  spofData.forEach(s => {{
    const badge = `<span class="spof-badge ${{s.type}}">${{s.type}}</span>`;
    html += `<div class="spof-item">`;
    html += `<div class="spof-name">${{badge}}${{s.name}}</div>`;
    html += `<div class="spof-meta">${{s.affected_flows}} flow${{s.affected_flows !== 1 ? "s" : ""}} affected`;
    if (s.isolated_count) html += ` \\u00b7 ${{s.isolated_count}} node${{s.isolated_count !== 1 ? "s" : ""}} isolated`;
    html += `</div></div>`;
  }});
  spofContent.innerHTML = html;
}}

function row(label, value) {{ return `<div class="row"><span class="lbl">${{label}}</span><span class="val">${{value || "\\u2014"}}</span></div>`; }}

function showNodeDetail(n) {{
  const ifaces = (n.interfaces || []);
  const nodeLinks = links.filter(l => l.source_node === n.name || l.target_node === n.name);
  const nodeFlows = flows.filter(f => f.source_node === n.name || f.dest_node === n.name);
  let html = `<h3>${{n.name}}</h3>`;
  html += row("Vendor", n.vendor);
  html += row("Model", n.model);
  html += row("Platform", n.platform);
  html += row("Location", n.location);
  html += row("Capacity", n.capacity_gbps + " Gbps");
  if (n.latitude || n.longitude) html += row("Coordinates", `${{n.latitude}}, ${{n.longitude}}`);
  html += row("MTBF", n.mtbf_hours ? n.mtbf_hours + "h" : "\\u2014");
  html += row("MTTR", n.mttr_hours ? n.mttr_hours + "h" : "\\u2014");
  if (ifaces.length) {{
    html += `<div class="section">Interfaces (${{ifaces.length}})</div>`;
    ifaces.forEach(i => {{
      const status = i.status === "reserved" ? " \\u25cf reserved" : "";
      html += `<div class="iface"><span>${{i.name}}${{status}}</span><span class="spd">${{i.speed_gbps}}G</span></div>`;
    }});
  }}
  if (nodeLinks.length) {{
    html += `<div class="section">Links (${{nodeLinks.length}})</div>`;
    nodeLinks.forEach(l => {{
      const peer = l.source_node === n.name ? l.target_node : l.source_node;
      const u = l._util || null;
      const pctStr = u ? ` (${{Math.round(u.utilization_pct)}}%)` : "";
      html += `<div class="iface"><span>\\u2194 ${{peer}}${{pctStr}}</span><span class="spd">${{l.bandwidth_gbps}}G</span></div>`;
    }});
  }}
  if (pathsIncluded && nodeFlows.length) {{
    html += `<div class="section">Flows (${{nodeFlows.length}})</div>`;
    nodeFlows.forEach(f => {{
      html += `<div class="iface"><span>${{f.name}}</span><span class="spd">${{f.bandwidth_gbps}}G</span></div>`;
    }});
  }}
  content.innerHTML = html;
  panel.classList.add("open");
}}

function showLinkDetail(l) {{
  const u = l._util || null;
  const pct = u ? Math.round(u.utilization_pct) : 0;
  const color = utilColor(pct);
  let html = `<h3 style="color:${{color}}">Link${{u ? " \\u2014 " + pct + "%" : ""}}</h3>`;
  html += row("Source", l.source_node);
  html += row("Target", l.target_node);
  html += row("Src Interface", l.source_interface);
  html += row("Tgt Interface", l.target_interface);
  html += row("Bandwidth", l.bandwidth_gbps + " Gbps");
  if (l.is_planned) html += row("Status", "Planned");
  if (u) {{
    html += row("Used / Capacity", u.used_gbps.toFixed(2) + " / " + l.bandwidth_gbps + " Gbps");
    html += row("Utilization", pct + "%");
  }}
  html += row("Latency", l.latency_display || (l.latency_ms + " ms"));
  html += row("Cost", l.cost);
  html += row("Carrier", l.carrier);
  html += row("MTBF", l.mtbf_hours ? l.mtbf_hours + "h" : "\\u2014");
  html += row("MTTR", l.mttr_hours ? l.mttr_hours + "h" : "\\u2014");
  /* Per-demand bandwidth contribution (falls back to a flat flow list). */
  const contribs = (u && u.demand_contributions) ? u.demand_contributions : null;
  if (contribs && contribs.length) {{
    html += `<div class="section">Demands on this link (${{contribs.length}})</div>`;
    contribs.forEach(c => {{
      const sharePct = u.used_gbps > 0 ? Math.round(c.contrib_gbps / u.used_gbps * 100) : 0;
      html += `<div class="iface"><span>${{c.flow_name}}</span><span class="spd">${{c.contrib_gbps.toFixed(2)}}G (${{sharePct}}%)</span></div>`;
    }});
  }} else if (u && u.flow_names && u.flow_names.length) {{
    html += `<div class="section">Demands on this link (${{u.flow_names.length}})</div>`;
    u.flow_names.forEach(name => {{
      const f = flows.find(fl => fl.name === name);
      const bw = f ? f.bandwidth_gbps + "G" : "";
      html += `<div class="iface"><span>${{name}}</span><span class="spd">${{bw}}</span></div>`;
    }});
  }}
  content.innerHTML = html;
  panel.classList.add("open");
}}

/* --- Views, demand paths, Monte Carlo reports --- */
const exportViews = DATA._export_views || [];
const flowPaths = DATA._flow_paths || [];
let activeView = null;  /* null = full topology (no filter) */

function setLinkHighlight(entry, on) {{
  entry.highlighted = on;
  if (on) {{
    entry.el.setAttribute("stroke", "#f472b6");
    entry.el.setAttribute("stroke-width", "5");
    entry.el.parentNode.appendChild(entry.el);
  }} else {{
    entry.el.setAttribute("stroke", entry.baseColor);
    entry.el.setAttribute("stroke-width", "2.5");
  }}
}}
function clearHighlight() {{ linkEls.forEach(e => {{ if (e.highlighted) setLinkHighlight(e, false); }}); }}

function applyViewFilter(view) {{
  activeView = view;
  clearHighlight();
  const sw = document.getElementById("view-swatch");
  if (sw) sw.style.background = (view && view.color) ? view.color : "transparent";
  const visible = (view && view.node_names) ? new Set(view.node_names) : null;
  nodes.forEach(n => {{
    const ne = nodeEls[n.name]; if (!ne) return;
    const show = !visible || visible.has(n.name);
    ne.grp.style.display = show ? "" : "none";
    ne.setBaseStroke(n._view_color || T.nodeStroke);
  }});
  linkEls.forEach(e => {{
    const show = !visible || (visible.has(e.src) && visible.has(e.tgt));
    e.el.style.display = show ? "" : "none";
    if (e.hitEl) e.hitEl.style.display = show ? "" : "none";
    if (e.labelEl) e.labelEl.style.display = show ? "" : "none";
  }});
  fitAll(visible);
}}

/* The export carries a single flattened view (Default + selected); apply it. */
if (exportViews.length) applyViewFilter(exportViews[0]);

/* Link color mode: "util" (utilization, default) or "view" (view color; links
   spanning views/default fall back to neutral gray). */
function applyLinkColorMode(mode) {{
  linkEls.forEach(e => {{
    const c = (mode === "view") ? (e.viewColor || T.neutralLink)
            : (mode === "plain") ? T.plainLink
            : e.utilColor;
    e.baseColor = c;
    if (!e.highlighted) e.el.setAttribute("stroke", c);
    if (e.labelEl) e.labelEl.setAttribute("fill", c);
  }});
}}
const linkColorSelect = document.getElementById("linkcolor-select");
/* Once the user picks a link-color mode themselves, theme switches stop
   overriding it. Both themes DEFAULT to Plain (gray); Utilization / View
   color are the opt-in overlays. */
let linkModeUserPicked = false;
linkColorSelect.addEventListener("change", () => {{
  linkModeUserPicked = true;
  applyLinkColorMode(linkColorSelect.value);
}});
if (!viewLegend.length) {{
  /* No view colors in this export — drop the dead option. */
  const vopt = linkColorSelect.querySelector('option[value="view"]');
  if (vopt) vopt.remove();
}}

/* Legend entries mapping each selected view's color to its name. */
if (viewLegend.length) {{
  const legend = document.getElementById("legend");
  const hdr = document.createElement("div");
  hdr.className = "views-hdr"; hdr.textContent = "Views";
  legend.appendChild(hdr);
  viewLegend.forEach(vl => {{
    const d = document.createElement("div");
    const sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = vl.color; sw.style.width = "10px"; sw.style.height = "10px"; sw.style.borderRadius = "3px";
    d.appendChild(sw);
    d.appendChild(document.createTextNode(" " + vl.name));
    legend.appendChild(d);
  }});
}}

const PATH_HUES = ["#f472b6", "#f59e0b", "#22d3ee", "#a3e635", "#c084fc"];
const pathSelect = document.getElementById("path-select");
flowPaths.forEach((fp, i) => {{
  const opt = document.createElement("option");
  opt.value = String(i); opt.textContent = fp.customer_name ? fp.flow_name + " (" + fp.customer_name + ")" : fp.flow_name;
  pathSelect.appendChild(opt);
}});
pathSelect.addEventListener("change", () => {{
  if (pathSelect.value === "") {{ clearHighlight(); closePanel(); return; }}
  highlightFlow(flowPaths[parseInt(pathSelect.value, 10)]);
}});

function highlightFlow(fp) {{
  clearHighlight();
  if (!fp) return;
  (fp.paths_named || []).forEach((path, pi) => {{
    const hue = PATH_HUES[pi % PATH_HUES.length];
    for (let k = 0; k + 1 < path.length; k++) {{
      const entry = linkByPair[path[k] + "||" + path[k+1]];
      if (!entry) continue;
      entry.highlighted = true;
      entry.el.setAttribute("stroke", hue);
      entry.el.setAttribute("stroke-width", "5");
      entry.el.parentNode.appendChild(entry.el);
    }}
  }});
  let html = `<h3 style="color:#f472b6">${{fp.flow_name}} \\u2014 demand path</h3>`;
  const multi = (fp.paths_named || []).length > 1;
  (fp.paths_named || []).forEach((path, pi) => {{
    const bw = (fp.bandwidth_per_path_gbps || [])[pi];
    const lat = (fp.latency_per_path_ms || [])[pi];
    html += `<div class="path-row">`;
    html += `<div style="color:${{PATH_HUES[pi % PATH_HUES.length]}};font-weight:600">Path ${{pi+1}}${{multi ? " (ECMP)" : ""}}</div>`;
    html += `<div style="margin:3px 0">${{path.join(" \\u2192 ")}}</div>`;
    html += `<div class="muted">${{bw != null ? bw + " Gbps" : ""}}${{lat != null ? " \\u00b7 " + lat + " ms" : ""}}</div>`;
    html += `</div>`;
  }});
  content.innerHTML = html;
  panel.classList.add("open");
}}

const mcPanel = document.getElementById("mc-panel");
const mcContent = document.getElementById("mc-content");
const mcBtn = document.getElementById("mc-btn");
function toggleMc() {{
  const open = mcPanel.classList.toggle("open");
  if (mcBtn) mcBtn.classList.toggle("active", open);
  if (open) renderMc();
}}
function renderMc() {{
  const withReports = flows.filter(f => f.availability_report);
  if (!withReports.length) {{
    mcContent.innerHTML = `<h3>\\ud83d\\udcca Monte Carlo reports</h3><div class="muted" style="padding:8px 0">No Monte Carlo availability reports were run for this topology.</div>`;
    return;
  }}
  let html = `<h3>\\ud83d\\udcca Monte Carlo reports (${{withReports.length}})</h3>`;
  withReports.forEach(f => {{
    const r = f.availability_report;
    html += `<div class="mc-flow"><div class="mc-title">${{f.name}}</div>`;
    if (r.error) {{ html += `<div style="color:#ef4444">${{r.error}}</div></div>`; return; }}
    html += row("Availability", (((r.availability == null ? 0 : r.availability)) * 100).toFixed(5) + "%");
    html += row("Downtime", (r.downtime_min_yr != null ? r.downtime_min_yr.toFixed(1) : "?") + " min/yr" + (r.downtime_std_min_yr != null ? " \\u00b1 " + r.downtime_std_min_yr.toFixed(1) : ""));
    if (r.disjoint_paths != null) html += row("Disjoint paths", r.disjoint_paths);
    if (r.iterations != null) html += row("Iterations", r.iterations);
    const dp = r.downtime_percentiles_min;
    if (dp) html += row("Downtime p50/p90/p99", `${{(dp.p50||0).toFixed(0)}} / ${{(dp.p90||0).toFixed(0)}} / ${{(dp.p99||0).toFixed(0)}} min`);
    const hist = r.downtime_histogram_min || [];
    if (hist.length) {{
      const maxCount = Math.max(1, ...hist.map(b => b.count));
      html += `<div class="mc-hist">`;
      hist.forEach(b => {{
        const h = Math.round(b.count / maxCount * 100);
        html += `<div class="bar" style="height:${{h}}%" title="${{b.bin_start.toFixed(1)}}-${{b.bin_end.toFixed(1)}} min: ${{b.count}}"></div>`;
      }});
      html += `</div>`;
    }}
    html += `</div>`;
  }});
  mcContent.innerHTML = html;
}}

/* --- Search --- */
const searchInput = document.getElementById("search-input");
const searchResults = document.getElementById("search-results");

searchInput.addEventListener("input", () => {{
  const q = searchInput.value.toLowerCase().trim();
  if (!q) {{ searchResults.classList.remove("open"); return; }}
  const matches = nodes.filter(n => n.name.toLowerCase().includes(q)).slice(0, 10);
  if (!matches.length) {{ searchResults.classList.remove("open"); return; }}
  searchResults.innerHTML = matches.map(n => `<div data-name="${{n.name}}">${{n.name}}</div>`).join("");
  searchResults.classList.add("open");
}});

searchResults.addEventListener("click", e => {{
  const name = e.target.dataset.name;
  if (!name) return;
  const n = nodeMap[name];
  if (!n) return;
  vx = wrap.clientWidth / 2 - n.x * vscale;
  vy = wrap.clientHeight / 2 - n.y * vscale;
  applyTransform();
  showNodeDetail(n);
  searchResults.classList.remove("open");
  searchInput.value = "";
}});

document.addEventListener("click", e => {{
  if (!e.target.closest("#search")) searchResults.classList.remove("open");
}});

/* ---- Theme toggle: restyle the imperatively-colored SVG chrome. CSS vars
   handle everything else. View-colored nodes keep their view colors. ---- */
function applyTheme(name) {{
  T = THEMES[name] || THEMES.dark;
  document.body.classList.toggle("light", name === "light");
  const btn = document.getElementById("theme-btn");
  if (btn) btn.innerHTML = name === "light" ? "&#9789; Dark" : "&#9788; Light";
  nodes.forEach(n => {{
    const ne = nodeEls[n.name];
    if (!ne) return;
    ne.circ.setAttribute("fill", T.mask);
    if (!ne.vColored) {{
      ne.setBaseStroke(T.nodeStroke);
      ne.glyphEls.forEach(el => el.setAttribute("stroke", T.glyph));
    }}
  }});
  // Links start Plain (gray) in BOTH themes (until the user picks a mode);
  // Utilization / View color overlay the demand/view colors on request.
  if (!linkModeUserPicked) {{
    linkColorSelect.value = "plain";
  }}
  applyLinkColorMode(linkColorSelect.value);
}}
function toggleTheme() {{
  applyTheme(document.body.classList.contains("light") ? "dark" : "light");
}}
applyTheme("{theme}");
</script>
</body>
</html>'''


def _import_topology_data(data: dict, db: Session, inherited_name_to_id: dict | None = None) -> Topology:
    """Import a topology from serialized data. Returns the new Topology.

    inherited_name_to_id: for restoring a CHILD topology, a map of inherited
    (ancestor) node name -> existing node id. Link/flow endpoints that name an
    inherited node resolve to it instead of being dropped — child rows may
    reference parent nodes, which aren't in this snapshot's own node list."""
    inherited = inherited_name_to_id or {}
    topo_info = data.get("topology", {})
    topo = Topology(
        name=topo_info.get("name", "Imported Topology"),
        description=topo_info.get("description", ""),
    )
    db.add(topo)
    db.flush()

    # Import node templates (skip if one with same name+vendor+model already exists)
    template_name_map = {}  # exported template name -> db template id
    for t_data in data.get("node_templates", []):
        existing = db.query(NodeTemplate).filter(
            NodeTemplate.name == t_data["name"],
            NodeTemplate.vendor == t_data["vendor"],
            NodeTemplate.model == t_data["model"],
        ).first()
        if existing:
            template_name_map[t_data["name"]] = existing.id
        else:
            tmpl = NodeTemplate(
                name=t_data["name"], vendor=t_data["vendor"], model=t_data["model"],
                platform=t_data.get("platform", ""),
                capacity_gbps=t_data.get("capacity_gbps"),
                default_mtbf_hours=t_data.get("default_mtbf_hours"),
                default_mttr_hours=t_data.get("default_mttr_hours"),
                default_interfaces_json=json.dumps(t_data.get("default_interfaces", [])),
                created_by=t_data.get("created_by", "import"),
            )
            db.add(tmpl)
            db.flush()
            template_name_map[t_data["name"]] = tmpl.id

    # Import nodes
    node_name_to_id = {}
    for n_data in data.get("nodes", []):
        template_id = template_name_map.get(n_data.get("template_name"))
        node = NodeInstance(
            topology_id=topo.id, template_id=template_id,
            name=n_data["name"],
            vendor=n_data.get("vendor", ""), model=n_data.get("model", ""),
            platform=n_data.get("platform", ""),
            x=n_data.get("x", 0), y=n_data.get("y", 0),
            latitude=n_data.get("latitude"), longitude=n_data.get("longitude"),
            geo_latitude=n_data.get("geo_latitude"), geo_longitude=n_data.get("geo_longitude"),
            location=n_data.get("location", ""),
            interfaces_json=json.dumps(n_data.get("interfaces", [])),
            mtbf_hours=n_data.get("mtbf_hours"), mttr_hours=n_data.get("mttr_hours"),
            capacity_gbps=n_data.get("capacity_gbps"),
            prometheus_id=n_data.get("prometheus_id", ""),
            mgmt_ip=n_data.get("mgmt_ip", ""),
            is_planned=1 if n_data.get("is_planned") else 0,
            role=n_data.get("role", "router"),
            color=n_data.get("color", ""),
            is_route_reflector=1 if n_data.get("is_route_reflector") else 0,
            is_super_route_reflector=1 if n_data.get("is_super_route_reflector") else 0,
            custom_attrs_json=json.dumps(n_data.get("custom_attrs", {})),
            custom_iface_refs_json=json.dumps(n_data.get("custom_iface_refs", {})),
            config_template_id=n_data.get("config_template_id"),
            config_template_group_id=n_data.get("config_template_group_id"),
        )
        db.add(node)
        db.flush()
        node_name_to_id[n_data["name"]] = node.id

    # Import links (resolve node names to IDs)
    link_name_to_id = {}  # (src_name, tgt_name, src_iface, tgt_iface) -> link_id
    for l_data in data.get("links", []):
        src_id = node_name_to_id.get(l_data.get("source_node")) or inherited.get(l_data.get("source_node"))
        tgt_id = node_name_to_id.get(l_data.get("target_node")) or inherited.get(l_data.get("target_node"))
        if not src_id or not tgt_id:
            continue
        link = Link(
            topology_id=topo.id,
            source_node_id=src_id, target_node_id=tgt_id,
            source_interface=l_data.get("source_interface", ""),
            target_interface=l_data.get("target_interface", ""),
            bandwidth_gbps=l_data.get("bandwidth_gbps", 10),
            cost=l_data.get("cost", 1), latency_ms=l_data.get("latency_ms") or 0.0,
            # Files from before latency sources: a value is known unless it's
            # the old 1.0 default (see services/latency.py).
            latency_source=infer_latency_source(l_data.get("latency_ms"), l_data.get("latency_source")),
            transport=l_data.get("transport") or "terrestrial",
            orbit=l_data.get("orbit") or "",
            carrier=l_data.get("carrier", ""),
            mtbf_hours=l_data.get("mtbf_hours"), mttr_hours=l_data.get("mttr_hours"),
            sr_attributes_json=json.dumps(l_data.get("sr_attributes", [])),
            circuit_cost=l_data.get("circuit_cost", 0),
            circuit_id=l_data.get("circuit_id", ""),
            link_id_label=l_data.get("link_id_label", ""),
            source_ip=l_data.get("source_ip", ""),
            target_ip=l_data.get("target_ip", ""),
            source_ipv6=l_data.get("source_ipv6", ""),
            target_ipv6=l_data.get("target_ipv6", ""),
            is_planned=1 if l_data.get("is_planned") else 0,
            color=l_data.get("color", ""),
            geo_path_json=json.dumps(l_data.get("geo_path", [])),
        )
        db.add(link)
        db.flush()
        # Identity key -> new id, so flows' pinned explicit_link_keys re-resolve.
        link_name_to_id[(l_data.get("source_node"), l_data.get("target_node"),
                         link.source_interface, link.target_interface)] = link.id

    # Import flows (resolve node names to IDs)
    for f_data in data.get("flows", []):
        src_id = node_name_to_id.get(f_data.get("source_node")) or inherited.get(f_data.get("source_node"))
        dst_id = node_name_to_id.get(f_data.get("dest_node")) or inherited.get(f_data.get("dest_node"))
        if not src_id or not dst_id:
            continue
        # Resolve explicit path node names to IDs (own nodes first, then inherited)
        explicit_path = [node_name_to_id.get(n) or inherited.get(n)
                         for n in f_data.get("explicit_path_nodes", [])]
        explicit_path = [nid for nid in explicit_path if nid]
        # Pinned explicit links: resolve each "src||tgt||siface||tiface" identity
        # key back to the recreated link id.
        explicit_link_ids = []
        for k in f_data.get("explicit_link_keys", []):
            parts = k.split("||") if isinstance(k, str) else k
            if len(parts) == 4:
                lid = link_name_to_id.get((parts[0], parts[1], parts[2], parts[3]))
                if lid:
                    explicit_link_ids.append(lid)
        flow = Flow(
            topology_id=topo.id, name=f_data["name"],
            description=f_data.get("description", ""),
            source_node_id=src_id, dest_node_id=dst_id,
            source_interface=f_data.get("source_interface", ""),
            dest_interface=f_data.get("dest_interface", ""),
            bandwidth_gbps=f_data.get("bandwidth_gbps", 1),
            routing_behavior=f_data.get("routing_behavior", "shortest_path"),
            ecmp_enabled=1 if f_data.get("ecmp_enabled") else 0,
            explicit_path_json=json.dumps(explicit_path),
            explicit_link_ids_json=json.dumps(explicit_link_ids),
        )
        db.add(flow)

    # Import customers
    from app.models.customer import Customer
    customer_name_to_id = {}
    for c_data in data.get("customers", []):
        cust = Customer(
            topology_id=topo.id, name=c_data["name"],
            contact_email=c_data.get("contact_email", ""),
            contact_phone=c_data.get("contact_phone", ""),
            notes=c_data.get("notes", ""),
            color=c_data.get("color", ""),
        )
        db.add(cust)
        db.flush()
        customer_name_to_id[c_data["name"]] = cust.id

    # Resolve customer_name + rr_peer_names references on nodes (second pass, so
    # peers can point at any node — including inherited ancestors).
    for n_data in data.get("nodes", []):
        nid = node_name_to_id.get(n_data["name"])
        if not nid:
            continue
        patch = {}
        cname = n_data.get("customer_name")
        if cname and cname in customer_name_to_id:
            patch["customer_id"] = customer_name_to_id[cname]
        peer_names = n_data.get("rr_peer_names")
        if peer_names is not None:
            peer_ids = [node_name_to_id.get(nm) or inherited.get(nm) for nm in peer_names]
            peer_ids = [pid for pid in peer_ids if pid and pid != nid]
            patch["rr_peer_ids_json"] = json.dumps(peer_ids)
        if patch:
            db.query(NodeInstance).filter(NodeInstance.id == nid).update(patch)

    # Import SRLGs
    from app.models.srlg import SrlgGroup, SrlgMember
    for sg_data in data.get("srlgs", []):
        sg = SrlgGroup(
            topology_id=topo.id, name=sg_data["name"],
            description=sg_data.get("description", ""),
            srlg_type=sg_data.get("srlg_type", "custom"),
        )
        db.add(sg)
        db.flush()
        for m_data in sg_data.get("members", []):
            member_ref = m_data.get("member_ref", "")
            member_type = m_data.get("member_type", "")
            member_id = None
            if member_type == "node":
                member_id = node_name_to_id.get(member_ref)
            elif member_type == "link" and "||" in member_ref:
                parts = member_ref.split("||")
                if len(parts) == 4:
                    src_id = node_name_to_id.get(parts[0])
                    tgt_id = node_name_to_id.get(parts[1])
                    if src_id and tgt_id:
                        link = db.query(Link).filter(
                            Link.topology_id == topo.id,
                            Link.source_node_id == src_id, Link.target_node_id == tgt_id,
                            Link.source_interface == parts[2], Link.target_interface == parts[3],
                        ).first()
                        if link:
                            member_id = link.id
            if member_id:
                db.add(SrlgMember(srlg_id=sg.id, member_type=member_type, member_id=member_id))

    # Layout/view state. View membership + default-view exclusions are resolved
    # by NAME first (node_names, chain-aware via the inherited fallback) — the
    # durable identity across delete-and-recreate. Older snapshots without
    # names fall back to the _local_node_names id bridge; ids that resolve to
    # nothing are dropped rather than kept as dead references.
    from app.models.topology_view import TopologyView
    from app.models.node_position_override import NodePositionOverride

    old_local = data.get("_local_node_names", {})  # old_id(str) -> name

    def _resolve_ref_name(name):
        # INHERITED FIRST: for a name that exists both locally and on an
        # ancestor (a shadowed copy — e.g. a full-copy child), the canvas
        # renders the ANCESTOR row, so view membership must reference it.
        # Resolving to the shadowed local row would produce members that
        # exist in the DB but never render — an empty-looking view.
        return inherited.get(name) or node_name_to_id.get(name)

    def _remap_id(nid):
        name = old_local.get(str(nid))
        if name is not None:
            return _resolve_ref_name(name)
        # Not a local node of the snapshot — an inherited (ancestor) id, which
        # is stable across restores. Pass through.
        return nid

    # Every node id that can legitimately appear in a view/exclusion after this
    # import: the recreated local nodes plus inherited ancestors. Anything else
    # is a dead reference from an old snapshot — drop it rather than carry a
    # ghost member that renders as an empty canvas.
    _valid_node_ids = set(node_name_to_id.values()) | set(inherited.values())

    def _resolve_members(entry: dict, names_key: str, ids_key: str) -> list[int]:
        if names_key in entry:
            ids = [_resolve_ref_name(nm) for nm in entry.get(names_key, [])]
        else:
            ids = [_remap_id(i) for i in entry.get(ids_key, [])]
        return [i for i in ids if i and i in _valid_node_ids]

    for v_data in data.get("topology_views", []):
        db.add(TopologyView(
            topology_id=topo.id, name=v_data.get("name", ""),
            description=v_data.get("description", ""), color=v_data.get("color", ""),
            node_ids_json=json.dumps(_resolve_members(v_data, "node_names", "node_ids")),
        ))
    for o in data.get("position_overrides", []):
        nid = _remap_id(o.get("node_id"))
        if nid and nid in _valid_node_ids:
            db.add(NodePositionOverride(
                topology_id=topo.id, node_id=nid,
                x=o.get("x"), y=o.get("y"), latitude=o.get("latitude"), longitude=o.get("longitude"),
                geo_latitude=o.get("geo_latitude"), geo_longitude=o.get("geo_longitude"),
            ))
    from app.models.patch_panel import PatchPanel
    created_panels = []  # (row, export dict) — chains resolve after all exist
    for pp in data.get("patch_panels", []):
        ports = {}
        for k, v in (pp.get("ports") or {}).items():
            nid = node_name_to_id.get(v.get("node_name")) if v.get("node_name") else None
            lid = None
            if v.get("link_key"):
                parts = v["link_key"].split("||")
                if len(parts) == 4:
                    lid = link_name_to_id.get((parts[0], parts[1], parts[2], parts[3]))
            ports[k] = {"label": v.get("label", ""), "status": v.get("status", "unused"),
                        "node_id": nid, "interface": v.get("interface", "") if nid else "",
                        "link_id": lid, "notes": v.get("notes", "")}
            if v.get("rear_port"):
                ports[k]["rear_port"] = v["rear_port"]
        row = PatchPanel(
            topology_id=topo.id, location=pp.get("location", ""), name=pp.get("name", ""),
            rack=pp.get("rack", ""), start_u=pp.get("start_u"), height_u=pp.get("height_u") or 1,
            rows=pp.get("rows") or 1, ports_per_row=pp.get("ports_per_row") or 1,
            notes=pp.get("notes", ""), ports_json=json.dumps(ports))
        db.add(row)
        created_panels.append((row, pp))
    if created_panels:
        db.flush()
        by_key = {(r.location, r.name): r for r, _ in created_panels}
        for row, pp in created_panels:
            behind = by_key.get((row.location, pp.get("in_front_of"))) if pp.get("in_front_of") else None
            if behind is not None and behind is not row:
                row.in_front_of_id = behind.id
            else:
                # Unresolvable chain: jumpers have nowhere to land.
                ports = row.ports
                for v in ports.values():
                    v.pop("rear_port", None)
                row.ports = ports
    if "default_view_excluded_node_names" in data or "default_view_excluded_node_ids" in data:
        topo.default_view_excluded_node_ids = _resolve_members(
            data, "default_view_excluded_node_names", "default_view_excluded_node_ids")

    db.commit()
    db.refresh(topo)
    return topo


def _restore_into_topology(data: dict, topology_id: int, db: Session):
    """Restore a snapshot into an existing topology, replacing all children."""
    from app.models.customer import Customer
    from app.models.srlg import SrlgGroup
    from app.models.maintenance_preset import MaintenancePreset
    from app.models.node_position_override import NodePositionOverride
    from app.models.topology_view import TopologyView

    # Delete all existing children
    db.query(Flow).filter(Flow.topology_id == topology_id).delete()
    db.query(Link).filter(Link.topology_id == topology_id).delete()
    db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).delete()
    db.query(Customer).filter(Customer.topology_id == topology_id).delete()
    db.query(SrlgGroup).filter(SrlgGroup.topology_id == topology_id).delete()
    db.query(MaintenancePreset).filter(MaintenancePreset.topology_id == topology_id).delete()
    db.query(NodePositionOverride).filter(NodePositionOverride.topology_id == topology_id).delete()
    db.query(TopologyView).filter(TopologyView.topology_id == topology_id).delete()
    from app.models.patch_panel import PatchPanel
    db.query(PatchPanel).filter(PatchPanel.topology_id == topology_id).delete()
    db.flush()

    # Re-import using the same logic, but patch topology_id into data
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        return

    # Temporarily create a wrapper that _import uses, then move children
    # Instead, we'll inline the import logic pointing at the existing topo
    data_copy = dict(data)
    data_copy["topology"] = {"name": topo.name, "description": topo.description or ""}

    # Inherited (ancestor) node name -> existing id, so a child's links/flows
    # that reference inherited nodes re-link to them instead of being dropped.
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    inherited_name_to_id = {}
    ancestor_ids = [t for t in chain if t != topology_id]
    if ancestor_ids:
        for n in db.query(NodeInstance).filter(NodeInstance.topology_id.in_(ancestor_ids)).all():
            inherited_name_to_id.setdefault(n.name, n.id)

    # Use _import_topology_data but we need the new topo to be our existing one.
    # Simplest: create via import, then move children. But that's wasteful.
    # Better: factor out. For now, create temp, move, delete temp.
    temp_topo = _import_topology_data(data_copy, db, inherited_name_to_id)

    # Move all children from temp to existing topology. Views, position
    # overrides, and default-view exclusions were already re-created by
    # _import_topology_data (name-first resolution with the inherited
    # fallback), so they just move along with everything else.
    db.query(NodeInstance).filter(NodeInstance.topology_id == temp_topo.id).update({"topology_id": topology_id})
    db.query(Link).filter(Link.topology_id == temp_topo.id).update({"topology_id": topology_id})
    db.query(Flow).filter(Flow.topology_id == temp_topo.id).update({"topology_id": topology_id})
    db.query(Customer).filter(Customer.topology_id == temp_topo.id).update({"topology_id": topology_id})
    db.query(SrlgGroup).filter(SrlgGroup.topology_id == temp_topo.id).update({"topology_id": topology_id})
    db.query(TopologyView).filter(TopologyView.topology_id == temp_topo.id).update({"topology_id": topology_id})
    db.query(PatchPanel).filter(PatchPanel.topology_id == temp_topo.id).update({"topology_id": topology_id})
    db.query(NodePositionOverride).filter(NodePositionOverride.topology_id == temp_topo.id).update({"topology_id": topology_id})
    topo.default_view_excluded_node_ids = temp_topo.default_view_excluded_node_ids
    db.delete(temp_topo)
    db.commit()


@router.post("/import", response_model=TopologyOut)
async def import_topology(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import a topology from a JSON or YAML file."""
    content = await file.read()
    text = content.decode("utf-8")

    filename = (file.filename or "").lower()
    try:
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except Exception as e:
        raise HTTPException(400, f"Failed to parse file: {e}")

    if not isinstance(data, dict):
        raise HTTPException(400, "Invalid format: expected a JSON/YAML object")

    return _import_topology_data(data, db)

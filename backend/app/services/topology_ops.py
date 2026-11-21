"""Topology mutations shared by the REST routes, the importer and (later)
the lab-session launcher and AI agent, so every path applies the same
naming / interface / validation rules."""

from __future__ import annotations

import re
from sqlalchemy.orm import Session

from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.services.device_kinds import get_kind, is_bridge, is_known_kind, is_traffic_image


class TopologyError(ValueError):
    pass


_PREFIX = {"router": "R", "switch": "SW", "host": "H", "workstation": "WS", "firewall": "FW", "server": "S",
           "cloud": "NET"}


def next_node_name(db: Session, topology_id: int, icon: str) -> str:
    prefix = _PREFIX.get(icon or "router", "N")
    taken = {n for (n,) in db.query(NodeInstance.name).filter(NodeInstance.topology_id == topology_id)}
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    nums = [int(m.group(1)) for n in taken if (m := pat.match(n))]
    i = (max(nums) + 1) if nums else 1
    while f"{prefix}{i}" in taken:
        i += 1
    return f"{prefix}{i}"


def create_node(db: Session, topology_id: int, *, template_id: int | None = None, name: str | None = None,
                kind: str | None = None, image_ref: str | None = None, icon: str | None = None,
                interface_count: int | None = None, x: float | None = None, y: float | None = None,
                startup_config: str | None = None, network: dict | None = None) -> NodeInstance:
    tmpl = db.get(NodeTemplate, template_id) if template_id else None
    if template_id and not tmpl:
        raise TopologyError(f"Template {template_id} not found")
    kind = kind or (tmpl.kind if tmpl else None)
    if not is_known_kind(kind):
        raise TopologyError(f"Unknown device kind '{kind}'")
    k = get_kind(kind)
    icon = icon or (tmpl.icon if tmpl else "router")
    count = interface_count if interface_count is not None else (tmpl.interface_count if tmpl else k.default_interfaces)
    name = (name or "").strip() or next_node_name(db, topology_id, icon)
    if db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id, NodeInstance.name == name).first():
        raise TopologyError(f"A device named '{name}' already exists in this topology")
    if x is None or y is None:
        # No position given (API / agent / import): next free slot on a grid.
        i = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).count()
        x, y = 120.0 + (i % 5) * 220.0, 120.0 + (i // 5) * 180.0
    node = NodeInstance(
        topology_id=topology_id, template_id=tmpl.id if tmpl else None, name=name, kind=kind,
        image_ref=image_ref if image_ref is not None else (tmpl.image_ref if tmpl else ""),
        icon=icon, x=x, y=y,
        startup_config=startup_config if startup_config is not None else (tmpl.startup_config if tmpl else ""),
        min_memory_mb=tmpl.min_memory_mb if tmpl else 0,
        min_cpu_cores=tmpl.min_cpu_cores if tmpl else 0,
    )
    if is_bridge(kind):
        node.image_ref = ""
        node.startup_config = ""
        node.interfaces = []
        node.network = network or ({"mode": "internal"} if icon == "switch" else {"mode": "access", "nic": "", "vlan": 0})
    else:
        node.interfaces = k.interfaces(count)
        if is_traffic_image(kind, node.image_ref):
            from app.services.traffic_gen import default_config
            node.traffic = default_config()
        if k.default_user and (k.default_user, k.default_password) != k.builtin_credentials:
            node.credentials = {"provision": {"username": k.default_user, "password": k.default_password}}
    db.add(node)
    db.flush()
    return node


def used_interfaces(db: Session, node_id: int, exclude_link_id: int | None = None) -> set[str]:
    used = set()
    q = db.query(Link).filter((Link.source_node_id == node_id) | (Link.target_node_id == node_id))
    for l in q:
        if l.id == exclude_link_id:
            continue
        if l.source_node_id == node_id:
            used.add(l.source_interface)
        if l.target_node_id == node_id:
            used.add(l.target_interface)
    return used


def check_interface_count(db: Session, node: NodeInstance, count: int) -> list[dict]:
    k = get_kind(node.kind)
    new = k.interfaces(count)
    keep = {i["name"] for i in new}
    orphaned = used_interfaces(db, node.id) - keep
    if orphaned:
        raise TopologyError(
            f"Can't drop to {count} interfaces: {', '.join(sorted(orphaned))} still cabled. Remove those links first.")
    return new


def set_interface_count(db: Session, node: NodeInstance, count: int) -> None:
    node.interfaces = check_interface_count(db, node, count)


RESTART_FIELDS = ("image_ref", "interface_count", "startup_config")


def apply_pending(db: Session, node: NodeInstance) -> list[str]:
    """Apply changes saved for this device's next restart. Returns the fields
    applied (a pending interface count that no longer fits its cables is
    dropped with the reason)."""
    applied = []
    for k, v in node.pending.items():
        if k == "interface_count":
            try:
                set_interface_count(db, node, int(v))
            except TopologyError as e:
                print(f"[lab] {node.name}: pending interface count dropped: {e}", flush=True)
                continue
        elif k == "image_ref":
            node.image_ref = v
        elif k == "startup_config":
            node.startup_config = v
            node.carry_config = ""
        else:
            continue
        applied.append(k)
    node.pending = {}
    return applied


def _pick_free(db: Session, node: NodeInstance, wanted: str | None, exclude_link_id: int | None = None) -> str:
    names = [i["name"] for i in node.interfaces]
    used = used_interfaces(db, node.id, exclude_link_id)
    if is_bridge(node.kind) and not wanted:
        # Network nodes are switches: ports appear as devices are cabled.
        for n in names:
            if n not in used:
                return n
        k = get_kind(node.kind)
        if len(names) >= k.max_interfaces:
            raise TopologyError(f"{node.name} has no free ports")
        node.interfaces = k.interfaces(len(names) + 1)
        return node.interfaces[-1]["name"]
    if wanted:
        if wanted not in names:
            raise TopologyError(f"{node.name} has no interface '{wanted}'")
        if wanted in used:
            raise TopologyError(f"{node.name} {wanted} is already cabled")
        return wanted
    for n in names:
        if n not in used:
            return n
    raise TopologyError(f"{node.name} has no free interfaces — add more in its settings")


def create_link(db: Session, topology_id: int, source_node_id: int, target_node_id: int,
                source_interface: str | None = None, target_interface: str | None = None,
                label: str = "") -> Link:
    if source_node_id == target_node_id:
        raise TopologyError("A link must connect two different devices")
    a, b = db.get(NodeInstance, source_node_id), db.get(NodeInstance, target_node_id)
    if not a or not b or a.topology_id != topology_id or b.topology_id != topology_id:
        raise TopologyError("Both endpoints must be devices in this topology")
    if is_bridge(a.kind) and is_bridge(b.kind):
        raise TopologyError("Two Network nodes can't be cabled directly — connect devices to them")
    link = Link(
        topology_id=topology_id, source_node_id=a.id, target_node_id=b.id,
        source_interface=_pick_free(db, a, source_interface),
        target_interface=_pick_free(db, b, target_interface),
        label=label or "",
    )
    db.add(link)
    db.flush()
    return link


def update_link_interfaces(db: Session, link: Link, source_interface: str | None, target_interface: str | None) -> None:
    if source_interface and source_interface != link.source_interface:
        link.source_interface = _pick_free(db, db.get(NodeInstance, link.source_node_id), source_interface, link.id)
    if target_interface and target_interface != link.target_interface:
        link.target_interface = _pick_free(db, db.get(NodeInstance, link.target_node_id), target_interface, link.id)


def set_network(db: Session, node: NodeInstance, network: dict) -> None:
    from app.services.host_net import HostNetError, Segment, validate_segment
    if not is_bridge(node.kind):
        raise TopologyError("Only Network nodes have a network setting")
    seg = Segment.from_dict(network)
    if seg.mode != "trunk":
        seg = Segment(mode=seg.mode, nic=seg.nic if seg.mode == "access" else "", vlan=0)
    try:
        validate_segment(seg)
    except HostNetError as e:
        raise TopologyError(str(e))
    mgmt = bool(network.get("mgmt"))
    if mgmt and seg.mode == "internal":
        raise TopologyError("The management network must be a physical NIC or a VLAN on one")
    others = db.query(NodeInstance).filter(NodeInstance.topology_id == node.topology_id,
                                           NodeInstance.id != node.id, NodeInstance.kind == node.kind).all()
    if seg.mode != "internal" and seg.nic:
        for other in others:
            o = Segment.from_dict(other.network)
            if o.mode == seg.mode and o.nic == seg.nic and o.vlan == seg.vlan:
                raise TopologyError(f"{other.name} already connects this lab to {seg.describe()} — cable devices to it instead")
    if mgmt:
        for other in others:
            if other.network.get("mgmt"):
                raise TopologyError(f"{other.name} is already this lab's management network (a lab has one)")
    node.network = {**seg.to_dict(), "mgmt": mgmt}

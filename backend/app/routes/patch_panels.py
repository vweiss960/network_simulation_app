"""Patch panels — documentation of how existing links physically reach a
device through a patch panel (module: PHANERO_PATCH_PANELS_ENABLED).

Physical path: WAN link -> wan-facing panel (behind) -> jumper ->
customer-facing panel (front) -> device. The device is documented on the
customer-facing port; the WAN link is mirrored on every port of the path.

Documentation only: nothing here creates, modifies or deletes links, nodes or
interfaces, and simulation/routing never reads panel data. Ports may only
reference items that already exist in the topology. Panels are scoped to a
LOCATION of a ROOT topology (child topologies aren't supported yet) and are
visible from every device at that location.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.patch_panel import PatchPanel, PORT_STATUSES
from app.models.topology import Topology

router = APIRouter(prefix="/api/topologies/{topology_id}/patch-panels", tags=["patch-panels"])

MAX_PORTS = 288


class PanelCreate(BaseModel):
    location: str
    name: str
    rack: str = ""
    start_u: Optional[int] = None
    height_u: int = 1
    rows: int = 1
    ports_per_row: int = 24
    notes: str = ""
    in_front_of_id: Optional[int] = None


class PanelUpdate(BaseModel):
    name: Optional[str] = None
    rack: Optional[str] = None
    start_u: Optional[int] = None
    height_u: Optional[int] = None
    rows: Optional[int] = None
    ports_per_row: Optional[int] = None
    notes: Optional[str] = None
    # Explicit null clears the chain; omit to leave unchanged.
    in_front_of_id: Optional[int] = None


class ConnectionCreate(BaseModel):
    port: int        # port on THIS (front) panel
    rear_port: int   # port on the panel directly behind it


class PortUpdate(BaseModel):
    status: str = "unused"
    label: str = ""
    node_id: Optional[int] = None
    interface: str = ""
    link_id: Optional[int] = None
    notes: str = ""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _root_topology(db: Session, topology_id: int) -> Topology:
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    if topo.parent_topology_id:
        raise HTTPException(400, "Patch panels aren't supported on child topologies yet")
    return topo


def _get_panel(db: Session, topology_id: int, panel_id: int) -> PatchPanel:
    p = db.query(PatchPanel).filter(PatchPanel.id == panel_id,
                                    PatchPanel.topology_id == topology_id).first()
    if not p:
        raise HTTPException(404, "Patch panel not found")
    return p


def _validate_geometry(rows: int, ports_per_row: int, height_u: int, start_u: Optional[int]):
    if rows < 1 or ports_per_row < 1:
        raise HTTPException(400, "Rows and ports per row must each be at least 1")
    if rows * ports_per_row > MAX_PORTS:
        raise HTTPException(400, f"A panel can have at most {MAX_PORTS} ports")
    if height_u < 1:
        raise HTTPException(400, "Height must be at least 1U")
    if start_u is not None and start_u < 1:
        raise HTTPException(400, "Start U must be at least 1")


def _validate_in_front_of(db: Session, panel: Optional[PatchPanel], topology_id: int,
                          location: str, target_id: Optional[int]) -> None:
    """target_id must be another panel at the same location, and the chain
    must not loop back to `panel`."""
    if target_id is None:
        return
    target = db.query(PatchPanel).filter(PatchPanel.id == target_id,
                                         PatchPanel.topology_id == topology_id).first()
    if not target:
        raise HTTPException(400, "The panel behind doesn't exist")
    if target.location != location:
        raise HTTPException(400, f"{target.name} isn't at {location}")
    if panel is not None:
        seen, cur = set(), target
        while cur is not None and cur.id not in seen:
            if cur.id == panel.id:
                raise HTTPException(400, "That would make the panels loop back on each other")
            seen.add(cur.id)
            cur = db.query(PatchPanel).get(cur.in_front_of_id) if cur.in_front_of_id else None


def _front_refs(db: Session, panel: PatchPanel) -> dict:
    """{rear port number on `panel`: (front panel, front port key)} for every
    jumper landing on this panel from panels in front of it."""
    out = {}
    for f in db.query(PatchPanel).filter(PatchPanel.in_front_of_id == panel.id).all():
        for k, v in f.ports.items():
            if v.get("rear_port"):
                out[int(v["rear_port"])] = (f, k)
    return out


def _clear_rear_ports(panel: PatchPanel, only: Optional[set] = None) -> None:
    """Drop rear jumpers from `panel`'s ports (all, or those landing on the
    `only` rear port numbers). Ports left empty are removed."""
    ports = panel.ports
    for k in list(ports):
        v = ports[k]
        if v.get("rear_port") and (only is None or int(v["rear_port"]) in only):
            v.pop("rear_port", None)
            if _port_is_empty(v):
                ports.pop(k)
    panel.ports = ports


def _port_is_empty(v: dict) -> bool:
    return ((v.get("status") or "unused") == "unused" and not (v.get("label") or "").strip()
            and v.get("node_id") is None and not v.get("link_id")
            and not (v.get("notes") or "").strip() and not v.get("rear_port"))


def _blank_port() -> dict:
    return {"label": "", "status": "unused", "node_id": None, "interface": "", "link_id": None, "notes": ""}


def _edit_port(panel: PatchPanel, port: int, fn) -> None:
    """Apply `fn` to one port entry (created if missing); empty ports are dropped."""
    ports = panel.ports
    e = ports.get(str(port)) or _blank_port()
    fn(e)
    if _port_is_empty(e):
        ports.pop(str(port), None)
    else:
        ports[str(port)] = e
    panel.ports = ports


def _has_front_panels(db: Session, panel: PatchPanel) -> bool:
    """True for wan-facing (and intermediate) panels: some panel sits in front."""
    return db.query(PatchPanel).filter(PatchPanel.in_front_of_id == panel.id).first() is not None


def _front_chain(db: Session, panel: PatchPanel, port: int) -> list:
    """[(panel, port), ...] jumpered in front of `port`, customer-facing first,
    ending with (panel, port) itself."""
    out, seen = [(panel, port)], {(panel.id, port)}
    while True:
        ref = _front_refs(db, out[0][0]).get(out[0][1])
        if not ref or (ref[0].id, int(ref[1])) in seen:
            return out
        seen.add((ref[0].id, int(ref[1])))
        out.insert(0, (ref[0], int(ref[1])))


def _rear_chain(db: Session, panel: PatchPanel, port: int) -> list:
    """(panel, port) followed by the ports its rear jumpers lead to, ending at
    the wan-facing end."""
    out, seen = [(panel, port)], {(panel.id, port)}
    while True:
        cur, n = out[-1]
        rp = cur.ports.get(str(n), {}).get("rear_port")
        behind = db.query(PatchPanel).get(cur.in_front_of_id) if rp and cur.in_front_of_id else None
        if behind is None or (behind.id, int(rp)) in seen:
            return out
        seen.add((behind.id, int(rp)))
        out.append((behind, int(rp)))


def _path(db: Session, panel: PatchPanel, port: int) -> list:
    """The whole jumpered path through (panel, port): customer-facing port
    first, wan-facing port last."""
    return _front_chain(db, panel, port)[:-1] + _rear_chain(db, panel, port)


def _is_wan_link(db: Session, link: Link, location: str) -> bool:
    """A link leaving the site: one end on a device here, the other on a
    network (non-customer) device at a different location."""
    ends = db.query(NodeInstance).filter(
        NodeInstance.id.in_([link.source_node_id, link.target_node_id])).all()
    here = any((n.location or "") == location for n in ends)
    away = any((n.location or "") != location and (n.role or "router") != "customer" for n in ends)
    return here and away


def _port_name(panel: PatchPanel, port: int) -> str:
    return f"{panel.name} port {port}"


def _iface_names(node: NodeInstance) -> set:
    return {i.get("name") for i in (node.interfaces or []) if isinstance(i, dict) and i.get("name")}


def _panel_to_dict(p: PatchPanel, db: Session) -> dict:
    """Panel + display-enriched ports (names resolved; missing refs flagged)."""
    ports = p.ports
    node_ids = {v.get("node_id") for v in ports.values() if v.get("node_id")}
    link_ids = {v.get("link_id") for v in ports.values() if v.get("link_id")}
    links = {l.id: l for l in db.query(Link).filter(Link.id.in_(link_ids)).all()} if link_ids else {}
    for l in links.values():
        node_ids.update({l.source_node_id, l.target_node_id})
    nodes = {n.id: n for n in db.query(NodeInstance).filter(NodeInstance.id.in_(node_ids)).all()} if node_ids else {}

    out_ports = {}
    for key, v in ports.items():
        e = dict(v)
        missing = False
        nid, lid = v.get("node_id"), v.get("link_id")
        if nid:
            n = nodes.get(nid)
            e["node_name"] = n.name if n else None
            missing |= n is None
        if lid:
            l = links.get(lid)
            if l is None:
                missing = True
            else:
                if nid:
                    near_is_src = l.source_node_id == nid
                else:
                    # No device side documented (e.g. a wan-facing port):
                    # the near end is the link endpoint at this location.
                    src = nodes.get(l.source_node_id)
                    near_is_src = bool(src and (src.location or "") == p.location)
                far_id = l.target_node_id if near_is_src else l.source_node_id
                far = nodes.get(far_id)
                e["link"] = {
                    "far_node": far.name if far else "?",
                    "far_role": (far.role or "router") if far else "",
                    "far_interface": (l.target_interface if near_is_src else l.source_interface) or "",
                    "carrier": l.carrier or "",
                    "circuit_id": l.circuit_id or "",
                    "bandwidth_gbps": l.bandwidth_gbps,
                    "is_planned": bool(l.is_planned),
                }
        e["missing"] = missing
        out_ports[key] = e

    return {
        "id": p.id, "topology_id": p.topology_id, "location": p.location,
        "name": p.name, "rack": p.rack or "", "start_u": p.start_u,
        "height_u": p.height_u or 1, "rows": p.rows, "ports_per_row": p.ports_per_row,
        "notes": p.notes or "", "in_front_of_id": p.in_front_of_id, "ports": out_ports,
    }


def strip_patch_panel_references(db: Session, node_ids=(), link_ids=()) -> None:
    """Called when nodes/links are deleted: clear their references from ports
    but keep each port's status, label and notes (the physical cabling record
    survives; the port is shown as needing attention). No commit."""
    node_ids, link_ids = set(node_ids), set(link_ids)
    if not node_ids and not link_ids:
        return
    for p in db.query(PatchPanel).all():
        ports, changed = p.ports, False
        for v in ports.values():
            if v.get("node_id") in node_ids:
                v["node_id"], v["interface"] = None, ""
                v["link_id"] = None
                v["dangling"] = True
                changed = True
            if v.get("link_id") in link_ids:
                v["link_id"] = None
                v["dangling"] = True
                changed = True
        if changed:
            p.ports = ports


def delete_topology_panels(db: Session, topology_id: int) -> None:
    db.query(PatchPanel).filter(PatchPanel.topology_id == topology_id).delete()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@router.get("/")
def list_panels(topology_id: int, location: Optional[str] = Query(None), db: Session = Depends(get_db)):
    _root_topology(db, topology_id)
    q = db.query(PatchPanel).filter(PatchPanel.topology_id == topology_id)
    if location is not None:
        q = q.filter(PatchPanel.location == location)
    return [_panel_to_dict(p, db) for p in q.order_by(PatchPanel.name).all()]


@router.post("/")
def create_panel(topology_id: int, data: PanelCreate, db: Session = Depends(get_db)):
    _root_topology(db, topology_id)
    location, name = data.location.strip(), data.name.strip()
    if not location:
        raise HTTPException(400, "A patch panel needs a location")
    if not name:
        raise HTTPException(400, "A patch panel needs a name")
    _validate_geometry(data.rows, data.ports_per_row, data.height_u, data.start_u)
    if db.query(PatchPanel).filter(PatchPanel.topology_id == topology_id,
                                   PatchPanel.location == location,
                                   PatchPanel.name == name).first():
        raise HTTPException(409, f"A panel named '{name}' already exists at {location}")
    _validate_in_front_of(db, None, topology_id, location, data.in_front_of_id)
    p = PatchPanel(topology_id=topology_id, location=location, name=name, rack=data.rack,
                   start_u=data.start_u, height_u=data.height_u, rows=data.rows,
                   ports_per_row=data.ports_per_row, notes=data.notes, ports_json="{}",
                   in_front_of_id=data.in_front_of_id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _panel_to_dict(p, db)


@router.put("/{panel_id}")
def update_panel(topology_id: int, panel_id: int, data: PanelUpdate,
                 force: bool = Query(False), db: Session = Depends(get_db)):
    _root_topology(db, topology_id)
    p = _get_panel(db, topology_id, panel_id)
    rows = data.rows if data.rows is not None else p.rows
    ppr = data.ports_per_row if data.ports_per_row is not None else p.ports_per_row
    height = data.height_u if data.height_u is not None else (p.height_u or 1)
    start = data.start_u if "start_u" in data.model_fields_set else p.start_u
    _validate_geometry(rows, ppr, height, start)
    # Shrinking drops ports beyond the new size — refuse unless forced when
    # any of them carry data (including jumpers landing on them from panels
    # in front).
    front_refs = _front_refs(db, p)
    lost = sorted({int(k) for k in p.ports if int(k) > rows * ppr}
                  | {n for n in front_refs if n > rows * ppr})
    if lost and not force:
        raise HTTPException(409, {"message": "Resizing would remove ports that have data",
                                  "ports": lost})
    # Moving the panel to sit in front of a different panel invalidates its
    # existing rear jumpers.
    chain_changed = ("in_front_of_id" in data.model_fields_set
                     and data.in_front_of_id != p.in_front_of_id)
    if chain_changed:
        _validate_in_front_of(db, p, topology_id, p.location, data.in_front_of_id)
        jumpered = sorted(int(k) for k, v in p.ports.items() if v.get("rear_port"))
        if jumpered and not force:
            raise HTTPException(409, {"message": "Changing the panel behind removes this panel's port connections",
                                      "ports": jumpered})
    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(400, "A patch panel needs a name")
        if name != p.name and db.query(PatchPanel).filter(
                PatchPanel.topology_id == topology_id, PatchPanel.location == p.location,
                PatchPanel.name == name).first():
            raise HTTPException(409, f"A panel named '{name}' already exists at {p.location}")
        p.name = name
    if data.rack is not None:
        p.rack = data.rack
    if data.notes is not None:
        p.notes = data.notes
    p.start_u, p.height_u, p.rows, p.ports_per_row = start, height, rows, ppr
    if lost:
        p.ports = {k: v for k, v in p.ports.items() if int(k) <= rows * ppr}
        dropped = {n for n in front_refs if n > rows * ppr}
        for f in {id(fp): fp for fp, _ in front_refs.values()}.values():
            _clear_rear_ports(f, only=dropped)
    if chain_changed:
        _clear_rear_ports(p)
        p.in_front_of_id = data.in_front_of_id
    db.commit()
    db.refresh(p)
    return _panel_to_dict(p, db)


@router.put("/{panel_id}/ports/{port}")
def update_port(topology_id: int, panel_id: int, port: int, data: PortUpdate,
                db: Session = Depends(get_db)):
    """Upsert one port. References must point at EXISTING topology items; this
    never changes the referenced link/node/interface. The link is mirrored onto
    every port jumpered to this one, so both ends of a jumper always agree."""
    _root_topology(db, topology_id)
    p = _get_panel(db, topology_id, panel_id)
    if port < 1 or port > p.port_count:
        raise HTTPException(400, f"Port must be between 1 and {p.port_count}")
    if data.status not in PORT_STATUSES:
        raise HTTPException(400, f"Status must be one of {', '.join(PORT_STATUSES)}")

    node_id, interface, link_id = data.node_id, (data.interface or "").strip(), data.link_id
    path = _path(db, p, port)
    chained = len(path) > 1
    # Wan-facing (and intermediate) panels face the WAN: the device is
    # documented on the customer-facing port.
    wan_side = _has_front_panels(db, p)
    if wan_side and (node_id is not None or interface):
        start = path[0]
        where = f" ({_port_name(*start)})" if start[0].id != p.id else ""
        raise HTTPException(400, "Wan-facing ports carry the WAN link only — document the device "
                                 f"on the customer-facing port{where}")
    if link_id:
        link = db.query(Link).filter(Link.id == link_id, Link.topology_id == topology_id).first()
        if not link:
            raise HTTPException(400, "That link doesn't exist in this topology")
        ends = db.query(NodeInstance).filter(
            NodeInstance.id.in_([link.source_node_id, link.target_node_id]),
            NodeInstance.location == p.location).all()
        if not ends:
            raise HTTPException(400, f"That link doesn't land on a device at {p.location}")
        if (chained or wan_side) and not _is_wan_link(db, link, p.location):
            raise HTTPException(400, f"That link doesn't leave {p.location} — wan-facing ports and the "
                                     f"ports jumpered to them carry WAN links")
        if not wan_side:
            if node_id is None:
                # Device side defaults to the link's endpoint at this location.
                if len(ends) != 1:
                    raise HTTPException(400, "Pick which device at this location the link lands on")
                node_id = ends[0].id
            if node_id not in (link.source_node_id, link.target_node_id):
                raise HTTPException(400, "That link isn't connected to the selected device")
            link_iface = link.source_interface if link.source_node_id == node_id else link.target_interface
            if not interface:
                interface = link_iface or ""
            elif link_iface and interface != link_iface:
                raise HTTPException(400, f"The link lands on {link_iface}, not {interface}")
        # The link is mirrored along the path: it must reach the device
        # documented on any other port of it.
        for pp, n in path:
            other = pp.ports.get(str(n), {}).get("node_id")
            if (pp.id, n) != (p.id, port) and other is not None \
                    and other not in (link.source_node_id, link.target_node_id):
                raise HTTPException(400, f"{_port_name(pp, n)} documents a device that link doesn't "
                                         f"connect to — clear it first")

    if node_id is not None:
        node = db.query(NodeInstance).filter(NodeInstance.id == node_id,
                                             NodeInstance.topology_id == topology_id).first()
        if not node:
            raise HTTPException(400, "That device doesn't exist in this topology")
        # Customer devices can be patched from any site; network devices must
        # be at this location.
        if (node.role or "router") != "customer" and (node.location or "") != p.location:
            raise HTTPException(400, f"{node.name} isn't at {p.location}")
        if interface and interface not in _iface_names(node):
            raise HTTPException(400, f"{node.name} has no interface {interface}")
    elif interface:
        raise HTTPException(400, "Pick a device for the interface")

    def apply(e):
        e.update({"label": data.label.strip(), "status": data.status, "node_id": node_id,
                  "interface": interface, "link_id": link_id, "notes": data.notes})
        e.pop("dangling", None)
    _edit_port(p, port, apply)  # rear_port (if any) is kept: jumpers are edited via /connections
    for pp, n in path:
        if not (pp.id == p.id and n == port):
            _sync_port(pp, n, link_id, data.status)
    db.commit()
    db.refresh(p)
    return _panel_to_dict(p, db)


def _sync_port(panel: PatchPanel, port: int, link_id: Optional[int], status: str) -> None:
    """Mirror the link and status onto a jumpered port: both ends of a jumper
    are the same circuit, so they're cabled/reserved/used together."""
    def fn(e):
        e["link_id"], e["status"] = link_id, status
        if link_id:
            e.pop("dangling", None)
    _edit_port(panel, port, fn)


@router.put("/{panel_id}/connections")
def connect_ports(topology_id: int, panel_id: int, data: ConnectionCreate, db: Session = Depends(get_db)):
    """Jumper `port` on this panel to `rear_port` on the panel directly behind
    it — the wan-facing side (replacing any jumper either port already had).
    The resulting path carries one WAN link and one status (the most advanced
    of the ports joined), mirrored on every port, and the
    device is documented on its customer-facing port."""
    _root_topology(db, topology_id)
    p = _get_panel(db, topology_id, panel_id)
    if not p.in_front_of_id:
        raise HTTPException(400, f"{p.name} isn't in front of another panel")
    behind = _get_panel(db, topology_id, p.in_front_of_id)
    if not 1 <= data.port <= p.port_count:
        raise HTTPException(400, f"{p.name} port must be between 1 and {p.port_count}")
    if not 1 <= data.rear_port <= behind.port_count:
        raise HTTPException(400, f"{behind.name} port must be between 1 and {behind.port_count}")

    # The path as it will be once connected (existing jumpers off either port
    # toward the other side are replaced).
    path = _front_chain(db, p, data.port) + _rear_chain(db, behind, data.rear_port)
    entries = [(pp, n, pp.ports.get(str(n), {})) for pp, n in path]
    with_link = [(pp, n, e) for pp, n, e in entries if e.get("link_id")]
    if len({e["link_id"] for _, _, e in with_link}) > 1:
        a, b = with_link[0], next(x for x in with_link if x[2]["link_id"] != with_link[0][2]["link_id"])
        raise HTTPException(400, f"{_port_name(a[0], a[1])} and {_port_name(b[0], b[1])} carry different "
                                 f"links — clear one of them first")
    link_id = with_link[0][2]["link_id"] if with_link else None
    if link_id:
        link = db.query(Link).get(link_id)
        if link and not _is_wan_link(db, link, p.location):
            pp, n, _ = with_link[0]
            raise HTTPException(400, f"{_port_name(pp, n)} carries a link that doesn't leave {p.location} — "
                                     f"only WAN links run through wan-facing panels")
    # The device belongs on the customer-facing port (the start of the path):
    # one documented further along (e.g. before the panels were chained) is
    # MOVED there instead of making the user clear it by hand.
    start_panel, start_port, start_e = entries[0]
    devs = {(e["node_id"], e.get("interface") or ""): (pp, n, e)
            for pp, n, e in entries[1:] if e.get("node_id") is not None}
    if len(devs) > 1:
        raise HTTPException(400, "More than one device is documented along this path — clear all but one first")
    move = next(iter(devs.items()), None)
    if move and start_e.get("node_id") is not None and \
            (start_e["node_id"], start_e.get("interface") or "") != move[0]:
        raise HTTPException(400, f"{_port_name(start_panel, start_port)} already documents a different "
                                 f"device interface — clear one of them first")
    dev_id = move[0][0] if move else start_e.get("node_id")
    if link_id and link and dev_id is not None and dev_id not in (link.source_node_id, link.target_node_id):
        pp, n, _ = with_link[0]
        raise HTTPException(400, f"{_port_name(pp, n)} carries a link that doesn't connect to the device "
                                 f"documented on this path — clear one of them first")

    # One jumper per rear port: unhook whichever front port held it.
    taken = _front_refs(db, behind).get(data.rear_port)
    if taken and not (taken[0].id == p.id and taken[1] == str(data.port)):
        _clear_rear_ports(taken[0], only={data.rear_port})
    _edit_port(p, data.port, lambda e: e.update(rear_port=data.rear_port))
    if move:
        (nid, iface), (_, _, src) = move
        for pp, n, e in entries[1:]:
            if e.get("node_id") is not None:
                _edit_port(pp, n, lambda e: e.update(node_id=None, interface=""))

        def put_device(e):
            e["node_id"], e["interface"] = nid, iface
            if (e.get("status") or "unused") == "unused":
                e["status"] = src.get("status") or "unused"
        _edit_port(start_panel, start_port, put_device)
    # The joined path takes the most advanced status either side had.
    status = max((e.get("status") or "unused" for _, _, e in entries), key=PORT_STATUSES.index)
    if link_id or status != "unused":
        for pp, n in path:
            _sync_port(pp, n, link_id, status)
    db.commit()
    db.refresh(p)
    out = _panel_to_dict(p, db)
    out["moved_device_to"] = {"panel": start_panel.name, "port": start_port} if move else None
    return out


@router.delete("/{panel_id}/connections/{port}")
def disconnect_port(topology_id: int, panel_id: int, port: int, db: Session = Depends(get_db)):
    """Remove the rear jumper from `port` on this panel."""
    _root_topology(db, topology_id)
    p = _get_panel(db, topology_id, panel_id)
    ports = p.ports
    v = ports.get(str(port))
    if v and v.pop("rear_port", None) is not None and _port_is_empty(v):
        ports.pop(str(port))
    p.ports = ports
    db.commit()
    db.refresh(p)
    return _panel_to_dict(p, db)


@router.delete("/{panel_id}")
def delete_panel(topology_id: int, panel_id: int, db: Session = Depends(get_db)):
    _root_topology(db, topology_id)
    p = _get_panel(db, topology_id, panel_id)
    # Panels in front of this one lose their chain + jumpers (their own port
    # documentation stays).
    for f in db.query(PatchPanel).filter(PatchPanel.in_front_of_id == p.id).all():
        _clear_rear_ports(f)
        f.in_front_of_id = None
    db.delete(p)
    db.commit()
    return {"ok": True}

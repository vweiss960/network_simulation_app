"""Site templates — validate a site definition and drop it into a topology.

A site template is a small, reusable site: 1-4 devices (each built from a
device template), the links between them, and the site's patch panels with
their port documentation and jumpers already in place. Any text field may
hold {{variable}} placeholders; the user fills the variables in when adding
the site, and every node / link / panel is created in one transaction.

Definition shape (SiteTemplate.definition_json):
{
  "variables": [{"name": "site", "label": "Site code", "default": "", "required": true}],
  "nodes": [{"key": "n1", "template_id": 3, "name": "{{site}}-pe1",
             "location": "{{location}}", "mgmt_ip": "", "is_planned": false,
             "dx": 0, "dy": 0,                   # dx/dy: offset from the drop point
             "reserved_interfaces": ["Gi0/0/0/9"]}],  # created with status "reserved"
  "links": [{"key": "l1", "source": "n1", "source_interface": "Hu0/0/0/0",
             "target": "n2", "target_interface": "Hu0/0/0/0", "latency_ms": 1.0,
             "carrier": "", "circuit_id": "", "link_id_label": "", "is_planned": false}],
  "patch_panels": [{"key": "p1", "name": "PP-{{site}}-01", "location": "{{location}}",
                    "rack": "", "start_u": null, "height_u": 1, "rows": 1,
                    "ports_per_row": 24, "notes": "", "in_front_of": null,   # panel key behind
                    "ports": {"1": {"status": "cabled_used", "label": "", "node": "n1",
                                    "interface": "Gi0/0/0/1", "link": "l1", "notes": "",
                                    "rear_port": 5}}}]
}
Nodes, links and panels reference each other by `key`, never by database id.
Panel rules mirror routes/patch_panels.py: devices are documented on
customer-facing ports only, and a jumpered path carries one status. Links in a
template stay inside the site, so they can't run through chained panels (those
carry WAN links, which don't exist until the site is connected).
"""
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.services.latency import infer_latency_source
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.models.patch_panel import PatchPanel, PORT_STATUSES
from app.models.topology import Topology
from app.routes.links import REFERENCE_BANDWIDTH_GBPS  # same auto-cost rule
from app.routes.patch_panels import MAX_PORTS as MAX_PANEL_PORTS  # same cap

MAX_NODES = 4
MAX_PANELS = 8

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
VAR_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

NODE_TEXT = ("name", "location", "mgmt_ip")
LINK_TEXT = ("carrier", "circuit_id", "link_id_label")
PANEL_TEXT = ("name", "location", "rack", "notes")
PORT_TEXT = ("label", "notes")


class SiteTemplateError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _err(msg: str, status: int = 400):
    raise SiteTemplateError(msg, status)


def _s(v) -> str:
    return "" if v is None else str(v)


def _num(v, default: float, what: str) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        _err(f"{what} must be a number")


def _opt_int(v, what: str) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        _err(f"{what} must be a whole number")


def _items(v, what: str) -> list[dict]:
    """A list of objects from the definition; anything else is a 400."""
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        _err(f"{what} must be a list of objects")
    return v


def _int_or(v, default: int, what: str) -> int:
    n = _opt_int(v, what)
    return default if n is None else n


def template_interfaces(t: NodeTemplate) -> list[dict]:
    return [i for i in (t.default_interfaces or []) if isinstance(i, dict) and i.get("name")]


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def normalize_definition(db: Session, raw) -> dict:
    """Validate a site definition and return it cleaned up (types coerced,
    missing keys filled). Raises SiteTemplateError with a user-facing message."""
    if not isinstance(raw, dict):
        _err("A site template definition must be an object")

    # Variables
    variables, var_names = [], set()
    for v in _items(raw.get("variables"), "variables"):
        name = _s(v.get("name")).strip()
        if not VAR_NAME_RE.fullmatch(name):
            _err(f"Variable name '{name}' must be letters, digits or _ and can't start with a digit")
        if name in var_names:
            _err(f"Variable '{name}' is defined twice")
        var_names.add(name)
        variables.append({"name": name, "label": _s(v.get("label")).strip(),
                          "default": _s(v.get("default")), "required": bool(v.get("required", True))})

    # Nodes
    raw_nodes = _items(raw.get("nodes"), "nodes")
    if not raw_nodes:
        _err("A site template needs at least one device")
    if len(raw_nodes) > MAX_NODES:
        _err(f"A site template can have at most {MAX_NODES} devices")
    tmpl_ids = {_opt_int(n.get("template_id"), "Device template") for n in raw_nodes}
    templates = {t.id: t for t in db.query(NodeTemplate).filter(NodeTemplate.id.in_(tmpl_ids - {None})).all()}
    nodes, node_ifaces, raw_reserved = [], {}, {}  # node_ifaces: node key -> {iface name: speed}
    for i, n in enumerate(raw_nodes):
        key = _s(n.get("key")).strip() or f"n{i + 1}"
        if key in node_ifaces:
            _err(f"Device key '{key}' is used twice")
        label = _s(n.get("name")).strip() or key
        tid = _opt_int(n.get("template_id"), f"{label}: device template")
        t = templates.get(tid)
        if t is None:
            _err(f"{label}: pick a device template" if tid is None
                 else f"{label}: its device template no longer exists — pick another")
        if not _s(n.get("name")).strip():
            _err(f"Device {i + 1} needs a name (e.g. {{{{site}}}}-r{i + 1})")
        node_ifaces[key] = {it["name"]: it.get("speed_gbps") for it in template_interfaces(t)}
        nodes.append({
            "key": key, "template_id": t.id,
            "name": _s(n.get("name")).strip(), "location": _s(n.get("location")).strip(),
            "mgmt_ip": _s(n.get("mgmt_ip")).strip(), "is_planned": bool(n.get("is_planned")),
            "dx": _num(n.get("dx"), 0.0, f"{label}: x offset"),
            "dy": _num(n.get("dy"), 0.0, f"{label}: y offset"),
        })
        raw_reserved[key] = n.get("reserved_interfaces")
    node_label = {n["key"]: n["name"] for n in nodes}

    # Links
    links, link_by_key, used_ifaces = [], {}, set()
    for i, l in enumerate(_items(raw.get("links"), "links")):
        key = _s(l.get("key")).strip() or f"l{i + 1}"
        if key in link_by_key:
            _err(f"Link key '{key}' is used twice")
        src, tgt = _s(l.get("source")), _s(l.get("target"))
        si, ti = _s(l.get("source_interface")).strip(), _s(l.get("target_interface")).strip()
        what = f"Link {i + 1}"
        if src not in node_ifaces or tgt not in node_ifaces:
            _err(f"{what}: pick both devices")
        if src == tgt:
            _err(f"{what}: a link must join two different devices")
        for nk, iface in ((src, si), (tgt, ti)):
            if not iface:
                _err(f"{what}: pick an interface on {node_label[nk]}")
            if iface not in node_ifaces[nk]:
                _err(f"{what}: {node_label[nk]} has no interface {iface}")
            if (nk, iface) in used_ifaces:
                _err(f"{what}: {node_label[nk]} {iface} is already used by another link")
            used_ifaces.add((nk, iface))
        if node_ifaces[src][si] != node_ifaces[tgt][ti]:
            _err(f"{what}: interface speed mismatch ({si} is {node_ifaces[src][si]}G, "
                 f"{ti} is {node_ifaces[tgt][ti]}G)")
        latency = _num(l.get("latency_ms"), 1.0, f"{what}: latency")
        if latency < 0:
            _err(f"{what}: latency can't be negative")
        link = {"key": key, "source": src, "source_interface": si, "target": tgt, "target_interface": ti,
                "latency_ms": latency, "carrier": _s(l.get("carrier")).strip(),
                "circuit_id": _s(l.get("circuit_id")).strip(),
                "link_id_label": _s(l.get("link_id_label")).strip(),
                "is_planned": bool(l.get("is_planned"))}
        links.append(link)
        link_by_key[key] = link

    # Reserved ports: real interfaces on the device, and not cabled by a link
    # here (a reserved port can't carry a link in the topology either).
    for n in nodes:
        raw_res = raw_reserved[n["key"]]
        if raw_res is None:
            raw_res = []
        if not isinstance(raw_res, list):
            _err(f"{n['name']}: reserved_interfaces must be a list of interface names")
        reserved = []
        for iface in (_s(x).strip() for x in raw_res):
            if not iface or iface in reserved:
                continue
            if iface not in node_ifaces[n["key"]]:
                _err(f"{n['name']}: can't reserve {iface} — the device has no such interface")
            if (n["key"], iface) in used_ifaces:
                _err(f"{n['name']}: {iface} is used by a link, so it can't be reserved")
            reserved.append(iface)
        n["reserved_interfaces"] = reserved

    panels = _normalize_panels(_items(raw.get("patch_panels"), "patch_panels"), node_ifaces, node_label, link_by_key)

    # Every placeholder must be a declared variable.
    texts = ([n[f] for n in nodes for f in NODE_TEXT] + [l[f] for l in links for f in LINK_TEXT]
             + [p[f] for p in panels for f in PANEL_TEXT]
             + [port[f] for p in panels for port in p["ports"].values() for f in PORT_TEXT])
    undefined = sorted({m for t in texts for m in VAR_RE.findall(t)} - var_names)
    if undefined:
        _err("Undefined variable" + ("s " if len(undefined) > 1 else " ")
             + ", ".join("{{" + u + "}}" for u in undefined) + " — add to the variables list")

    return {"variables": variables, "nodes": nodes, "links": links, "patch_panels": panels}


def _normalize_panels(raw_panels, node_ifaces, node_label, link_by_key) -> list[dict]:
    if len(raw_panels) > MAX_PANELS:
        _err(f"A site template can have at most {MAX_PANELS} patch panels")
    panels, by_key = [], {}
    for i, p in enumerate(raw_panels):
        key = _s(p.get("key")).strip() or f"p{i + 1}"
        if key in by_key:
            _err(f"Patch panel key '{key}' is used twice")
        name = _s(p.get("name")).strip()
        if not name:
            _err(f"Patch panel {i + 1} needs a name")
        rows = _int_or(p.get("rows"), 1, f"{name}: rows")
        ppr = _int_or(p.get("ports_per_row"), 24, f"{name}: ports per row")
        height = _int_or(p.get("height_u"), 1, f"{name}: height")
        start_u = _opt_int(p.get("start_u"), f"{name}: start U")
        if rows < 1 or ppr < 1:
            _err(f"{name}: rows and ports per row must each be at least 1")
        if rows * ppr > MAX_PANEL_PORTS:
            _err(f"{name}: a panel can have at most {MAX_PANEL_PORTS} ports")
        if height < 1 or (start_u is not None and start_u < 1):
            _err(f"{name}: height and start U must be at least 1")
        panel = {"key": key, "name": name, "location": _s(p.get("location")).strip(),
                 "rack": _s(p.get("rack")).strip(), "start_u": start_u, "height_u": height,
                 "rows": rows, "ports_per_row": ppr, "notes": _s(p.get("notes")),
                 "in_front_of": _s(p.get("in_front_of")).strip() or None,
                 "_raw_ports": p.get("ports") or {}}
        panels.append(panel)
        by_key[key] = panel

    names = {p["key"]: p["name"] for p in panels}
    for p in panels:
        behind = p["in_front_of"]
        if behind is None:
            continue
        if behind not in by_key or behind == p["key"]:
            _err(f"{p['name']}: the panel behind it doesn't exist")
        if by_key[behind]["location"] != p["location"]:
            _err(f"{p['name']} and {names[behind]} must be at the same location to be chained")
        seen, cur = {p["key"]}, behind
        while cur is not None:
            if cur in seen:
                _err(f"{p['name']}: the panels loop back on each other")
            seen.add(cur)
            cur = by_key[cur]["in_front_of"]

    has_front = {p["in_front_of"] for p in panels if p["in_front_of"]}
    rear_taken = set()  # (behind panel key, port)
    for p in panels:
        count = p["rows"] * p["ports_per_row"]
        ports = {}
        raw_ports = p.pop("_raw_ports")
        if not isinstance(raw_ports, dict):
            _err(f"{p['name']}: ports must be an object keyed by port number")
        for k, v in raw_ports.items():
            n = _opt_int(k, f"{p['name']}: port number")
            where = f"{p['name']} port {n}"
            if n is None or not 1 <= n <= count:
                _err(f"{p['name']}: port {k} is outside 1-{count}")
            v = v or {}
            if not isinstance(v, dict):
                _err(f"{where}: must be an object")
            status = _s(v.get("status")) or "unused"
            if status not in PORT_STATUSES:
                _err(f"{where}: status must be one of {', '.join(PORT_STATUSES)}")
            node = _s(v.get("node")).strip() or None
            iface = _s(v.get("interface")).strip()
            link = _s(v.get("link")).strip() or None
            rear = _opt_int(v.get("rear_port"), f"{where}: rear port")
            if node is not None and node not in node_ifaces:
                _err(f"{where}: that device isn't in the template")
            if iface and node is None:
                _err(f"{where}: pick a device for the interface")
            if iface and iface not in node_ifaces[node]:
                _err(f"{where}: {node_label[node]} has no interface {iface}")
            if p["key"] in has_front and node is not None:
                _err(f"{where}: wan-facing ports carry the WAN link only — document the device "
                     f"on the customer-facing panel")
            if link is not None:
                l = link_by_key.get(link)
                if l is None:
                    _err(f"{where}: that link isn't in the template")
                if node is None:
                    _err(f"{where}: pick which device the link lands on")
                if node not in (l["source"], l["target"]):
                    _err(f"{where}: that link isn't connected to {node_label[node]}")
                link_iface = l["source_interface"] if l["source"] == node else l["target_interface"]
                if iface and iface != link_iface:
                    _err(f"{where}: the link lands on {link_iface}, not {iface}")
                iface = link_iface
            if rear is not None:
                behind = p["in_front_of"]
                if behind is None:
                    _err(f"{where}: {p['name']} isn't in front of another panel, so it has no rear ports")
                b = by_key[behind]
                if not 1 <= rear <= b["rows"] * b["ports_per_row"]:
                    _err(f"{where}: {b['name']} has no port {rear}")
                if (behind, rear) in rear_taken:
                    _err(f"{b['name']} port {rear} is jumpered from more than one port")
                rear_taken.add((behind, rear))
            entry = {"status": status, "label": _s(v.get("label")).strip(), "node": node,
                     "interface": iface, "link": link, "notes": _s(v.get("notes")), "rear_port": rear}
            if (status != "unused" or entry["label"] or node or link or entry["notes"].strip()
                    or rear is not None):
                ports[str(n)] = entry
        p["ports"] = ports

    # Links stay inside the site: they can't run through chained panels.
    for p in panels:
        for k, e in p["ports"].items():
            if e["link"] and (e["rear_port"] is not None or (p["key"], int(k)) in rear_taken):
                _err(f"{p['name']} port {k}: jumpered paths carry WAN links — a link inside the "
                     f"site can't run through chained panels")
    return panels


# --------------------------------------------------------------------------
# instantiation
# --------------------------------------------------------------------------

def substitute(text: str, values: dict) -> str:
    return VAR_RE.sub(lambda m: values.get(m.group(1), ""), text or "")


def resolve_variables(defn: dict, values: dict) -> dict:
    """Fill each variable from `values`, falling back to its default.
    Raises if a required one is still empty."""
    out, missing = {}, []
    for v in defn["variables"]:
        val = _s((values or {}).get(v["name"])).strip() or v["default"].strip()
        if v["required"] and not val:
            missing.append(v["label"] or v["name"])
        out[v["name"]] = val
    if missing:
        _err("Fill in: " + ", ".join(missing))
    return out


def instantiate(db: Session, topology_id: int, defn: dict, values: dict, x: float, y: float,
                panels_enabled: bool) -> dict:
    """Create the site's nodes, links and patch panels in `topology_id`, the
    nodes offset from (x, y). `defn` must come from normalize_definition.
    Commits on success; on error nothing is written (caller rolls back).
    Returns {"nodes": [NodeInstance], "links": [Link], "patch_panels": int,
    "warnings": [str]}."""
    from app.routes.nodes import xy_to_lat_lng
    from app.routes.patch_panels import _port_is_empty
    from app.services.topology_chain import get_topology_chain

    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        _err("Topology not found", 404)
    vals = resolve_variables(defn, values)

    # Names: non-empty, distinct within the site, free across the chain.
    names = {n["key"]: substitute(n["name"], vals).strip() for n in defn["nodes"]}
    if any(not v for v in names.values()):
        _err("A device name came out empty — check the variables used in it")
    if len(set(names.values())) != len(names):
        _err("Two devices would get the same name — make the name patterns differ")
    chain = get_topology_chain(db, topology_id)
    taken = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain),
                                          NodeInstance.name.in_(list(names.values()))).all()
    if taken:
        _err("Already in the topology: " + ", ".join(sorted(n.name for n in taken)), 409)

    templates = {t.id: t for t in db.query(NodeTemplate).filter(
        NodeTemplate.id.in_([n["template_id"] for n in defn["nodes"]])).all()}
    nodes = {}
    for n in defn["nodes"]:
        t = templates[n["template_id"]]
        nx, ny = round(x + n["dx"], 1), round(y + n["dy"], 1)
        lat, lng = xy_to_lat_lng(nx, ny)
        role = t.role or "router"
        node = NodeInstance(
            topology_id=topology_id, template_id=t.id, name=names[n["key"]],
            vendor=t.vendor, model=t.model, platform=t.platform or "",
            x=nx, y=ny, latitude=lat, longitude=lng,
            interfaces=[{"name": i["name"], "speed_gbps": i.get("speed_gbps"),
                         "description": i.get("description", ""),
                         "status": "reserved" if i["name"] in n["reserved_interfaces"] else "free"}
                        for i in template_interfaces(t)],
            mtbf_hours=t.default_mtbf_hours,
            mttr_hours=0 if role == "customer" else t.default_mttr_hours,
            capacity_gbps=t.capacity_gbps if t.capacity_gbps is not None else 100.0,
            location=substitute(n["location"], vals).strip(),
            mgmt_ip=substitute(n["mgmt_ip"], vals).strip(),
            is_planned=1 if n["is_planned"] else 0, role=role,
        )
        db.add(node)
        nodes[n["key"]] = node
    db.flush()

    links = {}
    for l in defn["links"]:
        src, tgt = nodes[l["source"]], nodes[l["target"]]
        speed = next(i["speed_gbps"] for i in src.interfaces if i["name"] == l["source_interface"])
        bw = float(speed or 10.0)
        link = Link(
            topology_id=topology_id, source_node_id=src.id, target_node_id=tgt.id,
            source_interface=l["source_interface"], target_interface=l["target_interface"],
            bandwidth_gbps=bw, cost=REFERENCE_BANDWIDTH_GBPS / bw if bw else 1.0,
            latency_ms=l["latency_ms"], latency_source=infer_latency_source(l["latency_ms"]),
            carrier=substitute(l["carrier"], vals).strip(),
            circuit_id=substitute(l["circuit_id"], vals).strip(),
            link_id_label=substitute(l["link_id_label"], vals).strip(),
            is_planned=1 if l["is_planned"] else 0,
        )
        db.add(link)
        links[l["key"]] = link
    db.flush()

    warnings, panel_count = [], 0
    if defn["patch_panels"]:
        n_p = len(defn["patch_panels"])
        what = f"{n_p} patch panel{'s' if n_p > 1 else ''}"
        if not panels_enabled:
            warnings.append(f"The patch panel module is off — the template's {what} weren't added")
        elif topo.parent_topology_id:
            warnings.append(f"Patch panels aren't supported on planning layers — the template's {what} weren't added")
        else:
            panel_count = _create_panels(db, topology_id, defn, vals, nodes, links, _port_is_empty)

    db.commit()
    for obj in [*nodes.values(), *links.values()]:
        db.refresh(obj)
    return {"nodes": list(nodes.values()), "links": list(links.values()),
            "patch_panels": panel_count, "warnings": warnings}


def _create_panels(db: Session, topology_id: int, defn: dict, vals: dict,
                   nodes: dict, links: dict, port_is_empty) -> int:
    resolved = {}
    for p in defn["patch_panels"]:
        name = substitute(p["name"], vals).strip()
        loc = substitute(p["location"], vals).strip()
        if not name:
            _err(f"Patch panel '{p['name']}' name came out empty — check its variables")
        if not loc:
            _err(f"Patch panel {name} needs a location — check its variables")
        resolved[p["key"]] = (name, loc)
    seen = set()
    for name, loc in resolved.values():
        if (loc, name) in seen:
            _err(f"Two patch panels would be named '{name}' at {loc}")
        seen.add((loc, name))
        if db.query(PatchPanel).filter(PatchPanel.topology_id == topology_id, PatchPanel.location == loc,
                                       PatchPanel.name == name).first():
            _err(f"A panel named '{name}' already exists at {loc}", 409)

    # A jumpered path is one circuit: every port on it takes the most
    # advanced status of any of them (same rule as connecting ports by hand).
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for p in defn["patch_panels"]:
        for k, e in p["ports"].items():
            if e["rear_port"] is not None:
                parent[find((p["key"], int(k)))] = find((p["in_front_of"], e["rear_port"]))
    group_status = {}
    for p in defn["patch_panels"]:
        for k, e in p["ports"].items():
            r = find((p["key"], int(k)))
            group_status[r] = max(group_status.get(r, "unused"), e["status"], key=PORT_STATUSES.index)

    rows = {}
    for p in defn["patch_panels"]:
        name, loc = resolved[p["key"]]
        ports = {}
        for k, e in p["ports"].items():
            node = nodes[e["node"]] if e["node"] else None
            if node is not None and (node.role or "router") != "customer" and (node.location or "") != loc:
                _err(f"{name} port {k} documents {node.name}, which isn't at {loc} — give the "
                     f"device and the panel the same location")
            entry = {"label": substitute(e["label"], vals).strip(), "status": e["status"],
                     "node_id": node.id if node else None, "interface": e["interface"],
                     "link_id": links[e["link"]].id if e["link"] else None,
                     "notes": substitute(e["notes"], vals)}
            if e["rear_port"] is not None:
                entry["rear_port"] = e["rear_port"]
            ports[k] = entry
        rows[p["key"]] = ports
    for (pk, port), r in list(parent.items()):
        status = group_status.get(find((pk, port)), "unused")
        e = rows[pk].setdefault(str(port), {"label": "", "status": "unused", "node_id": None,
                                            "interface": "", "link_id": None, "notes": ""})
        e["status"] = max(e["status"], status, key=PORT_STATUSES.index)

    created = {}
    for p in defn["patch_panels"]:
        name, loc = resolved[p["key"]]
        ports = {k: v for k, v in rows[p["key"]].items() if not port_is_empty(v)}
        panel = PatchPanel(topology_id=topology_id, location=loc, name=name,
                           rack=substitute(p["rack"], vals).strip(), start_u=p["start_u"],
                           height_u=p["height_u"], rows=p["rows"], ports_per_row=p["ports_per_row"],
                           notes=substitute(p["notes"], vals))
        panel.ports = ports
        db.add(panel)
        created[p["key"]] = panel
    db.flush()
    for p in defn["patch_panels"]:
        if p["in_front_of"]:
            created[p["key"]].in_front_of_id = created[p["in_front_of"]].id
    return len(created)

"""Topology import / export.

Native format (also the `topology:` block of curriculum lab files):

    name: SP Core
    description: ...
    nodes:
      - name: P1
        kind: cisco_xrd
        image: ios-xr/xrd-control-plane:25.4.2
        icon: router
        interfaces: 4
        position: [100, 200]
        startup_config: |
          interface Loopback0 ...
        enabled: false        # optional: starts powered off
        notes: ...            # optional
    links:
      - [P1, GigabitEthernet0/0/0/0, P2, GigabitEthernet0/0/0/0]
      - [P1, GigabitEthernet0/0/0/1, P3, GigabitEthernet0/0/0/0, "optional label"]

Plain containerlab files (`topology: {nodes, links}`) import too: kernel
interface names in `endpoints` are mapped back to device-native names.
"""

from __future__ import annotations

import yaml
from sqlalchemy.orm import Session

from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.topology import Topology
from app.services.device_kinds import get_kind, is_known_kind
from app.services.topology_ops import TopologyError, create_link, create_node


def export_topology(db: Session, topology_id: int) -> dict:
    t = db.get(Topology, topology_id)
    if not t:
        raise TopologyError("Topology not found")
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).order_by(NodeInstance.id).all()
    names = {n.id: n.name for n in nodes}
    out_nodes = []
    for n in nodes:
        d = {"name": n.name, "kind": n.kind, "image": n.image_ref or "", "icon": n.icon or "router",
             "interfaces": len(n.interfaces), "position": [round(n.x or 0, 1), round(n.y or 0, 1)]}
        if n.startup_config:
            d["startup_config"] = n.startup_config
        if n.enabled == 0:
            d["enabled"] = False            # starts powered off
        if n.notes:
            d["notes"] = n.notes
        if n.kind == "bridge":
            d.pop("image")
            d["network"] = n.network
        out_nodes.append(d)
    links = db.query(Link).filter(Link.topology_id == topology_id).order_by(Link.id).all()
    return {
        "name": t.name, "description": t.description or "",
        "nodes": out_nodes,
        "links": [[names[l.source_node_id], l.source_interface, names[l.target_node_id], l.target_interface]
                  + ([l.label] if l.label else []) for l in links],
        **({"annotations": t.annotations} if t.annotations else {}),
    }


def export_yaml(db: Session, topology_id: int) -> str:
    return yaml.safe_dump(export_topology(db, topology_id), sort_keys=False, allow_unicode=True)


def _kernel_to_config(kind: str, kernel: str) -> tuple[str, int]:
    k = get_kind(kind)
    for i in range(k.max_interfaces):
        if k.kernel_iface(i) == kernel or k.config_iface(i) == kernel:
            return k.config_iface(i), i + 1
    raise TopologyError(f"Interface '{kernel}' is not valid for kind {kind}")


def _from_clab(doc: dict) -> dict:
    topo = doc.get("topology") or {}
    kinds_defaults = topo.get("kinds") or {}
    nodes, needed = [], {}
    for name, nd in (topo.get("nodes") or {}).items():
        nd = nd or {}
        kind = nd.get("kind") or "linux"
        image = nd.get("image") or (kinds_defaults.get(kind) or {}).get("image", "")
        nodes.append({"name": name, "kind": kind, "image": image})
        needed[name] = 0
    by_name = {n["name"]: n for n in nodes}
    links = []
    for l in topo.get("links") or []:
        eps = l.get("endpoints") or []
        if len(eps) != 2:
            continue
        row = []
        for ep in eps:
            nname, _, kernel = ep.partition(":")
            if nname not in by_name:
                raise TopologyError(f"Link endpoint '{ep}' references an unknown node")
            cfg, idx = _kernel_to_config(by_name[nname]["kind"], kernel)
            needed[nname] = max(needed[nname], idx)
            row += [nname, cfg]
        links.append(row)
    for i, n in enumerate(nodes):
        n["interfaces"] = max(needed[n["name"]], get_kind(n["kind"]).default_interfaces)
        n["position"] = [150 + (i % 5) * 220, 120 + (i // 5) * 180]
    return {"name": doc.get("name") or "Imported lab", "nodes": nodes, "links": links}


def parse(text: str) -> dict:
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise TopologyError(f"Not valid YAML: {e}")
    if not isinstance(doc, dict):
        raise TopologyError("Expected a YAML mapping")
    if isinstance(doc.get("topology"), dict) and "nodes" in doc["topology"] and "links" not in doc:
        return _from_clab(doc)
    if isinstance(doc.get("topology"), dict) and isinstance(doc["topology"].get("nodes"), dict):
        return _from_clab(doc)
    return doc


def build_into(db: Session, topology: Topology, spec: dict) -> None:
    """Create the nodes/links described by `spec` inside an (empty) topology."""
    if isinstance(spec.get("annotations"), list):
        from pydantic import ValidationError
        from app.schemas.schemas import Annotation
        try:
            topology.annotations = [Annotation(**a).model_dump() for a in spec["annotations"][:500]]
        except (TypeError, ValidationError) as e:
            raise TopologyError(f"Invalid annotations: {e}")
    ids: dict[str, int] = {}
    for nd in spec.get("nodes") or []:
        kind = nd.get("kind") or "linux"
        if not is_known_kind(kind):
            raise TopologyError(f"Node {nd.get('name')}: unknown kind '{kind}'")
        pos = nd.get("position") or [0, 0]
        n = create_node(
            db, topology.id, name=nd.get("name"), kind=kind, image_ref=nd.get("image") or "",
            icon=nd.get("icon"), interface_count=nd.get("interfaces"),
            x=float(pos[0]), y=float(pos[1]), startup_config=nd.get("startup_config") or "",
            network=nd.get("network"),
        )
        if nd.get("enabled") is False:
            n.enabled = 0
        if nd.get("notes"):
            n.notes = str(nd["notes"])[:4000]
        ids[n.name] = n.id
    for row in spec.get("links") or []:
        if isinstance(row, dict):
            row = [row.get("a"), row.get("a_interface"), row.get("b"), row.get("b_interface")] + \
                  ([row["label"]] if row.get("label") else [])
        if len(row) not in (4, 5) or row[0] not in ids or row[2] not in ids:
            raise TopologyError(f"Bad link entry {row!r}")
        create_link(db, topology.id, ids[row[0]], ids[row[2]], row[1], row[3],
                    label=str(row[4]) if len(row) == 5 else "")


def import_topology(db: Session, text: str, name: str | None = None) -> Topology:
    spec = parse(text)
    t = Topology(name=name or spec.get("name") or "Imported lab", description=spec.get("description") or "")
    db.add(t)
    db.flush()
    build_into(db, t, spec)
    return t

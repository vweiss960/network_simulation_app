"""Lab view: the runtime projection of a topology.

Every node on a Phanero Labs canvas is a real device (it carries its own
containerlab kind + image), so there is no design→lab translation. This
module just resolves the per-node runtime facts the lab service, console and
YAML generator need: clab-safe names, container names, kernel interface
names for links, and credentials.
"""

import re
from sqlalchemy.orm import Session
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.topology import Topology
from app.services.device_kinds import get_kind, is_bridge
from app.services.host_net import Segment, bridge_port_name

LAB_PREFIX = "plabs"

_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")


def safe_node_name(name: str) -> str:
    """containerlab node names must match [A-Za-z0-9_-]+."""
    s = _NAME_SANITIZE_RE.sub("-", name).strip("-")
    return s or "node"


def safe_lab_name(topology: Topology) -> str:
    return f"{LAB_PREFIX}-{topology.id}-{safe_node_name(topology.name)}"


def node_credentials(node: NodeInstance) -> tuple[str, str]:
    """What the app logs in with: the node's own override, else what it was
    provisioned with (new devices: the kind's default), else — a device made
    before provisioning existed — the image's built-in credentials."""
    k = get_kind(node.kind)
    creds = node.credentials
    base = creds.get("provision") or {}
    bu, bp = k.builtin_credentials
    return (creds.get("username") or base.get("username") or bu,
            creds.get("password") or base.get("password") or bp)


def provision_credentials(node: NodeInstance) -> tuple[str, str] | None:
    """Credentials the device must be booted with, when they differ from what
    the image sets up by itself (None: leave the image's own). Fixed when the
    device is created, so editing the login on the canvas never changes the
    device's definition (which would recreate it)."""
    p = node.credentials.get("provision") or {}
    if not p.get("username"):
        return None
    pair = (p["username"], p.get("password") or "")
    return None if pair == get_kind(node.kind).builtin_credentials else pair


def compute_lab_view(db: Session, topology_id: int) -> dict:
    """Resolve the runtime view of a topology.

    Returns {"lab_name", "nodes": [...], "links": [...], "skipped_nodes": [...]}.
    Node entries keep the historical `lab_*` keys used across the lab modules.
    Link entries carry KERNEL interface names in source/target_interface (what
    veth / netem tooling needs) and the config names in original_*.
    """
    topology = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topology:
        return {"lab_name": f"{LAB_PREFIX}-{topology_id}", "nodes": [], "links": [], "skipped_nodes": []}
    lab_name = safe_lab_name(topology)
    nodes = (
        db.query(NodeInstance)
        .filter(NodeInstance.topology_id == topology_id)
        .order_by(NodeInstance.id)
        .all()
    )
    links = db.query(Link).filter(Link.topology_id == topology_id).order_by(Link.id).all()

    safe_names: dict[int, str] = {}
    used: set[str] = set()
    out_nodes, skipped = [], []
    bridge_names: dict[int, str] = {}
    for n in nodes:
        if not is_bridge(n.kind):
            continue
        seg = Segment.from_dict(n.network)
        if not seg.configured:
            skipped.append({"id": n.id, "name": n.name, "reason": "Network not connected — choose a NIC in its settings"})
            continue
        bname = seg.bridge(topology_id, n.id)
        if bname in used:
            skipped.append({"id": n.id, "name": n.name, "reason": "Another Network node in this lab already uses that NIC / VLAN"})
            continue
        used.add(bname)
        bridge_names[n.id] = bname
    for n in nodes:
        if is_bridge(n.kind):
            continue
        base = safe_node_name(n.name)
        cand, i = base, 1
        while cand in used:
            i += 1
            cand = f"{base}-{i}"
        used.add(cand)
        safe_names[n.id] = cand

    by_id: dict[int, NodeInstance] = {}
    for n in nodes:
        if is_bridge(n.kind):
            if n.id not in bridge_names:
                continue
            seg = Segment.from_dict(n.network)
            by_id[n.id] = n
            out_nodes.append({
                "id": n.id, "topology_id": n.topology_id, "template_id": n.template_id,
                "name": n.name, "clab_name": bridge_names[n.id],
                "lab_kind": n.kind, "lab_image": "", "lab_container_name": None,
                "lab_ssh_user": None, "mgmt_ip": "", "interfaces": n.interfaces, "startup_config": "",
                "min_memory_mb": 0, "min_cpu_cores": 0, "requires_hugepages": False,
                "is_bridge": True, "segment": seg.to_dict(), "bridge": bridge_names[n.id],
            })
            continue
        k = get_kind(n.kind)
        user, _pw = node_credentials(n)
        if n.enabled == 0:
            skipped.append({"id": n.id, "name": n.name, "reason": "Off — start it to bring it up", "off": True})
            continue
        if not n.image_ref:
            skipped.append({"id": n.id, "name": n.name, "reason": "No image set (build or load one under Images)"})
            continue
        by_id[n.id] = n
        out_nodes.append({
            "id": n.id, "topology_id": n.topology_id, "template_id": n.template_id,
            "name": n.name, "clab_name": safe_names[n.id],
            "lab_kind": n.kind, "lab_image": n.image_ref,
            "lab_container_name": f"clab-{lab_name}-{safe_names[n.id]}",
            "lab_ssh_user": user if k.ssh else None,
            "mgmt_ip": n.mgmt_ipv4 or "",
            "interfaces": n.interfaces,
            "startup_config": n.carry_config or n.startup_config or "",
            "min_memory_mb": n.min_memory_mb or k.min_memory_mb,
            "min_cpu_cores": n.min_cpu_cores or k.min_cpu_cores,
            "requires_hugepages": k.requires_hugepages or ("vrouter" in (n.image_ref or "")),
            "is_bridge": False,
        })

    def kernel(node: NodeInstance, iface: str) -> str:
        if is_bridge(node.kind):
            # Bridge ports are host-side veths: must be unique host-wide.
            idx = next((i for i, x in enumerate(node.interfaces) if x.get("name") == iface), 0)
            return bridge_port_name(topology_id, node.id, idx + 1)
        return node.kernel_iface(iface)

    out_links = []
    for l in links:
        s, t = by_id.get(l.source_node_id), by_id.get(l.target_node_id)
        if not s or not t:
            continue
        if is_bridge(s.kind) and is_bridge(t.kind):
            continue  # two bridges can't be cabled by a veth in clab; skip
        out_links.append({
            "id": l.id, "topology_id": l.topology_id,
            "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
            "source_interface": kernel(s, l.source_interface),
            "target_interface": kernel(t, l.target_interface),
            "original_source_interface": l.source_interface,
            "original_target_interface": l.target_interface,
        })

    return {"lab_name": lab_name, "nodes": out_nodes, "links": out_links, "skipped_nodes": skipped}

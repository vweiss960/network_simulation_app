"""Lab view: a derived rendering of a topology with templates and interface
names translated for containerlab deployment.

The design topology uses real device templates (e.g. "Cisco ASR 9001",
"Juniper MX204") and platform-native interface names (e.g.
"TenGigE0/0/0/3"). For deployment we need to swap to containerlab-supported
images and use the naming convention each clab kind expects.

This module computes that translation without modifying the source topology
— callers get a list of derived nodes and links suitable for both rendering
in lab mode and generating containerlab YAML.
"""

import re
from sqlalchemy.orm import Session
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.models.topology import Topology
from app.services.topology_chain import get_merged_topology_data
from app.services.latency import latency_infos


_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_node_name(name: str) -> str:
    s = _NAME_SANITIZE_RE.sub("-", name).strip("-")
    return s or "node"


def _safe_lab_name(topology: Topology) -> str:
    return f"phanero-{topology.id}-{_safe_node_name(topology.name)}"


# Platform → containerlab kind. Used to pick a deployable template when the
# source template doesn't have clab_kind set.
PLATFORM_TO_KIND: dict[str, str] = {
    "iosxr": "cisco_xrd",
    "iosxe": "linux",       # No good lab image for IOS-XE; FRR as generic placeholder
    "ios": "linux",
    "junos": "juniper_crpd",
    "eos": "arista_ceos",
    "srlinux": "nokia_srlinux",
    "linux": "linux",
}
DEFAULT_KIND = "linux"

# Per-kind SSH defaults — used by the "Connect to device" UI action. Kinds
# without SSHD (e.g. plain linux / FRR) are absent here; for those the user
# enters via `docker exec` instead.
SSH_DEFAULTS: dict[str, dict] = {
    "cisco_xrd": {"user": "clab", "port": 22},
    "arista_ceos": {"user": "admin", "port": 22},
    "nokia_srlinux": {"user": "admin", "port": 22},
    "juniper_crpd": {"user": "root", "port": 22},
}

# Sequential host-port assignments start here. Each SSH-capable node gets
# `SSH_HOST_PORT_BASE + index` published to the host's port 22 in clab.
SSH_HOST_PORT_BASE = 22000


def kind_supports_ssh(kind: str) -> bool:
    return kind in SSH_DEFAULTS


# How to format the N-th KERNEL interface name for each clab kind. These
# names appear in:
#   - `ip link show` inside the container,
#   - the `endpoints:` list in the clab YAML (clab uses them to wire veths).
# {n1} = 1-indexed,  {n0} = 0-indexed.
KIND_IFACE_PATTERNS: dict[str, str] = {
    "linux": "eth{n1}",
    "cisco_xrd": "Gi0-0-0-{n0}",
    "cisco_xrv9k": "Gi0-0-0-{n0}",
    "arista_ceos": "eth{n1}",
    "nokia_srlinux": "e1-{n1}",
    "juniper_crpd": "eth{n1}",
}

# How the N-th interface is referenced inside the DEVICE'S OWN CONFIG (e.g.
# `interface GigabitEthernet0/0/0/1 / ipv4 address …` in IOS-XR). For most
# kinds this matches the kernel name; cisco_xrd / cisco_xrv9k / arista_ceos
# / nokia_srlinux all rewrite to their vendor convention. Use
# _format_config_iface() to look up the right one for a kind.
KIND_CONFIG_IFACE_PATTERNS: dict[str, str] = {
    "cisco_xrd": "GigabitEthernet0/0/0/{n0}",
    "cisco_xrv9k": "GigabitEthernet0/0/0/{n0}",
    "arista_ceos": "Ethernet{n1}",
    "nokia_srlinux": "ethernet-1/{n1}",
    # linux / juniper_crpd: config refers to the kernel iface directly.
}


def _format_iface(kind: str, idx: int) -> str:
    pattern = KIND_IFACE_PATTERNS.get(kind, KIND_IFACE_PATTERNS["linux"])
    return pattern.format(n0=idx, n1=idx + 1)


def _format_config_iface(kind: str, idx: int) -> str:
    """Vendor-config form of the N-th interface name (e.g. GigabitEthernet0/0/0/1
    for IOS-XR). Falls back to the kernel name for kinds whose config uses the
    same identifier (linux / cRPD)."""
    pattern = KIND_CONFIG_IFACE_PATTERNS.get(kind)
    if pattern is None:
        return _format_iface(kind, idx)
    return pattern.format(n0=idx, n1=idx + 1)


def _pick_lab_template(db: Session, source_template: NodeTemplate | None, role: str) -> tuple[NodeTemplate | None, str]:
    """Choose a deployable template matching the source. Returns (template, kind).

    Strategy:
      1. If source template already has clab_kind + image_ref set, use it as-is.
      2. Otherwise find an existing deployable template whose clab_kind matches
         the platform mapping.
      3. Fall back to the linux/FRR template if nothing matches.
    Customer-role source nodes return (None, "") so callers can skip them.
    """
    # Customers and satellite dishes / PoPs aren't deployable lab devices.
    if role in ("customer", "sat_dish", "sat_pop"):
        return None, ""

    # Source is already deployable
    if source_template and source_template.clab_kind and source_template.image_ref:
        return source_template, source_template.clab_kind

    # Map source platform → desired kind
    desired_kind = PLATFORM_TO_KIND.get((source_template.platform if source_template else "").lower(), DEFAULT_KIND)

    # Find any existing template with that kind AND an image_ref set (so it's deployable)
    candidate = db.query(NodeTemplate).filter(
        NodeTemplate.clab_kind == desired_kind,
        NodeTemplate.image_ref != "",
    ).first()
    if candidate:
        return candidate, desired_kind

    # Last resort: any deployable template
    fallback = db.query(NodeTemplate).filter(
        NodeTemplate.clab_kind != "",
        NodeTemplate.image_ref != "",
    ).first()
    if fallback:
        return fallback, fallback.clab_kind or DEFAULT_KIND

    return None, ""


def compute_lab_view(db: Session, topology_id: int) -> dict:
    """Build the translated lab view for a topology.

    Returns:
        {
          "nodes": [
            {<all original node fields>,
             "id": <same id>, "name": <same>,
             "lab_template_id": <id of substituted template>,
             "lab_template_name": ..., "lab_kind": "cisco_xrd",
             "lab_image": "ios-xr/xrd-vrouter:25.4.2",
             "lab_interfaces": ["Gi0-0-0-0", "Gi0-0-0-1", ...],
             "lab_translation_notes": "Substituted XRd for ASR 9001"  # optional
            }, ...
          ],
          "links": [
            {<all original link fields>,
             "source_interface": <translated>, "target_interface": <translated>,
             "original_source_interface": <untouched>, "original_target_interface": <untouched>}
          ],
          "skipped_nodes": [{id, name, reason}],
          "deployable_template_ids": [...],
        }
    """
    topology = db.query(Topology).filter(Topology.id == topology_id).first()
    lab_name = _safe_lab_name(topology) if topology else f"phanero-{topology_id}"
    nodes, links, _flows, _remap = get_merged_topology_data(db, topology_id)
    lat = latency_infos(db, nodes, links)

    # Pre-fetch all templates we might reference
    template_ids = {n.template_id for n in nodes if n.template_id}
    tmpls = db.query(NodeTemplate).filter(NodeTemplate.id.in_(template_ids)).all() if template_ids else []
    tmpl_by_id = {t.id: t for t in tmpls}

    # Build per-node interface translation map. Each node's interfaces are
    # renamed in encounter order based on its (new) clab kind's pattern.
    # We collect all interface NAMES the node uses (from its own interfaces
    # list, plus any link references — links are authoritative since they're
    # what we need to translate).
    iface_translation: dict[int, dict[str, str]] = {}  # node_id → {orig_iface → kernel lab_iface}
    # Parallel map producing the VENDOR-CONFIG form of the same interface
    # (e.g. GigabitEthernet0/0/0/1 instead of Gi0-0-0-1). Kept in sync with
    # iface_translation so the indices line up — the kernel name and the
    # config name are just two views of the same physical port.
    iface_config_translation: dict[int, dict[str, str]] = {}
    lab_kinds: dict[int, str] = {}  # node_id → kind
    lab_templates: dict[int, NodeTemplate | None] = {}
    skipped_nodes = []

    for n in nodes:
        src_tmpl = tmpl_by_id.get(n.template_id) if n.template_id else None
        lab_tmpl, kind = _pick_lab_template(db, src_tmpl, n.role or "router")
        if not lab_tmpl:
            if n.role == "customer":
                skipped_nodes.append({"id": n.id, "name": n.name, "reason": "Customer node (not deployable)"})
            elif n.role in ("sat_dish", "sat_pop"):
                skipped_nodes.append({"id": n.id, "name": n.name, "reason": "Satellite node (not deployable)"})
            else:
                skipped_nodes.append({"id": n.id, "name": n.name, "reason": "No deployable template available for this platform"})
            continue
        lab_kinds[n.id] = kind
        lab_templates[n.id] = lab_tmpl
        iface_translation[n.id] = {}
        iface_config_translation[n.id] = {}

    # Walk links, deduplicate interface usage per node in stable order
    for l in links:
        for nid, orig_name in [(l.source_node_id, l.source_interface), (l.target_node_id, l.target_interface)]:
            if nid not in iface_translation:
                continue
            if not orig_name:
                continue
            if orig_name in iface_translation[nid]:
                continue
            kind = lab_kinds[nid]
            idx = len(iface_translation[nid])
            iface_translation[nid][orig_name] = _format_iface(kind, idx)
            iface_config_translation[nid][orig_name] = _format_config_iface(kind, idx)

    # Also include the node's own interface list (for stuff not yet referenced by links)
    for n in nodes:
        if n.id not in iface_translation:
            continue
        kind = lab_kinds[n.id]
        for iface in n.interfaces:
            name = iface.get("name") if isinstance(iface, dict) else None
            if not name or name in iface_translation[n.id]:
                continue
            idx = len(iface_translation[n.id])
            iface_translation[n.id][name] = _format_iface(kind, idx)
            iface_config_translation[n.id][name] = _format_config_iface(kind, idx)

    # Resolve clab container names — this is how containerlab names each
    # spawned docker container. We need this to correlate live status back to
    # the Phanero node it represents. Same sanitize rules + uniqueness
    # collision handling that the YAML generator does.
    safe_names: dict[int, str] = {}
    used: set[str] = set()
    for n in nodes:
        if n.id not in iface_translation:
            continue
        base = _safe_node_name(n.name)
        candidate = base
        suffix = 1
        while candidate in used:
            suffix += 1
            candidate = f"{base}-{suffix}"
        used.add(candidate)
        safe_names[n.id] = candidate

    # Assign SSH host ports in stable order — sequential within the lab so
    # they're predictable. Only for kinds that actually run SSHD.
    ssh_port_by_node: dict[int, int] = {}
    ssh_index = 0
    for n in nodes:
        if n.id not in lab_kinds:
            continue
        if kind_supports_ssh(lab_kinds[n.id]):
            ssh_port_by_node[n.id] = SSH_HOST_PORT_BASE + ssh_index
            ssh_index += 1

    # Build translated node + link records
    out_nodes = []
    for n in nodes:
        if n.id not in iface_translation:
            continue
        lab_tmpl = lab_templates[n.id]
        translation = iface_translation[n.id]
        clab_container_name = f"clab-{lab_name}-{safe_names[n.id]}"
        # Translated interface list — same shape as n.interfaces, with name replaced
        translated_ifaces = []
        for iface in (n.interfaces if isinstance(n.interfaces, list) else []):
            name = iface.get("name") if isinstance(iface, dict) else None
            new_name = translation.get(name, name)
            translated_ifaces.append({
                **iface,
                "name": new_name,
                "speed_gbps": 10.0,  # Speed is irrelevant inside containerlab; flatten to a sane value
                "original_name": name,
            })
        out_nodes.append({
            "id": n.id, "topology_id": n.topology_id, "template_id": n.template_id,
            "name": n.name, "vendor": lab_tmpl.vendor, "model": lab_tmpl.model, "platform": lab_tmpl.platform,
            "x": n.x, "y": n.y,
            "latitude": n.latitude, "longitude": n.longitude,
            "interfaces": translated_ifaces,
            "mtbf_hours": n.mtbf_hours, "mttr_hours": n.mttr_hours,
            "capacity_gbps": lab_tmpl.capacity_gbps, "location": n.location or "",
            "prometheus_id": n.prometheus_id or "",
            "mgmt_ip": n.mgmt_ip or "",
            "is_planned": bool(n.is_planned),
            "role": n.role or "router",
            "customer_id": n.customer_id,
            "color": n.color or "",
            "lab_template_id": lab_tmpl.id,
            "lab_template_name": lab_tmpl.name,
            "lab_kind": lab_kinds[n.id],
            "lab_image": lab_tmpl.image_ref,
            "lab_container_name": clab_container_name,
            "lab_ssh_host_port": ssh_port_by_node.get(n.id),
            "lab_ssh_user": SSH_DEFAULTS.get(lab_kinds[n.id], {}).get("user"),
            # Per-node design→lab interface name mapping. Two flavours:
            #   - lab_iface_translation: KERNEL names (e.g. Gi0-0-0-1). These
            #     go into the clab YAML links section since clab uses them to
            #     wire veth pairs.
            #   - lab_iface_config_translation: VENDOR-CONFIG names (e.g.
            #     GigabitEthernet0/0/0/1). These go into the device's own
            #     running-config — IOS-XR's CLI does not accept the kernel
            #     shorthand. Consumed by the config renderer when producing
            #     generated_lab_config.
            "lab_iface_translation": dict(translation),
            "lab_iface_config_translation": dict(iface_config_translation.get(n.id, {})),
            "lab_translation_notes": (
                None if (n.template_id == lab_tmpl.id) else f"Using {lab_tmpl.name} for lab deployment"
            ),
        })

    out_links = []
    for l in links:
        if l.source_node_id not in iface_translation or l.target_node_id not in iface_translation:
            continue
        src_orig = l.source_interface or ""
        tgt_orig = l.target_interface or ""
        out_links.append({
            "id": l.id, "topology_id": l.topology_id,
            "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
            "source_interface": iface_translation[l.source_node_id].get(src_orig, src_orig),
            "target_interface": iface_translation[l.target_node_id].get(tgt_orig, tgt_orig),
            "original_source_interface": src_orig,
            "original_target_interface": tgt_orig,
            "bandwidth_gbps": 10.0,  # Flattened in lab view
            "cost": l.cost, "latency_ms": lat[l.id].ms or 0.0,
            "latency_effective_ms": lat[l.id].ms, "latency_effective_source": lat[l.id].source,
            "carrier": l.carrier, "mtbf_hours": l.mtbf_hours, "mttr_hours": l.mttr_hours,
            "is_planned": bool(l.is_planned),
            "color": l.color or "",
        })

    return {
        "lab_name": lab_name,
        "nodes": out_nodes,
        "links": out_links,
        "skipped_nodes": skipped_nodes,
        "deployable_template_ids": sorted({n["lab_template_id"] for n in out_nodes}),
    }

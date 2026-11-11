"""Containerlab YAML generator.

Takes a Phanero topology (nodes + links + their templates) and produces a
containerlab topology YAML plus a summary of what was included and skipped.
"""

import ipaddress
import os
import re
import yaml
from sqlalchemy.orm import Session
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.node_template import NodeTemplate
from app.models.topology import Topology
from app.services.topology_chain import get_merged_topology_data
from app.services.lab_view import compute_lab_view


_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _mgmt_gateway(env_var: str, default: str) -> str:
    """Gateway (first host, e.g. .1) of the configured mgmt subnet, or `default`
    (containerlab's default gateway) when the override env var is unset/invalid.
    Used so a node's mgmt default route matches whatever subnet the bridge uses."""
    subnet = os.environ.get(env_var, "").strip()
    if subnet:
        try:
            return str(next(ipaddress.ip_network(subnet, strict=False).hosts()))
        except (ValueError, StopIteration):
            pass
    return default


def _safe_name(name: str) -> str:
    """containerlab requires node names matching [A-Za-z0-9_-]+ — sanitize."""
    s = _NAME_SANITIZE_RE.sub("-", name).strip("-")
    return s or "node"


def _safe_lab_name(topology: Topology) -> str:
    """Lab name unique per topology. clab uses this as a prefix for containers."""
    return f"phanero-{topology.id}-{_safe_name(topology.name)}"


def generate_clab_yaml(db: Session, topology_id: int) -> dict:
    """Generate a containerlab topology YAML for a Phanero topology.

    Uses compute_lab_view() to translate the source design (real device
    templates, platform-native interface names) into the deployable form
    (containerlab-kind images, kind-native interface naming). The same view
    is rendered in lab mode in the UI so what the user sees is what gets
    deployed.

    Returns the YAML plus inclusion / skip lists and a resource estimate.
    """
    topology = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topology:
        raise ValueError(f"Topology {topology_id} not found")

    view = compute_lab_view(db, topology_id)
    lab_nodes = view["nodes"]
    lab_links = view["links"]
    skipped_nodes = list(view["skipped_nodes"])

    # Fetch the actual lab templates we picked (we need startup_config + resource fields)
    template_ids = {n["lab_template_id"] for n in lab_nodes}
    tmpls = db.query(NodeTemplate).filter(NodeTemplate.id.in_(template_ids)).all() if template_ids else []
    tmpl_by_id = {t.id: t for t in tmpls}

    included_nodes = []
    node_name_map: dict[int, str] = {}

    for ln in lab_nodes:
        tmpl = tmpl_by_id.get(ln["lab_template_id"])
        kind = ln["lab_kind"]
        image = ln["lab_image"]
        if not kind or not image:
            skipped_nodes.append({"id": ln["id"], "name": ln["name"], "reason": "Lab view did not resolve an image"})
            continue
        safe = _safe_name(ln["name"])
        base, i = safe, 1
        while safe in node_name_map.values():
            i += 1
            safe = f"{base}-{i}"
        node_name_map[ln["id"]] = safe
        from app.services.clab_kind_defaults import apply_defaults_to_template_fields
        tmpl_resources = apply_defaults_to_template_fields(kind, {
            "min_memory_mb": tmpl.min_memory_mb if tmpl else 0,
            "min_cpu_cores": tmpl.min_cpu_cores if tmpl else 0,
            "requires_hugepages": bool(tmpl.requires_hugepages) if tmpl else False,
        })
        # Prefer the Config-Design-generated lab config (rendered with
        # lab-translated interface names) over the template's static
        # startup_config — that way the deployed container actually boots
        # with the user's design applied, not the template's placeholder.
        from app.models.node_instance import NodeInstance as _NI
        ni = db.query(_NI).filter(_NI.id == ln["id"]).first()
        generated_lab_cfg = (ni.generated_lab_config or "") if ni else ""
        included_nodes.append({
            "id": ln["id"], "name": safe, "kind": kind, "image": image,
            "template_id": tmpl.id if tmpl else None,
            "startup_config": tmpl.startup_config if tmpl else "",
            "generated_lab_config": generated_lab_cfg,
            "min_memory_mb": tmpl_resources["min_memory_mb"],
            "min_cpu_cores": tmpl_resources["min_cpu_cores"],
            "requires_hugepages": tmpl_resources["requires_hugepages"],
        })

    included_links = []
    skipped_links = []
    for l in lab_links:
        src_name = node_name_map.get(l["source_node_id"])
        tgt_name = node_name_map.get(l["target_node_id"])
        if not src_name or not tgt_name:
            skipped_links.append({"id": l["id"], "reason": "One or both endpoints are not deployable"})
            continue
        src_iface = l["source_interface"] or "eth1"
        tgt_iface = l["target_interface"] or "eth1"
        included_links.append({
            "id": l["id"],
            "endpoints": [f"{src_name}:{src_iface}", f"{tgt_name}:{tgt_iface}"],
        })

    # Build the containerlab YAML structure
    # Map node id → published SSH host port so we can emit `ports:` entries.
    ssh_ports_by_lab_id: dict[int, int] = {
        ln["id"]: ln["lab_ssh_host_port"]
        for ln in lab_nodes
        if ln.get("lab_ssh_host_port") is not None
    }
    # Where the YAML will live on disk — must match lab_service._yaml_path_for.
    # We mirror that here (without importing lab_service to avoid the cycle)
    # so we can drop per-node startup-config files next to the YAML and use
    # a relative path in the YAML's startup-config field.
    from pathlib import Path
    import os as _os
    lab_state_dir = Path(_os.environ.get("LAB_STATE_DIR", "/app/lab_state"))
    lab_dir = lab_state_dir / _safe_lab_name(topology)
    configs_dir = lab_dir / "configs"

    def _xrd_clab_defaults(node_name: str) -> str:
        """Mirror the first-boot.cfg clab generates for `cisco_xrd` so the
        deployed container keeps clab's clab/clab@123 user, SSH server, gRPC
        & netconf agents, and the management-iface/route plumbing that lets
        SSH from the host actually work. Without this prepend, supplying
        startup-config REPLACES clab's defaults and the node becomes
        unreachable on the published SSH port.

        The mgmt-bridge gateway is derived from PHANERO_LAB_MGMT_SUBNET (its
        .1), falling back to containerlab's default (172.20.20.1 /
        2001:172:20:20::1) — so the node's default route matches whatever
        subnet the bridge actually uses.
        """
        gw_v4 = _mgmt_gateway("PHANERO_LAB_MGMT_SUBNET", "172.20.20.1")
        gw_v6 = _mgmt_gateway("PHANERO_LAB_MGMT_SUBNET6", "2001:172:20:20::1")
        return (
            "!\n"
            f"hostname {node_name}\n"
            "!\n"
            "username clab\n"
            " group root-lr\n"
            " group cisco-support\n"
            " secret clab@123\n"
            "!\n"
            "grpc\n"
            " port 9339\n"
            " no-tls\n"
            " address-family dual\n"
            "!\n"
            "line default\n"
            "  transport input ssh\n"
            "!\n"
            "netconf-yang agent\n"
            " ssh\n"
            "!\n"
            "interface MgmtEth0/RP0/CPU0/0\n"
            "!\n"
            "router static\n"
            " address-family ipv4 unicast\n"
            f"  0.0.0.0/0 MgmtEth0/RP0/CPU0/0 {gw_v4}\n"
            " !\n"
            " address-family ipv6 unicast\n"
            f"  ::/0 MgmtEth0/RP0/CPU0/0 {gw_v6}\n"
            " !\n"
            "!\n"
            "ssh server v2\n"
            "ssh server netconf\n"
            "!\n"
        )

    clab_nodes = {}
    rendered_config_paths: list[str] = []
    for n in included_nodes:
        entry: dict = {"kind": n["kind"], "image": n["image"]}
        # Priority for startup-config:
        #   1. A non-empty generated_lab_config from Config Design — that's
        #      what the user authored, rendered against the lab-side iface
        #      names. Write it to disk and reference relatively.
        #   2. The node template's static startup_config field (legacy).
        if n.get("generated_lab_config"):
            try:
                configs_dir.mkdir(parents=True, exist_ok=True)
                cfg_path = configs_dir / f"{n['name']}.cfg"
                # cisco_xrd's startup-config REPLACES clab's first-boot.cfg
                # rather than merging, so we have to recreate the defaults
                # ourselves — otherwise SSH access dies and the mgmt route
                # is missing. Other kinds (linux/FRR) don't have this
                # replace-vs-merge problem; we just write the user's body.
                body = (n.get("generated_lab_config") or "").rstrip("\n") + "\n"
                if n["kind"] == "cisco_xrd":
                    body = _xrd_clab_defaults(n["name"]) + body + "end\n"
                cfg_path.write_text(body)
                # Relative to YAML's parent dir (lab_dir) so clab picks it up
                # whether it runs from inside the backend container or the host.
                entry["startup-config"] = f"configs/{n['name']}.cfg"
                rendered_config_paths.append(str(cfg_path))
            except Exception:
                # If we can't write the file, fall back to whatever the
                # template specifies so deploy still has a chance.
                if n["startup_config"]:
                    entry["startup-config"] = n["startup_config"]
        elif n["startup_config"]:
            entry["startup-config"] = n["startup_config"]
        # Publish SSH on a host port so the user can reach the node from any
        # machine that can reach the lab host (or via ssh:// from the UI).
        host_port = ssh_ports_by_lab_id.get(n["id"])
        if host_port:
            entry["ports"] = [f"{host_port}:22"]
        clab_nodes[n["name"]] = entry

    clab_links = [{"endpoints": l["endpoints"]} for l in included_links]

    lab_name = _safe_lab_name(topology)
    clab_topology: dict = {"name": lab_name}
    # Optional management-network override. Containerlab's default mgmt subnet
    # (172.20.20.0/24) can overlap another Docker network on the host; set
    # PHANERO_LAB_MGMT_SUBNET (and optionally _SUBNET6) to a free range to avoid
    # the collision. Unset → containerlab's default (unchanged behavior).
    mgmt_v4 = os.environ.get("PHANERO_LAB_MGMT_SUBNET", "").strip()
    if mgmt_v4:
        mgmt = {"ipv4-subnet": mgmt_v4}
        mgmt_v6 = os.environ.get("PHANERO_LAB_MGMT_SUBNET6", "").strip()
        if mgmt_v6:
            mgmt["ipv6-subnet"] = mgmt_v6
        clab_topology["mgmt"] = mgmt
    clab_topology["topology"] = {
        "nodes": clab_nodes,
        "links": clab_links,
    }

    yaml_str = yaml.safe_dump(clab_topology, sort_keys=False, default_flow_style=False)

    # Resource estimate
    total_mem = sum(n["min_memory_mb"] for n in included_nodes)
    total_cpu = sum(n["min_cpu_cores"] for n in included_nodes)
    any_hugepages = any(n["requires_hugepages"] for n in included_nodes)

    return {
        "yaml": yaml_str,
        "lab_name": lab_name,
        "included_nodes": included_nodes,
        "skipped_nodes": skipped_nodes,
        "included_links": included_links,
        "skipped_links": skipped_links,
        # Paths of per-node startup-config files written to disk this pass
        # (sourced from generated_lab_config). Useful for surfacing in the UI
        # / diagnostics — clab reads them on deploy.
        "rendered_config_paths": rendered_config_paths,
        "resource_estimate": {
            "memory_mb": total_mem,
            "cpu_cores": total_cpu,
            "requires_hugepages": any_hugepages,
            "node_count": len(included_nodes),
            "link_count": len(included_links),
        },
    }


def get_host_capacity() -> dict:
    """Best-effort host capacity discovery from inside the backend container.

    The compose file mounts the host's /proc when pid:host is set, so
    /proc/meminfo reflects the host. Falls back to container limits if not.
    """
    mem_total_mb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    # "MemTotal:       16384000 kB"
                    mem_total_mb = int(line.split()[1]) // 1024
                    break
    except Exception:
        pass

    cpu_cores = 0
    try:
        with open("/proc/cpuinfo") as f:
            cpu_cores = sum(1 for line in f if line.startswith("processor"))
    except Exception:
        pass

    hugepages_count = 0
    try:
        with open("/proc/sys/vm/nr_hugepages") as f:
            hugepages_count = int(f.read().strip())
    except Exception:
        pass

    return {
        "memory_mb": mem_total_mb,
        "cpu_cores": cpu_cores,
        "hugepages_count": hugepages_count,
    }

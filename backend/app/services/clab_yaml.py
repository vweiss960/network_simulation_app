"""Containerlab YAML generator.

Turns a topology (every node is a device with its own kind + image) into a
containerlab topology file. Devices are reached on their management IP — no
host port publishing.
"""

import ipaddress
import os
import yaml
from sqlalchemy.orm import Session

from app.config import LAB_STATE_DIR
from app.models.topology import Topology
from app.services.lab_view import compute_lab_view


def _mgmt_settings(db: Session):
    from app.services.lab_network import get_settings
    return get_settings(db)


def _first_host(subnet: str, default: str) -> str:
    if subnet:
        try:
            return str(next(ipaddress.ip_network(subnet, strict=False).hosts()))
        except (ValueError, StopIteration):
            pass
    return default


def xrd_base_config(node_name: str, gw_v4: str, gw_v6: str) -> str:
    """containerlab's own first-boot config for cisco_xrd. A user
    startup-config REPLACES clab's defaults on XRd, so we prepend this to keep
    the clab user, SSH, NETCONF, gRPC and the mgmt default route."""
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


def generate_clab_yaml(db: Session, topology_id: int, assign_ips: bool = True) -> dict:
    topology = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topology:
        raise ValueError(f"Topology {topology_id} not found")

    view = compute_lab_view(db, topology_id)
    lab_name = view["lab_name"]
    lab_dir = LAB_STATE_DIR / lab_name
    configs_dir = lab_dir / "configs"
    mgmt = _mgmt_settings(db)
    if mgmt.bridged and assign_ips:
        from app.services.lab_network import assign_ips as _assign
        ips = _assign(db, mgmt, [n["id"] for n in view["nodes"] if not n["is_bridge"]])
        for n in view["nodes"]:
            if n["id"] in ips:
                n["mgmt_ip"] = ips[n["id"]]
    gw_v4 = mgmt.effective_gateway()
    gw_v6 = "2001:172:20:20::1"

    clab_nodes: dict = {}
    included_nodes = []
    rendered_config_paths: list[str] = []
    for n in view["nodes"]:
        if n["is_bridge"]:
            clab_nodes[n["clab_name"]] = {"kind": "bridge"}
            continue
        entry: dict = {"kind": n["lab_kind"], "image": n["lab_image"]}
        body = (n.get("startup_config") or "").strip("\n")
        if body:
            configs_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = configs_dir / f"{n['clab_name']}.cfg"
            text = body + "\n"
            if n["lab_kind"] == "cisco_xrd":
                text = xrd_base_config(n["clab_name"], gw_v4, gw_v6) + text + "end\n"
            cfg_path.write_text(text)
            # Relative to the YAML so clab resolves it from the lab dir.
            entry["startup-config"] = f"configs/{n['clab_name']}.cfg"
            rendered_config_paths.append(str(cfg_path))
        if mgmt.bridged and n.get("mgmt_ip"):
            entry["mgmt-ipv4"] = n["mgmt_ip"]
        clab_nodes[n["clab_name"]] = entry
        included_nodes.append({
            "id": n["id"], "name": n["clab_name"], "kind": n["lab_kind"], "image": n["lab_image"],
            "min_memory_mb": n["min_memory_mb"], "min_cpu_cores": n["min_cpu_cores"],
            "requires_hugepages": n["requires_hugepages"],
        })

    names = {n["id"]: n["clab_name"] for n in view["nodes"]}
    included_links, skipped_links = [], []
    for l in view["links"]:
        a, b = names.get(l["source_node_id"]), names.get(l["target_node_id"])
        if not a or not b or not l["source_interface"] or not l["target_interface"]:
            skipped_links.append({"id": l["id"], "reason": "Endpoint not deployable or interface missing"})
            continue
        included_links.append({
            "id": l["id"],
            "endpoints": [f"{a}:{l['source_interface']}", f"{b}:{l['target_interface']}"],
        })

    m = {"network": mgmt.docker_network, "ipv4-subnet": mgmt.subnet}
    if mgmt.bridged:
        m["ipv4-gw"] = gw_v4
    doc: dict = {"name": lab_name, "mgmt": m}
    doc["topology"] = {
        "nodes": clab_nodes,
        "links": [{"endpoints": l["endpoints"]} for l in included_links],
    }

    return {
        "yaml": yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
        "lab_name": lab_name,
        "included_nodes": included_nodes,
        "skipped_nodes": view["skipped_nodes"],
        "included_links": included_links,
        "skipped_links": skipped_links,
        "rendered_config_paths": rendered_config_paths,
        "resource_estimate": {
            "memory_mb": sum(n["min_memory_mb"] for n in included_nodes),
            "bridges": [{"node_id": n["id"], "bridge": n["bridge"], "segment": n["segment"]}
                        for n in view["nodes"] if n["is_bridge"]],
            "cpu_cores": sum(n["min_cpu_cores"] for n in included_nodes),
            "requires_hugepages": any(n["requires_hugepages"] for n in included_nodes),
            "node_count": len(included_nodes),
            "link_count": len(included_links),
        },
    }


def get_host_capacity() -> dict:
    """Host memory / CPU / hugepages from /proc (native process or pid:host)."""
    mem_total_mb = mem_avail_mb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_mb = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_avail_mb = int(line.split()[1]) // 1024
    except Exception:
        pass
    cpu_cores = os.cpu_count() or 0
    hugepages_count = 0
    try:
        with open("/proc/sys/vm/nr_hugepages") as f:
            hugepages_count = int(f.read().strip())
    except Exception:
        pass
    return {
        "memory_mb": mem_total_mb,
        "memory_available_mb": mem_avail_mb,
        "cpu_cores": cpu_cores,
        "hugepages_count": hugepages_count,
    }

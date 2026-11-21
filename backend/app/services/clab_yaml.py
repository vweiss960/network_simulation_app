"""Containerlab YAML generator.

Turns a topology (every node is a device with its own kind + image) into a
containerlab topology file. Devices are reached on their management IP — no
host port publishing.
"""

import hashlib
import ipaddress
import os
import yaml
from sqlalchemy.orm import Session

from app.config import LAB_STATE_DIR
from app.models.topology import Topology
from app.models.node_instance import NodeInstance
from app.services.lab_view import compute_lab_view, provision_credentials


def _first_host(subnet: str, default: str) -> str:
    if subnet:
        try:
            return str(next(ipaddress.ip_network(subnet, strict=False).hosts()))
        except (ValueError, StopIteration):
            pass
    return default


def xrd_base_config(node_name: str, gw_v4: str, gw_v6: str, mgmt_distance: int | None = None,
                    username: str = "clab", password: str = "clab@123") -> str:
    """containerlab's own first-boot config for cisco_xrd. A user
    startup-config REPLACES clab's defaults on XRd, so we prepend this to keep
    the clab user, SSH, NETCONF, gRPC and the mgmt default route.
    mgmt_distance: make that default route a floating static (lesson labs),
    so a default learned from a routing protocol (e.g. an OSPF stub area's
    O*IA) wins — otherwise the mgmt default hides it and swallows lab traffic."""
    ad = f" {mgmt_distance}" if mgmt_distance else ""
    return (
        "!\n"
        f"hostname {node_name}\n"
        "!\n"
        f"username {username}\n"
        " group root-lr\n"
        " group cisco-support\n"
        f" secret {password}\n"
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
        f"  0.0.0.0/0 MgmtEth0/RP0/CPU0/0 {gw_v4}{ad}\n"
        " !\n"
        " address-family ipv6 unicast\n"
        f"  ::/0 MgmtEth0/RP0/CPU0/0 {gw_v6}{ad}\n"
        " !\n"
        "!\n"
        "ssh server v2\n"
        "ssh server netconf\n"
        "!\n"
    )


# Lines of an IOS / IOS-XE running config that must not be replayed as a
# day-0 config: the self-signed certificate belongs to RSA keys a fresh VM
# doesn't have (replaying it crashed CVAC and reloaded the VM), license /
# platform / version lines are rejected or trigger reloads.
_XE_DROP_BLOCKS = ("crypto pki trustpoint TP-self-signed", "crypto pki certificate chain TP-self-signed",
                   "crypto pki trustpoint SLA-TrustPoint", "crypto pki certificate chain SLA-TrustPoint")
_XE_DROP_LINES = ("version ", "boot-start-marker", "boot-end-marker", "license udi", "platform ",
                  "Building configuration", "Current configuration", "! Last configuration change",
                  "! NVRAM config last updated")


def day0_config(family: str, text: str) -> str:
    """Make a running config safe to boot from (IOS / IOS-XE)."""
    if family not in ("iosxe", "ios") or not text.strip():
        return text
    out, skipping = [], False
    for line in text.splitlines():
        if skipping:
            if line.startswith((" ", "\t")) or not line.strip():
                continue
            skipping = False
        if line.startswith(_XE_DROP_BLOCKS):
            skipping = True
            continue
        if line.startswith(_XE_DROP_LINES) or line.strip() == "end":
            continue
        out.append(line)
    # A running config prints `shutdown` only for shut interfaces, never
    # `no shutdown`; a fresh VM starts its data ports shut. Make "up" explicit.
    res, i = [], 0
    while i < len(out):
        res.append(out[i])
        if out[i].startswith("interface "):
            j = i + 1
            while j < len(out) and out[j].startswith(" "):
                res.append(out[j])
                j += 1
            if not any(l.strip() == "shutdown" for l in out[i + 1:j]):
                res.append(" no shutdown")
            i = j
            continue
        i += 1
    return "\n".join(res + ["end"])     # without it IOS-XE logs PARSER-4-BADCFG


def linux_exec_commands(kind: str, image: str, startup: str) -> list[str] | None:
    """For a plain Linux host (not FRR / the workstation), the startup config
    is a list of shell commands, one per line (# comments allowed). None for
    every other device."""
    img = (image or "").lower()
    if kind != "linux" or "frr" in img:
        return None
    out = []
    for line in (startup or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line if line.startswith(("sh -c", "bash -c")) else "sh -c " + _sh_quote(line))
    return out


def frr_startup(kind: str, image: str, startup: str) -> tuple[str, list[str]] | None:
    """For an FRR node (a Linux node with an FRR image): (frr.conf text, shell
    commands). Lines starting with `$ ` are shell commands run once the
    container is up (MACsec / wpa_supplicant setup, sysctls…), in order;
    everything else is FRR configuration, mounted as /etc/frr/frr.conf so the
    daemons load it at boot (containerlab ignores `startup-config` on Linux
    nodes). None for every other device."""
    if kind != "linux" or "frr" not in (image or "").lower():
        return None
    conf, shell = [], []
    for line in (startup or "").splitlines():
        if line.lstrip().startswith("$ "):
            cmd = line.lstrip()[2:].strip()
            if cmd:
                shell.append(cmd if cmd.startswith(("sh -c", "bash -c")) else "sh -c " + _sh_quote(cmd))
        else:
            conf.append(line)
    return "\n".join(conf).strip("\n"), shell


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def generate_clab_yaml(db: Session, topology_id: int, assign_ips: bool = False) -> dict:
    """assign_ips=True (deploy / reconcile only) sets up a bridged mgmt network
    and takes DHCP leases for devices; previews leave the host untouched."""
    topology = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topology:
        raise ValueError(f"Topology {topology_id} not found")

    view = compute_lab_view(db, topology_id)
    lab_name = view["lab_name"]
    lab_dir = LAB_STATE_DIR / lab_name
    configs_dir = lab_dir / "configs"
    from app.services import lab_network
    mgmt = lab_network.lab_mgmt(db, topology_id)
    if mgmt.bridged and assign_ips:
        lab_network.ensure_mgmt(db, mgmt, lab_network.get_settings(db).mtu or None)
        ips = lab_network.assign_ips(db, mgmt, [n["id"] for n in view["nodes"] if not n["is_bridge"]])
        for n in view["nodes"]:
            if n["id"] in ips:
                n["mgmt_ip"] = ips[n["id"]]
    gw_v4 = mgmt.gateway or "172.30.0.1"
    gw_v6 = "2001:172:20:20::1"

    clab_nodes: dict = {}
    included_nodes = []
    rendered_config_paths: list[str] = []
    from app.services.device_kinds import get_kind as _gk
    for n in view["nodes"]:
        if n["is_bridge"]:
            continue    # a host Linux bridge (lab_service prepares it); ports join it in apply_patches
        entry: dict = {"kind": n["lab_kind"], "image": n["lab_image"]}
        node_row = db.get(NodeInstance, n["id"])
        creds = provision_credentials(node_row) if node_row else None   # None: the image's own
        if creds and _gk(n["lab_kind"]).is_vm:
            # vrnetlab: containerlab builds --username/--password from these.
            entry["env"] = {"USERNAME": creds[0], "PASSWORD": creds[1]}
        body = day0_config(_gk(n["lab_kind"]).family, n.get("startup_config") or "").strip("\n")
        cmds = linux_exec_commands(n["lab_kind"], n["lab_image"], body)
        if cmds is not None:
            # A plain Linux host has no config file: its startup config is
            # commands containerlab runs once the container is up.
            if cmds:
                entry["exec"] = cmds
            body = ""
        frr = frr_startup(n["lab_kind"], n["lab_image"], body)
        if frr is not None:
            body, shell = frr
            if shell:
                entry["exec"] = shell
        # In a lesson lab every XRd gets our base config (floating mgmt
        # default), even a blank one — clab's own default would shadow OSPF.
        learning_lab = bool(topology.lesson_id or getattr(topology, "scenario_id", None))
        # Same for an XRd provisioned with non-default credentials: they live
        # in that base config.
        lesson_xrd = n["lab_kind"] == "cisco_xrd" and (learning_lab or bool(creds))
        if body or lesson_xrd:
            configs_dir.mkdir(parents=True, exist_ok=True)
            text = body + "\n" if body else ""
            if n["lab_kind"] == "cisco_xrd":
                # Lesson labs: the mgmt default must not shadow the lab's own
                # routing (kept as-is for other labs: changing it would
                # recreate their XRd devices).
                text = xrd_base_config(n["clab_name"], gw_v4, gw_v6, 254 if learning_lab else None,
                                       *(creds or ())) + text + "end\n"
            # Content hash in the file name: a changed startup config changes
            # the YAML, so a reconcile recreates exactly that device.
            digest = hashlib.sha1(text.encode()).hexdigest()[:8]
            cfg_name = f"{n['clab_name']}-{digest}.cfg"
            cfg_path = configs_dir / cfg_name
            for old in configs_dir.glob(f"{n['clab_name']}-*.cfg"):
                if old.name != cfg_name:
                    old.unlink(missing_ok=True)
            cfg_path.write_text(text)
            # Relative to the YAML so clab resolves it from the lab dir.
            if frr is not None:
                entry["binds"] = [f"configs/{cfg_name}:/etc/frr/frr.conf"]
            else:
                entry["startup-config"] = f"configs/{cfg_name}"
            rendered_config_paths.append(str(cfg_path))
        if mgmt.bridged and n.get("mgmt_ip"):
            entry["mgmt-ipv4"] = n["mgmt_ip"]
        clab_nodes[n["clab_name"]] = entry
        included_nodes.append({
            "id": n["id"], "name": n["clab_name"], "kind": n["lab_kind"], "image": n["lab_image"],
            "min_memory_mb": n["min_memory_mb"], "min_cpu_cores": n["min_cpu_cores"],
            "requires_hugepages": n["requires_hugepages"],
        })

    # Pre-provisioned ports: every port of every device is a veth whose
    # other half sits on the host (host_net.port_name), created when the
    # device is. Cables are not in this file at all — lab_service patches
    # the host halves together (or into a Network node's bridge) with
    # host_net.apply_patches. Drawing or deleting a cable therefore never
    # changes a device's definition, so nothing restarts; XRd and vrnetlab
    # VMs, which only see the interfaces present at boot, get all of them.
    from app.services.host_net import port_name
    port_of: dict[tuple[int, str], str] = {}
    yaml_links, ports = [], []
    for n in view["nodes"]:
        if n["is_bridge"]:
            continue
        for idx, iface in enumerate(n["interfaces"] or []):
            half = port_name(topology_id, n["id"], idx)
            port_of[(n["id"], iface["kernel"])] = half
            ports.append(half)
            yaml_links.append({"endpoints": [f"{n['clab_name']}:{iface['kernel']}", f"host:{half}"]})
    bridge_of = {n["id"]: n["bridge"] for n in view["nodes"] if n["is_bridge"]}

    included_links, skipped_links, patches = [], [], []
    for l in view["links"]:
        ends = []
        for nid, iface in ((l["source_node_id"], l["source_interface"]), (l["target_node_id"], l["target_interface"])):
            ends.append(("bridge", bridge_of[nid]) if nid in bridge_of else ("port", port_of.get((nid, iface))))
        if any(v is None for _k, v in ends):
            skipped_links.append({"id": l["id"], "reason": "Endpoint not deployable or interface missing"})
            continue
        (ka, va), (kb, vb) = ends
        if ka == "bridge":
            (ka, va), (kb, vb) = (kb, vb), (ka, va)
        patch = {"a": va, "bridge": vb} if kb == "bridge" else {"a": va, "b": vb}
        patches.append(patch)
        included_links.append({"id": l["id"], **patch})

    m = {"network": mgmt.docker_network}
    if mgmt.subnet:
        m["ipv4-subnet"] = mgmt.subnet
    if mgmt.bridged and mgmt.gateway:
        m["ipv4-gw"] = mgmt.gateway
    doc: dict = {"name": lab_name, "mgmt": m}
    doc["topology"] = {
        "nodes": clab_nodes,
        "links": yaml_links,
    }

    return {
        "yaml": yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
        "lab_name": lab_name,
        "included_nodes": included_nodes,
        "skipped_nodes": view["skipped_nodes"],
        "included_links": included_links,
        "skipped_links": skipped_links,
        "rendered_config_paths": rendered_config_paths,
        "ports": ports,
        "patches": patches,
        "resource_estimate": {
            "memory_mb": sum(n["min_memory_mb"] for n in included_nodes),
            "mgmt": {"bridged": mgmt.bridged, "bridge": mgmt.bridge, "network": mgmt.docker_network,
                     "subnet": mgmt.subnet, "gateway": mgmt.gateway, "node_id": mgmt.node_id},
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

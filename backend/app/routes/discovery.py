"""
Network discovery API route.

POST /api/topologies/{topology_id}/discover
  - Accepts DiscoveryRequest JSON
  - Returns SSE stream of progress events
  - On completion, bulk-creates nodes and links in the topology
"""

import asyncio
import json
import math

import networkx as nx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.flow import Flow
from app.schemas.schemas import DiscoveryRequest
from app.services.discovery import discover_topology, discover_topology_igp, DiscoveryResult

router = APIRouter(prefix="/api/topologies/{topology_id}", tags=["discovery"])

# Reference bandwidth for auto-cost (same as links.py)
REFERENCE_BANDWIDTH_GBPS = 400


def _layout_nodes(result: DiscoveryResult) -> dict[str, tuple[float, float]]:
    """Use networkx spring layout to compute (x, y) positions for discovered devices."""
    G = nx.Graph()
    for hostname in result.devices:
        G.add_node(hostname)
    for hostA, ifA, hostB, ifB in result.links:
        if hostA in result.devices and hostB in result.devices:
            G.add_edge(hostA, hostB)

    if len(G.nodes) == 0:
        return {}

    if len(G.nodes) == 1:
        name = list(G.nodes)[0]
        return {name: (400.0, 300.0)}

    # Spring layout returns positions in [-1, 1] range; scale to canvas
    pos = nx.spring_layout(G, seed=42, k=2.0 / math.sqrt(len(G.nodes)))

    # Scale to a reasonable canvas area (200–800 x, 100–600 y)
    positions: dict[str, tuple[float, float]] = {}
    for name, (px, py) in pos.items():
        x = 200 + (px + 1) * 300  # map [-1,1] -> [200, 800]
        y = 100 + (py + 1) * 250  # map [-1,1] -> [100, 600]
        positions[name] = (round(x, 1), round(y, 1))

    return positions


def _build_topology_objects(
    topology_id: int,
    result: DiscoveryResult,
    db: Session,
    clear_existing: bool = True,
) -> tuple[int, int]:
    """Create NodeInstance and Link records from discovery result. Returns (nodes_created, links_created)."""

    if clear_existing:
        # Delete existing flows, links, then nodes (order matters for foreign keys)
        db.query(Flow).filter(Flow.topology_id == topology_id).delete()
        db.query(Link).filter(Link.topology_id == topology_id).delete()
        db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).delete()
        db.flush()

    positions = _layout_nodes(result)

    # Create nodes
    hostname_to_node_id: dict[str, int] = {}
    nodes_created = 0

    for hostname, device in result.devices.items():
        x, y = positions.get(hostname, (400.0, 300.0))
        ifaces_json = json.dumps(device.interfaces)

        node = NodeInstance(
            topology_id=topology_id,
            name=hostname,
            vendor=device.vendor,
            model=device.model,
            platform=device.os_type,
            x=x, y=y,
            interfaces_json=ifaces_json,
            capacity_gbps=sum(i.get("speed_gbps", 10) for i in device.interfaces) if device.interfaces else 100.0,
        )
        db.add(node)
        db.flush()  # get the ID
        hostname_to_node_id[hostname] = node.id
        nodes_created += 1

    # Create links
    links_created = 0
    for hostA, ifA, hostB, ifB in result.links:
        src_id = hostname_to_node_id.get(hostA)
        tgt_id = hostname_to_node_id.get(hostB)
        if not src_id or not tgt_id:
            continue  # one side wasn't discovered

        # Determine bandwidth from interface speed
        speed = 10.0  # default
        dev_a = result.devices.get(hostA)
        if dev_a:
            for iface in dev_a.interfaces:
                if iface["name"] == ifA:
                    speed = iface.get("speed_gbps", 10.0)
                    break

        cost = REFERENCE_BANDWIDTH_GBPS / speed if speed > 0 else 1.0

        link = Link(
            topology_id=topology_id,
            source_node_id=src_id, target_node_id=tgt_id,
            source_interface=ifA, target_interface=ifB,
            bandwidth_gbps=speed,
            cost=round(cost, 2),
            latency_ms=0.0, latency_source="unknown",  # estimated until measured
            carrier="",
            sr_attributes_json="{}",
        )
        db.add(link)
        links_created += 1

    db.commit()
    return nodes_created, links_created


def _enrich_from_prometheus(topology_id: int, db: Session) -> dict:
    """
    After discovery, enrich nodes and links with data from Prometheus/SNMP:
    - Set prometheus_id on nodes by matching sysName to node hostname
    - Update interface speeds from SNMP ifHighSpeed (replacing name-based guesses)
    - Add missing interfaces from SNMP that discovery didn't find
    - Update link bandwidths with real interface speeds
    Returns enrichment summary.
    """
    from app.models.prometheus_config import PrometheusConfig
    from app.services.prometheus_service import PrometheusClient, PrometheusError

    cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    if not cfg:
        return {"enriched": False, "reason": "No Prometheus configured for this topology"}

    client = PrometheusClient(
        url=cfg.url, auth_type=cfg.auth_type,
        username=cfg.username, password=cfg.password, bearer_token=cfg.bearer_token,
    )

    try:
        client.test_connection()
    except PrometheusError as e:
        return {"enriched": False, "reason": f"Prometheus connection failed: {e}"}

    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).all()

    # Step 1: Get sysName for all SNMP targets and map to discovered nodes
    try:
        sys_name_results = client.query("sysName")
    except PrometheusError:
        return {"enriched": False, "reason": "Failed to query sysName from Prometheus"}

    # Build sysName -> instance map
    sysname_to_instance: dict[str, str] = {}
    for r in sys_name_results:
        instance = r["metric"].get("instance", "")
        sysname = r["metric"].get("sysName", "")
        if instance and sysname:
            sysname_to_instance[sysname.lower()] = instance

    # Match nodes to Prometheus instances by hostname
    matched = 0
    node_instance_map: dict[int, str] = {}  # node_id -> prometheus instance
    for node in nodes:
        hostname_lower = node.name.lower().strip()
        # Try exact match, then without domain suffix
        instance = sysname_to_instance.get(hostname_lower)
        if not instance:
            # Try matching just the first part before any dots
            short_name = hostname_lower.split(".")[0]
            instance = sysname_to_instance.get(short_name)
        if not instance:
            # Try matching any sysName that starts with the node name
            for sn, inst in sysname_to_instance.items():
                if sn.startswith(hostname_lower) or hostname_lower.startswith(sn):
                    instance = inst
                    break

        if instance:
            node.prometheus_id = instance
            node_instance_map[node.id] = instance
            matched += 1

    # Step 2: Get interface data from SNMP for all matched nodes
    iface_label = cfg.interface_label or "ifName"
    instance_label = cfg.instance_label or "instance"
    speed_metric = cfg.speed_metric or "ifHighSpeed"

    # Query all interface names, speeds, and oper status
    try:
        all_if_names = client.query(iface_label)
        all_if_speeds = client.query(speed_metric)
        all_if_descr = client.query("ifDescr")
        all_if_oper = client.query("ifOperStatus")
    except PrometheusError as e:
        db.commit()  # save prometheus_id mappings at least
        return {"enriched": True, "matched_nodes": matched, "reason": f"Mapped nodes but SNMP query failed: {e}"}

    # Build per-instance interface data
    # instance -> {ifIndex -> {name, speed_mbps, description, oper_status}}
    instance_ifaces: dict[str, dict[str, dict]] = {}

    for r in all_if_names:
        inst = r["metric"].get(instance_label, "")
        idx = r["metric"].get("ifIndex", "")
        name = r["metric"].get(iface_label, "")
        if inst and idx and name:
            instance_ifaces.setdefault(inst, {}).setdefault(idx, {})["name"] = name

    for r in all_if_speeds:
        inst = r["metric"].get(instance_label, "")
        idx = r["metric"].get("ifIndex", "")
        val = float(r["value"][1]) if r["value"][1] != "NaN" else 0
        if inst and idx:
            instance_ifaces.setdefault(inst, {}).setdefault(idx, {})["speed_mbps"] = val

    for r in all_if_descr:
        inst = r["metric"].get(instance_label, "")
        idx = r["metric"].get("ifIndex", "")
        descr = r["metric"].get("ifDescr", "")
        if inst and idx and descr:
            instance_ifaces.setdefault(inst, {}).setdefault(idx, {})["description"] = descr

    for r in all_if_oper:
        inst = r["metric"].get(instance_label, "")
        idx = r["metric"].get("ifIndex", "")
        val = int(float(r["value"][1])) if r["value"][1] != "NaN" else 0
        if inst and idx:
            instance_ifaces.setdefault(inst, {}).setdefault(idx, {})["oper_status"] = val

    # Step 3: Enrich each matched node's interfaces
    interfaces_updated = 0
    interfaces_added = 0
    interfaces_replaced = 0

    def _normalize_iface_name(name: str) -> str:
        """Normalize interface name for matching.
        Converts short forms to long forms and lowercases.
        E.g. 'Gi0/0/0/0' -> 'gigabitethernet0/0/0/0'
             'Te0/0/0/0' -> 'tengige0/0/0/0'
        """
        n = name.strip().lower()
        # Common IOS-XR / IOS short -> long mappings
        prefixes = [
            ("gi", "gigabitethernet"),
            ("te", "tengige"),
            ("hu", "hundredgige"),
            ("fo", "fourhundredgige"),
            ("fa", "fastethernet"),
            ("be", "bundle-ether"),
            ("mg", "mgmteth"),
            ("lo", "loopback"),
            ("nu", "null"),
        ]
        for short, long in prefixes:
            if n.startswith(short) and len(n) > len(short) and n[len(short):][0:1] in "0123456789/":
                return long + n[len(short):]
        return n

    for node in nodes:
        prom_inst = node_instance_map.get(node.id)
        if not prom_inst or prom_inst not in instance_ifaces:
            continue

        snmp_ifaces = instance_ifaces[prom_inst]
        existing_ifaces = json.loads(node.interfaces_json) if node.interfaces_json else []

        # Build SNMP lookup: normalized name -> (original name, data)
        snmp_by_norm: dict[str, tuple[str, dict]] = {}
        for idx, data in snmp_ifaces.items():
            name = data.get("name", "")
            if name:
                norm = _normalize_iface_name(name)
                snmp_by_norm[norm] = (name, data)
                # Also index by ifDescr normalized
                descr = data.get("description", "")
                if descr:
                    snmp_by_norm[_normalize_iface_name(descr)] = (name, data)

        # Track which SNMP interfaces have been matched
        matched_snmp_names: set[str] = set()

        # Update existing interfaces: match by normalized name, update speed, and
        # replace the short discovery name with the canonical SNMP name
        for iface in existing_ifaces:
            norm = _normalize_iface_name(iface["name"])
            match = snmp_by_norm.get(norm)
            if match:
                snmp_name, snmp_data = match
                matched_snmp_names.add(snmp_name)
                # Replace short name with canonical SNMP name
                if iface["name"] != snmp_name:
                    iface["name"] = snmp_name
                    interfaces_replaced += 1
                # Update speed from SNMP
                if snmp_data.get("speed_mbps", 0) > 0:
                    real_speed = snmp_data["speed_mbps"] / 1000
                    if real_speed != iface.get("speed_gbps", 0):
                        iface["speed_gbps"] = real_speed
                        interfaces_updated += 1

        existing_names = {i["name"] for i in existing_ifaces}

        # Add interfaces from SNMP that discovery missed (excluding loopback, mgmt, null, tunnel)
        skip_patterns = ["null", "loopback", "mgmt", "management", "srte_", "bvi", "tunnel", "nve"]
        seen_snmp_names: set[str] = set()
        for norm_name, (snmp_name, data) in snmp_by_norm.items():
            if snmp_name in matched_snmp_names or snmp_name in existing_names or snmp_name in seen_snmp_names:
                continue
            seen_snmp_names.add(snmp_name)
            lower = snmp_name.lower()
            if any(skip in lower for skip in skip_patterns):
                continue
            speed_mbps = data.get("speed_mbps", 0)
            if speed_mbps <= 0:
                continue  # skip interfaces with no speed (virtual/down)
            existing_ifaces.append({
                "name": snmp_name,
                "speed_gbps": speed_mbps / 1000,
                "description": data.get("description", ""),
                "status": "free",
            })
            interfaces_added += 1

        node.interfaces_json = json.dumps(existing_ifaces)
        node.capacity_gbps = sum(i.get("speed_gbps", 0) for i in existing_ifaces) or node.capacity_gbps

    # Step 3.5: Use LLDP data from SNMP to fill in missing link interfaces
    # Query LLDP neighbor tables from Prometheus
    lldp_neighbors: dict[str, list[dict]] = {}  # instance -> [{local_ifindex, remote_sysname, remote_port}]
    try:
        lldp_sysname_results = client.query("lldpRemSysName")
        lldp_portid_results = client.query("lldpRemPortId")

        # Build lldp remote sysname lookup: (instance, localPortNum) -> remote_sysname
        lldp_remote_sys: dict[tuple[str, str], str] = {}
        for r in lldp_sysname_results:
            inst = r["metric"].get(instance_label, "")
            local_port = r["metric"].get("lldpRemLocalPortNum", "")
            remote_sys = r["metric"].get("lldpRemSysName", "")
            if inst and local_port and remote_sys:
                lldp_remote_sys[(inst, local_port)] = remote_sys

        # Build lldp remote port lookup: (instance, localPortNum) -> remote_port_id
        lldp_remote_port: dict[tuple[str, str], str] = {}
        for r in lldp_portid_results:
            inst = r["metric"].get(instance_label, "")
            local_port = r["metric"].get("lldpRemLocalPortNum", "")
            remote_port = r["metric"].get("lldpRemPortId", "")
            if inst and local_port and remote_port:
                lldp_remote_port[(inst, local_port)] = remote_port

        # Build ifIndex -> ifName lookup per instance
        idx_to_name: dict[tuple[str, str], str] = {}
        for r in all_if_names:
            inst = r["metric"].get(instance_label, "")
            idx = r["metric"].get("ifIndex", "")
            name = r["metric"].get(iface_label, "")
            if inst and idx and name:
                idx_to_name[(inst, idx)] = name

        # Combine: for each LLDP entry, resolve local_port_num to ifName
        # lldpRemLocalPortNum usually equals ifIndex on IOS-XR
        for (inst, local_port), remote_sys in lldp_remote_sys.items():
            local_ifname = idx_to_name.get((inst, local_port), "")
            remote_port_id = lldp_remote_port.get((inst, local_port), "")
            if local_ifname:
                lldp_neighbors.setdefault(inst, []).append({
                    "local_interface": local_ifname,
                    "remote_hostname": remote_sys,
                    "remote_interface": remote_port_id,
                })
    except PrometheusError:
        pass  # LLDP data not available — not critical

    # Now fill in missing link interfaces using LLDP
    node_id_to_name = {n.id: n.name for n in nodes}
    node_name_to_prom = {n.name.lower(): node_instance_map.get(n.id, "") for n in nodes}

    links_iface_filled = 0
    for link in links:
        src_name = node_id_to_name.get(link.source_node_id, "").lower()
        tgt_name = node_id_to_name.get(link.target_node_id, "").lower()
        src_prom = node_instance_map.get(link.source_node_id, "")
        tgt_prom = node_instance_map.get(link.target_node_id, "")

        # Fill missing source interface: check LLDP on src device for neighbor matching tgt
        if not link.source_interface and src_prom in lldp_neighbors:
            for entry in lldp_neighbors[src_prom]:
                if entry["remote_hostname"].lower() == tgt_name:
                    link.source_interface = entry["local_interface"]
                    if not link.target_interface and entry["remote_interface"]:
                        link.target_interface = entry["remote_interface"]
                    links_iface_filled += 1
                    break

        # Fill missing target interface: check LLDP on tgt device for neighbor matching src
        if not link.target_interface and tgt_prom in lldp_neighbors:
            for entry in lldp_neighbors[tgt_prom]:
                if entry["remote_hostname"].lower() == src_name:
                    link.target_interface = entry["local_interface"]
                    if not link.source_interface and entry["remote_interface"]:
                        link.source_interface = entry["remote_interface"]
                    links_iface_filled += 1
                    break

    # Step 3.7: Use IP subnet correlation to fill remaining missing link interfaces
    # Query ipAdEntIfIndex and ipAdEntNetMask to build (instance, ifIndex) -> (ip, mask) map
    # Then find interface pairs on the same /30 or /31 subnet across different devices
    try:
        ip_addr_results = client.query("ipAdEntIfIndex")
        ip_mask_results = client.query("ipAdEntNetMask")
    except PrometheusError:
        ip_addr_results = []
        ip_mask_results = []

    import ipaddress

    def _decode_hex_ip(hex_str: str) -> str:
        """Decode hex-encoded IP from SNMP (e.g. '0xC0A80100' -> '192.168.1.0')."""
        h = hex_str
        if h.startswith("0x") or h.startswith("0X"):
            h = h[2:]
        # Take only first 8 hex chars (4 bytes = IPv4)
        h = h[:8]
        if len(h) < 8:
            h = h.zfill(8)
        try:
            octets = [int(h[i:i+2], 16) for i in range(0, 8, 2)]
            return ".".join(str(o) for o in octets)
        except (ValueError, IndexError):
            return ""

    # Build (instance, ifIndex) -> ip_address
    iface_ip_map: dict[tuple[str, str], str] = {}
    for r in ip_addr_results:
        inst = r["metric"].get(instance_label, "")
        ip_hex = r["metric"].get("ipAdEntAddr", "")
        ifidx = str(int(float(r["value"][1]))) if r["value"][1] != "NaN" else ""
        if inst and ip_hex and ifidx:
            ip = _decode_hex_ip(ip_hex)
            if ip and not ip.startswith("0.") and not ip.startswith("127."):
                iface_ip_map[(inst, ifidx)] = ip

    # Build (instance, ifIndex) -> subnet_mask
    iface_mask_map: dict[tuple[str, str], str] = {}
    for r in ip_mask_results:
        inst = r["metric"].get(instance_label, "")
        ip_hex = r["metric"].get("ipAdEntAddr", "")
        mask = r["metric"].get("ipAdEntNetMask", "")
        # Find the ifIndex for this IP from iface_ip_map
        if inst and ip_hex and mask:
            ip = _decode_hex_ip(ip_hex)
            # Find ifIndex by matching IP
            for (i, idx), addr in iface_ip_map.items():
                if i == inst and addr == ip:
                    iface_mask_map[(inst, idx)] = mask
                    break

    # Build subnet -> list of (instance, ifIndex, ip) for point-to-point subnets (/30, /31)
    subnet_members: dict[str, list[tuple[str, str, str]]] = {}
    for (inst, ifidx), ip in iface_ip_map.items():
        mask = iface_mask_map.get((inst, ifidx), "")
        if not mask:
            continue
        try:
            network = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
            # Only consider /30 and /31 (point-to-point links)
            if network.prefixlen >= 30:
                net_key = str(network)
                subnet_members.setdefault(net_key, []).append((inst, ifidx, ip))
        except (ValueError, ipaddress.AddressValueError):
            continue

    # For each link with missing interfaces, find matching subnet pairs
    prom_to_node_id = {v: k for k, v in node_instance_map.items()}

    for link in links:
        if link.source_interface and link.target_interface:
            continue  # both already filled

        src_prom = node_instance_map.get(link.source_node_id, "")
        tgt_prom = node_instance_map.get(link.target_node_id, "")
        if not src_prom or not tgt_prom:
            continue

        # Find a subnet that has one member from src and one from tgt
        for net_key, members in subnet_members.items():
            src_members = [(inst, idx, ip) for inst, idx, ip in members if inst == src_prom]
            tgt_members = [(inst, idx, ip) for inst, idx, ip in members if inst == tgt_prom]

            if src_members and tgt_members:
                src_inst, src_idx, src_ip = src_members[0]
                tgt_inst, tgt_idx, tgt_ip = tgt_members[0]

                # Resolve ifIndex to ifName
                src_ifname = ""
                tgt_ifname = ""
                if src_prom in instance_ifaces and src_idx in instance_ifaces[src_prom]:
                    src_ifname = instance_ifaces[src_prom][src_idx].get("name", "")
                if tgt_prom in instance_ifaces and tgt_idx in instance_ifaces[tgt_prom]:
                    tgt_ifname = instance_ifaces[tgt_prom][tgt_idx].get("name", "")

                if not link.source_interface and src_ifname:
                    link.source_interface = src_ifname
                    links_iface_filled += 1
                if not link.target_interface and tgt_ifname:
                    link.target_interface = tgt_ifname
                    links_iface_filled += 1
                # Set IPs on the link
                if src_ip:
                    link.source_ip = src_ip
                if tgt_ip:
                    link.target_ip = tgt_ip

                # Remove matched subnet so it's not reused for another link
                subnet_members[net_key] = [m for m in members
                                            if m[0] != src_prom and m[0] != tgt_prom]
                break

    # Build (instance, ifIndex) -> IP lookup for setting link IPs
    # Also build (instance, ifName) -> IP by combining with ifIndex->ifName map
    iface_name_to_ip: dict[tuple[str, str], str] = {}  # (instance, ifName) -> IP
    for (inst, ifidx), ip in iface_ip_map.items():
        # Find the ifName for this ifIndex
        if inst in instance_ifaces and ifidx in instance_ifaces[inst]:
            ifname = instance_ifaces[inst][ifidx].get("name", "")
            if ifname:
                iface_name_to_ip[(inst, ifname)] = ip

    # Step 3.8: Set management IP on nodes from SNMP interface IP data
    mgmt_patterns = ["mgmteth", "mgmt", "management", "fxp0", "me0"]
    for node in nodes:
        if node.mgmt_ip:
            continue  # already set
        prom_inst = node_instance_map.get(node.id)
        if not prom_inst:
            continue
        # Look for management interface IP
        for (inst, ifname), ip in iface_name_to_ip.items():
            if inst != prom_inst:
                continue
            if any(ifname.lower().startswith(pat) for pat in mgmt_patterns):
                node.mgmt_ip = ip
                break
        # Fallback: use the prometheus_id if it looks like an IP and no mgmt interface found
        if not node.mgmt_ip and prom_inst and prom_inst[0].isdigit():
            node.mgmt_ip = prom_inst

    # Step 4: Update link interface names and bandwidths using normalized matching
    links_updated = 0
    for link in links:
        src_prom = node_instance_map.get(link.source_node_id)
        tgt_prom = node_instance_map.get(link.target_node_id)

        # Build per-instance normalized lookup for quick matching
        def _find_snmp_match(prom_inst, iface_name):
            """Find SNMP data for an interface, using normalized name matching."""
            if not prom_inst or prom_inst not in instance_ifaces:
                return None, None
            norm = _normalize_iface_name(iface_name)
            for idx, data in instance_ifaces[prom_inst].items():
                snmp_name = data.get("name", "")
                snmp_descr = data.get("description", "")
                if _normalize_iface_name(snmp_name) == norm or _normalize_iface_name(snmp_descr) == norm:
                    return snmp_name, data
            return None, None

        # Match and update source interface
        src_snmp_name, src_data = _find_snmp_match(src_prom, link.source_interface)
        if src_snmp_name and src_snmp_name != link.source_interface:
            link.source_interface = src_snmp_name

        # Match and update target interface
        tgt_snmp_name, tgt_data = _find_snmp_match(tgt_prom, link.target_interface)
        if tgt_snmp_name and tgt_snmp_name != link.target_interface:
            link.target_interface = tgt_snmp_name

        # Update bandwidth from real speed
        new_speed = None
        if src_data and src_data.get("speed_mbps", 0) > 0:
            new_speed = src_data["speed_mbps"] / 1000
        elif tgt_data and tgt_data.get("speed_mbps", 0) > 0:
            new_speed = tgt_data["speed_mbps"] / 1000

        if new_speed and new_speed != link.bandwidth_gbps:
            link.bandwidth_gbps = new_speed
            link.cost = round(REFERENCE_BANDWIDTH_GBPS / new_speed, 2) if new_speed > 0 else link.cost
            links_updated += 1

        # Set IPs from SNMP if not already set
        if not link.source_ip and link.source_interface and src_prom:
            ip = iface_name_to_ip.get((src_prom, link.source_interface), "")
            if ip:
                link.source_ip = ip
        if not link.target_ip and link.target_interface and tgt_prom:
            ip = iface_name_to_ip.get((tgt_prom, link.target_interface), "")
            if ip:
                link.target_ip = ip

    db.commit()

    return {
        "enriched": True,
        "matched_nodes": matched,
        "total_nodes": len(nodes),
        "interfaces_renamed": interfaces_replaced,
        "interfaces_updated": interfaces_updated,
        "interfaces_added": interfaces_added,
        "links_iface_filled": links_iface_filled,
        "links_updated": links_updated,
    }


@router.post("/discover")
async def discover(topology_id: int, req: DiscoveryRequest, db: Session = Depends(get_db)):
    """
    Start network discovery from a seed device.
    Returns an SSE stream of progress events, ending with a 'done' event
    that includes the created node/link counts.
    """

    async def event_stream():
        # We'll collect events from the discovery coroutine
        event_queue: asyncio.Queue[dict] = asyncio.Queue()

        async def on_progress(event_type: str, message: str, data: dict | None = None):
            await event_queue.put({
                "type": event_type,
                "message": message,
                **(data or {}),
            })

        # Run discovery in background task
        discovery_result: DiscoveryResult | None = None
        discovery_error: str | None = None

        async def run_discovery():
            nonlocal discovery_result, discovery_error
            try:
                if req.discovery_method == "igp":
                    discovery_result = await discover_topology_igp(
                        seed_ip=req.seed_ip,
                        seed_os=req.os_type,
                        username=req.username,
                        password=req.password,
                        igp_protocol=req.igp_protocol,
                        timeout=req.timeout_per_device,
                        on_progress=on_progress,
                    )
                else:
                    discovery_result = await discover_topology(
                        seed_ip=req.seed_ip,
                        seed_os=req.os_type,
                        username=req.username,
                        password=req.password,
                        max_depth=req.max_depth,
                        max_devices=req.max_devices,
                        timeout_per_device=req.timeout_per_device,
                        on_progress=on_progress,
                    )
            except Exception as e:
                discovery_error = str(e)
            finally:
                await event_queue.put(None)  # sentinel

        task = asyncio.create_task(run_discovery())

        # Stream events as they come in
        while True:
            event = await event_queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

        # Discovery finished — now create topology objects
        if discovery_error:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Discovery failed: {discovery_error}'})}\n\n"
            return

        if not discovery_result or not discovery_result.devices:
            yield f"data: {json.dumps({'type': 'error', 'message': 'No devices discovered'})}\n\n"
            return

        try:
            nodes_created, links_created = _build_topology_objects(
                topology_id, discovery_result, db, clear_existing=req.clear_existing
            )
            yield f"data: {json.dumps({'type': 'progress', 'message': f'Created {nodes_created} nodes, {links_created} links'})}\n\n"

            # Enrich from Prometheus if configured
            yield f"data: {json.dumps({'type': 'progress', 'message': 'Checking Prometheus for SNMP enrichment...'})}\n\n"
            enrichment = _enrich_from_prometheus(topology_id, db)
            if enrichment.get("enriched"):
                enrich_msg = (
                    f"Prometheus enrichment: matched {enrichment['matched_nodes']}/{enrichment['total_nodes']} nodes, "
                    f"renamed {enrichment.get('interfaces_renamed', 0)} interfaces, "
                    f"updated {enrichment.get('interfaces_updated', 0)} speeds, "
                    f"added {enrichment.get('interfaces_added', 0)} new interfaces, "
                    f"corrected {enrichment.get('links_updated', 0)} link bandwidths"
                )
                yield f"data: {json.dumps({'type': 'progress', 'message': enrich_msg})}\n\n"
            else:
                skip_reason = enrichment.get("reason", "unknown")
                yield f"data: {json.dumps({'type': 'progress', 'message': f'Prometheus enrichment skipped: {skip_reason}'})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'message': f'Discovery complete: {nodes_created} nodes, {links_created} links', 'nodes_created': nodes_created, 'links_created': links_created, 'topology_id': topology_id, 'enrichment': enrichment})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to create topology objects: {e}'})}\n\n"

        await task  # ensure cleanup

    return StreamingResponse(event_stream(), media_type="text/event-stream")

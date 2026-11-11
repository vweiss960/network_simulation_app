"""
Network topology discovery via SSH.

Supports two discovery methods:
1. IGP (ISIS/OSPF) — SSH to seed device only, pull the full IGP database
   to discover all routers and adjacencies in the routing domain.
2. LLDP/CDP — Recursive BFS crawl from seed device via neighbor protocols.

Supports JUNOS, IOS-XE, IOS-XR, and NX-OS.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Awaitable

import asyncssh


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DiscoveredNeighbor:
    local_interface: str
    remote_system_name: str
    remote_interface: str
    remote_mgmt_ip: str | None = None


@dataclass
class DiscoveredDevice:
    hostname: str
    mgmt_ip: str
    os_type: str
    vendor: str = ""
    model: str = ""
    interfaces: list[dict] = field(default_factory=list)
    neighbors: list[DiscoveredNeighbor] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    devices: dict[str, DiscoveredDevice] = field(default_factory=dict)
    links: list[tuple[str, str, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Interface speed inference from name prefix
# ---------------------------------------------------------------------------

SPEED_MAP = [
    (re.compile(r"(?i)^(FourHundredGig|et-)", re.IGNORECASE), 400.0),
    (re.compile(r"(?i)^(HundredGig)", re.IGNORECASE), 100.0),
    (re.compile(r"(?i)^(et-)", re.IGNORECASE), 100.0),
    (re.compile(r"(?i)^(FortyGig|ae)", re.IGNORECASE), 40.0),
    (re.compile(r"(?i)^(TenGig|TenGigE|xe-)", re.IGNORECASE), 10.0),
    (re.compile(r"(?i)^(Gig|ge-)", re.IGNORECASE), 1.0),
    (re.compile(r"(?i)^(Fast)", re.IGNORECASE), 0.1),
]


def _infer_speed(iface_name: str) -> float:
    for pattern, speed in SPEED_MAP:
        if pattern.match(iface_name):
            return speed
    return 10.0  # default


# ---------------------------------------------------------------------------
# OS → vendor mapping
# ---------------------------------------------------------------------------

OS_VENDOR = {
    "junos": "Juniper",
    "iosxe": "Cisco",
    "iosxr": "Cisco",
    "nxos": "Cisco",
}


# ---------------------------------------------------------------------------
# SSH command execution
# ---------------------------------------------------------------------------

async def _ssh_run(ip: str, username: str, password: str, command: str,
                   timeout: int = 30) -> str:
    """Run a single command over SSH and return stdout."""
    # Network devices often use older SSH algorithms — accept everything
    conn = await asyncio.wait_for(
        asyncssh.connect(
            ip, username=username, password=password,
            known_hosts=None,  # lab/discovery — skip host-key checking
            connect_timeout=timeout,
            server_host_key_algs=[
                'ssh-rsa', 'rsa-sha2-256', 'rsa-sha2-512',
                'ssh-dss', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384',
                'ecdsa-sha2-nistp521', 'ssh-ed25519',
            ],
            kex_algs=[
                'diffie-hellman-group14-sha256', 'diffie-hellman-group14-sha1',
                'diffie-hellman-group16-sha512', 'diffie-hellman-group18-sha512',
                'diffie-hellman-group-exchange-sha256', 'diffie-hellman-group-exchange-sha1',
                'diffie-hellman-group1-sha1',
                'ecdh-sha2-nistp256', 'ecdh-sha2-nistp384', 'ecdh-sha2-nistp521',
                'curve25519-sha256', 'curve25519-sha256@libssh.org',
            ],
            encryption_algs=[
                'aes128-ctr', 'aes192-ctr', 'aes256-ctr',
                'aes128-cbc', 'aes192-cbc', 'aes256-cbc',
                'aes128-gcm@openssh.com', 'aes256-gcm@openssh.com',
                '3des-cbc',
            ],
        ),
        timeout=timeout,
    )
    async with conn:
        result = await asyncio.wait_for(conn.run(command), timeout=timeout)
        return result.stdout or ""


# ---------------------------------------------------------------------------
# Per-OS parsers
# ---------------------------------------------------------------------------

def _clean_hostname(name: str) -> str:
    """Strip domain suffix from a hostname, but preserve IP addresses."""
    name = name.strip()
    if not name:
        return ""
    # Don't strip IP addresses (contain only digits and dots)
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", name):
        return name
    # Strip domain suffix (e.g., "PE1.example.com" -> "PE1")
    return name.split(".")[0]


# ---- JUNOS ----------------------------------------------------------------

def _parse_junos_lldp(output: str) -> list[DiscoveredNeighbor]:
    neighbors: list[DiscoveredNeighbor] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return neighbors

    info = data.get("lldp-neighbors-information", [{}])
    if isinstance(info, list):
        info = info[0] if info else {}
    neighbor_list = info.get("lldp-neighbor-information", [])
    if not isinstance(neighbor_list, list):
        neighbor_list = [neighbor_list]

    for n in neighbor_list:
        def _get(key: str) -> str:
            v = n.get(key, [{}])
            if isinstance(v, list):
                v = v[0] if v else {}
            if isinstance(v, dict):
                return v.get("data", "")
            return str(v)

        local_if = _get("lldp-local-port-id") or _get("lldp-local-interface")
        remote_name = _get("lldp-remote-system-name")
        remote_port = _get("lldp-remote-port-id") or _get("lldp-remote-port-description")

        # Management address may be nested
        mgmt = ""
        mgmt_block = n.get("lldp-remote-management-address", [])
        if isinstance(mgmt_block, list) and mgmt_block:
            mgmt_block = mgmt_block[0]
        if isinstance(mgmt_block, dict):
            mgmt = mgmt_block.get("lldp-remote-management-address", "")
            if isinstance(mgmt, list) and mgmt:
                mgmt = mgmt[0] if isinstance(mgmt[0], str) else mgmt[0].get("data", "")
            elif isinstance(mgmt, dict):
                mgmt = mgmt.get("data", "")

        if remote_name:
            neighbors.append(DiscoveredNeighbor(
                local_interface=local_if.strip(),
                remote_system_name=_clean_hostname(remote_name),
                remote_interface=remote_port.strip(),
                remote_mgmt_ip=mgmt.strip() or None,
            ))
    return neighbors


# ---- IOS-XE / IOS-XR (text parsing) --------------------------------------

def _parse_ios_lldp(output: str) -> list[DiscoveredNeighbor]:
    """Parse LLDP neighbors detail for IOS-XE and IOS-XR."""
    neighbors: list[DiscoveredNeighbor] = []
    # Split into per-neighbor blocks
    blocks = re.split(r"-{20,}", output)
    for block in blocks:
        local = re.search(r"Local\s+Int(?:erf(?:ace)?|f):\s*(.+)", block)
        sysname = re.search(r"System Name:\s*(.+)", block)
        port_id = re.search(r"Port id:\s*(.+)", block)
        mgmt = re.search(r"(?:IP|IPv4):\s*(\d+\.\d+\.\d+\.\d+)", block)
        if sysname:
            neighbors.append(DiscoveredNeighbor(
                local_interface=(local.group(1).strip().rstrip(",") if local else ""),
                remote_system_name=_clean_hostname(sysname.group(1)),
                remote_interface=(port_id.group(1).strip() if port_id else ""),
                remote_mgmt_ip=(mgmt.group(1).strip() if mgmt else None),
            ))
    return neighbors


def _parse_ios_cdp(output: str) -> list[DiscoveredNeighbor]:
    """Parse CDP neighbors detail for IOS-XE, IOS-XR, NX-OS (text mode)."""
    neighbors: list[DiscoveredNeighbor] = []
    blocks = re.split(r"-{20,}", output)
    for block in blocks:
        device_id = re.search(r"Device ID:\s*(.+)", block)
        local_if = re.search(r"Interface:\s*(.+?),", block)
        port_id = re.search(r"Port ID\s*\(outgoing port\):\s*(.+)", block)
        ip_addr = re.search(r"(?:IP|IPv4)\s+address:\s*(\d+\.\d+\.\d+\.\d+)", block)
        platform = re.search(r"Platform:\s*(.+?),", block)
        if device_id:
            neighbors.append(DiscoveredNeighbor(
                local_interface=(local_if.group(1).strip() if local_if else ""),
                remote_system_name=_clean_hostname(device_id.group(1)),
                remote_interface=(port_id.group(1).strip() if port_id else ""),
                remote_mgmt_ip=(ip_addr.group(1).strip() if ip_addr else None),
            ))
    return neighbors


# ---- NX-OS (JSON parsing) ------------------------------------------------

def _parse_nxos_lldp_json(output: str) -> list[DiscoveredNeighbor]:
    neighbors: list[DiscoveredNeighbor] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return neighbors

    table = data.get("TABLE_nbor_detail", {}).get("ROW_nbor_detail", [])
    if isinstance(table, dict):
        table = [table]
    for entry in table:
        neighbors.append(DiscoveredNeighbor(
            local_interface=str(entry.get("l_port_id", "")).strip(),
            remote_system_name=_clean_hostname(str(entry.get("sys_name", ""))),
            remote_interface=str(entry.get("port_id", "")).strip(),
            remote_mgmt_ip=str(entry.get("mgmt_addr", "")).strip() or None,
        ))
    return neighbors


def _parse_nxos_cdp_json(output: str) -> list[DiscoveredNeighbor]:
    neighbors: list[DiscoveredNeighbor] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return neighbors

    table = data.get("TABLE_cdp_neighbor_detail_info", {}).get("ROW_cdp_neighbor_detail_info", [])
    if isinstance(table, dict):
        table = [table]
    for entry in table:
        neighbors.append(DiscoveredNeighbor(
            local_interface=str(entry.get("intf_id", "")).strip(),
            remote_system_name=_clean_hostname(str(entry.get("device_id", ""))),
            remote_interface=str(entry.get("port_id", "")).strip(),
            remote_mgmt_ip=str(entry.get("v4mgmtaddr", "")).strip() or None,
        ))
    return neighbors


# ---------------------------------------------------------------------------
# IGP (ISIS / OSPF) database parsers
# ---------------------------------------------------------------------------

# Commands to pull IGP topology from seed device
IGP_COMMANDS = {
    "junos": {
        "isis_adjacency": "show isis adjacency detail | display json",
        "isis_database": "show isis database extensive | display json",
        "isis_interface": "show isis interface detail | display json",
        "ospf_neighbor": "show ospf neighbor detail | display json",
        "ospf_database": "show ospf database router detail | display json",
    },
    "iosxe": {
        "isis_adjacency": "show isis neighbors detail",
        "isis_database": "show isis database detail",
        "isis_interface": "show isis interface brief",
        "ospf_neighbor": "show ip ospf neighbor detail",
        "ospf_database": "show ip ospf database router",
    },
    "iosxr": {
        "isis_adjacency": "show isis neighbors detail",
        "isis_database": "show isis database detail",
        "isis_interface": "show isis interface brief",
        "ospf_neighbor": "show ospf neighbor detail",
        "ospf_database": "show ospf database router",
    },
    "nxos": {
        "isis_adjacency": "show isis adjacency detail | json",
        "isis_database": "show isis database detail | json",
        "isis_interface": "show isis interface brief | json",
        "ospf_neighbor": "show ip ospf neighbors detail | json",
        "ospf_database": "show ip ospf database router detail | json",
    },
}


@dataclass
class IGPAdjacency:
    """Represents a single IGP adjacency (link between two routers)."""
    local_hostname: str
    local_interface: str
    remote_hostname: str
    remote_interface: str
    metric: int = 10
    ip_address: str = ""  # remote IP if known


@dataclass
class IGPRouter:
    """A router discovered from the IGP database."""
    hostname: str
    system_id: str = ""
    ip_address: str = ""  # router-id / loopback


# ---- ISIS text parsing (IOS-XE / IOS-XR) --------------------------------

def _parse_ios_isis_adjacency(output: str) -> list[IGPAdjacency]:
    """Parse 'show isis neighbors detail' for IOS-XE/IOS-XR.

    IOS-XR format (show isis neighbors detail):
        System Id      Interface        SNPA           State  Holdtime  Type  IETF-NSF
        PE2            Gi0/0/0/0        *PtoP*         Up     23        L1    Capable

      Or detailed blocks with:
        System Id: PE2
        Interface: GigabitEthernet0/0/0/0
        Neighbor IPv4 Address: 192.168.1.0
        ...
    """
    adjacencies: list[IGPAdjacency] = []

    # Try to parse the tabular summary format first (IOS-XR brief)
    # Lines like: "PE2            Gi0/0/0/0        *PtoP*         Up"
    table_pattern = re.finditer(
        r"^(\S+?)\s+((?:Gi|Te|Hu|Fo|Tw|TenGig|HundredGig|FourHundredGig|GigabitEthernet|TenGigE|Bundle-Ether|BE|ae)\S*)\s+\S+\s+(Up|Init)",
        output, re.MULTILINE | re.IGNORECASE,
    )
    for m in table_pattern:
        hostname = _clean_hostname(m.group(1))
        interface = m.group(2).strip()
        if hostname:
            adjacencies.append(IGPAdjacency(
                local_hostname="",
                local_interface=interface,
                remote_hostname=hostname,
                remote_interface="",
                ip_address="",
            ))

    # Also parse detailed block format
    blocks = re.split(r"\n(?=\S)", output)
    current_hostname = ""

    for block in blocks:
        sysid_match = re.search(r"System\s+Id\s*:\s*(\S+)", block)
        if sysid_match:
            pass  # system ID is less useful than hostname

        host_match = re.search(r"Hostname\s*:\s*(\S+)", block)
        if host_match:
            current_hostname = _clean_hostname(host_match.group(1))

        intf_match = re.search(r"Interface\s*:\s*(\S+)", block)
        state_match = re.search(r"State\s*:\s*(\S+)", block)

        metric = 10
        metric_match = re.search(r"Metric:\s*(\d+)", block)
        if metric_match:
            metric = int(metric_match.group(1))

        ip_match = re.search(r"(?:Neighbor\s+)?IP(?:v4)?\s+Address\s*:\s*(\d+\.\d+\.\d+\.\d+)", block)

        if current_hostname and intf_match:
            state = state_match.group(1) if state_match else ""
            if state.lower() in ("up", "init", ""):
                # Check if we already have this neighbor from the table parse
                already = any(a.remote_hostname == current_hostname and a.local_interface == intf_match.group(1).strip()
                              for a in adjacencies)
                if not already:
                    adjacencies.append(IGPAdjacency(
                        local_hostname="",
                        local_interface=intf_match.group(1).strip(),
                        remote_hostname=current_hostname,
                        remote_interface="",
                        metric=metric,
                        ip_address=ip_match.group(1) if ip_match else "",
                    ))

    return adjacencies


def _parse_ios_isis_database(output: str, seed_hostname: str) -> tuple[list[IGPRouter], list[IGPAdjacency]]:
    """
    Parse 'show isis database detail' for IOS-XE/IOS-XR.
    This gives us the FULL topology — all routers and their adjacencies.
    """
    routers: list[IGPRouter] = []
    adjacencies: list[IGPAdjacency] = []

    # Split into per-LSP blocks
    # Each LSP starts with a line like: "hostname.00-00" or "hostname.00-00  * ..."
    lsp_blocks = re.split(r"\n(?=\S+\.\d{2}-\d{2})", output)

    # Build hostname map from dynamic hostname TLVs (system-id → hostname)
    sysid_to_hostname: dict[str, str] = {}

    for block in lsp_blocks:
        lsp_match = re.match(r"(\S+?)\.(\d{2})-(\d{2})", block)
        if not lsp_match:
            continue

        raw_sysid = lsp_match.group(1)  # e.g., "PE2" or a system-id like "0000.0000.0002"
        pseudo_node = lsp_match.group(2)

        if pseudo_node != "00":
            continue  # Only process router LSPs (not pseudonodes)

        hostname = _clean_hostname(raw_sysid)

        # Extract hostname from dynamic hostname TLV (most reliable)
        dyn_host = re.search(r"Hostname\s*:\s*(\S+)", block)
        if dyn_host:
            hostname = _clean_hostname(dyn_host.group(1))

        sysid_to_hostname[raw_sysid] = hostname

        # Extract router IP
        ip_match = re.search(r"(?:IP Address|Router ID|IPv4 Router ID)\s*:\s*(\d+\.\d+\.\d+\.\d+)", block)
        router_ip = ip_match.group(1) if ip_match else ""

        routers.append(IGPRouter(
            hostname=hostname,
            system_id=lsp_match.group(0),
            ip_address=router_ip,
        ))

        # Parse IS neighbors from router LSPs ONLY (not pseudonode LSPs)
        # Match lines like:
        #   "  Metric: 10         IS-Extended PE2.01"
        #   "  Metric: 10         IS PE2.00"
        #   "  Metric: 10         IS-Extended-IP PE2.01"
        # But NOT MT (Multi-Topology) lines like:
        #   "  Metric: 10         MT (IPv6 Unicast) IS-Extended PE2.01"
        is_neighbor_pattern = re.finditer(
            r"^\s+Metric:\s*(\d+)\s+IS(?:-Extended(?:-IP)?)?(?:\s*\([^)]*\))?\s+(\S+?)\.(\d{2})\s*$",
            block,
            re.MULTILINE,
        )
        for m in is_neighbor_pattern:
            metric = int(m.group(1))
            nbr_raw = m.group(2)
            nbr_pseudo = m.group(3)

            # Resolve hostname from system-id if possible
            nbr_hostname = sysid_to_hostname.get(nbr_raw, _clean_hostname(nbr_raw))

            adjacencies.append(IGPAdjacency(
                local_hostname=hostname,
                local_interface="",
                remote_hostname=nbr_hostname if nbr_pseudo == "00" else f"{nbr_raw}.{nbr_pseudo}",
                remote_interface="",
                metric=metric,
            ))

    # Now process pseudonode LSPs to build the pseudonode membership lists
    # A pseudonode LSP (e.g., PE2.01-00) lists all routers on that LAN segment
    pseudonode_members: dict[str, list[tuple[str, int]]] = {}  # pn_name -> [(hostname, metric)]

    for block in lsp_blocks:
        lsp_match = re.match(r"(\S+?)\.(\d{2})-(\d{2})", block)
        if not lsp_match:
            continue

        raw_sysid = lsp_match.group(1)
        pseudo_node = lsp_match.group(2)

        if pseudo_node == "00":
            continue  # Skip router LSPs — already processed above

        pn_name = f"{raw_sysid}.{pseudo_node}"

        # Parse IS neighbors from pseudonode LSP — these are the LAN segment members
        for m in re.finditer(
            r"^\s+Metric:\s*(\d+)\s+IS(?:-Extended(?:-IP)?)?\s+(\S+?)\.(\d{2})\s*$",
            block,
            re.MULTILINE,
        ):
            metric = int(m.group(1))
            member_raw = m.group(2)
            member_hostname = sysid_to_hostname.get(member_raw, _clean_hostname(member_raw))

            if pn_name not in pseudonode_members:
                pseudonode_members[pn_name] = []
            pseudonode_members[pn_name].append((member_hostname, metric))

    # Resolve pseudonodes: each pseudonode represents a LAN segment
    # All members of a pseudonode are connected to each other
    # Also collect router→pseudonode adjacencies to find the metrics
    router_pn_adjacencies: dict[str, dict[str, int]] = {}  # {router: {pn_name: metric}}
    for adj in adjacencies:
        pn_match = re.match(r"(\S+)\.(\d{2})$", adj.remote_hostname)
        if pn_match and pn_match.group(2) != "00":
            if adj.local_hostname not in router_pn_adjacencies:
                router_pn_adjacencies[adj.local_hostname] = {}
            router_pn_adjacencies[adj.local_hostname][adj.remote_hostname] = adj.metric

    # Build direct router-to-router adjacencies
    resolved: list[IGPAdjacency] = []
    seen_pairs: set[tuple[str, str]] = set()

    # From pseudonode membership: create mesh of all members
    for pn_name, members in pseudonode_members.items():
        hostnames = [h for h, _ in members]
        for i, h1 in enumerate(hostnames):
            for h2 in hostnames[i + 1:]:
                pair = tuple(sorted([h1, h2]))
                if pair not in seen_pairs and pair[0] != pair[1]:
                    seen_pairs.add(pair)
                    # Use the metric from the router LSP (router→pseudonode)
                    metric = router_pn_adjacencies.get(h1, {}).get(pn_name, 10)
                    resolved.append(IGPAdjacency(
                        local_hostname=pair[0],
                        local_interface="",
                        remote_hostname=pair[1],
                        remote_interface="",
                        metric=metric,
                    ))

    # Also include any direct point-to-point adjacencies (remote is .00, not pseudonode)
    for adj in adjacencies:
        pn_match = re.match(r"(\S+)\.(\d{2})$", adj.remote_hostname)
        if pn_match and pn_match.group(2) != "00":
            continue  # Skip pseudonode references — already handled above
        remote = _clean_hostname(adj.remote_hostname)
        if remote == adj.local_hostname:
            continue  # Skip self-adjacencies
        pair = tuple(sorted([adj.local_hostname, remote]))
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            resolved.append(IGPAdjacency(
                local_hostname=adj.local_hostname,
                local_interface="",
                remote_hostname=remote,
                remote_interface="",
                metric=adj.metric,
            ))

    return routers, resolved


    # _resolve_pseudonodes is now integrated into _parse_ios_isis_database


# ---- JUNOS ISIS JSON parsing --------------------------------------------

def _parse_junos_isis_adjacency(output: str) -> list[IGPAdjacency]:
    """Parse 'show isis adjacency detail | display json' for JUNOS."""
    adjacencies: list[IGPAdjacency] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return adjacencies

    adj_info = data.get("isis-adjacency-information", [{}])
    if isinstance(adj_info, list):
        adj_info = adj_info[0] if adj_info else {}

    adj_list = adj_info.get("isis-adjacency", [])
    if not isinstance(adj_list, list):
        adj_list = [adj_list]

    for adj in adj_list:
        def _get(key: str) -> str:
            v = adj.get(key, [{}])
            if isinstance(v, list):
                v = v[0] if v else {}
            if isinstance(v, dict):
                return v.get("data", "")
            return str(v)

        interface = _get("interface-name")
        sysname = _clean_hostname(_get("system-name"))
        state = _get("adjacency-state")
        ip = _get("ip-address")

        if sysname and state.lower() in ("up", ""):
            adjacencies.append(IGPAdjacency(
                local_hostname="",
                local_interface=interface,
                remote_hostname=sysname,
                remote_interface="",
                ip_address=ip,
            ))

    return adjacencies


def _parse_junos_isis_database(output: str) -> tuple[list[IGPRouter], list[IGPAdjacency]]:
    """Parse 'show isis database extensive | display json' for JUNOS."""
    routers: list[IGPRouter] = []
    adjacencies: list[IGPAdjacency] = []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return routers, adjacencies

    db_info = data.get("isis-database-information", [{}])
    if isinstance(db_info, list):
        db_info = db_info[0] if db_info else {}

    # Iterate levels
    for level_key in ["isis-database", "isis-database-L1", "isis-database-L2"]:
        db_list = db_info.get(level_key, [])
        if not isinstance(db_list, list):
            db_list = [db_list] if db_list else []

        for db_entry in db_list:
            lsp_list = db_entry.get("isis-database-entry", [])
            if not isinstance(lsp_list, list):
                lsp_list = [lsp_list] if lsp_list else []

            for lsp in lsp_list:
                def _get_lsp(key: str) -> str:
                    v = lsp.get(key, [{}])
                    if isinstance(v, list):
                        v = v[0] if v else {}
                    if isinstance(v, dict):
                        return v.get("data", "")
                    return str(v)

                lsp_id = _get_lsp("lsp-id")
                hostname = _clean_hostname(lsp_id.split(".")[0]) if lsp_id else ""

                # Check if this is a pseudonode
                parts = lsp_id.split(".") if lsp_id else []
                is_pseudonode = len(parts) >= 2 and parts[1] != "00" and not parts[1].startswith("00-")

                if not is_pseudonode and hostname:
                    # Extract IP from TLVs
                    ip = ""
                    tlv_list = lsp.get("isis-tlv", [])
                    if not isinstance(tlv_list, list):
                        tlv_list = [tlv_list] if tlv_list else []
                    for tlv in tlv_list:
                        ip_tlv = tlv.get("ip-prefix-tlv", tlv.get("ipaddress-tlv", []))
                        if ip_tlv:
                            if isinstance(ip_tlv, list):
                                ip_tlv = ip_tlv[0] if ip_tlv else {}
                            if isinstance(ip_tlv, dict):
                                addr = ip_tlv.get("address", "")
                                if isinstance(addr, list):
                                    addr = addr[0] if addr else ""
                                if isinstance(addr, dict):
                                    addr = addr.get("data", "")
                                if addr:
                                    ip = str(addr)
                                    break
                        hostname_tlv = tlv.get("hostname-tlv", [])
                        if hostname_tlv:
                            if isinstance(hostname_tlv, list):
                                hostname_tlv = hostname_tlv[0] if hostname_tlv else {}
                            if isinstance(hostname_tlv, dict):
                                h = hostname_tlv.get("hostname", "")
                                if isinstance(h, list):
                                    h = h[0] if h else ""
                                if isinstance(h, dict):
                                    h = h.get("data", "")
                                if h:
                                    hostname = _clean_hostname(str(h))

                    routers.append(IGPRouter(hostname=hostname, system_id=lsp_id, ip_address=ip))

                # Parse IS-neighbor TLVs for adjacencies
                tlv_list = lsp.get("isis-tlv", [])
                if not isinstance(tlv_list, list):
                    tlv_list = [tlv_list] if tlv_list else []
                for tlv in tlv_list:
                    is_nbr = tlv.get("is-neighbor-tlv", tlv.get("isis-reachability-tlv", []))
                    if not is_nbr:
                        continue
                    if not isinstance(is_nbr, list):
                        is_nbr = [is_nbr]
                    for nbr_entry in is_nbr:
                        nbr_list = nbr_entry.get("is-neighbor", nbr_entry.get("isis-topology-entry", []))
                        if not isinstance(nbr_list, list):
                            nbr_list = [nbr_list] if nbr_list else []
                        for nbr in nbr_list:
                            def _get_nbr(key: str) -> str:
                                v = nbr.get(key, "")
                                if isinstance(v, list):
                                    v = v[0] if v else ""
                                if isinstance(v, dict):
                                    return v.get("data", "")
                                return str(v)

                            nbr_id = _get_nbr("is-neighbor-id") or _get_nbr("address-prefix")
                            metric_str = _get_nbr("metric") or _get_nbr("isis-topology-metric")
                            metric = int(metric_str) if metric_str.isdigit() else 10
                            nbr_hostname = _clean_hostname(nbr_id.split(".")[0]) if nbr_id else ""

                            if nbr_hostname and nbr_hostname != hostname:
                                adjacencies.append(IGPAdjacency(
                                    local_hostname=hostname,
                                    local_interface="",
                                    remote_hostname=nbr_hostname,
                                    remote_interface="",
                                    metric=metric,
                                ))

    # Deduplicate
    seen = set()
    deduped = []
    for adj in adjacencies:
        key = tuple(sorted([adj.local_hostname, adj.remote_hostname]))
        if key not in seen:
            seen.add(key)
            deduped.append(adj)

    return routers, deduped


# ---- NX-OS ISIS JSON parsing --------------------------------------------

def _parse_nxos_isis_adjacency(output: str) -> list[IGPAdjacency]:
    """Parse 'show isis adjacency detail | json' for NX-OS."""
    adjacencies: list[IGPAdjacency] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return adjacencies

    table = data.get("TABLE_process", {}).get("ROW_process", {})
    if isinstance(table, list):
        table = table[0] if table else {}

    adj_table = table.get("TABLE_adj", {}).get("ROW_adj", [])
    if isinstance(adj_table, dict):
        adj_table = [adj_table]

    for adj in adj_table:
        sysname = _clean_hostname(str(adj.get("system_name", "") or adj.get("adj_sys_name", "")))
        intf = str(adj.get("intf", "") or adj.get("adj_intf", ""))
        ip = str(adj.get("ipv4_addr", "") or adj.get("adj_ipv4_addr", ""))
        state = str(adj.get("adj_state", "")).lower()

        if sysname and state in ("up", "init", ""):
            adjacencies.append(IGPAdjacency(
                local_hostname="",
                local_interface=intf,
                remote_hostname=sysname,
                remote_interface="",
                ip_address=ip,
            ))

    return adjacencies


# ---- OSPF parsing (IOS-XE / IOS-XR text) --------------------------------

def _parse_ios_ospf_neighbor(output: str) -> list[IGPAdjacency]:
    """Parse 'show ip ospf neighbor detail' for IOS-XE/IOS-XR."""
    adjacencies: list[IGPAdjacency] = []

    blocks = re.split(r"\n(?=\s*Neighbor\s+\d)", output)
    for block in blocks:
        nbr_match = re.search(r"Neighbor\s+(\d+\.\d+\.\d+\.\d+)", block)
        intf_match = re.search(r"(?:interface|in the area)\s+\S+\s+(?:via|interface)\s+(\S+)", block, re.IGNORECASE)
        if not intf_match:
            intf_match = re.search(r"interface\s+(\S+)", block, re.IGNORECASE)
        state_match = re.search(r"State\s+is\s+(\S+)", block, re.IGNORECASE)

        if nbr_match:
            state = state_match.group(1) if state_match else ""
            if "FULL" in state.upper() or not state:
                adjacencies.append(IGPAdjacency(
                    local_hostname="",
                    local_interface=intf_match.group(1) if intf_match else "",
                    remote_hostname=nbr_match.group(1),  # router-id as hostname
                    remote_interface="",
                    ip_address=nbr_match.group(1),
                ))

    return adjacencies


def _parse_ios_ospf_database(output: str, seed_hostname: str) -> tuple[list[IGPRouter], list[IGPAdjacency]]:
    """Parse 'show ip ospf database router' for IOS-XE/IOS-XR."""
    routers: list[IGPRouter] = []
    adjacencies: list[IGPAdjacency] = []

    # Split into per-router LSA blocks
    blocks = re.split(r"\n\s*(?=LS Type\s*:\s*Router|OSPF Router with ID)", output)

    for block in blocks:
        # Router ID
        rid_match = re.search(
            r"(?:Advertising Router|Link State ID|OSPF Router with ID)\s*:?\s*(\d+\.\d+\.\d+\.\d+)", block
        )
        if not rid_match:
            continue

        router_id = rid_match.group(1)
        routers.append(IGPRouter(
            hostname=router_id,  # Will try to resolve later
            system_id=router_id,
            ip_address=router_id,
        ))

        # Parse links within this router LSA
        link_blocks = re.finditer(
            r"Link connected to\s*:\s*(.*?)\n.*?"
            r"(?:\(Link ID\)|Neighboring Router ID)\s*(?::\s*)?(\d+\.\d+\.\d+\.\d+).*?"
            r"(?:TOS 0 Metric|Metric)\s*:\s*(\d+)",
            block, re.DOTALL,
        )
        for lm in link_blocks:
            link_type = lm.group(1).strip()
            link_id = lm.group(2)
            metric = int(lm.group(3))

            # Only care about transit and point-to-point links
            if "transit" in link_type.lower() or "point-to-point" in link_type.lower():
                adjacencies.append(IGPAdjacency(
                    local_hostname=router_id,
                    local_interface="",
                    remote_hostname=link_id,
                    remote_interface="",
                    metric=metric,
                ))

    return routers, adjacencies


# ---------------------------------------------------------------------------
# Main IGP discovery (single SSH session)
# ---------------------------------------------------------------------------

async def discover_topology_igp(
    seed_ip: str,
    seed_os: str,
    username: str,
    password: str,
    igp_protocol: str = "isis",
    timeout: int = 30,
    on_progress: "ProgressCallback | None" = None,
) -> DiscoveryResult:
    """
    Discover the full network topology from a single seed device using
    the IGP (ISIS or OSPF) link-state database. No recursive crawling needed.
    """

    async def emit(event_type: str, message: str, data: dict | None = None):
        if on_progress:
            await on_progress(event_type, message, data)

    cmds = IGP_COMMANDS.get(seed_os, IGP_COMMANDS["iosxe"])

    await emit("progress", f"Connecting to seed device {seed_ip}...")

    # Step 1: Get hostname of seed device
    host_cmds = COMMANDS.get(seed_os, COMMANDS["iosxe"])
    try:
        hostname_out = await _ssh_run(seed_ip, username, password, host_cmds["hostname"], timeout)
    except Exception as e:
        await emit("error", f"Failed to connect to {seed_ip}: {e}")
        return DiscoveryResult()

    seed_hostname = ""
    if seed_os == "junos":
        try:
            jdata = json.loads(hostname_out)
            si = jdata.get("software-information", [{}])
            if isinstance(si, list):
                si = si[0] if si else {}
            host_field = si.get("host-name", [{}])
            if isinstance(host_field, list):
                host_field = host_field[0] if host_field else {}
            if isinstance(host_field, dict):
                seed_hostname = host_field.get("data", "")
            else:
                seed_hostname = str(host_field)
        except json.JSONDecodeError:
            seed_hostname = _parse_hostname(hostname_out, seed_os)
    elif seed_os == "nxos":
        seed_hostname = _parse_hostname(hostname_out, seed_os)
    else:
        seed_hostname = _parse_hostname(hostname_out, seed_os)

    if not seed_hostname:
        seed_hostname = seed_ip  # fallback — will try to resolve later from IGP data
    seed_hostname = _clean_hostname(seed_hostname)
    # Detect if hostname resolution failed (got IP, truncated IP, or generic word)
    seed_hostname_is_ip = (
        seed_hostname == seed_ip
        or re.match(r"^\d+$", seed_hostname)  # truncated IP like "10"
        or seed_hostname.lower() in ("system", "cisco", "ios")
    )
    if seed_hostname_is_ip:
        seed_hostname = seed_ip  # reset to full IP, don't use truncated version

    await emit("device", f"Seed device: {seed_hostname}{' (hostname unresolved, using IP)' if seed_hostname_is_ip else ''}", {
        "hostname": seed_hostname, "ip": seed_ip, "os_type": seed_os,
    })

    # Step 2: Pull IGP adjacency table (direct neighbors with interfaces)
    await emit("progress", f"Pulling {igp_protocol.upper()} adjacency table...")
    adj_key = f"{igp_protocol}_adjacency" if igp_protocol == "isis" else "ospf_neighbor"
    direct_adjacencies: list[IGPAdjacency] = []
    try:
        adj_out = await _ssh_run(seed_ip, username, password, cmds[adj_key], timeout)
        if igp_protocol == "isis":
            if seed_os == "junos":
                direct_adjacencies = _parse_junos_isis_adjacency(adj_out)
            elif seed_os == "nxos":
                direct_adjacencies = _parse_nxos_isis_adjacency(adj_out)
            else:
                direct_adjacencies = _parse_ios_isis_adjacency(adj_out)
        else:  # ospf
            direct_adjacencies = _parse_ios_ospf_neighbor(adj_out)

        # Fill in local hostname
        for adj in direct_adjacencies:
            adj.local_hostname = seed_hostname

        await emit("progress", f"Found {len(direct_adjacencies)} direct {igp_protocol.upper()} adjacencies")
    except Exception as e:
        await emit("error", f"Failed to get adjacency table: {e}")

    # Step 3: Pull IGP database (full topology)
    await emit("progress", f"Pulling {igp_protocol.upper()} link-state database...")
    all_routers: list[IGPRouter] = []
    all_adjacencies: list[IGPAdjacency] = []
    try:
        db_key = f"{igp_protocol}_database" if igp_protocol == "isis" else "ospf_database"
        db_out = await _ssh_run(seed_ip, username, password, cmds[db_key], timeout * 2)  # DB can be large

        if igp_protocol == "isis":
            if seed_os == "junos":
                all_routers, all_adjacencies = _parse_junos_isis_database(db_out)
            else:
                all_routers, all_adjacencies = _parse_ios_isis_database(db_out, seed_hostname)
        else:  # ospf
            all_routers, all_adjacencies = _parse_ios_ospf_database(db_out, seed_hostname)

        # Log the parsed adjacencies for debugging
        for adj in all_adjacencies:
            await emit("progress", f"  Link: {adj.local_hostname} -- {adj.remote_hostname} (metric {adj.metric})")
        await emit("progress", f"IGP database: {len(all_routers)} routers, {len(all_adjacencies)} adjacencies")
    except Exception as e:
        await emit("error", f"Failed to parse IGP database: {e}")
        # Fall back to adjacency table only
        if direct_adjacencies:
            await emit("progress", "Falling back to adjacency table only")

    # Step 4: Merge data — use IGP database for topology, adjacency table for interface details

    # If seed hostname is still the IP (parsing failed), try to resolve it from IGP data
    if seed_hostname_is_ip and all_routers:
        # Strategy 1: Match by direct adjacency neighbors
        direct_neighbor_names = {adj.remote_hostname for adj in direct_adjacencies}
        if direct_neighbor_names:
            for router in all_routers:
                # Find which router in the DB has adjacencies to the same neighbors
                router_neighbors = {a.remote_hostname for a in all_adjacencies if a.local_hostname == router.hostname}
                if router_neighbors and router_neighbors & direct_neighbor_names:
                    await emit("progress", f"Resolved seed hostname: {seed_ip} → {router.hostname}")
                    seed_hostname = router.hostname
                    seed_hostname_is_ip = False
                    break

        # Strategy 2: If the ISIS DB was parsed from this device, the first router LSP marked with * is us
        # (The * indicates locally originated LSP in IOS-XR output)
        if seed_hostname_is_ip:
            # Just use the first router that isn't already known by a different method
            for router in all_routers:
                if router.hostname and not re.match(r"\d+\.\d+\.\d+\.\d+$", router.hostname):
                    await emit("progress", f"Resolved seed hostname (first router): {seed_ip} → {router.hostname}")
                    seed_hostname = router.hostname
                    seed_hostname_is_ip = False
                    break

    # Also try to get hostname directly via 'show hostname' for IOS-XR
    if seed_hostname_is_ip and seed_os == "iosxr":
        try:
            hostname_out2 = await _ssh_run(seed_ip, username, password, "show running-config hostname", timeout)
            m = re.search(r"hostname\s+(\S+)", hostname_out2)
            if m:
                resolved = _clean_hostname(m.group(1))
                await emit("progress", f"Resolved seed hostname (running-config): {seed_ip} → {resolved}")
                seed_hostname = resolved
                seed_hostname_is_ip = False
        except Exception:
            pass

    # Update direct adjacency local hostnames if seed hostname changed
    for adj in direct_adjacencies:
        if adj.local_hostname == seed_ip or adj.local_hostname != seed_hostname:
            adj.local_hostname = seed_hostname

    # Build interface map from direct adjacencies (these have interface names)
    # Maps (local_host, remote_host) -> local_interface
    iface_map: dict[tuple[str, str], str] = {}
    for adj in direct_adjacencies:
        if adj.local_interface:
            iface_map[(adj.local_hostname, adj.remote_hostname)] = adj.local_interface
        if adj.remote_interface:
            iface_map[(adj.remote_hostname, adj.local_hostname)] = adj.remote_interface

    # Log discovered interfaces for debugging
    if iface_map:
        await emit("progress", f"Interface map ({len(iface_map)} entries):")
        for (h1, h2), iface in sorted(iface_map.items()):
            await emit("progress", f"  {h1} → {h2}: {iface}")

    # Build device map
    devices: dict[str, DiscoveredDevice] = {}

    # Add seed device
    devices[seed_hostname] = DiscoveredDevice(
        hostname=seed_hostname,
        mgmt_ip=seed_ip,
        os_type=seed_os,
        vendor=OS_VENDOR.get(seed_os, "Unknown"),
    )

    # Log all routers and adjacencies for debugging
    await emit("progress", f"Seed hostname: {seed_hostname} (resolved={not seed_hostname_is_ip})")
    await emit("progress", f"Parsed {len(all_routers)} routers from IGP database:")
    for r in all_routers:
        await emit("progress", f"  Router: {r.hostname} (sysid={r.system_id}, ip={r.ip_address})")
    await emit("progress", f"Parsed {len(all_adjacencies)} adjacencies:")
    for a in all_adjacencies:
        await emit("progress", f"  Adj: {a.local_hostname} <-> {a.remote_hostname} (metric={a.metric})")

    # Add all routers from IGP database — skip any that match the seed device
    # All names that refer to the seed device — hostname, IP, and any bogus parse results
    seed_aliases = {seed_hostname, seed_ip, "System"}  # "System" from bad IOS-XR show version parse

    for router in all_routers:
        # If this router matches the seed device by hostname or IP, skip it
        if router.hostname in seed_aliases or router.ip_address in seed_aliases:
            await emit("progress", f"Skipping router {router.hostname} (matches seed device)")
            continue
        if router.hostname not in devices:
            devices[router.hostname] = DiscoveredDevice(
                hostname=router.hostname,
                mgmt_ip=router.ip_address or "",
                os_type=seed_os,  # assume same OS for now
                vendor=OS_VENDOR.get(seed_os, "Unknown"),
            )
            await emit("device", f"Discovered {router.hostname}", {
                "hostname": router.hostname,
                "ip": router.ip_address,
                "os_type": seed_os,
                "vendor": OS_VENDOR.get(seed_os, "Unknown"),
            })

    # If no routers from database, add from adjacency table
    if not all_routers:
        for adj in direct_adjacencies:
            if adj.remote_hostname not in devices:
                devices[adj.remote_hostname] = DiscoveredDevice(
                    hostname=adj.remote_hostname,
                    mgmt_ip=adj.ip_address or "",
                    os_type=_infer_os_type(adj.remote_hostname, seed_os),
                    vendor=OS_VENDOR.get(seed_os, "Unknown"),
                )
                await emit("device", f"Discovered {adj.remote_hostname}", {
                    "hostname": adj.remote_hostname,
                    "ip": adj.ip_address,
                    "os_type": seed_os,
                })

    # Use database adjacencies if available, otherwise use direct adjacencies
    final_adjacencies = all_adjacencies if all_adjacencies else direct_adjacencies

    # Build links from adjacencies
    link_set: set[tuple[str, str, str, str]] = set()
    for adj in final_adjacencies:
        local_h = adj.local_hostname
        remote_h = adj.remote_hostname

        if local_h not in devices or remote_h not in devices:
            continue

        # Get interface names from direct adjacency table
        local_iface = adj.local_interface or iface_map.get((local_h, remote_h), "")
        remote_iface = adj.remote_interface or iface_map.get((remote_h, local_h), "")

        # Build canonical link
        pair = tuple(sorted([
            (local_h, local_iface),
            (remote_h, remote_iface),
        ]))
        link_key = (pair[0][0], pair[0][1], pair[1][0], pair[1][1])
        link_set.add(link_key)

    # Remove devices that have no links (orphan entries from IGP database)
    connected_hosts: set[str] = set()
    for hostA, _, hostB, _ in link_set:
        connected_hosts.add(hostA)
        connected_hosts.add(hostB)
    await emit("progress", f"Connected hosts ({len(connected_hosts)}): {sorted(connected_hosts)}")
    await emit("progress", f"All devices ({len(devices)}): {sorted(devices.keys())}")
    # Always keep seed device; remove orphans
    orphans = [h for h in devices if h not in connected_hosts and h != seed_hostname]
    for h in orphans:
        del devices[h]
        await emit("progress", f"Removed orphan device: {h} (no adjacencies)")
    if not orphans:
        await emit("progress", "No orphan devices found")

    # Build interface lists per device
    for hostA, ifA, hostB, ifB in link_set:
        if ifA and hostA in devices:
            existing = [i["name"] for i in devices[hostA].interfaces]
            if ifA not in existing:
                devices[hostA].interfaces.append({
                    "name": ifA,
                    "speed_gbps": _infer_speed(ifA),
                    "description": f"to {hostB}",
                })
        if ifB and hostB in devices:
            existing = [i["name"] for i in devices[hostB].interfaces]
            if ifB not in existing:
                devices[hostB].interfaces.append({
                    "name": ifB,
                    "speed_gbps": _infer_speed(ifB),
                    "description": f"to {hostA}",
                })

    await emit("progress", f"Discovery complete: {len(devices)} devices, {len(link_set)} links")

    return DiscoveryResult(
        devices=devices,
        links=list(link_set),
    )


# ---------------------------------------------------------------------------
# Parse hostname from show commands
# ---------------------------------------------------------------------------

def _parse_hostname(output: str, os_type: str) -> str:
    """Try to extract hostname from 'show version' or 'show hostname' output."""
    if os_type == "junos":
        m = re.search(r"Hostname:\s*(\S+)", output)
        if m:
            return _clean_hostname(m.group(1))
    elif os_type == "nxos":
        # NX-OS show hostname
        for line in output.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("!"):
                return _clean_hostname(line)
    else:
        # IOS-XE / IOS-XR: look for hostname in show version
        # Try the explicit "hostname uptime" pattern first, but exclude generic words
        m = re.search(r"(?:hostname\s+|^\s*)(\S+)\s+uptime", output, re.MULTILINE | re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            # "System" is not a hostname — it comes from "System uptime is..."
            if candidate.lower() in ("system", "cisco", "software", "ios", "version"):
                m = None
        if not m:
            # IOS-XR: try the prompt line (e.g., "RP/0/RP0/CPU0:PE1#")
            m2 = re.search(r"RP/\d+/\S+:(\S+?)#", output)
            if m2:
                return _clean_hostname(m2.group(1))
        if m:
            return _clean_hostname(m.group(1))
    return ""


# ---------------------------------------------------------------------------
# Per-device discovery
# ---------------------------------------------------------------------------

# Commands per OS type
COMMANDS = {
    "junos": {
        "hostname": "show version | display json",
        "lldp": "show lldp neighbors detail | display json",
        "cdp": None,  # Junos doesn't support CDP
    },
    "iosxe": {
        "hostname": "show version",
        "lldp": "show lldp neighbors detail",
        "cdp": "show cdp neighbors detail",
    },
    "iosxr": {
        "hostname": "show version",
        "lldp": "show lldp neighbors detail",
        "cdp": "show cdp neighbors detail",
    },
    "nxos": {
        "hostname": "show hostname",
        "lldp": "show lldp neighbors detail | json",
        "cdp": "show cdp neighbors detail | json",
    },
}


async def _discover_device(ip: str, os_type: str, username: str, password: str,
                           timeout: int = 30) -> DiscoveredDevice:
    """SSH into a single device, run commands, parse results."""
    cmds = COMMANDS.get(os_type, COMMANDS["iosxe"])

    # Get hostname
    hostname_out = await _ssh_run(ip, username, password, cmds["hostname"], timeout)
    hostname = ""

    if os_type == "junos":
        try:
            jdata = json.loads(hostname_out)
            si = jdata.get("software-information", [{}])
            if isinstance(si, list):
                si = si[0] if si else {}
            host_field = si.get("host-name", [{}])
            if isinstance(host_field, list):
                host_field = host_field[0] if host_field else {}
            if isinstance(host_field, dict):
                hostname = host_field.get("data", "")
            else:
                hostname = str(host_field)
        except json.JSONDecodeError:
            hostname = _parse_hostname(hostname_out, os_type)
    else:
        hostname = _parse_hostname(hostname_out, os_type)

    if not hostname:
        hostname = ip  # fallback to IP as hostname

    hostname = _clean_hostname(hostname)

    # Get LLDP neighbors
    neighbors: list[DiscoveredNeighbor] = []
    try:
        lldp_out = await _ssh_run(ip, username, password, cmds["lldp"], timeout)
        if os_type == "junos":
            neighbors = _parse_junos_lldp(lldp_out)
        elif os_type == "nxos":
            neighbors = _parse_nxos_lldp_json(lldp_out)
        else:
            neighbors = _parse_ios_lldp(lldp_out)
    except Exception:
        pass  # LLDP may not be enabled

    # Fallback to CDP if no LLDP neighbors and CDP is available
    if not neighbors and cmds.get("cdp"):
        try:
            cdp_out = await _ssh_run(ip, username, password, cmds["cdp"], timeout)
            if os_type == "nxos":
                neighbors = _parse_nxos_cdp_json(cdp_out)
            else:
                neighbors = _parse_ios_cdp(cdp_out)
        except Exception:
            pass

    # Build interface list from discovered neighbors
    seen_ifaces: set[str] = set()
    interfaces: list[dict] = []
    for n in neighbors:
        if n.local_interface and n.local_interface not in seen_ifaces:
            seen_ifaces.add(n.local_interface)
            interfaces.append({
                "name": n.local_interface,
                "speed_gbps": _infer_speed(n.local_interface),
                "description": f"to {n.remote_system_name}",
            })

    return DiscoveredDevice(
        hostname=hostname,
        mgmt_ip=ip,
        os_type=os_type,
        vendor=OS_VENDOR.get(os_type, "Unknown"),
        interfaces=interfaces,
        neighbors=neighbors,
    )


# ---------------------------------------------------------------------------
# OS type inference for recursion
# ---------------------------------------------------------------------------

_OS_HINTS = {
    "junos": ["mx", "ptx", "qfx", "srx", "ex-", "junos", "juniper"],
    "nxos": ["nxos", "nexus", "n9k", "n7k", "n5k", "n3k"],
    "iosxr": ["iosxr", "xr", "asr9", "ncs", "xrd"],
    "iosxe": ["iosxe", "csr", "cat", "isr", "asr1"],
}


def _infer_os_type(hostname: str, default_os: str) -> str:
    """Guess OS type from hostname patterns."""
    lower = hostname.lower()
    for os_type, hints in _OS_HINTS.items():
        if any(h in lower for h in hints):
            return os_type
    return default_os


# ---------------------------------------------------------------------------
# Main recursive discovery
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str, str, dict | None], Awaitable[None]]


async def discover_topology(
    seed_ip: str,
    seed_os: str,
    username: str,
    password: str,
    max_depth: int = 5,
    max_devices: int = 50,
    timeout_per_device: int = 30,
    on_progress: ProgressCallback | None = None,
) -> DiscoveryResult:
    """
    BFS crawl from seed device, discovering neighbors via LLDP/CDP.

    on_progress(event_type, message, data) is called for SSE streaming.
    """

    async def emit(event_type: str, message: str, data: dict | None = None):
        if on_progress:
            await on_progress(event_type, message, data)

    visited_ips: set[str] = set()
    hostname_map: dict[str, DiscoveredDevice] = {}
    link_set: set[tuple[str, str, str, str]] = set()

    # BFS queue: (ip, os_type, depth)
    queue: list[tuple[str, str, int]] = [(seed_ip, seed_os, 0)]

    while queue:
        ip, os_type, depth = queue.pop(0)

        if ip in visited_ips:
            continue
        if depth > max_depth:
            await emit("progress", f"Skipping {ip} — max depth ({max_depth}) reached")
            continue
        if len(visited_ips) >= max_devices:
            await emit("progress", f"Max device limit ({max_devices}) reached, stopping")
            break

        visited_ips.add(ip)
        await emit("progress", f"Connecting to {ip} (depth {depth})...")

        try:
            device = await _discover_device(ip, os_type, username, password, timeout_per_device)
        except asyncio.TimeoutError:
            await emit("error", f"Timeout connecting to {ip}")
            continue
        except asyncssh.PermissionDenied:
            await emit("error", f"Authentication failed for {ip}")
            continue
        except asyncssh.DisconnectError as e:
            await emit("error", f"SSH disconnected from {ip}: {e}")
            continue
        except OSError as e:
            await emit("error", f"Cannot reach {ip}: {e}")
            continue
        except Exception as e:
            await emit("error", f"Failed to discover {ip}: {type(e).__name__}: {e}")
            continue

        # Avoid hostname collisions — append IP suffix if needed
        base_hostname = device.hostname
        if base_hostname in hostname_map:
            device.hostname = f"{base_hostname}_{ip.replace('.', '_')}"

        hostname_map[device.hostname] = device
        await emit("device", f"Discovered {device.hostname}", {
            "hostname": device.hostname,
            "ip": ip,
            "os_type": os_type,
            "vendor": device.vendor,
            "neighbor_count": len(device.neighbors),
            "interface_count": len(device.interfaces),
        })

        # Process neighbors
        for nbr in device.neighbors:
            # Build canonical link (sorted to deduplicate A->B and B->A)
            pair = tuple(sorted([
                (device.hostname, nbr.local_interface),
                (nbr.remote_system_name, nbr.remote_interface),
            ]))
            link_key = (pair[0][0], pair[0][1], pair[1][0], pair[1][1])
            link_set.add(link_key)

            # Queue neighbor for crawling
            if nbr.remote_mgmt_ip and nbr.remote_mgmt_ip not in visited_ips:
                nbr_os = _infer_os_type(nbr.remote_system_name, seed_os)
                queue.append((nbr.remote_mgmt_ip, nbr_os, depth + 1))

    return DiscoveryResult(
        devices=hostname_map,
        links=list(link_set),
    )

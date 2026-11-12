"""Lab management network.

Two modes:
  * internal (default) — a Docker bridge network private to the host
    (`plabs-mgmt`, 172.30.0.0/22). Devices are reachable from the server only.
  * bridged — the mgmt network is a Linux bridge uplinked to a physical NIC
    (or one VLAN on a trunk NIC). Every device gets a static IP from a pool on
    that real network, so it's reachable from anywhere that network routes.

In bridged mode we create the bridge and a Docker network bound to it with
`inhibit_ipv4` (Docker must not put the gateway address on the host bridge —
the gateway is the real upstream router). containerlab then reuses that
network by name.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.services import host_net
from app.services.host_net import HostNetError, Segment

KEY = "lab_network"
INTERNAL_NETWORK = os.environ.get("PLABS_LAB_MGMT_NETWORK", "plabs-mgmt").strip() or "plabs-mgmt"
BRIDGED_NETWORK = "plabs-mgmt-bridged"
DEFAULT_INTERNAL_SUBNET = os.environ.get("PLABS_LAB_MGMT_SUBNET", "").strip() or "172.30.0.0/22"


@dataclass
class MgmtSettings:
    mode: str = "internal"         # internal | bridged
    nic: str = ""
    vlan: int = 0                  # 0 = untagged (access)
    subnet: str = DEFAULT_INTERNAL_SUBNET
    gateway: str = ""              # upstream router on the mgmt segment
    host_ip: str = ""              # optional address for the server on the bridge (CIDR host)
    pool_start: str = ""
    pool_end: str = ""
    mtu: int = 0

    @property
    def bridged(self) -> bool:
        return self.mode == "bridged"

    @property
    def segment(self) -> Segment:
        return Segment(mode="trunk" if self.vlan else "access", nic=self.nic, vlan=self.vlan)

    @property
    def bridge(self) -> str:
        return host_net.segment_bridge_name(self.nic, self.vlan or None)

    @property
    def docker_network(self) -> str:
        return BRIDGED_NETWORK if self.bridged else INTERNAL_NETWORK

    def effective_gateway(self) -> str:
        if self.gateway:
            return self.gateway
        return str(next(ipaddress.ip_network(self.subnet, strict=False).hosts()))


def get_settings(db: Session) -> MgmtSettings:
    row = db.get(AppSetting, KEY)
    if not row:
        return MgmtSettings()
    try:
        data = json.loads(row.value_json or "{}")
    except json.JSONDecodeError:
        data = {}
    fields = MgmtSettings.__dataclass_fields__
    return MgmtSettings(**{k: v for k, v in data.items() if k in fields})


def validate(s: MgmtSettings, nics: list[dict] | None = None) -> MgmtSettings:
    if s.mode not in ("internal", "bridged"):
        raise HostNetError("mode must be 'internal' or 'bridged'")
    try:
        net = ipaddress.ip_network(s.subnet, strict=False)
    except ValueError:
        raise HostNetError(f"Invalid subnet '{s.subnet}'")
    if net.version != 4 or net.prefixlen > 29:
        raise HostNetError("Management subnet must be IPv4, /29 or larger")
    s.subnet = str(net)
    if not s.bridged:
        return s
    if not s.nic:
        raise HostNetError("Choose the NIC the management network uses")
    seg = s.segment
    if seg.mode == "trunk" and not 1 <= s.vlan <= 4094:
        raise HostNetError("VLAN ID must be between 1 and 4094")
    host_net.validate_segment(seg, nics)

    def addr(label: str, v: str, required: bool) -> ipaddress.IPv4Address | None:
        if not v:
            if required:
                raise HostNetError(f"{label} is required")
            return None
        try:
            a = ipaddress.ip_address(v.split("/")[0])
        except ValueError:
            raise HostNetError(f"{label} '{v}' is not an IP address")
        if a not in net or a in (net.network_address, net.broadcast_address):
            raise HostNetError(f"{label} {a} is not a usable address in {net}")
        return a

    gw = addr("Gateway", s.gateway, True)
    host = addr("Server address", s.host_ip, False)
    start = addr("Pool start", s.pool_start, True)
    end = addr("Pool end", s.pool_end, True)
    if int(end) < int(start):
        raise HostNetError("Pool end is before pool start")
    for label, a in (("Gateway", gw), ("Server address", host)):
        if a and int(start) <= int(a) <= int(end):
            raise HostNetError(f"{label} {a} is inside the device pool — move it out of the pool")
    if s.host_ip:
        s.host_ip = f"{host}/{net.prefixlen}"
    return s


def save_settings(db: Session, s: MgmtSettings) -> MgmtSettings:
    row = db.get(AppSetting, KEY) or AppSetting(key=KEY)
    row.value_json = json.dumps(asdict(s))
    db.merge(row)
    db.commit()
    return s


# --- IPAM --------------------------------------------------------------------

def pool(s: MgmtSettings) -> list[str]:
    a, b = int(ipaddress.ip_address(s.pool_start)), int(ipaddress.ip_address(s.pool_end))
    return [str(ipaddress.ip_address(i)) for i in range(a, b + 1)]


def assign_ips(db: Session, s: MgmtSettings, node_ids: list[int]) -> dict[int, str]:
    """Give each listed device a static mgmt IP from the pool (bridged mode).
    Addresses are unique across every topology and sticky: a device keeps its
    IP across deploys / destroys until it's deleted or the pool changes."""
    from app.models.node_instance import NodeInstance
    from app.services.device_kinds import is_bridge
    if not s.bridged:
        return {}
    candidates = pool(s)
    allowed = set(candidates)
    all_nodes = db.query(NodeInstance).filter(NodeInstance.mgmt_ipv4 != "").all()
    taken = {n.mgmt_ipv4: n.id for n in all_nodes}
    out: dict[int, str] = {}
    for nid in node_ids:
        n = db.get(NodeInstance, nid)
        if not n or is_bridge(n.kind):
            continue
        if n.mgmt_ipv4 in allowed and taken.get(n.mgmt_ipv4) == n.id:
            out[n.id] = n.mgmt_ipv4
            continue
        free = next((ip for ip in candidates if ip not in taken), None)
        if not free:
            raise HostNetError(f"The management IP pool ({s.pool_start}–{s.pool_end}) is exhausted")
        if n.mgmt_ipv4 in taken and taken[n.mgmt_ipv4] == n.id:
            del taken[n.mgmt_ipv4]
        n.mgmt_ipv4 = free
        taken[free] = n.id
        out[n.id] = free
    db.flush()
    return out


# --- host plumbing -----------------------------------------------------------

def _docker_network_subnet(name: str) -> str | None:
    rc, out, _ = host_net._run(["docker", "network", "inspect", name, "--format",
                                "{{range .IPAM.Config}}{{.Subnet}} {{end}}"])
    return out.split()[0] if rc == 0 and out.strip() else (None if rc != 0 else "")


def ensure_mgmt(s: MgmtSettings) -> None:
    """Create the bridged mgmt network on the host (no-op in internal mode —
    containerlab creates its own Docker network there)."""
    if not s.bridged:
        return
    host_net.ensure_bridge(s.bridge, s.segment, s.mtu or None)
    if s.host_ip:
        host_net.ensure_host_address(s.bridge, s.host_ip)
    existing = _docker_network_subnet(BRIDGED_NETWORK)
    if existing is not None and existing != s.subnet:
        raise HostNetError(
            f"Docker network {BRIDGED_NETWORK} exists with subnet {existing}; destroy all labs, then save the settings again")
    if existing is None:
        host_net._must([
            "docker", "network", "create", "-d", "bridge",
            "--subnet", s.subnet, "--gateway", s.effective_gateway(),
            "-o", f"com.docker.network.bridge.name={s.bridge}",
            "-o", "com.docker.network.bridge.inhibit_ipv4=true",
            "-o", "com.docker.network.bridge.enable_ip_masquerade=false",
            "--label", "plabs=mgmt",
            BRIDGED_NETWORK,
        ])


def teardown_mgmt(s: MgmtSettings) -> None:
    """Remove the bridged mgmt network (used when settings change)."""
    host_net._run(["docker", "network", "rm", BRIDGED_NETWORK])
    if s.bridged and s.nic:
        host_net.delete_bridge(s.bridge, s.segment)


def status(s: MgmtSettings) -> dict:
    if not s.bridged:
        return {"ok": True, "detail": "Internal Docker network — devices are reachable from this server only."}
    exists = host_net.link_exists(s.bridge)
    up = exists and host_net.carrier(s.nic)
    return {
        "ok": exists and up,
        "bridge": s.bridge, "bridge_exists": exists, "nic_carrier": host_net.carrier(s.nic),
        "docker_network": _docker_network_subnet(BRIDGED_NETWORK) is not None,
        "detail": ("Bridge up" if up else
                   f"{s.nic} has no link — check the cable / switch port" if exists else
                   "Bridge not created yet (it's created on the next deploy or when you save)"),
    }

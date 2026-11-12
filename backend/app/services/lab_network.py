"""Lab management network.

  * internal (default) — a Docker bridge network private to the host
    (`plabs-mgmt`, 172.30.0.0/22). Devices are reachable from the server only.
  * bridged — the mgmt network is a Linux bridge uplinked to a physical NIC
    (or one VLAN on a trunk NIC). Addressing comes from that network's own
    DHCP server: the server leases an address for itself (so it can reach
    devices), which also tells us the subnet / gateway / DNS, and every device
    gets its own lease, held and renewed by the backend and handed to
    containerlab as the device's static mgmt IP. Nothing is configured but the
    NIC and VLAN, so the same rule works on any network that runs DHCP.

Docker attaches to the bridge with `inhibit_ipv4` (it must not put the
gateway address on the host bridge). Every lab device gets an explicit,
DHCP-leased `mgmt-ipv4`; nothing else may join these Docker networks without
one, or Docker's own IPAM could pick an address the DHCP server owns.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.mgmt_lease import MgmtLease
from app.services import dhcp, host_net
from app.services.host_net import HostNetError, Segment

log = logging.getLogger(__name__)

KEY = "lab_network"
INTERNAL_NETWORK = os.environ.get("PLABS_LAB_MGMT_NETWORK", "plabs-mgmt").strip() or "plabs-mgmt"
DEFAULT_INTERNAL_SUBNET = os.environ.get("PLABS_LAB_MGMT_SUBNET", "").strip() or "172.30.0.0/22"
SERVER_HOSTNAME = "plabs-server"

_lock = threading.RLock()   # serializes DHCP + host plumbing for mgmt networks


@dataclass
class MgmtSettings:
    """Host-wide defaults. A lab without a management Network node uses the
    internal Docker network with this subnet."""
    subnet: str = DEFAULT_INTERNAL_SUBNET
    mtu: int = 0


@dataclass
class Learned:
    subnet: str
    gateway: str
    dns: list[str]
    server_ip: str


@dataclass
class LabMgmt:
    """Resolved management network for one lab."""
    bridged: bool
    docker_network: str
    subnet: str
    gateway: str
    segment: Segment | None = None
    bridge: str = ""
    node_id: int | None = None


def docker_network_for(bridge: str) -> str:
    return f"plabs-m-{bridge}"


def get_settings(db: Session) -> MgmtSettings:
    row = db.get(AppSetting, KEY)
    try:
        data = json.loads(row.value_json) if row else {}
    except json.JSONDecodeError:
        data = {}
    fields = MgmtSettings.__dataclass_fields__
    return MgmtSettings(**{k: v for k, v in data.items() if k in fields})


def validate(s: MgmtSettings) -> MgmtSettings:
    try:
        net = ipaddress.ip_network(s.subnet, strict=False)
    except ValueError:
        raise HostNetError(f"Invalid subnet '{s.subnet}'")
    if net.version != 4 or net.prefixlen > 29:
        raise HostNetError("Subnet must be IPv4, /29 or larger")
    s.subnet = str(net)
    return s


def save_settings(db: Session, s: MgmtSettings) -> MgmtSettings:
    row = db.get(AppSetting, KEY) or AppSetting(key=KEY)
    row.value_json = json.dumps(asdict(s))
    db.merge(row)
    db.commit()
    return s


def mgmt_node(db: Session, topology_id: int):
    """The lab's management Network node (network.mgmt = true), if any."""
    from app.models.node_instance import NodeInstance
    from app.services.device_kinds import BRIDGE_KIND
    for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id,
                                           NodeInstance.kind == BRIDGE_KIND).order_by(NodeInstance.id):
        net = n.network
        if net.get("mgmt") and net.get("mode") in ("access", "trunk") and net.get("nic"):
            return n
    return None


def lab_mgmt(db: Session, topology_id: int) -> LabMgmt:
    n = mgmt_node(db, topology_id)
    if n is None:
        s = get_settings(db)
        net = ipaddress.ip_network(s.subnet, strict=False)
        return LabMgmt(bridged=False, docker_network=INTERNAL_NETWORK, subnet=str(net),
                       gateway=str(next(net.hosts())))
    seg = Segment.from_dict(n.network)
    bridge = seg.bridge(topology_id, n.id)
    lr = learned(db, bridge)
    return LabMgmt(bridged=True, docker_network=docker_network_for(bridge),
                   subnet=lr.subnet if lr else "", gateway=lr.gateway if lr else "",
                   segment=seg, bridge=bridge, node_id=n.id)


# --- leases ------------------------------------------------------------------

def _to_lease(row: MgmtLease) -> dhcp.Lease:
    return dhcp.Lease(ip=row.ip, prefix=row.prefix, router=row.router, dns=json.loads(row.dns_json or "[]"),
                      server_id=row.server_id, lease_time=row.lease_time, obtained_at=row.obtained_at)


def _store(db: Session, row: MgmtLease | None, *, node_id: int | None, client_id: str, bridge: str,
           lease: dhcp.Lease) -> MgmtLease:
    row = row or MgmtLease(node_id=node_id, client_id=client_id)
    row.bridge, row.ip, row.prefix, row.router = bridge, lease.ip, lease.prefix, lease.router
    row.dns_json, row.server_id = json.dumps(lease.dns), lease.server_id
    row.lease_time, row.obtained_at = lease.lease_time, lease.obtained_at
    db.add(row)
    db.flush()
    return row


def _hold(db: Session, bridge: str, label: str, *, node_id: int | None, hostname: str,
          prefer_ip: str = "") -> MgmtLease:
    """Return a valid lease for this owner on this bridge, renewing or
    acquiring as needed. The server holds one lease per mgmt bridge; a device
    holds one lease (on its lab's mgmt bridge)."""
    client_id = dhcp.client_id_for("node", str(node_id)) if node_id is not None \
        else dhcp.client_id_for("server", bridge)
    row = (db.query(MgmtLease).filter(MgmtLease.client_id == client_id).first())
    now = time.time()
    if row and row.bridge == bridge and now < row.obtained_at + row.lease_time / 2:
        return row
    try:
        if row and row.bridge == bridge and now < row.obtained_at + row.lease_time:
            fresh = dhcp.renew(bridge, client_id, _to_lease(row), hostname)
            if fresh:
                return _store(db, row, node_id=node_id, client_id=client_id, bridge=bridge, lease=fresh)
        want = prefer_ip or (row.ip if row and row.bridge == bridge else "")
        lease = dhcp.acquire(bridge, client_id, hostname, requested_ip=want)
    except (dhcp.DhcpError, OSError) as e:
        raise HostNetError(f"DHCP on {label} failed: {e}")
    return _store(db, row, node_id=node_id, client_id=client_id, bridge=bridge, lease=lease)


def learned(db: Session, bridge: str) -> Learned | None:
    """Subnet / gateway / DNS of a mgmt bridge, from the server's own lease."""
    row = db.query(MgmtLease).filter(MgmtLease.node_id.is_(None), MgmtLease.bridge == bridge).first()
    if not row:
        return None
    l = _to_lease(row)
    return Learned(subnet=l.subnet, gateway=l.router, dns=l.dns, server_ip=f"{l.ip}/{l.prefix}")


def assign_ips(db: Session, m: LabMgmt, node_ids: list[int]) -> dict[int, str]:
    """Hold a DHCP lease for every listed device on the lab's mgmt bridge.
    Devices ask for their previous address, so IPs are stable across redeploys."""
    from app.models.node_instance import NodeInstance
    from app.services.device_kinds import is_bridge
    if not m.bridged:
        return {}
    out: dict[int, str] = {}
    with _lock:
        for nid in node_ids:
            n = db.get(NodeInstance, nid)
            if not n or is_bridge(n.kind):
                continue
            row = _hold(db, m.bridge, m.segment.describe(), node_id=n.id, hostname=_hostname(n.name),
                        prefer_ip=n.mgmt_ipv4)
            n.mgmt_ipv4 = row.ip
            out[n.id] = row.ip
        db.flush()
    return out


def _hostname(name: str) -> str:
    return "".join(c if c.isalnum() or c == "-" else "-" for c in name)[:63] or "device"


def release_node(db: Session, node_id: int) -> None:
    row = db.query(MgmtLease).filter(MgmtLease.node_id == node_id).first()
    if not row:
        return
    try:
        if host_net.link_exists(row.bridge):
            dhcp.release(row.bridge, row.client_id, _to_lease(row))
    except OSError:
        pass
    db.delete(row)


def release_bridge(db: Session, bridge: str) -> None:
    """Give back every lease held on a mgmt bridge we're tearing down."""
    from app.models.node_instance import NodeInstance
    for row in db.query(MgmtLease).filter(MgmtLease.bridge == bridge).all():
        try:
            if host_net.link_exists(bridge):
                dhcp.release(bridge, row.client_id, _to_lease(row))
        except OSError:
            pass
        if row.node_id is not None:
            n = db.get(NodeInstance, row.node_id)
            if n and n.mgmt_ipv4 == row.ip:
                n.mgmt_ipv4 = ""
        db.delete(row)
    db.commit()


# --- host plumbing -----------------------------------------------------------

def _docker_network_subnet(name: str) -> str | None:
    """None = network doesn't exist."""
    rc, out, _ = host_net._run(["docker", "network", "inspect", name, "--format",
                                "{{range .IPAM.Config}}{{.Subnet}} {{end}}"])
    if rc != 0:
        return None
    return out.split()[0] if out.strip() else ""


def ensure_mgmt(db: Session, m: LabMgmt, mtu: int | None = None) -> Learned | None:
    """For a bridged lab mgmt network: the bridge, the server's own lease on it
    (so the server can reach devices; also teaches us subnet/gateway/DNS) and
    the Docker network containerlab attaches devices to."""
    if not m.bridged:
        return None
    with _lock:
        host_net.ensure_bridge(m.bridge, m.segment, mtu)
        row = _hold(db, m.bridge, m.segment.describe(), node_id=None, hostname=SERVER_HOSTNAME)
        db.commit()
        host_net.ensure_host_address(m.bridge, f"{row.ip}/{row.prefix}")
        lr = learned(db, m.bridge)
        name = docker_network_for(m.bridge)
        existing = _docker_network_subnet(name)
        if existing is not None and existing != lr.subnet:
            host_net._run(["docker", "network", "rm", name])
            if _docker_network_subnet(name) is not None:
                raise HostNetError(f"Docker network {name} is in use with subnet {existing}; "
                                   "destroy the labs using it and try again")
            existing = None
        if existing is None:
            if not lr.gateway:
                raise HostNetError("The DHCP server didn't provide a gateway (router option)")
            host_net._must([
                "docker", "network", "create", "-d", "bridge",
                "--subnet", lr.subnet, "--gateway", lr.gateway,
                "-o", f"com.docker.network.bridge.name={m.bridge}",
                "-o", "com.docker.network.bridge.inhibit_ipv4=true",
                "-o", "com.docker.network.bridge.enable_ip_masquerade=false",
                "--label", "plabs=mgmt", "--label", f"plabs.bridge={m.bridge}",
                name,
            ])
        m.subnet, m.gateway = lr.subnet, lr.gateway
        return lr


def mgmt_docker_networks() -> dict[str, str]:
    """{docker network name: bridge} for every bridged mgmt network we created."""
    rc, out, _ = host_net._run(["docker", "network", "ls", "--filter", "label=plabs=mgmt",
                                "--format", '{{.Name}}\t{{.Label "plabs.bridge"}}'])
    if rc != 0:
        return {}
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


def teardown_unused(db: Session, keep_bridges: set[str]) -> None:
    """Release leases and remove the Docker network of mgmt bridges no
    deployed lab uses (the bridge itself is removed by host_net.gc_bridges)."""
    for name, bridge in mgmt_docker_networks().items():
        if bridge in keep_bridges:
            continue
        release_bridge(db, bridge)
        host_net._run(["docker", "network", "rm", name])
    for row in db.query(MgmtLease).filter(MgmtLease.bridge.notin_(keep_bridges)).all():
        db.delete(row)   # leftovers from a bridge already gone
    db.commit()


def status(db: Session) -> dict:
    s = get_settings(db)
    nets = []
    for name, bridge in mgmt_docker_networks().items():
        lr = learned(db, bridge)
        nets.append({"docker_network": name, "bridge": bridge, "learned": asdict(lr) if lr else None,
                     "leases": db.query(MgmtLease).filter(MgmtLease.bridge == bridge, MgmtLease.node_id.isnot(None)).count()})
    return {"internal_subnet": s.subnet, "bridged": nets}


# --- renewal -----------------------------------------------------------------

def renew_due(db: Session) -> None:
    """Renew server leases and the leases of devices in deployed labs once
    they pass T1. Leases of devices that aren't deployed are left to expire;
    the device asks for its old address again on its next deploy."""
    from app.models.lab_deployment import LabDeployment
    from app.models.node_instance import NodeInstance
    deployed = {d.topology_id for d in db.query(LabDeployment).filter(
        LabDeployment.status.in_(("deploying", "running", "syncing", "failed")))}
    now = time.time()
    with _lock:
        for row in db.query(MgmtLease).all():
            if now < row.obtained_at + row.lease_time / 2 or not host_net.link_exists(row.bridge):
                continue
            if row.node_id is not None:
                n = db.get(NodeInstance, row.node_id)
                if not n or n.topology_id not in deployed:
                    continue
            try:
                fresh = dhcp.renew(row.bridge, row.client_id, _to_lease(row))
                if fresh and fresh.ip == row.ip:
                    _store(db, row, node_id=row.node_id, client_id=row.client_id, bridge=row.bridge, lease=fresh)
                else:
                    log.warning("DHCP server refused renewing %s (client %s)", row.ip, row.client_id)
            except (dhcp.DhcpError, OSError) as e:
                log.warning("lease renewal for %s failed: %s", row.ip, e)
        db.commit()


def start_renewer() -> None:
    def loop():
        from app.models.database import SessionLocal
        while True:
            time.sleep(30)
            db = SessionLocal()
            try:
                renew_due(db)
            except Exception as e:  # never let the thread die
                log.warning("lease renewer: %s", e)
            finally:
                db.close()
    threading.Thread(target=loop, name="plabs-dhcp-renew", daemon=True).start()

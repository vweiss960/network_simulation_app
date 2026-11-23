"""Host networking for lab Network nodes and the bridged management network.

Every Network node (and the bridged management network) is a Linux bridge on
the lab host:

  * internal  — bridge only; a switch between lab devices
  * access    — the whole physical NIC is enslaved (untagged)
  * trunk     — an 802.1Q sub-interface `<nic>.<vlan>` is enslaved

Bridge names are deterministic from (nic, vlan), so every lab that points at
the same NIC/VLAN shares one bridge — the same physical segment. The backend
runs as root (systemd or privileged container) and drives `ip` / `iptables`
directly against the host network namespace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SYS_NET = "/sys/class/net"
_IFNAME_MAX = 15


class HostNetError(ValueError):
    pass


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out: {' '.join(cmd)}"


def _must(cmd: list[str]) -> str:
    rc, out, err = _run(cmd)
    if rc != 0:
        raise HostNetError(f"{' '.join(cmd)} failed: {(err or out).strip()}")
    return out


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


# --- naming ------------------------------------------------------------------

def _b36(n: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = chars[r] + out
        if n == 0:
            return out


def _fit(name: str, key: str) -> str:
    """Keep names within the kernel's 15-char limit and clab's [A-Za-z0-9_-]."""
    name = re.sub(r"[^A-Za-z0-9_-]", "-", name)
    if len(name) <= _IFNAME_MAX:
        return name
    return "pl-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def segment_bridge_name(nic: str, vlan: int | None) -> str:
    """Bridge for a physical segment. Shared by every lab using it."""
    if vlan:
        return _fit(f"pl-{nic}-{vlan}", f"{nic}.{vlan}")
    return _fit(f"pl-{nic}", nic)


def internal_bridge_name(topology_id: int, node_id: int) -> str:
    return f"pl-sw{_b36(topology_id)}-{_b36(node_id)}"


def bridge_port_name(topology_id: int, node_id: int, port_index: int) -> str:
    """Host-side veth name for a device cabled to a Network node. Lives in the
    host namespace, so it must be unique host-wide (≤ 15 chars)."""
    return f"pl{_b36(topology_id)}n{_b36(node_id)}p{_b36(port_index)}"


def port_name(topology_id: int, node_id: int, index: int) -> str:
    """Host-side half of a device port (every port is pre-provisioned at
    boot; cabling happens here on the host). Host-unique, <= 15 chars."""
    return f"pp{_b36(topology_id)}n{_b36(node_id)}p{_b36(index)}"


_REDIRECT = re.compile(r"Redirect to device (\S+?)\)")


def _redirect_target(dev: str) -> str:
    rc, out, _ = _run(["tc", "filter", "show", "dev", dev, "ingress"])
    m = _REDIRECT.search(out) if rc == 0 else None
    return m.group(1) if m else ""


def _master(dev: str) -> str:
    p = Path("/sys/class/net") / dev / "master"
    return p.resolve().name if p.exists() else ""


def apply_patches(ports: list[str], patches: list[dict]) -> list[str]:
    """Make the host patch panel match the canvas. `ports` are every device
    port half of a lab; `patches` are {"a": half, "b": half} (device to
    device: tc mirred both ways, every frame incl. LACP/LLDP/STP) or
    {"a": half, "bridge": name} (device to a Network node's bridge). Every
    other port is unplugged: no redirect, no bridge, link down. Idempotent
    and incremental: correct ports are left alone, so traffic on other
    cables never blips. Returns problems (missing halves / failed tc)."""
    want: dict[str, tuple[str, str]] = {}
    for p in patches:
        if p.get("bridge"):
            want[p["a"]] = ("bridge", p["bridge"])
        else:
            want[p["a"]] = ("peer", p["b"])
            want[p["b"]] = ("peer", p["a"])
    problems = []
    for dev in ports:
        if not link_exists(dev):
            problems.append(f"{dev}: missing")
            continue
        mode, target = want.get(dev, ("", ""))
        peer, master = _redirect_target(dev), _master(dev)
        if mode == "peer" and not link_exists(target):
            problems.append(f"{dev}: peer {target} missing")
            mode = ""
        if mode != "peer" and peer:
            _run(["tc", "filter", "del", "dev", dev, "ingress"])
        if mode != "bridge" and master:
            _run(["ip", "link", "set", dev, "nomaster"])
        if mode == "":
            _run(["ip", "link", "set", dev, "down"])
            continue
        if mode == "bridge" and master != target:
            rc, _o, e = _run(["ip", "link", "set", dev, "master", target])
            if rc != 0:
                problems.append(f"{dev} -> {target}: {e.strip()[:120]}")
        if mode == "peer" and peer != target:
            _run(["tc", "qdisc", "replace", "dev", dev, "clsact"])
            _run(["tc", "filter", "del", "dev", dev, "ingress"])
            rc, _o, e = _run(["tc", "filter", "add", "dev", dev, "ingress", "pref", "1", "handle", "1",
                              "matchall", "action", "mirred", "egress", "redirect", "dev", target])
            if rc != 0:
                problems.append(f"{dev} -> {target}: {e.strip()[:120]}")
        _run(["ip", "link", "set", dev, "up"])
    return problems


# --- parked ports of stopped devices ---------------------------------------------
#
# `containerlab stop` parks a device's data-plane interfaces in a network
# namespace, /run/netns/clab-park-<container>, each still a veth to its host
# half (host:pp…). Those namespaces live in memory: a server reboot loses them,
# and with the containerized backend a restart did too. `containerlab start`
# then fails, and the stopped device has no ports. rebuild_parked recreates
# them as they were, without starting the device.

PARK_PREFIX = "clab-park-"
NETNS_DIR = "/run/netns"


def clab_owner_altname(node: str, ifname: str) -> str:
    """containerlab's ownership marker on a link end (links/link.go
    ownershipAltNameFor): its reconcile refuses interfaces without it."""
    return "clab-o-" + hashlib.sha1(f"{node}\0{ifname}".encode()).hexdigest()[:16]


def _add_altname(ifname: str, alt: str, ns: str | None = None) -> None:
    pre = ["ip", "-n", ns] if ns else ["ip"]
    rc, out, _ = _run(pre + ["-d", "link", "show", ifname])
    if rc == 0 and alt not in out:
        _run(pre + ["link", "property", "add", "dev", ifname, "altname", alt])


def park_ns(container: str) -> str:
    return f"{PARK_PREFIX}{container}"


def netns_alive(name: str) -> bool:
    """A usable named netns (not a stale file left after its mount went away)."""
    if not os.path.exists(f"{NETNS_DIR}/{name}"):
        return False
    rc, _o, _e = _run(["ip", "netns", "exec", name, "true"], timeout=10)
    return rc == 0


def rebuild_parked(container: str, node: str, ports: list[tuple[str, str]], mtu: int = 9500) -> str:
    """Recreate a stopped device's parked namespace with its ports
    [(device interface, host half)]. Returns "ok", "alive" (nothing to do)
    or an error."""
    ns = park_ns(container)
    if netns_alive(ns):
        return "alive"
    stale = f"{NETNS_DIR}/{ns}"
    if os.path.exists(stale):
        try:
            os.unlink(stale)                   # a plain file whose namespace is gone
        except OSError as e:
            return f"can't remove stale {stale}: {e}"
    rc, _o, e = _run(["ip", "netns", "add", ns])
    if rc != 0:
        return f"ip netns add {ns}: {e.strip()}"
    errs = []
    for dev_if, half in ports:
        if link_exists(half):
            _run(["ip", "link", "del", half])  # its peer went with the namespace
        rc, _o, e = _run(["ip", "link", "add", half, "mtu", str(mtu), "type", "veth",
                          "peer", "name", dev_if, "mtu", str(mtu), "netns", ns])
        if rc != 0:
            errs.append(f"{dev_if}/{half}: {e.strip()[:120]}")
            continue
        _add_altname(half, clab_owner_altname("host", half))
        _add_altname(dev_if, clab_owner_altname(node, dev_if), ns)
        _run(["ip", "link", "set", half, "up"])
    return "ok" if not errs else "; ".join(errs)


def mark_owned(netns: str, node: str, ports: list[tuple[str, str]]) -> int:
    """Give a running device's ports (in its netns) and their host halves
    containerlab's ownership marker if missing. Returns how many were added."""
    n = 0
    for dev_if, half in ports:
        for ifname, owner, ns in ((dev_if, node, netns), (half, "host", None)):
            alt = clab_owner_altname(owner, ifname)
            pre = ["ip", "-n", ns] if ns else ["ip"]
            rc, out, _ = _run(pre + ["-d", "link", "show", ifname])
            if rc == 0 and alt not in out:
                _run(pre + ["link", "property", "add", "dev", ifname, "altname", alt])
                n += 1
    return n


def vlan_ifname(nic: str, vlan: int) -> str:
    name = f"{nic}.{vlan}"
    if len(name) > _IFNAME_MAX:
        name = f"pv{hashlib.sha1(name.encode()).hexdigest()[:6]}.{vlan}"
    return name


# --- discovery ---------------------------------------------------------------

def _primary_uplink() -> str | None:
    """The interface holding the lowest-metric IPv4 default route — the
    server's own connection, which a lab must never take over untagged."""
    rc, out, _ = _run(["ip", "-j", "route", "show", "default"])
    if rc != 0:
        return None
    try:
        routes = [r for r in json.loads(out or "[]") if r.get("dev")]
    except json.JSONDecodeError:
        return None
    if not routes:
        return None
    return min(routes, key=lambda r: r.get("metric", 0))["dev"]


NM_DROPIN = "/etc/NetworkManager/conf.d/90-plabs-unmanaged.conf"


def _nm_states() -> dict[str, str]:
    """NetworkManager device states (empty when NM isn't installed/reachable)."""
    rc, out, _ = _run(["nmcli", "-t", "-f", "DEVICE,STATE", "dev"])
    if rc != 0:
        return {}
    return dict(line.split(":", 1) for line in out.splitlines() if ":" in line)


def release_from_host(nic: str) -> None:
    """Stop the host's own network stack using a NIC we're about to bridge:
    mark it unmanaged in NetworkManager (runtime + persistent drop-in) and
    drop any address/route the host had on it (e.g. an auto-DHCP lease)."""
    if nic == _primary_uplink():
        raise HostNetError(f"{nic} is this server's primary uplink — refusing to take it over")
    states = _nm_states()
    if states.get(nic) and states[nic] != "unmanaged":
        names = set()
        try:
            for line in open(NM_DROPIN):
                if line.startswith("unmanaged-devices="):
                    names |= {x.split(":", 1)[1] for x in line.split("=", 1)[1].strip().split(";") if ":" in x}
        except OSError:
            pass
        names.add(nic)
        try:
            os.makedirs(os.path.dirname(NM_DROPIN), exist_ok=True)
            with open(NM_DROPIN, "w") as f:
                f.write("# Written by Phanero Labs: NICs used for lab networks.\n[keyfile]\n"
                        "unmanaged-devices=" + ";".join(f"interface-name:{n}" for n in sorted(names)) + "\n")
            _run(["nmcli", "general", "reload", "conf"])
        except OSError:
            pass  # read-only /etc (container) — the runtime switch below still applies
        rc, _, err = _run(["nmcli", "dev", "set", nic, "managed", "no"])
        if rc != 0:
            raise HostNetError(f"Couldn't release {nic} from NetworkManager: {err.strip()} "
                               f"(run: sudo nmcli dev set {nic} managed no)")
    if _addrs().get(nic):
        _run(["ip", "addr", "flush", "dev", nic])


def _addrs() -> dict[str, list[str]]:
    rc, out, _ = _run(["ip", "-j", "-4", "addr", "show"])
    res: dict[str, list[str]] = {}
    if rc == 0:
        try:
            for i in json.loads(out or "[]"):
                res[i["ifname"]] = [f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])]
        except (json.JSONDecodeError, KeyError):
            pass
    return res


def is_physical(nic: str) -> bool:
    return os.path.exists(f"{SYS_NET}/{nic}/device") and not os.path.exists(f"{SYS_NET}/{nic}/bridge")


def list_nics() -> list[dict]:
    """Physical NICs with link state and what they're attached to."""
    if not os.path.isdir(SYS_NET):
        return []
    uplink = _primary_uplink()
    addrs = _addrs()
    nm = _nm_states()
    out = []
    for nic in sorted(os.listdir(SYS_NET)):
        if not is_physical(nic):
            continue
        master = os.path.basename(os.path.realpath(f"{SYS_NET}/{nic}/master")) if os.path.exists(f"{SYS_NET}/{nic}/master") else ""
        speed = _read(f"{SYS_NET}/{nic}/speed")
        out.append({
            "name": nic,
            "mac": _read(f"{SYS_NET}/{nic}/address"),
            "operstate": _read(f"{SYS_NET}/{nic}/operstate"),
            "carrier": _read(f"{SYS_NET}/{nic}/carrier") == "1",
            "speed_mbps": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None,
            "addresses": addrs.get(nic, []),
            "master": master,
            # The server's own primary uplink: never usable untagged.
            "protected": nic == uplink,
            # Host currently configures it (e.g. NetworkManager auto-DHCP);
            # claiming it for a lab releases it and removes those addresses.
            "host_managed": bool(addrs.get(nic)) or nm.get(nic, "unmanaged") not in ("unmanaged", "unavailable", ""),
            "nm_state": nm.get(nic, ""),
        })
    return out


def link_exists(name: str) -> bool:
    return os.path.exists(f"{SYS_NET}/{name}")


def carrier(name: str) -> bool:
    return _read(f"{SYS_NET}/{name}/carrier") == "1"


# --- validation --------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    mode: str              # internal | access | trunk
    nic: str = ""
    vlan: int = 0

    @staticmethod
    def from_dict(d: dict | None) -> "Segment":
        d = d or {}
        return Segment(mode=d.get("mode") or "internal", nic=(d.get("nic") or "").strip(),
                       vlan=int(d.get("vlan") or 0))

    def to_dict(self) -> dict:
        return {"mode": self.mode, "nic": self.nic, "vlan": self.vlan}

    @property
    def configured(self) -> bool:
        return self.mode == "internal" or bool(self.nic)

    def bridge(self, topology_id: int, node_id: int) -> str:
        if self.mode == "internal":
            return internal_bridge_name(topology_id, node_id)
        return segment_bridge_name(self.nic, self.vlan if self.mode == "trunk" else None)

    def describe(self) -> str:
        if self.mode == "internal":
            return "Lab switch"
        if not self.nic:
            return "Not connected — choose a NIC"
        return f"{self.nic} · VLAN {self.vlan}" if self.mode == "trunk" else f"{self.nic} (untagged)"


def validate_segment(seg: Segment, nics: list[dict] | None = None) -> None:
    if seg.mode not in ("internal", "access", "trunk"):
        raise HostNetError(f"Unknown network mode '{seg.mode}'")
    if seg.mode == "internal" or not seg.nic:
        return
    nics = list_nics() if nics is None else nics
    by = {n["name"]: n for n in nics}
    if seg.nic not in by:
        raise HostNetError(f"'{seg.nic}' is not a physical NIC on this host")
    if seg.mode == "trunk" and not (1 <= seg.vlan <= 4094):
        raise HostNetError("VLAN ID must be between 1 and 4094")
    if seg.mode == "access" and by[seg.nic]["protected"]:
        raise HostNetError(
            f"{seg.nic} carries this server's own connection — use trunk mode with a VLAN tag instead of taking over the whole port")


# --- bridges -----------------------------------------------------------------

def _forward_rule(bridge: str, add: bool) -> None:
    """Docker loads br_netfilter and sets FORWARD to DROP, which would drop
    frames bridged between lab veths and the NIC. Allow them explicitly in
    DOCKER-USER (the chain Docker leaves to users)."""
    rule = ["DOCKER-USER", "-i", bridge, "-o", bridge, "-j", "ACCEPT"]
    rc, _, _ = _run(["iptables", "-C", *rule])
    if add and rc != 0:
        rc2, _, _ = _run(["iptables", "-L", "DOCKER-USER", "-n"])
        if rc2 == 0:
            _run(["iptables", "-I", *rule])
    elif not add and rc == 0:
        _run(["iptables", "-D", *rule])


def ensure_bridge(name: str, seg: Segment, mtu: int | None = None) -> None:
    """Idempotently create the bridge and attach its uplink."""
    if not link_exists(name):
        _must(["ip", "link", "add", name, "type", "bridge", "stp_state", "0"])
    uplink = None
    if seg.nic and seg.mode == "access":
        release_from_host(seg.nic)
    if seg.mode == "access" and seg.nic:
        uplink = seg.nic
    elif seg.mode == "trunk" and seg.nic:
        uplink = vlan_ifname(seg.nic, seg.vlan)
        if not link_exists(uplink):
            _must(["ip", "link", "add", "link", seg.nic, "name", uplink, "type", "vlan", "id", str(seg.vlan)])
        _must(["ip", "link", "set", seg.nic, "up"])
    if uplink:
        master = os.path.basename(os.path.realpath(f"{SYS_NET}/{uplink}/master")) if os.path.exists(f"{SYS_NET}/{uplink}/master") else ""
        if master and master != name:
            raise HostNetError(f"{uplink} is already attached to {master}")
        if master != name:
            _must(["ip", "link", "set", uplink, "master", name])
        _must(["ip", "link", "set", uplink, "up"])
    if mtu:
        _run(["ip", "link", "set", name, "mtu", str(mtu)])
    _must(["ip", "link", "set", name, "up"])
    _forward_rule(name, True)


def delete_bridge(name: str, seg: Segment) -> None:
    """Remove the bridge and the VLAN sub-interface we created. The physical
    NIC itself is detached and left down."""
    _forward_rule(name, False)
    if seg.mode == "trunk" and seg.nic:
        sub = vlan_ifname(seg.nic, seg.vlan)
        if link_exists(sub):
            _run(["ip", "link", "del", sub])
    elif seg.mode == "access" and seg.nic and link_exists(seg.nic):
        _run(["ip", "link", "set", seg.nic, "nomaster"])
    if link_exists(name):
        _run(["ip", "link", "del", name])


def ensure_host_address(bridge: str, cidr: str) -> None:
    rc, out, _ = _run(["ip", "-j", "addr", "show", "dev", bridge])
    have = set()
    if rc == 0:
        try:
            for i in json.loads(out or "[]"):
                have |= {f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])}
        except (json.JSONDecodeError, KeyError):
            pass
    if cidr not in have:
        _must(["ip", "addr", "add", cidr, "dev", bridge])


def list_plabs_bridges() -> list[str]:
    if not os.path.isdir(SYS_NET):
        return []
    return sorted(n for n in os.listdir(SYS_NET)
                  if n.startswith("pl-") and os.path.exists(f"{SYS_NET}/{n}/bridge"))


def gc_bridges(keep: set[str]) -> list[str]:
    """Delete Phanero Labs bridges (pl-*) nothing uses any more. VLAN
    sub-interfaces enslaved to them are deleted; physical NICs are detached."""
    removed = []
    for br in list_plabs_bridges():
        if br in keep:
            continue
        brif = f"{SYS_NET}/{br}/brif"
        for member in (os.listdir(brif) if os.path.isdir(brif) else []):
            if os.path.exists(f"/proc/net/vlan/{member}"):
                _run(["ip", "link", "del", member])
            elif is_physical(member):
                _run(["ip", "link", "set", member, "nomaster"])
        _forward_rule(br, False)
        _run(["ip", "link", "del", br])
        removed.append(br)
    return removed

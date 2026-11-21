"""Traffic generator nodes: a Linux container (image plabs/traffic) whose main
process, plabs-traffic (device_images/traffic), sends UDP streams with a DSCP
per stream, joins multicast groups (any-source or source-specific, IPv4 and
IPv6), reflects packets back to their sender and reports live statistics.

The node's configuration lives in `NodeInstance.traffic`. It's written to
`<lab dir>/traffic/<node>/config.json`, which the container mounts at
/etc/plabs-traffic (clab_yaml): plabs-traffic re-reads it within a second, so
edits apply live and never change the device definition (nothing restarts).
Stats come back in stats.json in the same directory.
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path

from app.config import LAB_STATE_DIR

CONTAINER_DIR = "/etc/plabs-traffic"

# DSCP names used by the UI, lessons and checks.
DSCP_NAMES = {
    "default": 0, "be": 0, "cs0": 0, "cs1": 8, "af11": 10, "af12": 12, "af13": 14,
    "cs2": 16, "af21": 18, "af22": 20, "af23": 22, "cs3": 24, "af31": 26, "af32": 28, "af33": 30,
    "cs4": 32, "af41": 34, "af42": 36, "af43": 38, "cs5": 40, "ef": 46, "cs6": 48, "cs7": 56,
}
DSCP_LABEL = {v: k for k, v in reversed(list(DSCP_NAMES.items()))}
DSCP_LABEL[0] = "default"

MAX_STREAMS, MAX_RECEIVERS = 16, 16
MAX_PPS, MAX_TOTAL_PPS = 5000, 10000


class TrafficConfigError(ValueError):
    pass


def dscp_value(v) -> int:
    """46, "46", "ef", "EF" → 46."""
    if isinstance(v, bool):
        raise TrafficConfigError(f"bad DSCP {v!r}")
    if isinstance(v, int):
        n = v
    else:
        s = str(v).strip().lower()
        if s in DSCP_NAMES:
            return DSCP_NAMES[s]
        try:
            n = int(s, 0)
        except ValueError:
            raise TrafficConfigError(f"unknown DSCP {v!r} (use 0-63 or a name: ef, af11, cs6…)")
    if not 0 <= n <= 63:
        raise TrafficConfigError(f"DSCP {n} is out of range (0-63)")
    return n


def dscp_label(n: int) -> str:
    return DSCP_LABEL.get(int(n), str(n))


def default_config() -> dict:
    return {"interface": {"name": "eth1", "ipv4": "", "gateway4": "", "ipv6": "", "gateway6": ""},
            "streams": [], "receivers": [], "reflector": {"enabled": False, "port": 7, "dscp": "keep"}}


def _addr(v: str, what: str, prefix: bool) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    try:
        if prefix:
            if "/" not in v:
                raise ValueError("needs a prefix length, e.g. 10.1.3.10/24")
            return str(ipaddress.ip_interface(v))
        return str(ipaddress.ip_address(v))
    except ValueError as e:
        raise TrafficConfigError(f"{what}: {e}")


def _port(v, what: str) -> int:
    try:
        p = int(v)
    except (TypeError, ValueError):
        raise TrafficConfigError(f"{what}: port must be a number")
    if not 1 <= p <= 65535:
        raise TrafficConfigError(f"{what}: port {p} out of range")
    return p


def normalize(cfg: dict | None) -> dict:
    """Validate and complete a traffic config (raises TrafficConfigError)."""
    cfg = dict(cfg or {})
    out = default_config()
    i = dict(cfg.get("interface") or {})
    out["interface"] = {
        "name": (i.get("name") or "eth1").strip() or "eth1",
        "ipv4": _addr(i.get("ipv4"), "IPv4 address", True),
        "gateway4": _addr(i.get("gateway4"), "IPv4 gateway", False),
        "ipv6": _addr(i.get("ipv6"), "IPv6 address", True),
        "gateway6": _addr(i.get("gateway6"), "IPv6 gateway", False),
    }
    for a, g in (("ipv4", "gateway4"), ("ipv6", "gateway6")):
        if out["interface"][g] and not out["interface"][a]:
            raise TrafficConfigError(f"a {a.upper()} gateway needs a {a.upper()} address")

    streams, names, total = [], set(), 0.0
    for n, s in enumerate(cfg.get("streams") or [], 1):
        s = dict(s or {})
        name = str(s.get("name") or f"S{n}").strip()[:16]
        if name in names:
            raise TrafficConfigError(f"two streams are called {name!r}")
        names.add(name)
        what = f"stream {name}"
        dst = _addr(s.get("dst"), f"{what} destination", False)
        if not dst:
            raise TrafficConfigError(f"{what}: needs a destination address or group")
        try:
            rate = float(s.get("rate_pps") or 10)
        except (TypeError, ValueError):
            raise TrafficConfigError(f"{what}: rate must be a number of packets per second")
        if not 0.1 <= rate <= MAX_PPS:
            raise TrafficConfigError(f"{what}: rate must be 0.1–{MAX_PPS} packets/s")
        try:
            size = int(s.get("size") or 100)
        except (TypeError, ValueError):
            raise TrafficConfigError(f"{what}: size must be a number of bytes")
        if not 40 <= size <= 1400:
            raise TrafficConfigError(f"{what}: payload size must be 40–1400 bytes")
        ttl = s.get("ttl")
        if ttl in (None, ""):
            ttl = None
        else:
            try:
                ttl = int(ttl)
            except (TypeError, ValueError):
                raise TrafficConfigError(f"{what}: TTL must be a number")
            if not 1 <= ttl <= 255:
                raise TrafficConfigError(f"{what}: TTL must be 1–255")
        enabled = bool(s.get("enabled", True))
        if enabled:
            total += rate
        streams.append({"name": name, "dst": dst, "port": _port(s.get("port") or 5001, what),
                        "rate_pps": rate if rate != int(rate) else int(rate), "size": size,
                        "dscp": dscp_value(s.get("dscp", 0)), "ttl": ttl, "enabled": enabled})
    if len(streams) > MAX_STREAMS:
        raise TrafficConfigError(f"at most {MAX_STREAMS} streams")
    if total > MAX_TOTAL_PPS:
        raise TrafficConfigError(f"the streams add up to {total:g} packets/s; the limit is {MAX_TOTAL_PPS}")
    out["streams"] = streams

    receivers, seen = [], set()
    for r in cfg.get("receivers") or []:
        r = dict(r or {})
        group = _addr(r.get("group"), "receiver group", False)
        source = _addr(r.get("source"), "receiver source", False)
        port = _port(r.get("port") or 5001, f"receiver {group or 'listener'}")
        if group and not ipaddress.ip_address(group).is_multicast:
            raise TrafficConfigError(f"{group} isn't a multicast group (leave the group empty for a unicast listener)")
        if source and not group:
            raise TrafficConfigError("a source needs a group (source-specific join)")
        if source and ipaddress.ip_address(source).version != ipaddress.ip_address(group).version:
            raise TrafficConfigError(f"source {source} and group {group} are different IP versions")
        ipv6 = bool(r.get("ipv6")) if not group else ":" in group
        key = (group, source, port, ipv6)
        if key in seen:
            continue
        seen.add(key)
        entry = {"group": group, "source": source, "port": port}
        if not group:
            entry["ipv6"] = ipv6
        receivers.append(entry)
    if len(receivers) > MAX_RECEIVERS:
        raise TrafficConfigError(f"at most {MAX_RECEIVERS} receivers")
    out["receivers"] = receivers

    rf = dict(cfg.get("reflector") or {})
    mark = rf.get("dscp", "keep")
    out["reflector"] = {"enabled": bool(rf.get("enabled")), "port": _port(rf.get("port") or 7, "reflector"),
                        "dscp": "keep" if mark in (None, "", "keep") else dscp_value(mark)}
    return out


def config_dir(lab_name: str, clab_name: str) -> Path:
    return LAB_STATE_DIR / lab_name / "traffic" / clab_name


def write_config(lab_name: str, clab_name: str, cfg: dict) -> Path:
    """Write the node's config where its container reads it (in place, so a
    bind mount of the directory sees it immediately)."""
    d = config_dir(lab_name, clab_name)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "config.json"
    text = json.dumps(normalize(cfg), indent=1)
    if not p.exists() or p.read_text() != text:
        p.write_text(text)
    return p


def read_stats_file(lab_name: str, clab_name: str) -> dict | None:
    p = config_dir(lab_name, clab_name) / "stats.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


STATS_COMMAND = f"cat {CONTAINER_DIR}/stats.json"

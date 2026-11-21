#!/usr/bin/env python3
"""plabs-traffic — the Phanero Labs traffic generator (sender, receiver, reflector).

Runs as the container's main process. It reads /etc/plabs-traffic/config.json
(re-read whenever it changes) and writes /etc/plabs-traffic/stats.json once a
second. The directory is bind-mounted from the lab directory, so the app edits
the config and reads the stats without restarting anything.

config.json:
  {"interface": {"name": "eth1", "ipv4": "10.1.3.10/24", "gateway4": "10.1.3.1",
                 "ipv6": "2001:db8:1:3::10/64", "gateway6": "2001:db8:1:3::1"},
   "streams":   [{"name": "VOICE", "dst": "10.1.4.10", "port": 5004, "rate_pps": 50,
                  "size": 160, "dscp": 46, "ttl": 64, "enabled": true}],
   "receivers": [{"group": "239.1.1.1", "source": "", "port": 5001},   # multicast (source: SSM)
                 {"group": "", "port": 5004}],                         # unicast listener
   "reflector": {"enabled": false, "port": 7, "dscp": "keep"}}         # or a DSCP number

Every stream packet carries a header (magic, flags, sequence number, send
time, stream name) so receivers measure loss, reordering and jitter per
stream, and see the DSCP each packet arrived with. A reflector sends a stream
packet back to its sender (keeping or re-marking its DSCP, and recording the
DSCP it arrived with), so the sender sees round-trip time, loss and marking
in both directions.

  plabs-traffic daemon [--dir /etc/plabs-traffic]
  plabs-traffic status            # print stats.json
"""
from __future__ import annotations

import collections
import heapq
import ipaddress
import json
import os
import selectors
import socket
import struct
import subprocess
import sys
import threading
import time

DIR = "/etc/plabs-traffic"
MAGIC = b"PLTG"
HDR = struct.Struct("!4sBBBBQQ16s")          # magic, version, flags, dscp at reflector, pad, seq, ts_ns, name
F_REFLECTED = 0x01
WINDOW = 10                                    # seconds of history for rates / loss / DSCP
RATE_WINDOW = 5

# Linux socket option numbers (not all are exposed by Python's socket module).
IP_TOS, IP_RECVTOS, IP_PKTINFO = 1, 13, 8
IP_ADD_SOURCE_MEMBERSHIP = 39
IPV6_RECVPKTINFO, IPV6_PKTINFO = 49, 50
IPV6_RECVTCLASS, IPV6_TCLASS = 66, 67
MCAST_JOIN_SOURCE_GROUP = 46
IPPROTO_IPV6 = socket.IPPROTO_IPV6


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def ifindex(name: str) -> int:
    try:
        return socket.if_nametoindex(name)
    except OSError:
        return 0


def is_v6(addr: str) -> bool:
    return ":" in addr


def is_multicast(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_multicast
    except ValueError:
        return False


# --- interface -----------------------------------------------------------------

class Interface:
    """Applies the address / gateway config to the data interface, idempotently."""

    def __init__(self):
        self.applied: dict = {}
        self.error = ""

    def apply(self, cfg: dict, retry: bool = False) -> bool:
        """Apply the config; True when the applied settings changed (sockets
        that depend on the interface must then be reopened)."""
        name = cfg.get("name") or "eth1"
        if not ifindex(name):
            if self.error != f"{name} doesn't exist yet (not cabled?)":
                log(f"waiting for {name}")
            self.error = f"{name} doesn't exist yet (not cabled?)"
            return False
        want = {k: (cfg.get(k) or "").strip() for k in ("ipv4", "gateway4", "ipv6", "gateway6")}
        same = want == {k: self.applied.get(k, "") for k in want} and self.applied.get("name") == name
        if same and not (retry and self.error):
            return False
        errs = []
        run(["ip", "link", "set", name, "up"])
        old = self.applied
        for fam, key in (("-4", "ipv4"), ("-6", "ipv6")):
            if old.get(key) and old.get(key) != want[key]:
                run(["ip", fam, "addr", "del", old[key], "dev", old.get("name", name)])
        if want["ipv4"]:
            rc, out = run(["ip", "-4", "addr", "replace", want["ipv4"], "dev", name])
            if rc:
                errs.append(f"ipv4 {want['ipv4']}: {out}")
        if want["ipv6"]:
            rc, out = run(["ip", "-6", "addr", "replace", want["ipv6"], "dev", name, "nodad"])
            if rc:
                errs.append(f"ipv6 {want['ipv6']}: {out}")
        # The data interface carries everything: default routes and multicast.
        if want["gateway4"]:
            rc, out = run(["ip", "-4", "route", "replace", "default", "via", want["gateway4"], "dev", name])
            if rc:
                errs.append(f"gateway {want['gateway4']}: {out}")
        if want["ipv4"]:
            run(["ip", "-4", "route", "replace", "224.0.0.0/4", "dev", name])
        if want["gateway6"]:
            rc, out = run(["ip", "-6", "route", "replace", "default", "via", want["gateway6"], "dev", name])
            if rc:
                errs.append(f"gateway {want['gateway6']}: {out}")
        changed = self.applied != {"name": name, **want}
        self.applied = {"name": name, **want}
        err = "; ".join(errs)
        if err != self.error or changed:
            log(f"interface {name}: {want}" + (f" — {err}" if err else " applied"))
        self.error = err
        return changed

    @property
    def name(self) -> str:
        return self.applied.get("name") or "eth1"

    def v4(self) -> str:
        a = self.applied.get("ipv4", "")
        return a.split("/")[0] if a else ""

    def v6(self) -> str:
        a = self.applied.get("ipv6", "")
        return a.split("/")[0] if a else ""


# --- statistics ----------------------------------------------------------------

class Window:
    """Per-second buckets: packet count, highest sequence number, DSCP counts."""

    def __init__(self):
        self.buckets: collections.OrderedDict[int, list] = collections.OrderedDict()

    def add(self, now: float, seq: int | None, dscp: int | None, nbytes: int) -> None:
        sec = int(now)
        b = self.buckets.get(sec)
        if b is None:
            b = self.buckets[sec] = [0, -1, collections.Counter(), 0]
            while self.buckets and next(iter(self.buckets)) < sec - WINDOW - 1:
                self.buckets.popitem(last=False)
        b[0] += 1
        b[3] += nbytes
        if seq is not None and seq > b[1]:
            b[1] = seq
        if dscp is not None:
            b[2][dscp] += 1

    def summary(self, now: float) -> dict:
        sec = int(now)
        full = [s for s in self.buckets if sec - RATE_WINDOW <= s < sec]
        pkts = sum(self.buckets[s][0] for s in full)
        nbytes = sum(self.buckets[s][3] for s in full)
        recent = [s for s in self.buckets if sec - WINDOW <= s < sec]
        dscp = collections.Counter()
        for s in recent:
            dscp.update(self.buckets[s][2])
        loss = None
        seqs = [(s, self.buckets[s][1]) for s in sorted(recent) if self.buckets[s][1] >= 0]
        if len(seqs) >= 2:
            first_s = seqs[0][0]
            expected = seqs[-1][1] - seqs[0][1]
            got = sum(self.buckets[s][0] for s, _ in seqs if s > first_s)
            if expected > 0:
                loss = max(0.0, min(100.0, 100.0 * (1 - got / expected)))
        span = max(1, len(full)) if full else RATE_WINDOW
        return {"pps": round(pkts / span, 1), "kbps": round(nbytes * 8 / span / 1000, 1),
                "loss_pct": None if loss is None else round(loss, 1),
                "dscp": {str(k): v for k, v in sorted(dscp.items())}}


class Flow:
    """One received stream (by source, destination, port and stream name)."""

    def __init__(self, src, dst, port, name):
        self.src, self.dst, self.port, self.name = src, dst, port, name
        self.packets = 0
        self.max_seq = -1
        self.lost_total = 0
        self.reordered = 0
        self.jitter = 0.0
        self.last_transit = None
        self.last_seen = 0.0
        self.window = Window()

    def packet(self, now, seq, ts_ns, dscp, nbytes):
        self.packets += 1
        self.last_seen = now
        if seq is not None:
            if self.max_seq >= 0 and (seq == 0 or self.max_seq - seq > 100000):
                self.max_seq = -1                      # the sender restarted the stream
                self.last_transit = None
            if seq > self.max_seq:
                if self.max_seq >= 0:
                    self.lost_total += seq - self.max_seq - 1
                self.max_seq = seq
            else:
                self.reordered += 1
                self.lost_total = max(0, self.lost_total - 1)
            transit = now - ts_ns / 1e9
            if self.last_transit is not None:      # RFC 3550 interarrival jitter
                self.jitter += (abs(transit - self.last_transit) - self.jitter) / 16
            self.last_transit = transit
        self.window.add(now, seq, dscp, nbytes)

    def to_dict(self, now):
        d = {"src": self.src, "dst": self.dst, "port": self.port, "stream": self.name,
             "multicast": is_multicast(self.dst), "packets": self.packets,
             "lost": self.lost_total, "reordered": self.reordered,
             "jitter_ms": round(self.jitter * 1000, 2), "last_seen_s": round(now - self.last_seen, 1)}
        d.update(self.window.summary(now))
        return d


# --- sockets -------------------------------------------------------------------

def udp_socket(v6: bool) -> socket.socket:
    s = socket.socket(socket.AF_INET6 if v6 else socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if v6:
        s.setsockopt(IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.setsockopt(IPPROTO_IPV6, IPV6_RECVTCLASS, 1)
        s.setsockopt(IPPROTO_IPV6, IPV6_RECVPKTINFO, 1)
    else:
        s.setsockopt(socket.IPPROTO_IP, IP_RECVTOS, 1)
        s.setsockopt(socket.IPPROTO_IP, IP_PKTINFO, 1)
    s.setblocking(False)
    return s


def recv(s: socket.socket):
    """(payload, (src, port), dst address, dscp)."""
    data, anc, _flags, addr = s.recvmsg(65535, 256)
    dscp, dst = None, ""
    for level, typ, val in anc:
        if level == socket.IPPROTO_IP and typ == IP_TOS and val:
            dscp = val[0] >> 2
        elif level == socket.IPPROTO_IP and typ == IP_PKTINFO and len(val) >= 12:
            dst = socket.inet_ntop(socket.AF_INET, val[8:12])
        elif level == IPPROTO_IPV6 and typ == IPV6_TCLASS and len(val) >= 4:
            dscp = struct.unpack("i", val[:4])[0] >> 2
        elif level == IPPROTO_IPV6 and typ == IPV6_PKTINFO and len(val) >= 16:
            dst = socket.inet_ntop(socket.AF_INET6, val[:16])
    return data, (addr[0], addr[1]), dst, dscp


def set_dscp(s: socket.socket, v6: bool, dscp: int) -> None:
    if v6:
        s.setsockopt(IPPROTO_IPV6, IPV6_TCLASS, dscp << 2)
    else:
        s.setsockopt(socket.IPPROTO_IP, IP_TOS, dscp << 2)


def sockaddr_in6(addr: str) -> bytes:
    raw = struct.pack("HHI16sI", socket.AF_INET6, 0, 0, socket.inet_pton(socket.AF_INET6, addr), 0)
    return raw.ljust(128, b"\0")


def join(s: socket.socket, group: str, source: str, iface: str, v4_addr: str) -> None:
    idx = ifindex(iface)
    if is_v6(group):
        if source:
            req = struct.pack("I4x", idx) + sockaddr_in6(group) + sockaddr_in6(source)
            s.setsockopt(IPPROTO_IPV6, MCAST_JOIN_SOURCE_GROUP, req)
        else:
            s.setsockopt(IPPROTO_IPV6, socket.IPV6_JOIN_GROUP,
                         socket.inet_pton(socket.AF_INET6, group) + struct.pack("I", idx))
    else:
        local = socket.inet_aton(v4_addr or "0.0.0.0")
        if source:
            req = socket.inet_aton(group) + local + socket.inet_aton(source)
            s.setsockopt(socket.IPPROTO_IP, IP_ADD_SOURCE_MEMBERSHIP, req)
        else:
            req = struct.pack("4s4si", socket.inet_aton(group), local, idx)   # struct ip_mreqn
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, req)


# --- the engine ----------------------------------------------------------------

class Stream:
    def __init__(self, cfg: dict, iface: Interface):
        self.cfg = cfg
        self.name = cfg["name"]
        self.dst = cfg["dst"]
        self.port = int(cfg["port"])
        self.v6 = is_v6(self.dst)
        self.interval = 1.0 / max(0.1, float(cfg.get("rate_pps") or 10))
        self.size = max(HDR.size, int(cfg.get("size") or 100))
        self.seq = 0
        self.sent = 0
        self.error = ""
        self.sent_window = Window()
        self.back = Flow("", "", 0, self.name)          # reflected packets coming back
        self.at_reflector = collections.Counter()
        self.rtt = collections.deque(maxlen=200)
        self.sock = udp_socket(self.v6)
        set_dscp(self.sock, self.v6, int(cfg.get("dscp") or 0))
        ttl = int(cfg.get("ttl") or (16 if is_multicast(self.dst) else 64))
        if self.v6:
            self.sock.setsockopt(IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, ttl)
            self.sock.setsockopt(IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, ttl)
            if is_multicast(self.dst) and ifindex(iface.name):
                self.sock.setsockopt(IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, ifindex(iface.name))
        else:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            if is_multicast(self.dst) and iface.v4():
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface.v4()))
        # Send from the configured address: a router advertisement can give the
        # interface a second (SLAAC) IPv6 address, and an SSM channel (S,G)
        # only matches the source the receivers asked for.
        src = iface.v6() if self.v6 else iface.v4()
        self.sock.bind((src or ("::" if self.v6 else "0.0.0.0"), 0))
        self.payload_pad = b"\0" * (self.size - HDR.size)
        self.name_b = self.name.encode()[:16]

    def send(self, now: float) -> None:
        hdr = HDR.pack(MAGIC, 1, 0, 0, 0, self.seq, time.time_ns(), self.name_b)
        try:
            self.sock.sendto(hdr + self.payload_pad, (self.dst, self.port))
            self.sent += 1
            self.sent_window.add(now, self.seq, None, self.size)
            self.error = ""
        except OSError as e:
            self.error = str(e)
        self.seq += 1

    def on_reflected(self, now, seq, ts_ns, dscp_back, dscp_at, nbytes):
        self.back.packet(now, seq, ts_ns, dscp_back, nbytes)
        if dscp_at is not None:
            self.at_reflector[dscp_at] += 1
        self.rtt.append((time.time_ns() - ts_ns) / 1e6)

    def to_dict(self, now):
        d = {k: self.cfg.get(k) for k in ("name", "dst", "port", "rate_pps", "size", "dscp", "ttl")}
        d.update({"enabled": True, "sent": self.sent, "error": self.error,
                  "pps": self.sent_window.summary(now)["pps"]})
        if self.back.packets:
            b = self.back.to_dict(now)
            d["reflected"] = {"received": b["packets"], "pps": b["pps"], "loss_pct": b["loss_pct"],
                              "dscp_back": b["dscp"],
                              "dscp_at_reflector": {str(k): v for k, v in sorted(self.at_reflector.items())},
                              "rtt_ms": round(sum(self.rtt) / len(self.rtt), 2) if self.rtt else None,
                              "last_seen_s": b["last_seen_s"]}
        return d


class Receiver:
    def __init__(self, cfg: dict, iface: Interface):
        self.cfg = cfg
        self.group = (cfg.get("group") or "").strip()
        self.source = (cfg.get("source") or "").strip()
        self.port = int(cfg.get("port") or 5001)
        self.error = ""
        v6 = is_v6(self.group) if self.group else bool(cfg.get("ipv6"))
        self.sock = udp_socket(v6)
        if self.group:
            self.sock.bind((self.group, self.port))
            join(self.sock, self.group, self.source, iface.name, iface.v4())
        else:
            self.sock.bind(("::" if v6 else "0.0.0.0", self.port))

    def to_dict(self):
        return {"group": self.group, "source": self.source, "port": self.port, "joined": not self.error,
                "error": self.error}


class Engine:
    def __init__(self, d: str):
        self.dir = d
        self.cfg_path = os.path.join(d, "config.json")
        self.stats_path = os.path.join(d, "stats.json")
        self.iface = Interface()
        self.cfg: dict = {}
        self.mtime = 0.0
        self.streams: dict[str, Stream] = {}
        self.stream_errors: dict[str, str] = {}
        self.receivers: dict[str, Receiver] = {}
        self.receiver_errors: list[dict] = []
        self.reflectors: list[socket.socket] = []
        self.reflector_cfg: dict = {}
        self.reflected = 0
        self.flows: dict[tuple, Flow] = {}
        self.unknown = collections.Counter()
        self.sel = selectors.DefaultSelector()
        self.lock = threading.Lock()
        self.config_error = ""

    # config ---------------------------------------------------------------
    def load(self) -> None:
        try:
            m = os.stat(self.cfg_path).st_mtime
        except OSError:
            m = 0.0
        if m == self.mtime and self.iface.applied and not self.iface.error:
            return
        if m == self.mtime and not self.iface.applied:
            # The interface didn't exist yet: apply it now, then (re)open everything on it.
            if self.iface.apply(self.cfg.get("interface") or {}):
                with self.lock:
                    self._reopen_all()
            return
        if m == self.mtime and self.iface.applied:
            # Unchanged config, interface had an error: retry just the interface.
            if self.iface.apply(self.cfg.get("interface") or {}, retry=True):
                with self.lock:
                    self._reopen_all()
            return
        try:
            cfg = json.load(open(self.cfg_path)) if m else {}
            self.config_error = ""
        except (OSError, ValueError) as e:
            self.config_error = f"config.json: {e}"
            return
        self.mtime = m
        self.cfg = cfg
        changed = self.iface.apply(cfg.get("interface") or {})
        with self.lock:
            if changed:
                self._reopen_all(sync=False)
            self._sync_streams(cfg.get("streams") or [])
            self._sync_receivers(cfg.get("receivers") or [])
            self._sync_reflector(cfg.get("reflector") or {})

    def _reopen_all(self, sync: bool = True) -> None:
        """Sockets carry the interface (multicast IF, group joins): reopen them."""
        for st in self.streams.values():
            self._unregister(st.sock)
        for r in self.receivers.values():
            self._unregister(r.sock)
        self.streams, self.receivers = {}, {}
        if sync:
            self._sync_streams(self.cfg.get("streams") or [])
            self._sync_receivers(self.cfg.get("receivers") or [])

    def _unregister(self, s: socket.socket) -> None:
        try:
            self.sel.unregister(s)
        except (KeyError, ValueError):
            pass
        s.close()

    def _sync_streams(self, items: list[dict]) -> None:
        want = {i["name"]: i for i in items if i.get("enabled", True) and i.get("name") and i.get("dst")}
        for name in list(self.streams):
            if name not in want or self.streams[name].cfg != want[name]:
                self._unregister(self.streams.pop(name).sock)
        self.stream_errors = {}
        for name, c in want.items():
            if name in self.streams:
                continue
            try:
                st = Stream(c, self.iface)
                log(f"stream {name} → {st.dst}:{st.port} dscp {c.get('dscp', 0)} at {c.get('rate_pps')} pps")
                self.streams[name] = st
                self.sel.register(st.sock, selectors.EVENT_READ, ("stream", st))
            except (OSError, ValueError) as e:
                log(f"stream {name}: {e}")
                self.stream_errors[name] = str(e)
        self.schedule = [(time.monotonic(), n) for n in self.streams]
        heapq.heapify(self.schedule)

    def _sync_receivers(self, items: list[dict]) -> None:
        def key(c):
            return json.dumps({k: c.get(k) for k in ("group", "source", "port", "ipv6")}, sort_keys=True)
        want = {key(c): c for c in items}
        for k in list(self.receivers):
            if k not in want:
                self._unregister(self.receivers.pop(k).sock)
        self.receiver_errors = []
        for k, c in want.items():
            if k in self.receivers:
                continue
            try:
                r = Receiver(c, self.iface)
                log(f"receiver {r.group or 'listener'}:{r.port}" + (f" source {r.source}" if r.source else "") + " open")
                self.receivers[k] = r
                self.sel.register(r.sock, selectors.EVENT_READ, ("receiver", r))
            except (OSError, ValueError) as e:
                log(f"receiver {c.get('group') or 'listener'}:{c.get('port')}: {e}")
                self.receiver_errors.append({"group": c.get("group", ""), "source": c.get("source", ""),
                                             "port": c.get("port"), "joined": False, "error": str(e)})

    def _sync_reflector(self, c: dict) -> None:
        c = {"enabled": bool(c.get("enabled")), "port": int(c.get("port") or 7), "dscp": c.get("dscp", "keep")}
        if c == self.reflector_cfg:
            return
        for s in self.reflectors:
            self._unregister(s)
        self.reflectors = []
        self.reflector_cfg = c
        if not c["enabled"]:
            return
        for v6 in (False, True):
            s = udp_socket(v6)
            s.bind(("::" if v6 else "0.0.0.0", c["port"]))
            self.reflectors.append(s)
            self.sel.register(s, selectors.EVENT_READ, ("reflector", v6))

    def _order(self) -> list[str]:
        return [c.get("name") for c in (self.cfg.get("streams") or []) if c.get("name")]

    # packets --------------------------------------------------------------
    def on_packet(self, kind, obj, s: socket.socket) -> None:
        try:
            data, (src, sport), dst, dscp = recv(s)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            return
        now = time.time()
        hdr = HDR.unpack_from(data) if len(data) >= HDR.size else None
        ours = hdr is not None and hdr[0] == MAGIC
        if kind == "stream":
            if ours and hdr[2] & F_REFLECTED:
                obj.on_reflected(now, hdr[5], hdr[6], dscp, hdr[3], len(data))
            return
        if kind == "reflector":
            if not ours or hdr[2] & F_REFLECTED:
                return
            out = HDR.pack(MAGIC, hdr[1], hdr[2] | F_REFLECTED, dscp if dscp is not None else 0, 0,
                           hdr[5], hdr[6], hdr[7]) + data[HDR.size:]
            mark = self.reflector_cfg.get("dscp", "keep")
            set_dscp(s, obj, int(dscp or 0) if mark == "keep" else int(mark))
            try:
                s.sendto(out, (src, sport))
                self.reflected += 1
            except OSError:
                pass
            return
        # receiver: a multicast group socket, or a unicast listener
        if not obj.group and dst and is_multicast(dst):
            return                                  # group traffic on the same port: its own receiver counts it
        name = hdr[7].rstrip(b"\0").decode(errors="replace") if ours else ""
        k = (src, dst or obj.group, obj.port, name)
        f = self.flows.get(k)
        if f is None:
            f = self.flows[k] = Flow(src, dst or obj.group, obj.port, name)
        if ours:
            f.packet(now, hdr[5], hdr[6], dscp, len(data))
        else:
            f.packet(now, None, 0, dscp, len(data))

    # loops ----------------------------------------------------------------
    def sender_loop(self) -> None:
        while True:
            with self.lock:
                if not self.schedule:
                    nxt = None
                else:
                    t, name = self.schedule[0]
                    now = time.monotonic()
                    if t <= now:
                        heapq.heappop(self.schedule)
                        st = self.streams.get(name)
                        if st is not None:
                            st.send(time.time())
                            # don't try to catch up after a stall: stay on the rate
                            heapq.heappush(self.schedule, (max(t + st.interval, now - st.interval), name))
                        continue
                    nxt = t - now
            time.sleep(min(nxt if nxt is not None else 0.2, 0.2))

    def write_stats(self) -> None:
        now = time.time()
        with self.lock:
            flows = [f.to_dict(now) for f in self.flows.values() if now - f.last_seen < 300]
            stats = {
                "ts": now,
                "interface": {**self.iface.applied, "error": self.iface.error},
                "config_error": self.config_error,
                "streams": [self.streams[n].to_dict(now) for n in self._order() if n in self.streams]
                + [{"name": n, "enabled": True, "error": e, "sent": 0, "pps": 0} for n, e in self.stream_errors.items()],
                "receivers": [r.to_dict() for r in self.receivers.values()] + self.receiver_errors,
                "flows": sorted(flows, key=lambda f: (f["dst"], f["port"], f["src"], f["stream"])),
                "reflector": {**self.reflector_cfg, "reflected": self.reflected},
            }
        tmp = self.stats_path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(stats, fh)
            os.replace(tmp, self.stats_path)
        except OSError as e:
            log("stats:", e)

    def run(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        self.schedule = []
        self.load()
        threading.Thread(target=self.sender_loop, daemon=True).start()
        last = 0.0
        while True:
            for key, _ in self.sel.select(timeout=0.2):
                kind, obj = key.data
                with self.lock:
                    self.on_packet(kind, obj, key.fileobj)
            if time.time() - last >= 1:
                last = time.time()
                self.load()
                self.write_stats()


def main() -> int:
    args = sys.argv[1:]
    d = DIR
    if "--dir" in args:
        d = args[args.index("--dir") + 1]
    if not args or args[0] == "daemon":
        Engine(d).run()
        return 0
    if args[0] == "status":
        try:
            print(open(os.path.join(d, "stats.json")).read())
            return 0
        except OSError as e:
            print(f"no stats yet: {e}", file=sys.stderr)
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

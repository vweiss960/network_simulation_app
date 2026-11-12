"""Minimal DHCPv4 client that acquires leases *on behalf of* lab devices.

The bridged management network takes its addressing from the network's own
DHCP server: for every device we lease an address (keyed by a stable
client-identifier), hold and renew it, and hand it to containerlab as the
device's static mgmt IP. The DHCP server stays the authority, so nothing
collides with its other clients, and no pool is configured anywhere.

Packets are sent and received on the host bridge with an AF_PACKET socket
(Linux, root): the bridge has no address of its own when we start, and
servers may answer by unicast to our MAC — a raw socket sees both.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import random
import select
import socket
import struct
import time
from dataclasses import dataclass, field

DISCOVER, OFFER, REQUEST, DECLINE, ACK, NAK, RELEASE = 1, 2, 3, 4, 5, 6, 7
MAGIC = b"\x63\x82\x53\x63"
ETH_P_IP = 0x0800


class DhcpError(Exception):
    pass


@dataclass
class Lease:
    ip: str
    prefix: int
    router: str = ""
    dns: list[str] = field(default_factory=list)
    server_id: str = ""
    lease_time: int = 3600
    obtained_at: float = 0.0

    @property
    def subnet(self) -> str:
        return str(ipaddress.ip_network(f"{self.ip}/{self.prefix}", strict=False))

    @property
    def renew_at(self) -> float:
        return self.obtained_at + self.lease_time / 2          # T1

    @property
    def expires_at(self) -> float:
        return self.obtained_at + self.lease_time


# --- packet encoding ---------------------------------------------------------

def _mac_bytes(mac: str) -> bytes:
    return bytes(int(x, 16) for x in mac.split(":"))


def build_message(msg_type: int, xid: int, mac: str, client_id: str, hostname: str = "",
                  requested_ip: str = "", server_id: str = "", ciaddr: str = "0.0.0.0") -> bytes:
    """BOOTP request with the broadcast flag set (so replies reach us even
    though the address isn't configured anywhere yet)."""
    hdr = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s",
        1, 1, 6, 0, xid, 0, 0x8000,
        socket.inet_aton(ciaddr), b"\0" * 4, b"\0" * 4, b"\0" * 4,
        _mac_bytes(mac).ljust(16, b"\0"), b"\0" * 64, b"\0" * 128,
    )
    cid = client_id.encode()[:250]
    opts = bytearray(MAGIC)
    opts += bytes([53, 1, msg_type])
    opts += bytes([61, len(cid) + 1, 0]) + cid          # type 0 = opaque string
    if hostname:
        hn = hostname.encode()[:63]
        opts += bytes([12, len(hn)]) + hn
    if requested_ip:
        opts += bytes([50, 4]) + socket.inet_aton(requested_ip)
    if server_id:
        opts += bytes([54, 4]) + socket.inet_aton(server_id)
    opts += bytes([55, 5, 1, 3, 6, 51, 54])             # mask, router, dns, lease time, server id
    opts += b"\xff"
    pkt = hdr + bytes(opts)
    return pkt + b"\0" * max(0, 300 - len(pkt))          # BOOTP minimum size


def parse_message(payload: bytes) -> dict | None:
    if len(payload) < 240 or payload[236:240] != MAGIC or payload[0] != 2:
        return None
    xid = struct.unpack("!I", payload[4:8])[0]
    out: dict = {"xid": xid, "yiaddr": socket.inet_ntoa(payload[16:20]), "opts": {}}
    i = 240
    while i < len(payload):
        code = payload[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        ln = payload[i + 1]
        out["opts"][code] = payload[i + 2:i + 2 + ln]
        i += 2 + ln
    t = out["opts"].get(53)
    out["type"] = t[0] if t else 0
    return out


def lease_from(msg: dict) -> Lease:
    o = msg["opts"]
    mask = socket.inet_ntoa(o[1]) if 1 in o else "255.255.255.0"
    prefix = ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen
    router = socket.inet_ntoa(o[3][:4]) if 3 in o and len(o[3]) >= 4 else ""
    dns = [socket.inet_ntoa(o[6][j:j + 4]) for j in range(0, len(o.get(6, b"")) - 3, 4)]
    return Lease(
        ip=msg["yiaddr"], prefix=prefix, router=router, dns=dns,
        server_id=socket.inet_ntoa(o[54]) if 54 in o else "",
        lease_time=struct.unpack("!I", o[51])[0] if 51 in o else 3600,
        obtained_at=time.time(),
    )


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def _frame(src_mac: str, payload: bytes) -> bytes:
    udp_len = 8 + len(payload)
    udp = struct.pack("!HHHH", 68, 67, udp_len, 0) + payload        # UDP checksum optional on IPv4
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + udp_len, random.randint(0, 0xFFFF), 0, 64, 17, 0,
                     b"\0" * 4, b"\xff" * 4)
    ip = ip[:10] + struct.pack("!H", _checksum(ip)) + ip[12:]
    eth = b"\xff" * 6 + _mac_bytes(src_mac) + struct.pack("!H", ETH_P_IP)
    return eth + ip + udp


def _extract_dhcp(frame: bytes) -> bytes | None:
    if len(frame) < 42 or struct.unpack("!H", frame[12:14])[0] != ETH_P_IP:
        return None
    ihl = (frame[14] & 0x0F) * 4
    if frame[23] != 17:
        return None
    udp = 14 + ihl
    sport, dport = struct.unpack("!HH", frame[udp:udp + 4])
    if sport != 67 or dport != 68:
        return None
    return frame[udp + 8:]


# --- transport ---------------------------------------------------------------

def mac_for(client_id: str) -> str:
    """Stable, locally-administered unicast MAC per client-id. Used as the
    lease's hardware address (and the device's mgmt MAC), so servers that
    key leases on chaddr rather than client-id still see distinct clients."""
    b = bytearray(hashlib.sha256(client_id.encode()).digest()[:6])
    b[0] = (b[0] & 0xFC) | 0x02
    return ":".join(f"{x:02x}" for x in b)


SOL_PACKET, PACKET_ADD_MEMBERSHIP, PACKET_MR_PROMISC = 263, 1, 1


class _Channel:
    def __init__(self, iface: str, mac: str):
        self.iface = iface
        self.mac = mac
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_IP))
        self.sock.bind((iface, 0))
        # Replies may be unicast to the per-device MAC, which isn't the
        # bridge's own — listen promiscuously (scoped to this socket).
        ifindex = socket.if_nametoindex(iface)
        mreq = struct.pack("IHH8s", ifindex, PACKET_MR_PROMISC, 0, b"")
        self.sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)

    def close(self):
        self.sock.close()

    def send(self, payload: bytes):
        self.sock.send(_frame(self.mac, payload))

    def wait(self, xid: int, want: set[int], timeout: float) -> dict | None:
        end = time.time() + timeout
        while (left := end - time.time()) > 0:
            r, _, _ = select.select([self.sock], [], [], left)
            if not r:
                return None
            payload = _extract_dhcp(self.sock.recv(4096))
            if payload is None:
                continue
            msg = parse_message(payload)
            if msg and msg["xid"] == xid and msg["type"] in want:
                return msg
        return None


def acquire(iface: str, client_id: str, hostname: str = "", requested_ip: str = "",
            tries: int = 3, timeout: float = 3.0) -> Lease:
    """DISCOVER → OFFER → REQUEST → ACK. `requested_ip` asks for the device's
    previous address so it stays stable across redeploys."""
    ch = _Channel(iface, mac_for(client_id))
    try:
        for _ in range(tries):
            xid = random.getrandbits(32)
            ch.send(build_message(DISCOVER, xid, ch.mac, client_id, hostname, requested_ip))
            offer = ch.wait(xid, {OFFER}, timeout)
            if not offer:
                continue
            sid = socket.inet_ntoa(offer["opts"][54]) if 54 in offer["opts"] else ""
            ch.send(build_message(REQUEST, xid, ch.mac, client_id, hostname, offer["yiaddr"], sid))
            ack = ch.wait(xid, {ACK, NAK}, timeout)
            if ack and ack["type"] == ACK:
                return lease_from(ack)
            requested_ip = ""   # NAK / no answer: let the server pick
        raise DhcpError(f"No DHCP lease on {iface} (is there a DHCP server on that network/VLAN?)")
    finally:
        ch.close()


def renew(iface: str, client_id: str, lease: Lease, hostname: str = "", timeout: float = 3.0) -> Lease | None:
    """Re-request the same address (broadcast, INIT-REBOOT style — works
    without an address on the host). Returns the refreshed lease, or None on
    NAK (the caller must re-acquire)."""
    ch = _Channel(iface, mac_for(client_id))
    try:
        for _ in range(2):
            xid = random.getrandbits(32)
            ch.send(build_message(REQUEST, xid, ch.mac, client_id, hostname, lease.ip))
            r = ch.wait(xid, {ACK, NAK}, timeout)
            if r is None:
                continue
            return lease_from(r) if r["type"] == ACK else None
        raise DhcpError(f"No answer renewing {lease.ip} on {iface}")
    finally:
        ch.close()


def release(iface: str, client_id: str, lease: Lease) -> None:
    ch = _Channel(iface, mac_for(client_id))
    try:
        ch.send(build_message(RELEASE, random.getrandbits(32), ch.mac, client_id,
                              server_id=lease.server_id, ciaddr=lease.ip))
    finally:
        ch.close()


def client_id_for(kind: str, ident: str) -> str:
    """Stable per-device identifier: survives redeploys, unique per host."""
    host = os.uname().nodename
    return f"plabs:{host}:{kind}:{ident}"

"""Packet capture on lab links.

Every cable is two host-side port halves joined on the host (host_net
.apply_patches), so a link can be sniffed on the server without touching the
devices: tshark on one half sees both directions (frames the device sends
arrive on it; frames from the peer leave through it) and every frame type —
LACP, LLDP, STP, IS-IS, BFD included.

One tshark process per capture writes a pcapng (download → Wireshark) and,
at the same time, prints a one-line summary per packet that the UI streams
live. Packet details and display filters run tshark over the file.
Captures live in memory (lost on a backend restart; their files are swept
at startup) and stop when the lab is destroyed.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.services import host_net
from app.services.lab_view import compute_lab_view

logger = logging.getLogger(__name__)

# Not under LAB_STATE_DIR: in the container that's a bind mount from the
# host, where tshark (4.4) can't read back the file its dumpcap child is
# writing ("You don't have permission to read the file"), even as root.
# Captures are temporary anyway (swept at startup, served for download).
CAPTURE_DIR = Path(os.environ.get("PLABS_CAPTURE_DIR") or tempfile.gettempdir()) / "plabs-captures"
MAX_FILE_MB = 100            # tshark stops the capture at this file size
MAX_RUNNING = 8              # concurrent captures on the server
MAX_ROWS = 100_000           # summaries kept in memory per capture (the file has all)
_FIELDS = ["frame.number", "frame.time_relative", "_ws.col.def_src", "_ws.col.def_dst",
           "_ws.col.protocol", "frame.len", "_ws.col.info"]


class CaptureError(Exception):
    pass


@dataclass
class Capture:
    id: str
    topology_id: int
    link_id: int
    username: str
    iface: str
    title: str                 # "R1 Gi0/0/0/0 ↔ R2 Gi0/0/0/0"
    side: str                  # "R1 Gi0/0/0/0" — where the capture point is
    capture_filter: str
    path: Path
    started: float = field(default_factory=time.time)
    ended: float | None = None
    state: str = "running"     # running | stopped | error
    note: str = ""
    rows: list = field(default_factory=list)   # [no, time, src, dst, proto, len, info]
    dropped: int = 0           # rows dropped from memory (MAX_ROWS)
    proc: subprocess.Popen | None = None
    stopping: bool = False
    stderr: list = field(default_factory=list)

    def summary(self) -> dict:
        return {"id": self.id, "topology_id": self.topology_id, "link_id": self.link_id,
                "title": self.title, "side": self.side, "filter": self.capture_filter,
                "state": self.state, "note": self.note, "packets": self.dropped + len(self.rows),
                "bytes": self.path.stat().st_size if self.path.exists() else 0,
                "started": self.started, "ended": self.ended, "username": self.username}


_CAPS: dict[str, Capture] = {}
_LOCK = threading.Lock()


def _tshark() -> str:
    exe = shutil.which("tshark")
    if not exe:
        raise CaptureError("tshark isn't installed on the server (rebuild the backend image, "
                           "or `apt install tshark` on a native install).")
    return exe


def link_endpoint(db: Session, topology_id: int, link_id: int) -> tuple[str, str, str]:
    """(host interface, title, side) for capturing a link: the host half of
    the cable's source device port (the target's when the source is a
    Network node, whose side is a Linux bridge)."""
    view = compute_lab_view(db, topology_id)
    link = next((l for l in view["links"] if l["id"] == link_id), None)
    if not link:
        raise CaptureError("That link isn't part of the running lab (is one of its devices off?).")
    nodes = {n["id"]: n for n in view["nodes"]}
    ends = [(link["source_node_id"], link["source_interface"], link["original_source_interface"]),
            (link["target_node_id"], link["target_interface"], link["original_target_interface"])]
    label = lambda e: f"{nodes[e[0]]['name']} {e[2]}"   # noqa: E731
    title = f"{label(ends[0])} ↔ {label(ends[1])}"
    dev = next((e for e in ends if not nodes[e[0]]["is_bridge"]), None)
    if not dev:
        raise CaptureError("Neither end of that link is a device.")
    n = nodes[dev[0]]
    idx = next((i for i, x in enumerate(n["interfaces"] or []) if x.get("kernel") == dev[1]), None)
    if idx is None:
        raise CaptureError(f"{n['name']} has no port {dev[2]}.")
    iface = host_net.port_name(topology_id, n["id"], idx)
    if not host_net.link_exists(iface):
        raise CaptureError(f"{n['name']} isn't running, so the link has nothing to capture yet.")
    return iface, title, label(dev)


def start(db: Session, topology_id: int, link_id: int, username: str, capture_filter: str = "") -> Capture:
    exe = _tshark()
    capture_filter = (capture_filter or "").strip()
    if len(capture_filter) > 500 or "\n" in capture_filter:
        raise CaptureError("Capture filter too long.")
    iface, title, side = link_endpoint(db, topology_id, link_id)
    with _LOCK:
        if sum(1 for c in _CAPS.values() if c.state == "running") >= MAX_RUNNING:
            raise CaptureError(f"{MAX_RUNNING} captures are already running; stop one first.")
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    cid = secrets.token_hex(6)
    cap = Capture(id=cid, topology_id=topology_id, link_id=link_id, username=username, iface=iface,
                  title=title, side=side, capture_filter=capture_filter,
                  path=CAPTURE_DIR / f"{topology_id}-{cid}.pcapng")
    cmd = [exe, "-i", iface, "-n", "-w", str(cap.path), "-P", "-l",
           "-a", f"filesize:{MAX_FILE_MB * 1024}", "-T", "fields",
           *[a for f in _FIELDS for a in ("-e", f)], "-E", "separator=/t", "-E", "quote=n"]
    if capture_filter:
        cmd += ["-f", capture_filter]
    cap.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                bufsize=1, errors="replace")
    threading.Thread(target=_read_rows, args=(cap,), daemon=True).start()
    threading.Thread(target=_read_err, args=(cap,), daemon=True).start()
    # A bad capture filter or interface makes tshark exit straight away.
    time.sleep(0.4)
    if cap.proc.poll() is not None:
        time.sleep(0.2)
        raise CaptureError(_error_text(cap) or "tshark exited straight away.")
    with _LOCK:
        _CAPS[cid] = cap
    return cap


def _read_rows(cap: Capture) -> None:
    assert cap.proc and cap.proc.stdout
    for line in cap.proc.stdout:
        parts = line.rstrip("\n").split("\t", 6)
        if len(parts) < 7:
            continue
        try:
            no, t, length = int(parts[0]), round(float(parts[1]), 6), int(parts[5] or 0)
        except ValueError:
            continue
        cap.rows.append([no, t, parts[2], parts[3], parts[4], length, parts[6]])
        if len(cap.rows) > MAX_ROWS:
            drop = len(cap.rows) - MAX_ROWS
            del cap.rows[:drop]
            cap.dropped += drop
    rc = cap.proc.wait()
    cap.ended = time.time()
    if cap.stopping:
        cap.state = "stopped"
    elif cap.path.exists() and cap.path.stat().st_size >= MAX_FILE_MB * 1024 * 1024 * 0.98:
        cap.state, cap.note = "stopped", f"Stopped at the {MAX_FILE_MB} MB limit."
    else:
        cap.state = "error" if rc not in (0, None) else "stopped"
        cap.note = _error_text(cap) or "The capture ended (the device was restarted or removed)."


def _read_err(cap: Capture) -> None:
    assert cap.proc and cap.proc.stderr
    for line in cap.proc.stderr:
        line = line.strip()
        if line and not line.startswith(("Running as user", "Capturing on")) and not re.match(r"^\d+ packets? captured", line):
            cap.stderr = (cap.stderr + [line])[-10:]


def _error_text(cap: Capture) -> str:
    return " ".join(cap.stderr)[-400:].replace("tshark: ", "")


def get(cid: str, topology_id: int) -> Capture:
    c = _CAPS.get(cid)
    if not c or c.topology_id != topology_id:
        raise KeyError(cid)
    return c


def list_for(topology_id: int) -> list[Capture]:
    return sorted((c for c in _CAPS.values() if c.topology_id == topology_id), key=lambda c: -c.started)


def stop(cap: Capture, timeout: float = 5.0) -> None:
    p = cap.proc
    if not p or p.poll() is not None:
        return
    cap.stopping = True
    p.send_signal(signal.SIGINT)        # tshark finishes the file cleanly
    try:
        p.wait(timeout)
    except subprocess.TimeoutExpired:
        p.kill()
    for _ in range(20):                 # let the reader thread record the end
        if cap.state != "running":
            break
        time.sleep(0.05)


def delete(cap: Capture) -> None:
    stop(cap)
    with _LOCK:
        _CAPS.pop(cap.id, None)
    cap.path.unlink(missing_ok=True)


def stop_topology(topology_id: int) -> None:
    """The lab is going away: stop its captures (files stay downloadable)."""
    for c in list_for(topology_id):
        try:
            stop(c)
        except Exception:
            logger.exception("Stopping capture %s", c.id)


def sweep_orphans() -> None:
    """At startup: capture files whose capture died with the old process."""
    if CAPTURE_DIR.exists():
        for f in CAPTURE_DIR.glob("*.pcapng"):
            f.unlink(missing_ok=True)


_HEX = re.compile(r"^[0-9a-f]{4,}  ")


def packet_detail(cap: Capture, number: int) -> dict:
    """Wireshark's protocol tree and hex dump for one packet."""
    if not cap.path.exists():
        raise CaptureError("The capture file is gone.")
    r = subprocess.run([_tshark(), "-r", str(cap.path), "-n", "-Y", f"frame.number == {int(number)}",
                        "-c", "1", "-V", "-x"], capture_output=True, text=True, timeout=30, errors="replace")
    lines = r.stdout.splitlines()
    tree = [l for l in lines if l.strip() and not _HEX.match(l)]
    hexdump = "\n".join(l for l in lines if _HEX.match(l))
    if not tree:
        raise CaptureError(f"Packet {number} isn't in the capture file yet.")
    return {"number": number, "tree": tree, "hex": hexdump}


def match(cap: Capture, display_filter: str) -> list[int]:
    """Frame numbers matching a Wireshark display filter."""
    display_filter = display_filter.strip()
    if len(display_filter) > 500 or "\n" in display_filter:
        raise CaptureError("Display filter too long.")
    if not cap.path.exists():
        return []
    r = subprocess.run([_tshark(), "-r", str(cap.path), "-n", "-Y", display_filter, "-T", "fields",
                        "-e", "frame.number"], capture_output=True, text=True, timeout=60, errors="replace")
    # A file still being written ends mid-packet: that's not a filter error.
    if r.returncode != 0 and not r.stdout.strip() and "cut short" not in r.stderr:
        msg = " ".join(l for l in r.stderr.splitlines() if not l.startswith("Running as user"))
        raise CaptureError(msg.replace("tshark: ", "").strip()[:300] or "Invalid display filter.")
    return [int(x) for x in r.stdout.split() if x.isdigit()]

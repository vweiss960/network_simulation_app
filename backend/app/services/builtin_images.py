"""The open-source lab images that ship with Phanero Labs (Linux host, FRR,
traffic generator, automation workstation), built from `device_images/` in
the repo.

At startup a background job builds every one that's missing, and rebuilds
one whose source changed since it was built (a content hash of its directory
is stored as an image label), so a fresh install has them all without anyone
running scripts/build-images.sh, and an update ships new tools. Order matters:
the traffic generator is built FROM plabs/host.
"""
from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from pathlib import Path

from app.config import REPO_ROOT

SOURCE_DIR = REPO_ROOT / "device_images"
LABEL = "plabs.source-hash"

# (image, directory, description, depends on)
BUILTIN: list[tuple[str, str, str, str | None]] = [
    ("plabs/host:latest", "linux-host", "Linux host: ping, traceroute, tcpdump, iperf2/3, snmpwalk", None),
    ("plabs/frr:latest", "frr-lab", "FRRouting with every daemon, plus MACsec / wpa_supplicant MKA", None),
    ("plabs/traffic:latest", "traffic", "Traffic generator (plabs-traffic)", "plabs/host:latest"),
    ("plabs/workstation:latest", "automation-workstation",
     "Automation workstation: Python, ncclient, netmiko, pyATS, Ansible, gnmic", None),
]

_state: dict[str, dict] = {}
_lock = threading.Lock()
_running = False


def source_hash(directory: Path) -> str:
    h = hashlib.sha1()
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(directory)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _image_label(image: str) -> str | None:
    """The image's source hash label, "" if built without one, None if absent."""
    r = subprocess.run(["docker", "image", "inspect", "-f", f'{{{{index .Config.Labels "{LABEL}"}}}}', image],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    v = r.stdout.strip()
    return "" if v in ("", "<no value>") else v


def _set(image: str, **kw) -> None:
    with _lock:
        _state.setdefault(image, {}).update(kw)


def status() -> list[dict]:
    """Every built-in image: present / missing / outdated / queued / building / failed."""
    out = []
    for image, d, desc, dep in BUILTIN:
        src = SOURCE_DIR / d
        with _lock:
            st = dict(_state.get(image, {}))
        if "state" not in st:
            if not src.is_dir():
                st["state"] = "no source"
            else:
                label = _image_label(image)
                st["state"] = ("missing" if label is None
                               else "present" if label in ("", source_hash(src)) else "outdated")
        out.append({"image": image, "directory": d, "description": desc, "depends_on": dep, **st})
    return out


def _build(image: str, directory: str) -> bool:
    src = SOURCE_DIR / directory
    digest = source_hash(src)
    _set(image, state="building", started=time.time(), log="")
    proc = subprocess.Popen(["docker", "build", "--label", f"{LABEL}={digest}", "-t", image, str(src)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail: list[str] = []
    for line in proc.stdout:                       # type: ignore[union-attr]
        tail = (tail + [line.rstrip()])[-40:]
        _set(image, log="\n".join(tail))
    ok = proc.wait() == 0
    _set(image, state="present" if ok else "failed", finished=time.time())
    print(f"[images] {image}: {'built' if ok else 'build FAILED'}", flush=True)
    return ok


def _wanted(force: set[str]) -> list[tuple[str, str]]:
    todo = []
    for image, d, _desc, _parent in BUILTIN:
        src = SOURCE_DIR / d
        if not src.is_dir():
            continue
        label = _image_label(image)
        rebuilt = {i for i, _ in todo}
        if image in force or label is None or (label and label != source_hash(src)) or _dep(image) in rebuilt:
            todo.append((image, d))            # (an image built FROM a rebuilt one is rebuilt too)
    return todo


def _dep(image: str) -> str | None:
    return next((b[3] for b in BUILTIN if b[0] == image), None)


def ensure(force: set[str] | None = None) -> bool:
    """Build (in the background) the built-in images that are missing or
    outdated, plus `force`. False if a build is already running."""
    global _running
    with _lock:
        if _running:
            return False
        _running = True
    force = set(force or ())
    try:
        todo = _wanted(force)
    except Exception as e:                          # noqa: BLE001 — Docker unreachable
        print(f"[images] can't check the built-in images: {e}", flush=True)
        with _lock:
            _running = False
        return True
    for image, _d in todo:
        _set(image, state="queued")

    def job():
        global _running
        failed: set[str] = set()
        try:
            for image, d in todo:
                dep = next(b[3] for b in BUILTIN if b[0] == image)
                if dep in failed:
                    _set(image, state="failed", log=f"not built: {dep} failed to build")
                    failed.add(image)
                    continue
                if not _build(image, d):
                    failed.add(image)
        finally:
            with _lock:
                _running = False

    if todo:
        print(f"[images] building {', '.join(i for i, _ in todo)}", flush=True)
        threading.Thread(target=job, daemon=True, name="builtin-images").start()
    else:
        with _lock:
            _running = False
    return True

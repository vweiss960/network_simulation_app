"""Lab telemetry add-on — backend-orchestrated sidecars.

When the LAB telemetry add-on is enabled (PLABS_LAB_TELEMETRY_ENABLED), Phanero Labs runs a sidecar on the containerlab
`clab` bridge for a deployed lab so it can reach node mgmt IPs directly:

  - gnmic collector (ghcr.io/openconfig/gnmic) — subscribes to each node's gNMI
    (:9339 plaintext) and exposes a Prometheus output on :9273.

The same gnmic image also backs one-shot gNMI path tests (`test_gnmi_path`).
Started on lab deploy, removed on lab destroy. Sidecars carry a
`plabs-lab-addon=<lab_name>` label (NOT the `containerlab=` label) so they stay
out of clab's node inventory and are torn down explicitly here.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.services.gnmic_config_generator import build_gnmic_config, DEFAULT_GNMI_PORT, DEFAULT_USER, DEFAULT_PASS

logger = logging.getLogger("app.lab_addons")

from app.config import LAB_STATE_DIR
from app.services.clab_yaml import MGMT_NETWORK as CLAB_NETWORK  # shared lab mgmt network
ADDON_LABEL = "plabs-lab-addon"

GNMIC_IMAGE = os.environ.get("PLABS_GNMIC_IMAGE", "ghcr.io/openconfig/gnmic:latest")
GNMIC_PROM_HOST_PORT = int(os.environ.get("PLABS_GNMIC_PROM_PORT", "9274"))
LAB_GNMI_USER = os.environ.get("PLABS_LAB_GNMI_USER", DEFAULT_USER)
LAB_GNMI_PASS = os.environ.get("PLABS_LAB_GNMI_PASS", DEFAULT_PASS)

# Kinds whose nodes speak gNMI (so we point the collector only at them).
GNMI_KINDS = {"cisco_xrd", "cisco_xrv9k", "nokia_srlinux", "arista_ceos", "juniper_crpd"}


def addon_enabled() -> bool:
    def _on(name: str) -> bool:
        return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")
    return _on("PLABS_LAB_TELEMETRY_ENABLED")


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except Exception as e:  # pragma: no cover - defensive
        return 1, "", str(e)


def _addon_name(kind: str, lab_name: str) -> str:
    return f"plabs-addon-{kind}-{lab_name}"


def _image_exists(image: str) -> bool:
    rc, _, _ = _run(["docker", "image", "inspect", image], timeout=15)
    return rc == 0


def _rm(name: str) -> None:
    _run(["docker", "rm", "-f", name], timeout=30)


def _gnmi_nodes(nodes: list[dict]) -> list[dict]:
    """Keep only nodes with a mgmt IP and a gNMI-capable kind (or unknown kind)."""
    out = []
    for n in nodes:
        if not (n.get("mgmt_ip") or "").strip():
            continue
        kind = (n.get("kind") or "").strip()
        if kind and kind not in GNMI_KINDS:
            continue
        out.append(n)
    return out


def start_gnmic_collector(lab_name: str, nodes: list[dict],
                          extra_subscriptions: list[dict] | None = None) -> dict:
    """Run the gnmic collector sidecar on the clab bridge for this lab.
    Idempotent — removes any prior instance and reruns, so it doubles as the
    'apply new subscriptions' path."""
    cname = _addon_name("gnmic", lab_name)
    _rm(cname)  # remove any stale instance from a prior deploy
    targets = _gnmi_nodes(nodes)
    if not targets:
        return {"ok": False, "reason": "no gNMI-capable nodes with a mgmt IP yet"}

    if not _image_exists(GNMIC_IMAGE):
        _run(["docker", "pull", GNMIC_IMAGE], timeout=300)
        if not _image_exists(GNMIC_IMAGE):
            return {"ok": False, "reason": f"image {GNMIC_IMAGE} unavailable"}

    cfg_dir = Path(LAB_STATE_DIR) / lab_name / "addons"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "gnmic.yaml"
    cfg_path.write_text(build_gnmic_config(targets, username=LAB_GNMI_USER, password=LAB_GNMI_PASS,
                                           port=DEFAULT_GNMI_PORT,
                                           extra_subscriptions=extra_subscriptions))
    try:
        cfg_path.chmod(0o600)  # contains gNMI credentials
    except OSError:
        pass

    rc, out, err = _run([
        "docker", "run", "-d", "--name", cname,
        "--network", CLAB_NETWORK,
        "--label", f"{ADDON_LABEL}={lab_name}",
        # Published on all host interfaces so Prometheus (on the compose bridge)
        # can reach it via host.docker.internal (the bridge-gateway IP); loopback
        # binding would be unreachable from there. Firewall the host to restrict.
        "-p", f"{GNMIC_PROM_HOST_PORT}:9273",
        "-v", f"{cfg_path}:/gnmic.yaml:ro",
        GNMIC_IMAGE,
        "--config", "/gnmic.yaml", "subscribe",
    ], timeout=120)
    if rc != 0:
        return {"ok": False, "error": (err or out)[-500:]}
    return {"ok": True, "container": cname, "targets": len(targets),
            "metrics": f"http://127.0.0.1:{GNMIC_PROM_HOST_PORT}/metrics"}


def test_gnmi_path(lab_name: str, nodes: list[dict], path: str,
                   target_name: str | None = None, timeout: int = 12) -> dict:
    """One-shot gNMI query of a single lab node to preview what a subscription
    path returns, before adding/applying it. Runs `gnmic subscribe --mode once`
    as a throwaway container on the clab bridge and returns the raw output
    (JSON if the device emits it).

    Creds are passed via GNMIC_* env (not argv) so they don't leak into the
    host process list. Returns {"ok", "target", "address", "path", "output",
    "error"}; ok=False with a "reason" means a pre-flight problem (no nodes,
    image missing), while ok=False with "error" is a device/gNMI-level error.
    """
    path = (path or "").strip()
    if not path:
        return {"ok": False, "reason": "path is required"}
    targets = _gnmi_nodes(nodes)
    if not targets:
        return {"ok": False, "reason": "no gNMI-capable nodes with a mgmt IP yet"}
    node = None
    if target_name:
        node = next((n for n in targets if n.get("name") == target_name), None)
        if node is None:
            return {"ok": False, "reason": f"node '{target_name}' is not a gNMI target"}
    node = node or targets[0]
    ip = (node.get("mgmt_ip") or "").strip()
    addr = f"{ip}:{DEFAULT_GNMI_PORT}"

    if not _image_exists(GNMIC_IMAGE):
        _run(["docker", "pull", GNMIC_IMAGE], timeout=300)
        if not _image_exists(GNMIC_IMAGE):
            return {"ok": False, "reason": f"image {GNMIC_IMAGE} unavailable"}

    rc, out, err = _run([
        "docker", "run", "--rm", "--network", CLAB_NETWORK,
        "-e", f"GNMIC_USERNAME={LAB_GNMI_USER}",
        "-e", f"GNMIC_PASSWORD={LAB_GNMI_PASS}",
        GNMIC_IMAGE,
        "-a", addr, "--insecure", "--encoding", "json_ietf", "--timeout", f"{timeout}s",
        "subscribe", "--mode", "once", "--path", path, "--format", "json",
    ], timeout=timeout + 8)

    out = (out or "").strip()
    err = (err or "").strip()
    ok = rc == 0
    return {
        "ok": ok,
        "target": node.get("name") or ip,
        "address": addr,
        "path": path,
        "output": out,
        "error": "" if ok else (err or out or f"gnmic exited {rc}"),
    }


def start_lab_addons(lab_name: str, nodes: list[dict],
                     extra_subscriptions: list[dict] | None = None) -> dict:
    """Start all lab add-on sidecars. No-op (returns disabled) when the add-on
    flag is off. Never raises — failures are reported per-sidecar so they don't
    break a lab deploy."""
    if not addon_enabled():
        return {"enabled": False}
    result = {"enabled": True}
    try:
        result["gnmic"] = start_gnmic_collector(lab_name, nodes, extra_subscriptions=extra_subscriptions)
    except Exception as e:  # pragma: no cover - defensive
        result["gnmic"] = {"ok": False, "error": str(e)}
    logger.info("lab add-ons for %s: %s", lab_name, result)
    return result


def stop_lab_addons(lab_name: str) -> dict:
    """Remove all sidecars for a lab (by the plabs-lab-addon label)."""
    rc, out, _ = _run(["docker", "ps", "-a",
                       "--filter", f"label={ADDON_LABEL}={lab_name}",
                       "--format", "{{.Names}}"], timeout=30)
    removed = []
    if rc == 0 and out.strip():
        for name in out.split():
            _rm(name)
            removed.append(name)
    return {"removed": removed}

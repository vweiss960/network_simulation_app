"""Service deployment engine — resolve targets, render templates, push config.

Two push paths (chosen by target_mode):
  - "lab"     → SSH to the running containerlab node via its host-published
                port (127.0.0.1:2200x) as clab/clab@123. Requires a running lab.
  - "mgmt_ip" → SSH to the topology node's management IP (:22) with credentials
                supplied in the deploy request (never persisted / logged).

v1 pushes only Cisco XRd (SSH → `configure` → paste → `commit`). Rendering and
preview work for any kind; other kinds return a "preview only" push result.

Reuses:
  - compute_lab_view (node kind / container / ssh port / mgmt ip)
  - lab_service.get_deployment_state (running check + live mgmt ip)
  - lab_addons.LAB_GNMI_USER/PASS (lab creds)
  - the broadened asyncssh algorithm set from discovery._ssh_run
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import asyncssh

from app.services.lab_view import compute_lab_view
from app.services.lab_service import get_deployment_state
from app.services.lab_addons import LAB_GNMI_USER, LAB_GNMI_PASS
from app.services.service_renderer import render_service_template


class ServiceError(Exception):
    """Deploy/render failure with a user-facing message (→ HTTP 400/409)."""


# Network OSes often negotiate legacy SSH algorithms — accept a broad set.
# Mirrors discovery._ssh_run so both push paths dial the same way.
_SSH_CONNECT_KWARGS = dict(
    known_hosts=None,  # lab / user-supplied targets — skip host-key checking
    server_host_key_algs=[
        "ssh-rsa", "rsa-sha2-256", "rsa-sha2-512",
        "ssh-dss", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521", "ssh-ed25519",
    ],
    kex_algs=[
        "diffie-hellman-group14-sha256", "diffie-hellman-group14-sha1",
        "diffie-hellman-group16-sha512", "diffie-hellman-group18-sha512",
        "diffie-hellman-group-exchange-sha256", "diffie-hellman-group-exchange-sha1",
        "diffie-hellman-group1-sha1",
        "ecdh-sha2-nistp256", "ecdh-sha2-nistp384", "ecdh-sha2-nistp521",
        "curve25519-sha256", "curve25519-sha256@libssh.org",
    ],
    encryption_algs=[
        "aes128-ctr", "aes192-ctr", "aes256-ctr",
        "aes128-cbc", "aes192-cbc", "aes256-cbc",
        "aes128-gcm@openssh.com", "aes256-gcm@openssh.com",
        "3des-cbc",
    ],
)

# Substrings that indicate the device rejected the config (case-insensitive).
# The full transcript is always returned, so this is only a coarse pass/fail.
_XRD_ERROR_MARKERS = (
    "invalid input detected", "% invalid", "failed to commit",
    "syntax error", "% error", "config failed", "aborted",
)

PUSHABLE_KINDS = {"cisco_xrd"}


# --------------------------------------------------------------------------
# Variable resolution + rendering
# --------------------------------------------------------------------------

def effective_variables(service, supplied: dict) -> tuple[dict, list[str]]:
    """Merge supplied variables over the service's declared defaults. Returns
    (variables, missing_required) — missing_required lists declared required
    variables that neither the payload nor a default provides."""
    merged: dict = {}
    for v in service.variables:
        if v.default_json not in (None, ""):
            try:
                merged[v.name] = json.loads(v.default_json)
            except (json.JSONDecodeError, TypeError):
                merged[v.name] = v.default_json
    merged.update(supplied or {})
    missing = [v.name for v in service.variables if v.required and v.name not in merged]
    return merged, missing


def _select_templates(service, names: Optional[list]):
    """Templates to render for a deployment: the named ones (in the order given)
    or all templates in position order. Raises ServiceError on an unknown name."""
    by_name = {t.name: t for t in service.templates}
    if not names:
        return list(service.templates)  # already ordered by position
    out = []
    for n in names:
        t = by_name.get(n)
        if t is None:
            raise ServiceError(f"template '{n}' is not part of this service")
        out.append(t)
    return out


def render_deployment(service, deployment: dict, device_meta: dict) -> dict:
    """Render one deployment's templates into a single config string. Returns
    {device, ok, rendered, error}. Aborts on the first template error (no
    partial push)."""
    device = deployment.get("device")
    variables, missing = effective_variables(service, deployment.get("variables") or {})
    if missing:
        return {"device": device, "ok": False, "rendered": "",
                "error": f"missing required variable(s): {', '.join(missing)}"}
    try:
        templates = _select_templates(service, deployment.get("templates"))
    except ServiceError as e:
        return {"device": device, "ok": False, "rendered": "", "error": str(e)}

    parts = []
    for t in templates:
        r = render_service_template(t.body, variables, device_meta)
        if not r["ok"]:
            return {"device": device, "ok": False, "rendered": "",
                    "error": f"template '{t.name}': {r['error']}"}
        parts.append(r["rendered"])
    return {"device": device, "ok": True, "rendered": "\n".join(parts), "error": ""}


# --------------------------------------------------------------------------
# Target resolution
# --------------------------------------------------------------------------

def _resolve_targets(db, topology_id: int) -> dict:
    """Map node name → target facts (kind, container, ssh port/user, mgmt ip)
    from the lab view. Used for both modes (kind/name resolution); the transport
    differs per mode."""
    view = compute_lab_view(db, topology_id)
    targets: dict[str, dict] = {}
    for n in view.get("nodes", []):
        targets[n["name"]] = {
            "name": n["name"], "id": n["id"], "kind": n.get("lab_kind") or "",
            "mgmt_ip": n.get("mgmt_ip") or "",
            "container": n.get("lab_container_name"),
            "ssh_host_port": n.get("lab_ssh_host_port"),
            "ssh_user": n.get("lab_ssh_user"),
        }
    return targets


def resolve_device(db, topology_id: int, device: str, target_mode: str,
                   targets: dict, live_mgmt: dict) -> dict:
    """Resolve a deployment's `device` (a node name) to target facts + the
    `device` namespace exposed to templates. In mgmt_ip mode an unrecognized
    value is treated as a literal IP (kind assumed XRd, the only pushable kind)."""
    t = targets.get(device)
    if t is None:
        if target_mode == "mgmt_ip":
            # Literal IP not matching any node — best-effort XRd target.
            return {"target": {"name": device, "kind": "cisco_xrd", "mgmt_ip": device,
                               "container": None, "ssh_host_port": None, "ssh_user": None},
                    "device_meta": {"name": device, "mgmt_ip": device, "kind": "cisco_xrd", "container": None}}
        raise ServiceError(f"device '{device}' is not a node in topology {topology_id}")
    mgmt_ip = live_mgmt.get(t["name"]) or t["mgmt_ip"]
    return {"target": t,
            "device_meta": {"name": t["name"], "mgmt_ip": mgmt_ip,
                            "kind": t["kind"], "container": t["container"]}}


# --------------------------------------------------------------------------
# SSH push (XRd)
# --------------------------------------------------------------------------

async def _push_xrd_config(host: str, port: int, user: str, password: str,
                           config_text: str, timeout: int = 60) -> dict:
    """Apply config to an IOS-XR node over SSH: enter config mode, paste the
    rendered lines, commit, end, exit. Returns {ok, output, error}; `output` is
    the full session transcript (echo + device responses) for inspection."""
    lines = [ln.rstrip() for ln in (config_text or "").splitlines() if ln.strip()]
    if not lines:
        return {"ok": True, "output": "", "error": ""}
    # terminal length 0 disables paging; end returns to exec; exit closes SSH
    # (→ EOF, so communicate() returns).
    script = "terminal length 0\nconfigure\n" + "\n".join(lines) + "\ncommit\nend\nexit\n"
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(host, port=port, username=user, password=password,
                             connect_timeout=timeout, **_SSH_CONNECT_KWARGS),
            timeout=timeout,
        )
    except Exception as e:  # asyncssh errors, timeouts, refused connections
        return {"ok": False, "output": "", "error": f"SSH connect failed: {e}"}
    try:
        async with conn:
            proc = await conn.create_process(term_type="vt100")
            try:
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(input=script), timeout=timeout)
            except asyncio.TimeoutError:
                proc.terminate()
                return {"ok": False, "output": "", "error": f"push timed out after {timeout}s"}
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "output": "", "error": str(e)}

    transcript = stdout or ""
    lowered = transcript.lower()
    bad = next((m for m in _XRD_ERROR_MARKERS if m in lowered), None)
    return {"ok": bad is None, "output": transcript,
            "error": "" if bad is None else f"device reported an error ('{bad}') — see output"}


def _push_params(target: dict, target_mode: str, credentials: Optional[dict]) -> tuple[Optional[dict], Optional[str]]:
    """Pick SSH host/port/creds for a target based on the mode. Returns
    (params, error)."""
    if target_mode == "lab":
        if not target.get("ssh_host_port"):
            return None, "node has no lab SSH port (not an SSH-capable kind, or lab not deployed)"
        return {"host": "127.0.0.1", "port": target["ssh_host_port"],
                "user": target.get("ssh_user") or LAB_GNMI_USER,
                "password": LAB_GNMI_PASS}, None
    # mgmt_ip
    ip = target.get("mgmt_ip")
    if not ip:
        return None, "node has no management IP set"
    if not credentials or not credentials.get("username"):
        return None, "credentials (username/password) required for mgmt_ip mode"
    return {"host": ip, "port": 22, "user": credentials["username"],
            "password": credentials.get("password", "")}, None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def preview_service(db, service, topology_id: int, target_mode: str, deployments: list) -> list:
    """Render every deployment without pushing. Returns per-device
    {device, ok, rendered, error}. Does not require a running lab."""
    targets = _resolve_targets(db, topology_id)
    out = []
    for d in deployments:
        try:
            res = resolve_device(db, topology_id, d.get("device"), target_mode, targets, live_mgmt={})
        except ServiceError as e:
            out.append({"device": d.get("device"), "ok": False, "rendered": "", "error": str(e)})
            continue
        out.append(render_deployment(service, d, res["device_meta"]))
    return out


async def deploy_service(db, service, topology_id: int, target_mode: str,
                         deployments: list, credentials: Optional[dict]) -> list:
    """Render + push every deployment. Returns per-device
    {device, ok, transport, pushed, rendered, output, error}."""
    targets = _resolve_targets(db, topology_id)
    live_mgmt: dict = {}
    if target_mode == "lab":
        state = get_deployment_state(db, topology_id)
        if state.get("status") != "running":
            raise ServiceError(f"no running lab for topology {topology_id} — deploy it first")
        live_mgmt = {c.get("name"): c.get("mgmt_ip") for c in state.get("containers", [])}

    results = []
    for d in deployments:
        device = d.get("device")
        try:
            res = resolve_device(db, topology_id, device, target_mode, targets, live_mgmt)
        except ServiceError as e:
            results.append({"device": device, "ok": False, "transport": target_mode,
                            "pushed": False, "rendered": "", "output": "", "error": str(e)})
            continue
        target, device_meta = res["target"], res["device_meta"]

        rendered = render_deployment(service, d, device_meta)
        if not rendered["ok"]:
            results.append({"device": device, "ok": False, "transport": target_mode,
                            "pushed": False, "rendered": "", "output": "", "error": rendered["error"]})
            continue

        if target.get("kind") not in PUSHABLE_KINDS:
            results.append({"device": device, "ok": False, "transport": target_mode,
                            "pushed": False, "rendered": rendered["rendered"], "output": "",
                            "error": f"push not implemented for kind '{target.get('kind')}' (preview only in v1)"})
            continue

        params, perr = _push_params(target, target_mode, credentials)
        if perr:
            results.append({"device": device, "ok": False, "transport": target_mode,
                            "pushed": False, "rendered": rendered["rendered"], "output": "", "error": perr})
            continue

        push = await _push_xrd_config(params["host"], params["port"], params["user"],
                                      params["password"], rendered["rendered"])
        results.append({"device": device, "ok": push["ok"], "transport": target_mode,
                        "pushed": True, "rendered": rendered["rendered"],
                        "output": push["output"], "error": push["error"]})
    return results

"""Device access layer: talk to lab devices the way an engineer (or the lab
agent) would — CLI over SSH, configuration with commit/abort semantics,
running-config, NETCONF, RESTCONF and gNMI against the device's management IP.

Linux-kind devices (FRR, hosts, the automation workstation) have no SSH
server in the image, so their CLI goes through `docker exec`.

Every entry point takes a resolved `Target` (see `target_for`) so callers —
REST routes, snapshots, the grader, the AI agent — share one code path.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass

import asyncssh
from sqlalchemy.orm import Session

from app.models.node_instance import NodeInstance
from app.services.device_kinds import get_kind, is_bridge
from app.services.lab_view import compute_lab_view, node_credentials


class ExecError(Exception):
    """User-facing failure talking to a device."""


@dataclass
class Target:
    node_id: int
    name: str
    kind: str
    family: str
    host: str
    username: str
    password: str
    container: str | None
    image: str = ""

    @property
    def uses_ssh(self) -> bool:
        return self.family not in ("linux",)


SSH_OPTS = dict(
    known_hosts=None,   # lab devices regenerate host keys on every boot
    server_host_key_algs=["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
                          "rsa-sha2-512", "rsa-sha2-256", "ssh-rsa"],
    kex_algs=["curve25519-sha256", "curve25519-sha256@libssh.org", "ecdh-sha2-nistp256", "ecdh-sha2-nistp384",
              "ecdh-sha2-nistp521", "diffie-hellman-group16-sha512", "diffie-hellman-group14-sha256",
              "diffie-hellman-group-exchange-sha256", "diffie-hellman-group14-sha1",
              "diffie-hellman-group-exchange-sha1", "diffie-hellman-group1-sha1"],
    encryption_algs=["aes128-gcm@openssh.com", "aes256-gcm@openssh.com", "aes128-ctr", "aes192-ctr",
                     "aes256-ctr", "aes128-cbc", "aes256-cbc", "3des-cbc"],
)

# Prompt at the end of the buffer: XR "RP/0/RP0/CPU0:R1#", "...(config-if)#",
# IOS/XE/EOS "R1#", "R1>", "R1(config)#".
_PROMPT = re.compile(r"(?:^|[\r\n])([\w./:@\-]+(?:\([\w\-./]+\))?[#>])\s*$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Confirmation questions, e.g. XR "commit replace": "Do you wish to proceed? [no]:"
_QUESTION = re.compile(r"(\[(?:yes|no|confirm)\]|\((?:yes|no)[^)]*\))\s*[:?]?\s*$", re.I)

_PAGER_OFF = {
    "iosxr": ["terminal length 0", "terminal width 0"],
    "iosxe": ["terminal length 0", "terminal width 0"],
    "ios": ["terminal length 0", "terminal width 0"],
    "eos": ["terminal length 0", "terminal width 32767"],
}
_RUNNING_CONFIG = {
    "iosxr": "show running-config",
    "iosxe": "show running-config",
    "ios": "show running-config",
    "eos": "show running-config",
    "junos": "show configuration | display set",
}
_CLI_ERRORS = re.compile(r"^\s*%\s*(Invalid input|Incomplete command|Ambiguous command|Unknown command|"
                         r"Invalid|Error|Failed)", re.M)


# --- target resolution -------------------------------------------------------

def container_ip(container: str) -> str:
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container],
            capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.split() or [""])[0]


def target_for(db: Session, topology_id: int, node_id: int) -> Target:
    n = db.get(NodeInstance, node_id)
    if not n or n.topology_id != topology_id:
        raise ExecError("Device not found")
    if is_bridge(n.kind):
        raise ExecError(f"{n.name} is a network, not a device")
    view = next((v for v in compute_lab_view(db, topology_id)["nodes"] if v["id"] == node_id), None)
    if not view:
        raise ExecError(f"{n.name} isn't deployable (no image)")
    k = get_kind(n.kind)
    user, pw = node_credentials(n)
    host = n.mgmt_ipv4 or container_ip(view["lab_container_name"])
    if not host and k.family != "linux":
        raise ExecError(f"{n.name} has no management IP — is the lab deployed and the device running?")
    return Target(node_id=n.id, name=n.name, kind=n.kind, family=k.family, host=host, username=user,
                  password=pw, container=view["lab_container_name"], image=n.image_ref or "")


# --- docker exec (linux kinds) -------------------------------------------------

def _docker_exec(t: Target, argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(["docker", "exec", t.container, *argv], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ExecError(f"{t.name}: command timed out after {timeout}s")
    return p.returncode, (p.stdout + p.stderr)


def _is_frr(t: Target) -> bool:
    return "frr" in t.image.lower()


# --- interactive SSH CLI ---------------------------------------------------------

class CliSession:
    """One interactive shell on a network OS: send a line, read until the
    next prompt."""

    def __init__(self, t: Target, timeout: float = 30):
        self.t, self.timeout = t, timeout
        self.conn = None
        self.proc = None
        self.prompt = ""

    async def __aenter__(self):
        try:
            self.conn = await asyncio.wait_for(
                asyncssh.connect(self.t.host, username=self.t.username, password=self.t.password,
                                 connect_timeout=self.timeout, **SSH_OPTS),
                timeout=self.timeout + 5)
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
            raise ExecError(f"{self.t.name}: SSH to {self.t.host} failed ({e.__class__.__name__}: {e}). "
                            f"The device may still be booting.")
        self.proc = await self.conn.create_process(term_type="vt100", term_size=(250, 100))
        await self._read_until_prompt(first=True)
        for cmd in _PAGER_OFF.get(self.t.family, []):
            await self.send(cmd)
        return self

    async def __aexit__(self, *exc):
        try:
            self.proc.stdin.write("exit\n")
        except Exception:
            pass
        self.conn.close()

    async def _read_until_prompt(self, first: bool = False, timeout: float | None = None,
                                 questions: bool = False) -> str:
        buf = ""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout or self.timeout)
        while True:
            left = deadline - loop.time()
            if left <= 0:
                raise ExecError(f"{self.t.name}: timed out waiting for the prompt")
            try:
                chunk = await asyncio.wait_for(self.proc.stdout.read(65536), timeout=min(left, 2.0 if first else left))
            except asyncio.TimeoutError:
                if first and not buf:
                    self.proc.stdin.write("\n")    # some devices need a nudge
                continue
            if not chunk:
                raise ExecError(f"{self.t.name}: connection closed")
            buf += chunk
            clean = _ANSI.sub("", buf).replace("\r", "")
            m = _PROMPT.search(clean)
            if m:
                self.prompt = m.group(1)
                return clean[: m.start(1)]
            if questions and _QUESTION.search(clean):
                self.prompt = ""
                return clean

    async def send(self, line: str, timeout: float | None = None, questions: bool = False) -> str:
        """Send a line, return its output. With questions=True a yes/no
        prompt also ends the read (check `self.prompt == ""`)."""
        self.proc.stdin.write(line + "\n")
        out = await self._read_until_prompt(timeout=timeout, questions=questions)
        lines = out.split("\n")
        if lines and line.strip() and line.strip()[:40] in lines[0]:
            lines = lines[1:]                       # drop the echoed command
        return "\n".join(lines).strip("\n")


async def run_cli(t: Target, commands: list[str], timeout: float = 60) -> list[dict]:
    """Run operational commands; returns [{command, output}]."""
    if not t.uses_ssh:
        out = []
        for cmd in commands:
            argv = ["vtysh", "-c", cmd] if _is_frr(t) and not cmd.startswith(("sh ", "ip ", "ping ")) \
                else ["sh", "-c", cmd]
            _rc, text = await asyncio.to_thread(_docker_exec, t, argv, int(timeout))
            out.append({"command": cmd, "output": text.rstrip()})
        return out
    async with CliSession(t, timeout) as s:
        return [{"command": c, "output": await s.send(c, timeout)} for c in commands]


def _config_lines(config: str | list[str]) -> list[str]:
    lines = config.splitlines() if isinstance(config, str) else list(config)
    return [l.rstrip() for l in lines if l.strip() and l.strip() not in ("end", "configure", "configure terminal",
                                                                          "conf t", "commit")]


async def configure(t: Target, config: str | list[str], timeout: float = 120, replace: bool = False) -> dict:
    """Apply configuration. IOS-XR: candidate + commit (aborted on any error);
    IOS-XE/IOS/EOS: line by line (errors reported, applied lines stay);
    FRR: vtysh. `replace` (IOS-XR only) replaces the whole running config."""
    lines = _config_lines(config)
    if not t.uses_ssh:
        if not _is_frr(t):
            raise ExecError(f"{t.name}: configuration push isn't supported for plain Linux devices — use run commands")
        argv = ["vtysh", "-c", "configure terminal", *sum((["-c", l] for l in lines), []), "-c", "end",
                "-c", "write memory"]
        rc, text = await asyncio.to_thread(_docker_exec, t, argv, int(timeout))
        errs = [l for l in text.splitlines() if l.startswith("%") or "Unknown command" in l]
        return {"ok": rc == 0 and not errs, "errors": errs, "output": text.strip()}
    if t.family not in ("iosxr", "iosxe", "ios", "eos"):
        raise ExecError(f"{t.name}: configuration push isn't implemented for {t.family} yet")
    transcript, errors = [], []
    async with CliSession(t, timeout) as s:
        transcript.append(await s.send("configure terminal"))
        for l in lines:
            out = await s.send(l)
            if out:
                transcript.append(f"{l}\n{out}")
            if _CLI_ERRORS.search(out):
                errors.append(f"{l.strip()}: {out.strip().splitlines()[-1] if out.strip() else 'error'}")
        if t.family == "iosxr":
            if errors:
                transcript.append(await s.send("abort"))
            else:
                out = await s.send("commit replace" if replace else "commit", timeout=timeout, questions=True)
                if not s.prompt:                      # "commit replace" asks to confirm
                    out += "\n" + await s.send("yes", timeout=timeout)
                transcript.append(out)
                if re.search(r"Failed to commit|% ?Failed|!!%", out):
                    errors.append(out.strip())
                    transcript.append(await s.send("show configuration failed"))
                    transcript.append(await s.send("abort"))
                else:
                    transcript.append(await s.send("end"))
        else:
            transcript.append(await s.send("end"))
    return {"ok": not errors, "errors": errors, "output": "\n".join(x for x in transcript if x)}


def ping_command(family: str, target: str, count: int = 5, source: str | None = None) -> str:
    """The ping command for an OS family (count / source spelled its way)."""
    if family == "iosxr":
        return f"ping {target} count {count}" + (f" source {source}" if source else "")
    if family in ("iosxe", "ios", "eos"):
        return f"ping {target} repeat {count}" + (f" source {source}" if source else "")
    return f"ping -c {count} -W 1 {target}" + (f" -I {source}" if source else "")


_SUCCESS_RATE = re.compile(r"Success rate is (\d+) percent")
_PKT_LOSS = re.compile(r"(\d+(?:\.\d+)?)% packet loss")


def ping_success(output: str) -> int | None:
    """Percent of pings answered, from Cisco or Linux ping output (None if unknown)."""
    m = _SUCCESS_RATE.search(output)
    if m:
        return int(m.group(1))
    m = _PKT_LOSS.search(output)
    if m:
        return round(100 - float(m.group(1)))
    return None


async def save_config(t: Target, timeout: float = 120) -> str:
    """Make the running config the device's own startup config, so it comes
    back after the device is stopped and started. Returns what was done."""
    if not t.uses_ssh:
        if not _is_frr(t):
            return "nothing to save (plain Linux)"
        rc, text = await asyncio.to_thread(_docker_exec, t, ["vtysh", "-c", "write memory"], int(timeout))
        if rc != 0:
            raise ExecError(f"{t.name}: write memory failed: {text.strip()[-200:]}")
        return "write memory"
    if t.family == "iosxr":
        return "already saved (IOS-XR commits persist)"
    if t.family not in ("iosxe", "ios", "eos"):
        raise ExecError(f"{t.name}: saving the config isn't implemented for {t.family}")
    async with CliSession(t, timeout) as s:
        out = await s.send("write memory", timeout=timeout, questions=True)
        if not s.prompt:                        # "[confirm]" / overwrite questions
            out += "\n" + await s.send("", timeout=timeout)
    if re.search(r"%\s*(Error|Invalid|Failed)|\[FAILED\]", out):
        raise ExecError(f"{t.name}: write memory failed: {out.strip().splitlines()[-1]}")
    return "write memory"


def normalize_config(family: str, text: str) -> str:
    """Strip banners / timestamps a device prints around its running config."""
    out = []
    for line in text.splitlines():
        if family == "iosxr" and (line.lstrip("! ").startswith("Building configuration") or
                                  re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \w{3} +\d+ ", line) or
                                  line.startswith("!! Last configuration change")):
            continue
        if family in ("iosxe", "ios") and (line.startswith(("Building configuration", "Current configuration"))):
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


async def running_config(t: Target, timeout: float = 60) -> str:
    if not t.uses_ssh:
        if not _is_frr(t):
            raise ExecError(f"{t.name}: plain Linux devices have no running config")
        _rc, text = await asyncio.to_thread(_docker_exec, t, ["vtysh", "-c", "show running-config"], int(timeout))
        return text
    cmd = _RUNNING_CONFIG.get(t.family)
    if not cmd:
        raise ExecError(f"{t.name}: running-config isn't implemented for {t.family}")
    (res,) = await run_cli(t, [cmd], timeout)
    return normalize_config(t.family, res["output"])


# --- NETCONF ---------------------------------------------------------------------

def _netconf(t: Target):
    from ncclient import manager
    try:
        return manager.connect(host=t.host, port=830, username=t.username, password=t.password,
                               hostkey_verify=False, look_for_keys=False, allow_agent=False, timeout=30,
                               device_params={"name": {"iosxr": "iosxr", "iosxe": "iosxe"}.get(t.family, "default")})
    except Exception as e:
        raise ExecError(f"{t.name}: NETCONF to {t.host}:830 failed: {e}")


def netconf(t: Target, op: str, *, filter_xml: str = "", config_xml: str = "", datastore: str = "",
            commit: bool = True) -> dict:
    """op: capabilities | get | get-config | edit-config. Blocking — call via
    asyncio.to_thread from async code."""
    from ncclient.operations import RPCError
    try:
        return _netconf_op(t, op, filter_xml, config_xml, datastore, commit)
    except RPCError as e:
        raise ExecError(f"{t.name}: NETCONF {op} rejected by the device: {e.message or e} "
                        f"[{e.tag or ''}{' at ' + e.path if getattr(e, 'path', None) else ''}]")


def _netconf_op(t: Target, op: str, filter_xml: str, config_xml: str, datastore: str, commit: bool) -> dict:
    with _netconf(t) as m:
        if op == "capabilities":
            return {"capabilities": sorted(m.server_capabilities)}
        if op in ("get", "get-config"):
            flt = ("subtree", filter_xml) if filter_xml.strip() else None
            r = m.get(filter=flt) if op == "get" else m.get_config(source=datastore or "running", filter=flt)
            return {"xml": r.data_xml}
        if op == "edit-config":
            caps = " ".join(m.server_capabilities)
            target = datastore or ("candidate" if ":candidate" in caps else "running")
            r = m.edit_config(target=target, config=config_xml)
            committed = False
            if target == "candidate" and commit:
                m.commit()
                committed = True
            return {"ok": r.ok, "target": target, "committed": committed}
    raise ExecError(f"Unknown NETCONF operation '{op}'")


# --- RESTCONF ----------------------------------------------------------------------

def restconf(t: Target, method: str, path: str, body: dict | str | None = None) -> dict:
    import requests
    import urllib3
    urllib3.disable_warnings()
    url = f"https://{t.host}/restconf/{path.lstrip('/')}"
    if path.startswith("/restconf"):
        url = f"https://{t.host}{path}"
    headers = {"Accept": "application/yang-data+json", "Content-Type": "application/yang-data+json"}
    data = json.dumps(body) if isinstance(body, (dict, list)) else body
    try:
        r = requests.request(method.upper(), url, auth=(t.username, t.password), headers=headers, data=data,
                             verify=False, timeout=30)
    except requests.RequestException as e:
        raise ExecError(f"{t.name}: RESTCONF to {t.host} failed: {e}")
    try:
        payload = r.json() if r.content else None
    except ValueError:
        payload = r.text
    return {"status": r.status_code, "body": payload}


# --- gNMI -------------------------------------------------------------------------

def gnmi(t: Target, op: str, *, paths: list[str] | None = None, update: list | None = None,
         replace: list | None = None, delete: list[str] | None = None, encoding: str = "json_ietf") -> dict:
    """op: capabilities | get | set. Insecure (no TLS) to the kind's gNMI port."""
    from pygnmi.client import gNMIclient
    port = get_kind(t.kind).services.get("gnmi", 57400)
    try:
        with gNMIclient(target=(t.host, port), username=t.username, password=t.password,
                        insecure=True, timeout=20) as gc:
            if op == "capabilities":
                return gc.capabilities()
            if op == "get":
                return gc.get(path=paths or [], encoding=encoding)
            if op == "set":
                # [[path, value], ...] from JSON → [(path, value), ...] for pygnmi
                as_tuples = lambda xs: [tuple(x) for x in xs] if xs else None  # noqa: E731
                return gc.set(update=as_tuples(update), replace=as_tuples(replace), delete=delete or None,
                              encoding=encoding)
    except Exception as e:
        raise ExecError(f"{t.name}: gNMI to {t.host}:{port} failed: {e}")
    raise ExecError(f"Unknown gNMI operation '{op}'")

"""Lesson task checks: assertions run against the live devices of a lab.

Check types (one dict each, in a task's `checks` list):

  cli     {device, command, expect: [...]}           output of a command
  route   {device, prefix, protocol?, present?,      a route in the table (protocol e.g. "O IA", "O E2");
           metric?, via?}                            IPv4 or IPv6 (from the prefix); metric = the [AD/metric]
                                                     cost, via = text in the entry (next hop / interface)
  ping    {from, to, count?, source?, min_success?}  percent of pings answered
  config  {device, expect: [...]}                    the running config

`expect` items: {contains | not_contains | regex | not_regex: str,
count?: int (at least this many matches), ignore_case?: bool}.
Every check may carry `describe` (shown to the learner); one is generated
otherwise. Every device command goes through device_exec.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import time

from sqlalchemy.orm import Session

from app.models.node_instance import NodeInstance
from app.services import device_exec
from app.services.device_kinds import is_bridge

CHECK_TYPES = ("cli", "route", "ping", "config")
EXPECT_KEYS = ("contains", "not_contains", "regex", "not_regex")
EXCERPT = 1500
CHECK_TIMEOUT = 90


# --- validation (import / editor) ------------------------------------------------

def check_devices(c: dict) -> list[str]:
    return [d for d in ([c.get("from")] if c.get("type") == "ping" else [c.get("device")]) if d]


def validate_check(c: dict, devices: set[str] | None = None) -> list[str]:
    """Problems with one check (empty when fine). `devices`: names in the
    lesson topology (case-insensitive), to catch typos."""
    errs = []
    t = c.get("type")
    if t not in CHECK_TYPES:
        return [f"unknown check type {t!r} (use {', '.join(CHECK_TYPES)})"]
    if t == "ping":
        if not c.get("from") or not c.get("to"):
            errs.append("ping needs 'from' and 'to'")
    elif not c.get("device"):
        errs.append(f"{t} needs 'device'")
    if t == "cli" and not str(c.get("command") or "").strip():
        errs.append("cli needs 'command'")
    if t == "route":
        if not c.get("prefix"):
            errs.append("route needs 'prefix'")
        elif _net(str(c["prefix"])) is None:
            errs.append(f"route prefix {c['prefix']!r} isn't an IPv4 / IPv6 prefix")
        if c.get("metric") is not None:
            try:
                int(c["metric"])
            except (TypeError, ValueError):
                errs.append("route 'metric' must be a number")
    if t in ("cli", "config"):
        exp = c.get("expect")
        if not isinstance(exp, list) or not exp:
            errs.append(f"{t} needs a non-empty 'expect' list")
        else:
            for e in exp:
                keys = [k for k in EXPECT_KEYS if k in (e or {})]
                if len(keys) != 1:
                    errs.append(f"each expect item needs exactly one of {', '.join(EXPECT_KEYS)}")
                    continue
                if keys[0] in ("regex", "not_regex"):
                    try:
                        re.compile(str(e[keys[0]]))
                    except re.error as ex:
                        errs.append(f"bad regex {e[keys[0]]!r}: {ex}")
    if devices is not None:
        low = {d.lower() for d in devices}
        for d in check_devices(c):
            if str(d).lower() not in low:
                errs.append(f"device {d!r} isn't in the lesson topology")
    return errs


def describe(c: dict) -> str:
    if c.get("describe"):
        return str(c["describe"])
    t = c.get("type")
    if t == "cli":
        return f"{c.get('device')}: {c.get('command')}"
    if t == "route":
        return f"{c.get('device')}: route {_route_what(c)} {'present' if c.get('present', True) else 'absent'}"
    if t == "ping":
        return f"{c.get('from')}: ping {c.get('to')}"
    if t == "config":
        return f"{c.get('device')}: running config"
    return str(t)


# --- evaluation ------------------------------------------------------------------

def eval_expect(text: str, expect: list[dict]) -> tuple[bool, str]:
    """All expectations hold? Returns (ok, reason of the first failure)."""
    for e in expect:
        flags = re.M | (re.I if e.get("ignore_case") else 0)
        need = int(e.get("count") or 1)
        if "contains" in e:
            s = str(e["contains"])
            n = text.lower().count(s.lower()) if e.get("ignore_case") else text.count(s)
            if n < need:
                return False, f"expected {s!r}" + (f" at least {need}×, found {n}" if need > 1 else "")
        elif "not_contains" in e:
            s = str(e["not_contains"])
            if (s.lower() in text.lower()) if e.get("ignore_case") else (s in text):
                return False, f"didn't expect {s!r}"
        elif "regex" in e:
            n = len(re.findall(str(e["regex"]), text, flags))
            if n < need:
                return False, f"expected to match /{e['regex']}/" + (f" at least {need}×, found {n}" if need > 1 else "")
        elif "not_regex" in e:
            if re.search(str(e["not_regex"]), text, flags):
                return False, f"didn't expect to match /{e['not_regex']}/"
    return True, ""


_ROUTE_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9*> ]{0,10}?)\s+([0-9A-Fa-f.:]+/\d{1,3})(?=[\s,\[]|$)")
_NO_MASK_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9*> ]{0,10}?)\s+(\d{1,3}(?:\.\d{1,3}){3})(?=\s|\[|,|$)")
_SUBNETTED = re.compile(r"^\s+\d{1,3}(?:\.\d{1,3}){3}/(\d{1,2}) is subnetted")
_METRIC = re.compile(r"\[(\d+)/(\d+)\]")


def _net(prefix: str):
    try:
        return ipaddress.ip_network(prefix.strip(), strict=False)
    except ValueError:
        return None


def _code(s: str) -> str:
    """Protocol code without spaces / selection marks; IOS-XE's IPv6 "OI"
    (inter-area) is the same thing as "O IA"."""
    c = re.sub(r"[\s*>]", "", s).upper()
    return "OIA" if c == "OI" else c


def _route_what(c: dict) -> str:
    what = f"{c.get('protocol') + ' ' if c.get('protocol') else ''}{c.get('prefix')}"
    if c.get("metric") is not None:
        what += f" metric {c['metric']}"
    if c.get("via"):
        what += f" via {c['via']}"
    return what


def route_entries(table: str) -> list[tuple[str, str, str]]:
    """(code, prefix, entry text) per route of a routing table (IOS-XR /
    IOS-XE / FRR, IPv4 or IPv6). The entry includes its continuation lines
    ("[110/3] via …" on the next lines: IPv6 tables, ECMP paths)."""
    out: list[list[str]] = []
    mask = None             # IOS-XE: "3.0.0.0/32 is subnetted, 1 subnets" → its routes print no mask
    for line in table.splitlines():
        h = _SUBNETTED.match(line)
        if h:
            mask = h.group(1)
            continue
        if re.match(r"^\s+\S+ is variably subnetted", line):
            mask = None
            continue
        m = _ROUTE_LINE.match(line)
        if m and _net(m.group(2)) is not None:
            out.append([m.group(1), m.group(2), line.strip()])
            continue
        m = _NO_MASK_LINE.match(line)
        if m and mask:
            out.append([m.group(1), f"{m.group(2)}/{mask}", line.strip()])
        elif out and re.match(r"^\s+(\[|via\b)", line):
            out[-1][2] += "\n" + line.strip()
    return [(a, b, c) for a, b, c in out]


def find_routes(table: str, prefix: str, protocol: str | None = None,
                metric: int | None = None, via: str | None = None) -> list[str]:
    """Entries of a routing table for `prefix` whose protocol code matches
    (`O IA` matches "O IA", "O*IA", "OI"…), with that metric and next hop."""
    want = _net(prefix)
    out = []
    for code, pfx, entry in route_entries(table):
        if want is None or _net(pfx) != want:
            continue
        if protocol and _code(code) != _code(protocol):
            continue
        if metric is not None and int(metric) not in {int(m.group(2)) for m in _METRIC.finditer(entry)}:
            continue
        if via and str(via).lower() not in entry.lower():
            continue
        out.append(entry)
    return out


def route_command(family: str, image: str = "", ipv6: bool = False) -> str:
    if family == "iosxr":
        return "show route ipv6" if ipv6 else "show route ipv4"
    if family in ("iosxe", "ios", "eos"):
        return "show ipv6 route" if ipv6 else "show ip route"
    if "frr" in (image or "").lower():
        return "show ipv6 route" if ipv6 else "show ip route"
    return "ip -6 route" if ipv6 else "ip route"


def _excerpt(text: str) -> str:
    text = text.strip()
    return text if len(text) <= EXCERPT else text[:EXCERPT] + "\n…"


def _command(c: dict, t) -> str:
    """The device command a check needs."""
    kind = c.get("type")
    if kind == "cli":
        return str(c["command"])
    if kind == "route":
        net = _net(str(c["prefix"]))
        return route_command(t.family, t.image, ipv6=bool(net and net.version == 6))
    if kind == "ping":
        return device_exec.ping_command(t.family, str(c["to"]), int(c.get("count") or 5), c.get("source"))
    if kind == "config":
        if t.family == "linux":
            return "show running-config"            # FRR: vtysh
        cmd = device_exec._RUNNING_CONFIG.get(t.family)
        if not cmd:
            raise device_exec.ExecError(f"{t.name}: running config isn't supported for {t.family}")
        return cmd
    raise ValueError(f"unknown check type {kind!r}")


def _evaluate(c: dict, t, output: str) -> dict:
    """Judge one check from its command's output."""
    res = {"ok": False, "describe": describe(c), "detail": "", "output": ""}
    kind = c.get("type")
    if kind == "cli":
        res["output"] = _excerpt(output)
        res["ok"], res["detail"] = eval_expect(output, c.get("expect") or [])
    elif kind == "config":
        cfg = device_exec.normalize_config(t.family, output) if t.family != "linux" else output
        res["output"] = _excerpt(cfg)
        res["ok"], res["detail"] = eval_expect(cfg, c.get("expect") or [])
    elif kind == "route":
        metric = int(c["metric"]) if c.get("metric") is not None else None
        lines = find_routes(output, str(c["prefix"]), c.get("protocol"), metric, c.get("via"))
        want = c.get("present", True) is not False
        what = _route_what(c)
        res["ok"] = bool(lines) == want
        if lines:
            res["output"] = "\n".join(lines)
        else:   # show what IS there for that prefix, if anything
            near = find_routes(output, str(c["prefix"]))
            res["output"] = "\n".join(near) if near else _excerpt(output)
        if not res["ok"]:
            res["detail"] = f"no {what} route in the table" if want else f"{what} is still in the table"
    elif kind == "ping":
        res["output"] = _excerpt(output)
        pct = device_exec.ping_success(output)
        need = int(c.get("min_success") or 80)
        res["ok"] = pct is not None and pct >= need
        if not res["ok"]:
            res["detail"] = f"{pct}% of pings answered (need {need}%)" if pct is not None else "ping didn't run"
    return res


async def _run_device(t, cmds: list[str]) -> dict[str, str]:
    """Every command for one device in ONE session (network OSes rate-limit
    and reset parallel SSH logins). One retry if the connection is refused."""
    for attempt in (1, 2):
        try:
            out = await device_exec.run_cli(t, cmds, timeout=60)
            return {r["command"]: r["output"] for r in out}
        except device_exec.ExecError as e:
            if attempt == 2 or not re.search(r"reset|refused|closed|timed out", str(e), re.I):
                raise
            await asyncio.sleep(4)
    return {}


def run_checks(db: Session, topology_id: int, checks: list[dict]) -> list[dict]:
    """Run checks against the topology's lab. One result per check, in order.
    Devices are queried in parallel, each over a single session."""
    from app.services import lab_service
    state = lab_service.get_deployment_state(db, topology_id)
    nodes = {n.name.lower(): n for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id)}
    targets, unavailable = {}, {}
    for c in checks:
        for d in check_devices(c):
            key = str(d).lower()
            if key in targets or key in unavailable:
                continue
            n = nodes.get(key)
            if not n or is_bridge(n.kind):
                continue
            rt = state["nodes"].get(n.id) or {}
            if rt.get("state") != "running":
                unavailable[key] = f"{n.name} isn't running — start it first."
                continue
            try:
                targets[key] = device_exec.target_for(db, topology_id, n.id)
            except device_exec.ExecError as e:
                unavailable[key] = str(e)

    results: list[dict | None] = [None] * len(checks)
    plan: dict[str, list[tuple[int, str]]] = {}          # device → [(check index, command)]
    for i, c in enumerate(checks):
        dev = next(iter(check_devices(c)), None)
        key = str(dev or "").lower()
        base = {"ok": False, "describe": describe(c), "detail": "", "output": ""}
        if key in unavailable:
            results[i] = {**base, "detail": unavailable[key]}
        elif key not in targets:
            results[i] = {**base, "detail": f"No device named {dev!r} in this lab."}
        else:
            try:
                plan.setdefault(key, []).append((i, _command(c, targets[key])))
            except (KeyError, ValueError, TypeError) as e:
                results[i] = {**base, "detail": f"Bad check definition: {e}"}
            except device_exec.ExecError as e:
                results[i] = {**base, "detail": str(e)}

    async def one_device(key: str, items: list[tuple[int, str]]):
        t = targets[key]
        cmds = list(dict.fromkeys(cmd for _i, cmd in items))
        try:
            outs = await asyncio.wait_for(_run_device(t, cmds), CHECK_TIMEOUT + 30 * len(cmds))
            err = ""
        except asyncio.TimeoutError:
            outs, err = {}, "timed out"
        except device_exec.ExecError as e:
            outs, err = {}, str(e)
        except Exception as e:                      # noqa: BLE001 — reported to the learner
            outs, err = {}, f"{type(e).__name__}: {e}"
        for i, cmd in items:
            if err:
                results[i] = {"ok": False, "describe": describe(checks[i]), "detail": err, "output": ""}
            else:
                results[i] = _evaluate(checks[i], t, outs.get(cmd, ""))

    async def all_():
        await asyncio.gather(*(one_device(k, v) for k, v in plan.items()))

    if plan:
        asyncio.run(all_())
    return [r for r in results if r is not None]


def task_result(results: list[dict]) -> dict:
    return {"passed": bool(results) and all(r["ok"] for r in results), "checked_at": time.time(), "checks": results}

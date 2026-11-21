"""Lesson task checks: assertions run against the live devices of a lab.

Check types (one dict each, in a task's `checks` list):

  cli     {device, command, expect: [...]}           output of a command
  route   {device, prefix, protocol?, present?,      a route in the table (protocol e.g. "O IA", "O E2");
           metric?, via?}                            IPv4 or IPv6 (from the prefix); metric = the [AD/metric]
                                                     cost, via = text in the entry (next hop / interface)
  ping    {from, to, count?, source?, min_success?}  percent of pings answered
  config  {device, expect: [...]}                    the running config
  traffic {device, flow | stream, min_pps?,          a traffic generator's live stats (traffic_gen):
           max_pps?, max_loss?, dscp?,               `flow` {stream?, src?, dst? (or group?), port?}
           reflected?, dscp_at_reflector?,           selects received flows (summed); `stream` NAME one
           present?}                                 of its own streams (`reflected: true` = the copies a
                                                     reflector sent back). dscp: a name / number, or a
                                                     list (each seen, together ≥ 90% of the packets);
                                                     present: false = nothing matching is arriving

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

CHECK_TYPES = ("cli", "route", "ping", "config", "traffic")
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
    if t == "traffic":
        errs += _validate_traffic(c)
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
    if t == "traffic":
        what = (f"stream {c['stream']}" + (" (reflected)" if c.get("reflected") else "")) if c.get("stream") \
            else "receiving " + ", ".join(f"{k} {v}" for k, v in (c.get("flow") or {}).items())
        return f"{c.get('device')}: {what}"
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


# IOS-XE's IPv6 table abbreviates codes the IPv4 tables (and IOS-XR) spell
# out: OSPF inter-area "OI" = "O IA"; IS-IS "I1" / "I2" / "IA" = "i L1" /
# "i L2" / "i ia".
_XE_V6_CODES = {"OI": "OIA", "I1": "IL1", "I2": "IL2", "IA": "IIA"}


def _code(s: str) -> str:
    """Protocol code without spaces / selection marks, XE IPv6 spellings mapped."""
    c = re.sub(r"[\s*>]", "", s).upper()
    return _XE_V6_CODES.get(c, c)


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
    if kind == "traffic":
        from app.services.traffic_gen import STATS_COMMAND
        return STATS_COMMAND
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
    elif kind == "traffic":
        res["ok"], res["detail"], res["output"] = eval_traffic(c, output)
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


# --- traffic generator checks --------------------------------------------------------

_TRAFFIC_KEYS = {"type", "device", "describe", "flow", "stream", "min_pps", "max_pps", "max_loss", "dscp",
                 "reflected", "dscp_at_reflector", "present"}


def _dscp_list(v) -> list[int]:
    from app.services.traffic_gen import dscp_value
    return [dscp_value(x) for x in (v if isinstance(v, list) else [v])]


def _validate_traffic(c: dict) -> list[str]:
    errs = []
    unknown = set(c) - _TRAFFIC_KEYS
    if unknown:
        errs.append(f"traffic check: unknown keys {', '.join(sorted(unknown))}")
    if bool(c.get("flow")) == bool(c.get("stream")):
        errs.append("traffic needs exactly one of 'flow' (what it receives) or 'stream' (one it sends)")
    if c.get("flow") is not None and not isinstance(c.get("flow"), dict):
        errs.append("traffic 'flow' must be a mapping (stream / src / dst / group / port)")
    elif isinstance(c.get("flow"), dict) and set(c["flow"]) - {"stream", "src", "dst", "group", "port"}:
        errs.append("traffic 'flow' keys are stream, src, dst (or group) and port")
    for k in ("min_pps", "max_pps", "max_loss"):
        if c.get(k) is not None:
            try:
                float(c[k])
            except (TypeError, ValueError):
                errs.append(f"traffic '{k}' must be a number")
    for k in ("dscp", "dscp_at_reflector"):
        if c.get(k) is not None:
            try:
                _dscp_list(c[k])
            except ValueError as e:
                errs.append(f"traffic '{k}': {e}")
    if c.get("dscp_at_reflector") is not None and not c.get("reflected"):
        errs.append("traffic 'dscp_at_reflector' needs 'reflected: true'")
    return errs


def _dscp_ok(counts: dict, want: list[int]) -> tuple[bool, str]:
    from app.services.traffic_gen import dscp_label
    total = sum(counts.values())
    seen = {int(k): v for k, v in counts.items()}
    if not total:
        return False, "no packets in the last 10 s to read a DSCP from"
    got = ", ".join(f"{dscp_label(k)} {100 * v / total:.0f}%" for k, v in sorted(seen.items(), key=lambda x: -x[1]))
    missing = [w for w in want if not seen.get(w)]
    share = sum(seen.get(w, 0) for w in want) / total
    if missing or share < 0.9:
        return False, f"arriving as {got}; expected {', '.join(dscp_label(w) for w in want)}"
    return True, got


def _flow_line(f: dict) -> str:
    from app.services.traffic_gen import dscp_label
    d = ", ".join(f"{dscp_label(int(k))}×{v}" for k, v in (f.get("dscp") or {}).items())
    name = f" {f['stream']}" if f.get("stream") else ""
    loss = "" if f.get("loss_pct") is None else f", loss {f['loss_pct']}%"
    return f"{f.get('src')} → {f.get('dst')}:{f.get('port')}{name}: {f.get('pps')} pps{loss}, DSCP {d or '-'}"


def _age(d: dict) -> float:
    """Seconds since a flow was last seen (0.0 is "just now", not missing)."""
    v = d.get("last_seen_s")
    return 99.0 if v is None else float(v)


def eval_traffic(c: dict, output: str) -> tuple[bool, str, str]:
    """(ok, detail, excerpt) for a traffic check from plabs-traffic's stats.json."""
    import json
    try:
        st = json.loads(output)
    except ValueError:
        return False, "the traffic generator isn't reporting (not started yet?)", _excerpt(output)
    if c.get("stream"):
        s = next((x for x in st.get("streams") or [] if x.get("name") == c["stream"]), None)
        if s is None:
            names = ", ".join(x.get("name", "?") for x in st.get("streams") or []) or "none"
            return False, f"no running stream {c['stream']!r} (running: {names})", ""
        if s.get("error"):
            return False, f"stream {c['stream']}: {s['error']}", ""
        if c.get("reflected"):
            r = s.get("reflected") or {}
            if not r or _age(r) > 5:
                return (c.get("present") is False), "nothing is coming back from a reflector", ""
            agg = {"pps": r.get("pps") or 0, "loss_pct": r.get("loss_pct"), "dscp": r.get("dscp_back") or {}}
            text = (f"stream {c['stream']} reflected: {agg['pps']} pps, loss {agg['loss_pct']}%, "
                    f"DSCP back {agg['dscp']}, at the reflector {r.get('dscp_at_reflector')}, RTT {r.get('rtt_ms')} ms")
        else:
            agg = {"pps": s.get("pps") or 0, "loss_pct": None, "dscp": {str(s.get("dscp", 0)): 1}}
            text = f"stream {c['stream']}: sending {agg['pps']} pps to {s.get('dst')}:{s.get('port')}"
            r = {}
    else:
        want = dict(c.get("flow") or {})
        if "group" in want:
            want["dst"] = want.pop("group")
        flows = [f for f in st.get("flows") or [] if _age(f) <= 5]
        def match(f):
            return all(str(f.get(k, "")).lower() == str(v).lower() for k, v in want.items())
        sel = [f for f in flows if match(f)]
        text = "\n".join(_flow_line(f) for f in (sel or flows)[:12]) or "(nothing arriving)"
        if not sel:
            if c.get("present") is False:
                return True, "", text
            return False, "nothing matching is arriving" + ("" if flows else " (no traffic at all)"), text
        if c.get("present") is False:
            return False, "it's still arriving", text
        pps = sum(float(f.get("pps") or 0) for f in sel)
        losses = [(float(f["loss_pct"]), float(f.get("pps") or 0)) for f in sel if f.get("loss_pct") is not None]
        loss = (sum(l * w for l, w in losses) / max(1e-9, sum(w for _, w in losses))) if losses else None
        dscp: dict = {}
        for f in sel:
            for k, v in (f.get("dscp") or {}).items():
                dscp[k] = dscp.get(k, 0) + v
        agg = {"pps": pps, "loss_pct": loss, "dscp": dscp}
        r = {}
    if c.get("present") is False and agg["pps"] > 0:
        return False, f"still {agg['pps']} pps", text
    if c.get("min_pps") is not None and agg["pps"] < float(c["min_pps"]):
        return False, f"{agg['pps']} pps (need at least {c['min_pps']})", text
    if c.get("max_pps") is not None and agg["pps"] > float(c["max_pps"]):
        return False, f"{agg['pps']} pps (expected at most {c['max_pps']})", text
    if c.get("max_loss") is not None:
        if agg["loss_pct"] is None:
            return False, "loss can't be measured yet (needs a few seconds of traffic)", text
        if agg["loss_pct"] > float(c["max_loss"]):
            return False, f"{agg['loss_pct']:.1f}% loss (at most {c['max_loss']}% allowed)", text
    if c.get("dscp") is not None:
        ok, why = _dscp_ok(agg["dscp"], _dscp_list(c["dscp"]))
        if not ok:
            return False, why, text
    if c.get("dscp_at_reflector") is not None:
        ok, why = _dscp_ok(r.get("dscp_at_reflector") or {}, _dscp_list(c["dscp_at_reflector"]))
        if not ok:
            return False, "at the reflector: " + why, text
    return True, "", text

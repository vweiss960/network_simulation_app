"""The lab agent: a network engineer with hands on the open lab.

It reads the lab (devices, OS, cabling, state), reads device configs and
show output, proposes configuration and applies it when asked, edits the
topology (devices, cables, group boxes, notes), starts devices, checks
reachability, and looks things up in Cisco's documentation.

Every tool goes through the same code the UI uses (topology_ops, the lab
reconcile, device_exec), and every change it makes is written to the
agent_actions audit table. Device-specific behaviour (how a config is read
or pushed) lives in device_exec / device_kinds, so the agent itself is
vendor-neutral; the prompt is Cisco-centric for now.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import func

from app import config
from app.models.agent_action import AgentAction
from app.models.agent_conversation import AgentConversation
from app.models.database import SessionLocal
from app.models.lab_deployment import LabDeployment
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.models.topology import Topology
from app.services import device_exec, lab_service, llm
from app.services.device_kinds import get_kind, is_bridge
from app.services.host_net import Segment

MAX_STEPS = 60                 # model turns per user message
CISCO_DOMAINS = ["cisco.com", "developer.cisco.com", "xrdocs.io", "community.cisco.com", "learningnetwork.cisco.com"]

SYSTEM_PROMPT = """You are the lab assistant in Phanero Labs, a network lab platform. You are a senior \
network engineer (CCIE-level across Service Provider, Enterprise and Automation) working with the user \
on the lab that is open on their canvas. Every device on the canvas is a real running device \
(Cisco IOS XR via XRd or XRv 9000, IOS XE via Catalyst 8000v / 9000v, FRR and Linux hosts).

How you work:
- Ground every answer in the actual lab. Before advising on a device, call get_lab_state to learn \
its kind/OS, its interfaces and what each is cabled to, and read the running configs you need \
(the device itself, and the neighbors on the relevant links) with get_running_config. Use \
run_show for operational state (neighbors, routes, interfaces) when it matters.
- Use the device's own syntax for its OS (IOS XR vs IOS XE vs FRR). Use the exact interface names \
from get_lab_state. Build on what is already configured: reuse existing addressing, loopbacks, \
process IDs and ASNs instead of inventing new ones; if something needed is missing (e.g. no IP on the \
link), propose it and say so.
- When you are not sure of a command or a feature's behaviour on a platform, look it up in Cisco's \
documentation and cite the page you used.
- Present configuration in fenced code blocks, one per device, each preceded by a heading line naming \
the device and OS, e.g. "**R4** (IOS XR)". Keep explanations short and practical: what the config \
does, anything the user must also do on the neighbor, and how to verify it (show commands).
- Applying changes: when the user asks *how* to do something, propose the config and end by asking \
whether to apply it, e.g. "Would you like me to apply this to R4?". Only call apply_config after the \
user agrees, or when the user directly asked you to set it up / configure it / build it. After \
applying, verify with show commands and report the result plainly, including any errors.
- Building labs: when asked to build a topology, plan it briefly (devices, roles, cabling, addressing), \
then do it end to end: add_devices (lay them out cleanly on the canvas: ~220 px apart, core in the \
middle, edges outside), connect_devices, add group boxes or notes where they help, start_devices, \
wait_for_devices, configure every device, then verify (adjacencies, routes, pings) and fix what fails. \
Choose consistent addressing (e.g. loopbacks 10.0.0.N/32, point-to-point /31s) and tell the user the plan.
- Never change management interfaces, management VRFs, AAA, usernames or SSH settings. Never reload, \
erase or format devices.
- If a tool fails, read the error and recover (fix the input, try another approach) rather than stopping. \
Be honest about anything you could not do.
- Write for a network engineer: concise, specific, no filler."""


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


DEVICE = {"type": "string", "description": "Device name as shown on the canvas, e.g. R4"}
TOOLS: list[dict] = [
    {"name": "get_lab_state",
     "description": "The open lab: every device (kind, OS family, image, power/boot state, management IP), "
                    "its interfaces with what each one is cabled to (peer device + interface) or 'free', "
                    "Network nodes (bridges to physical NICs/VLANs), group boxes and notes, and lab status. "
                    "Call this first in a conversation and again after changing the topology.",
     "input_schema": _schema({}, [])},
    {"name": "list_device_types",
     "description": "Device types that can be added to the lab (templates), with whether their image is available.",
     "input_schema": _schema({}, [])},
    {"name": "get_running_config",
     "description": "The current running configuration of a device (must be started).",
     "input_schema": _schema({"device": DEVICE}, ["device"])},
    {"name": "run_show",
     "description": "Run read-only operational commands on a device (show/ping/traceroute; on Linux hosts, "
                    "shell commands like 'ip addr'). Returns each command's output.",
     "input_schema": _schema({"device": DEVICE, "commands": {"type": "array", "items": {"type": "string"},
                                                              "minItems": 1, "maxItems": 12}},
                             ["device", "commands"])},
    {"name": "apply_config",
     "description": "Apply configuration to a device. Give the config lines exactly as typed in config mode "
                    "(no 'configure terminal'/'commit'/'end'). IOS XR: applied as one commit, aborted on any "
                    "error. IOS XE: applied line by line, errors reported. FRR: via vtysh. Only call this after "
                    "the user agreed or asked you to configure the device.",
     "input_schema": _schema({"device": DEVICE, "config": {"type": "string"}}, ["device", "config"])},
    {"name": "ping",
     "description": "Ping an address from a device and report the result.",
     "input_schema": _schema({"device": DEVICE, "target": {"type": "string"},
                              "source": {"type": "string", "description": "Optional source interface or address"},
                              "count": {"type": "integer", "minimum": 1, "maximum": 20}},
                             ["device", "target"])},
    {"name": "add_devices",
     "description": "Add devices to the canvas. type is a device type name from list_device_types "
                    "(e.g. 'Cisco XRd Control Plane', 'Cisco Catalyst 8000v', 'FRR Router', 'Linux Host'). "
                    "x/y are canvas coordinates (place devices ~220 px apart). If the lab is running, new "
                    "devices start automatically.",
     "input_schema": _schema({"devices": {"type": "array", "minItems": 1, "maxItems": 30, "items": _schema({
         "type": {"type": "string"}, "name": {"type": "string"},
         "x": {"type": "number"}, "y": {"type": "number"},
         "interfaces": {"type": "integer", "minimum": 1, "maximum": 24,
                        "description": "Number of data interfaces (default from the type)"}}, ["type"])}},
         ["devices"])},
    {"name": "connect_devices",
     "description": "Cable devices together. Interfaces are the device's own names (e.g. "
                    "GigabitEthernet0/0/0/2); omit them to use the next free interface. Cabling a running lab "
                    "is live (no restarts).",
     "input_schema": _schema({"links": {"type": "array", "minItems": 1, "maxItems": 60, "items": _schema({
         "a": {"type": "string"}, "b": {"type": "string"},
         "a_interface": {"type": "string"}, "b_interface": {"type": "string"}}, ["a", "b"])}}, ["links"])},
    {"name": "disconnect",
     "description": "Remove the cable between two devices (optionally the one on a given interface of device a).",
     "input_schema": _schema({"a": {"type": "string"}, "b": {"type": "string"},
                              "a_interface": {"type": "string"}}, ["a", "b"])},
    {"name": "remove_device",
     "description": "Delete a device (and its cables) from the lab.",
     "input_schema": _schema({"device": DEVICE}, ["device"])},
    {"name": "add_annotation",
     "description": "Draw on the canvas: a labelled group box (kind 'group', e.g. around the devices of an "
                    "OSPF area or a site), free text ('text') or a sticky note ('note'). For a group box, list "
                    "the devices it should surround in 'around' and the box is sized to fit them.",
     "input_schema": _schema({
         "kind": {"type": "string", "enum": ["group", "text", "note"]},
         "text": {"type": "string"},
         "color": {"type": "string", "enum": ["blue", "orange", "teal", "purple", "gray", "yellow"]},
         "around": {"type": "array", "items": {"type": "string"}},
         "x": {"type": "number"}, "y": {"type": "number"}}, ["kind", "text"])},
    {"name": "start_devices",
     "description": "Start devices. With no list, deploys/starts the whole lab. Devices need time to boot "
                    "(XRd ~1-2 min, IOS XE VMs ~4-5 min, Cat9000v ~12 min); follow with wait_for_devices.",
     "input_schema": _schema({"devices": {"type": "array", "items": {"type": "string"}}}, [])},
    {"name": "wait_for_devices",
     "description": "Wait until devices have finished booting (or the timeout passes) and report their state.",
     "input_schema": _schema({"devices": {"type": "array", "items": {"type": "string"}},
                              "timeout_s": {"type": "integer", "minimum": 10, "maximum": 1200}}, [])},
]
# OpenAI models don't get Anthropic's server-side web tools; they read a Cisco doc page by URL instead.
FETCH_DOC_TOOL = {
    "name": "fetch_cisco_doc",
    "description": "Fetch a page from Cisco's documentation (cisco.com, developer.cisco.com, xrdocs.io) and "
                   "return its text.",
    "input_schema": _schema({"url": {"type": "string"}}, ["url"]),
}
SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "allowed_domains": CISCO_DOMAINS, "max_uses": 8},
    {"type": "web_fetch_20260209", "name": "web_fetch", "allowed_domains": CISCO_DOMAINS, "max_uses": 8},
]
CHANGES = {"apply_config", "add_devices", "connect_devices", "disconnect", "remove_device",
           "add_annotation", "start_devices"}
TOPOLOGY_CHANGES = CHANGES - {"apply_config"}


class ToolError(Exception):
    pass


# --- conversations -------------------------------------------------------------

@dataclass
class Session_:
    id: str
    topology_id: int
    model: str
    convo: llm.AgentConversation
    owner: str = ""                               # username that started it
    title: str = ""
    ui: list = field(default_factory=list)       # what the user saw: [{role, parts, error?}]
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel: threading.Event = field(default_factory=threading.Event)
    used: float = field(default_factory=time.time)


_SESSIONS: dict[str, Session_] = {}
_SESSIONS_LOCK = threading.Lock()
# The running tool's session cancel flag (tools run on the session's worker thread).
_tool_ctx = threading.local()


def _new_convo(model: str, read_only: bool = False) -> llm.AgentConversation:
    """read_only: a lesson lab with AI policy "hints" — the assistant can look
    but not change anything (no CHANGES tools)."""
    anthropic = llm.provider_of(model) == "anthropic"
    tools = [t for t in TOOLS if not (read_only and t["name"] in CHANGES)]
    return llm.AgentConversation(model, tools + ([] if anthropic else [FETCH_DOC_TOOL]),
                                 SERVER_TOOLS if anthropic else None)


# --- lessons ---------------------------------------------------------------------

def lesson_policy(topology_id: int) -> str | None:
    """The AI policy of the lesson running in this lab (teach | hints | off),
    or None for an ordinary lab."""
    from app.models.lesson import Lesson
    from app.models.topology import Topology
    if not config.LEARNING_ENABLED:
        return None
    db = SessionLocal()
    try:
        t = db.get(Topology, topology_id)
        if t and t.lesson_role == "troubleshoot":
            return troubleshoot_policy(db, topology_id)
        if not t or not t.lesson_id or t.lesson_role != "attempt":
            return None
        l = db.get(Lesson, t.lesson_id)
        return l.ai_policy if l else None
    finally:
        db.close()


def troubleshoot_policy(db, topology_id: int) -> str:
    """Troubleshooting labs: a read-only coach at Guided difficulty, no AI
    otherwise — the assistant could read the configs and spot the faults."""
    from app.services import troubleshooting as ts
    a = ts.attempt_for_topology(db, topology_id)
    return "hints" if a and a.difficulty == "guided" else "off"


_TROUBLESHOOT_RULES = (
    "\n\n# This lab is a troubleshooting exercise\n"
    "Something in this network was deliberately broken, and the learner is working trouble tickets to find and "
    "fix it. You don't know what was broken and must not guess at it for them. Coach like a senior engineer: ask "
    "what they've checked, suggest the next show command or the layer to look at (adjacency? advertisement? "
    "policy? next hop?), and help them read the output. Never write the fix, never name the faulty device or "
    "setting before they have, and don't run a sweep of the network to find it yourself — run a command only "
    "when the learner asks you to.\n\nOpen tickets:\n")


_POLICY_RULES = {
    "teach": ("You are tutoring. Explain concepts and reasoning, show the commands and configuration, and you may "
              "apply configuration when the learner asks — but explain what each part does so they learn it."),
    "hints": ("You are a hint-giver, NOT a solver. You can read the lab (state, configs, show commands, pings) "
              "but you cannot change it, and you must not write out the configuration that solves a task. Diagnose "
              "with show commands, then nudge: point at the concept, the command family or the mistake (e.g. "
              "'compare the area type on both ends of that link'). Give at most a short syntax fragment, never a "
              "full working config. If the learner insists on the answer, tell them the lesson panel has a "
              "Solution button."),
}


def lesson_prompt(topology_id: int) -> str:
    """The lesson block appended to the system prompt in a lesson lab: the
    lesson, every task with its pass state, the current task and the rules."""
    from app.models.lesson import Lesson
    from app.services import lessons as lesson_svc
    if not config.LEARNING_ENABLED:
        return ""
    db = SessionLocal()
    try:
        from app.models.topology import Topology
        t = db.get(Topology, topology_id)
        if t and t.lesson_role == "troubleshoot":
            from app.services import troubleshooting as ts
            ta = ts.attempt_for_topology(db, topology_id)
            tickets = (ts.view(db, ta)["tickets"] if ta else [])
            return _TROUBLESHOOT_RULES + ("\n".join(f"- {x['reporter']}: {x['ticket']}" + (" (resolved)" if x["resolved"] else "")
                                                     for x in tickets) or "- none yet (Chaos hasn't struck)")
        a = lesson_svc.attempt_for_topology(db, topology_id)
        l = db.get(Lesson, a.lesson_id) if a else None
        if not l:
            return ""
        res = a.results
        lines = [f"\n\n# This lab is a lesson: {l.title}",
                 f"Level: {l.level}. Track: {l.track.replace('_', ' ')}. {l.summary or ''}".strip(),
                 _POLICY_RULES.get(l.ai_policy, ""), "", "Tasks (in order):"]
        current = None
        for i, t in enumerate(l.tasks, 1):
            r = res.get(t["id"])
            state = "passed" if r and r.get("passed") else ("checked, failing" if r else "not checked yet")
            if current is None and not (r and r.get("passed")):
                current = t
            lines.append(f"{i}. {t['title']} [{state}]")
        if current:
            lines += ["", f"The learner is on: {current['title']}", current.get("body") or ""]
            r = res.get(current["id"])
            fails = [c for c in (r or {}).get("checks", []) if not c.get("ok")]
            if fails:
                lines.append("Its last check found: " + "; ".join(f"{c['describe']}: {c.get('detail')}" for c in fails))
        else:
            lines += ["", "Every task has passed: help the learner explore or review."]
        lines.append("Answer in the context of this lesson and its devices.")
        return "\n".join(lines)
    except Exception:
        return ""
    finally:
        db.close()


def get_session(conversation_id: str | None, topology_id: int, model: str) -> Session_:
    """The conversation to continue (from memory, else from the database), or a new one."""
    with _SESSIONS_LOCK:
        for sid in [k for k, s in _SESSIONS.items() if time.time() - s.used > 6 * 3600 and not s.lock.locked()]:
            _SESSIONS.pop(sid, None)
        s = _SESSIONS.get(conversation_id or "")
        if s and s.topology_id == topology_id and s.model == model:
            s.used = time.time()
            return s
        if conversation_id:
            db = SessionLocal()
            try:
                rec = db.get(AgentConversation, conversation_id)
                if rec and rec.topology_id == topology_id and rec.model == model:
                    convo = _new_convo(model, read_only=lesson_policy(topology_id) == "hints")
                    convo.messages = json.loads(rec.native_json or "[]")
                    s = Session_(id=rec.id, topology_id=topology_id, model=model, convo=convo,
                                 owner=rec.username or "", title=rec.title or "",
                                 ui=json.loads(rec.ui_json or "[]"))
                    _SESSIONS[s.id] = s
                    return s
            finally:
                db.close()
        s = Session_(id=secrets.token_hex(8), topology_id=topology_id, model=model,
                     convo=_new_convo(model, read_only=lesson_policy(topology_id) == "hints"))
        _SESSIONS[s.id] = s
        return s


def drop_session(conversation_id: str) -> None:
    with _SESSIONS_LOCK:
        s = _SESSIONS.pop(conversation_id, None)
    if s:
        s.cancel.set()


def owner_of(conversation_id: str) -> str | None:
    """Username that owns an in-memory conversation (None when not in memory)."""
    s = _SESSIONS.get(conversation_id)
    return s.owner if s else None


def stop(conversation_id: str) -> bool:
    s = _SESSIONS.get(conversation_id)
    if not s:
        return False
    s.cancel.set()
    return True


def _jsonable(x: Any) -> Any:
    """Provider history as JSON (SDK content blocks -> their wire dicts)."""
    if hasattr(x, "to_dict"):
        return x.to_dict()
    if isinstance(x, list):
        return [_jsonable(i) for i in x]
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    return x


def _save(session: Session_, username: str) -> None:
    db = SessionLocal()
    try:
        rec = db.get(AgentConversation, session.id)
        if rec is None:
            rec = AgentConversation(id=session.id, topology_id=session.topology_id, username=username)
            db.add(rec)
        rec.title, rec.model = session.title, session.model
        rec.native_json = json.dumps(_jsonable(session.convo.messages))
        rec.ui_json = json.dumps(session.ui)
        rec.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


# --- the loop ------------------------------------------------------------------

Emit = Callable[[dict], None]


def _record(session: Session_, ev: dict) -> None:
    """Mirror UI events into the saved transcript (last message = this answer)."""
    m = session.ui[-1]
    t = ev["type"]
    if t == "text":
        if m["parts"] and m["parts"][-1]["kind"] == "text":
            m["parts"][-1]["text"] += ev["delta"]
        else:
            m["parts"].append({"kind": "text", "text": ev["delta"]})
    elif t == "tool":
        part = {"kind": "tool", "id": ev["id"], "label": ev["label"], "state": ev["state"], "summary": ev.get("summary", "")}
        for i, p in enumerate(m["parts"]):
            if p["kind"] == "tool" and p["id"] == ev["id"]:
                m["parts"][i] = part
                break
        else:
            m["parts"].append(part)
    elif t == "error":
        m["error"] = ev["message"]


def run_and_save(session: Session_, user_text: str, username: str, emit: Emit) -> None:
    """Run one user message (see run), keeping the saved conversation up to date."""
    session.cancel.clear()
    if not session.title:
        session.title = " ".join(user_text.split())[:80]
    session.ui += [{"role": "user", "parts": [{"kind": "text", "text": user_text}]},
                   {"role": "assistant", "parts": []}]

    def out(ev: dict) -> None:
        _record(session, ev)
        emit(ev)
    try:
        run(session, user_text, username, out)
    except Exception as e:      # noqa: BLE001 — provider / API errors end up in the chat
        out({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        # Mark tools that never finished (stop / error) so a reloaded transcript is honest.
        for p in session.ui[-1]["parts"]:
            if p["kind"] == "tool" and p["state"] == "running":
                p["state"] = "error"
                p["summary"] = "Stopped"
        _save(session, username)


def run(session: Session_, user_text: str, username: str, emit: Emit) -> None:
    """Run one user message to completion, emitting UI events:
    text {delta} · status {text} · tool {id, name, label, state, summary} · lab_changed · error."""
    convo = session.convo
    convo.add_user(user_text)
    system = SYSTEM_PROMPT + lesson_prompt(session.topology_id)
    for _ in range(MAX_STEPS):
        if session.cancel.is_set():
            emit({"type": "text", "delta": "\n\n_Stopped._"})
            return
        turn = convo.step(system, lambda d: emit({"type": "text", "delta": d}),
                          lambda t: emit({"type": "status", "text": t}))
        if turn.stop == "pause":
            continue
        if turn.note:
            emit({"type": "text", "delta": f"\n\n_{turn.note}_"})
        if not turn.tool_calls:
            return
        results = []
        for call in turn.tool_calls:
            if session.cancel.is_set():
                results.append((call.id, "Not run: the user stopped the assistant.", True))
                continue
            label = _label(call.name, call.input or {})
            emit({"type": "tool", "id": call.id, "name": call.name, "label": label, "state": "running"})
            ok, text = _run_tool(session, call, username, emit)
            emit({"type": "tool", "id": call.id, "name": call.name, "label": label,
                  "state": "done" if ok else "error", "summary": text[:300] if not ok else ""})
            if ok and call.name in TOPOLOGY_CHANGES:
                emit({"type": "lab_changed"})
            results.append((call.id, text, not ok))
        convo.add_tool_results(results)
    emit({"type": "text", "delta": "\n\n_Stopped: this took more steps than allowed. Ask me to continue._"})


def _label(name: str, a: dict) -> str:
    """Short UI label for a tool call (inputs may be partial or odd: be defensive)."""
    d = str(a.get("device", ""))
    names = lambda key: ", ".join(str(x) for x in (a.get(key) or []) if isinstance(x, str))  # noqa: E731
    count = lambda key: len(a.get(key) or []) if isinstance(a.get(key), list) else 0  # noqa: E731
    if name == "get_lab_state":
        return "Reading the lab"
    if name == "list_device_types":
        return "Listing device types"
    if name == "get_running_config":
        return f"Reading {d}'s running config"
    if name == "run_show":
        cmds = a.get("commands") if isinstance(a.get("commands"), list) else []
        return f"Running on {d}: {', '.join(str(c) for c in cmds[:2])}"
    if name == "apply_config":
        return f"Applying configuration to {d}"
    if name == "ping":
        return f"Pinging {a.get('target', '')} from {d}"
    if name == "add_devices":
        return f"Adding {count('devices')} device(s)"
    if name == "connect_devices":
        return f"Cabling {count('links')} link(s)"
    if name == "disconnect":
        return f"Disconnecting {a.get('a', '')} – {a.get('b', '')}"
    if name == "remove_device":
        return f"Removing {d}"
    if name == "add_annotation":
        return f"Drawing {a.get('kind', '')} \u201c{a.get('text', '')}\u201d"
    if name == "start_devices":
        return "Starting " + (names("devices") or "the lab")
    if name == "wait_for_devices":
        return "Waiting for devices to boot"
    if name == "fetch_cisco_doc":
        return "Reading a Cisco document"
    return name


def _validate(schema: dict, args: Any, path: str = "input") -> str | None:
    """Minimal JSON-schema check (types, required, enums, extra keys): tool
    inputs stream in and aren't validated server-side, so check before running."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(args, dict):
            return f"{path} must be an object"
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in args:
                return f"{path}.{r} is required"
        for k, v in args.items():
            if k not in props:
                return f"{path}.{k} is not a known field"
            err = _validate(props[k], v, f"{path}.{k}")
            if err:
                return err
    elif t == "array":
        if not isinstance(args, list):
            return f"{path} must be a list"
        if len(args) < schema.get("minItems", 0):
            return f"{path} needs at least {schema['minItems']} item(s)"
        if "maxItems" in schema and len(args) > schema["maxItems"]:
            return f"{path} allows at most {schema['maxItems']} item(s)"
        for i, v in enumerate(args):
            err = _validate(schema.get("items", {}), v, f"{path}[{i}]")
            if err:
                return err
    elif t == "string" and not isinstance(args, str):
        return f"{path} must be a string"
    elif t == "integer" and not (isinstance(args, int) and not isinstance(args, bool)):
        return f"{path} must be an integer"
    elif t == "number" and not (isinstance(args, (int, float)) and not isinstance(args, bool)):
        return f"{path} must be a number"
    if t in ("integer", "number"):
        if "minimum" in schema and args < schema["minimum"]:
            return f"{path} must be >= {schema['minimum']}"
        if "maximum" in schema and args > schema["maximum"]:
            return f"{path} must be <= {schema['maximum']}"
    if "enum" in schema and args not in schema["enum"]:
        return f"{path} must be one of {schema['enum']}"
    return None


def _run_tool(session: Session_, call: llm.ToolCall, username: str, emit: Emit) -> tuple[bool, str]:
    spec = next((t for t in TOOLS + [FETCH_DOC_TOOL] if t["name"] == call.name), None)
    if spec is None:
        return False, f"Unknown tool {call.name}"
    if call.input is None:
        return False, "The tool input wasn't valid JSON; send it again."
    if call.name in CHANGES and lesson_policy(session.topology_id) in ("hints", "off"):
        return False, "Not allowed in this lesson: the assistant gives hints only and can't change the lab."
    err = _validate(spec["input_schema"], call.input)
    if err:
        return False, f"Invalid input: {err}"
    db = SessionLocal()
    _tool_ctx.cancel = session.cancel
    try:
        fn = _IMPL[call.name]
        try:
            text = fn(db, session.topology_id, call.input, emit)
            ok = True
        except (ToolError, device_exec.ExecError, lab_service.LabError) as e:
            text, ok = str(e), False
        except Exception as e:      # noqa: BLE001 — report, don't kill the conversation
            text, ok = f"{type(e).__name__}: {e}", False
        if not ok:
            # Drop whatever a failed tool flushed (e.g. the first devices of a
            # failed add_devices) so the audit commit below doesn't persist it.
            db.rollback()
        if call.name in CHANGES:
            db.add(AgentAction(topology_id=session.topology_id, conversation_id=session.id, username=username,
                               model=session.model, tool=call.name,
                               input_json=json.dumps(call.input)[:20000], ok=ok, summary=text[:2000]))
            db.commit()
        return ok, text
    finally:
        _tool_ctx.cancel = None
        db.close()


# --- tool implementations ----------------------------------------------------------

def _node(db, tid: int, name: str) -> NodeInstance:
    n = (db.query(NodeInstance).filter(NodeInstance.topology_id == tid)
         .filter(func.lower(NodeInstance.name) == name.strip().lower()).first())
    if not n:
        names = [x.name for x in db.query(NodeInstance).filter(NodeInstance.topology_id == tid)]
        raise ToolError(f"No device named {name!r}. Devices: {', '.join(names) or 'none'}")
    return n


def _target(db, tid: int, name: str) -> device_exec.Target:
    n = _node(db, tid, name)
    if is_bridge(n.kind):
        raise ToolError(f"{n.name} is a Network node, not a device")
    return device_exec.target_for(db, tid, n.id)


def _rt(rt: dict, node_id: int) -> dict:
    """A device's runtime state from get_deployment_state (keyed by node id)."""
    return rt.get(node_id) or rt.get(str(node_id)) or {}


def _clip(text: str, limit: int = 30000) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… (truncated, {len(text) - limit} more characters)"


def t_get_lab_state(db, tid, a, emit) -> str:
    topo = db.get(Topology, tid)
    state = lab_service.get_deployment_state(db, tid)
    rt = state.get("nodes", {})
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == tid).order_by(NodeInstance.id).all()
    names = {n.id: n.name for n in nodes}
    peer: dict[tuple[int, str], str] = {}
    for l in db.query(Link).filter(Link.topology_id == tid):
        peer[(l.source_node_id, l.source_interface)] = f"{names.get(l.target_node_id)} {l.target_interface}"
        peer[(l.target_node_id, l.target_interface)] = f"{names.get(l.source_node_id)} {l.source_interface}"
    up = sum(1 for n in nodes if not is_bridge(n.kind) and _rt(rt, n.id).get("ready_state") == "up")
    devs = [n for n in nodes if not is_bridge(n.kind)]
    lines = [f'Lab "{topo.name}": {state.get("status", "idle")} ({up}/{len(devs)} devices up)']
    lines.append("Devices:")
    for n in devs:
        k = get_kind(n.kind)
        r = _rt(rt, n.id)
        power = "off" if n.enabled == 0 else (r.get("ready_state") or "not deployed")
        lines.append(f"- {n.name}: {k.label} ({k.family}), image {n.image_ref or 'none'}, state {power}"
                     + (f", mgmt {r.get('mgmt_ip')}" if r.get("mgmt_ip") else ""))
        free = []
        for i in n.interfaces:
            p = peer.get((n.id, i["name"]))
            if p:
                lines.append(f"    {i['name']} -> {p}")
            else:
                free.append(i["name"])
        if free:
            lines.append(f"    free: {', '.join(free)}")
    nets = [n for n in nodes if is_bridge(n.kind)]
    if nets:
        lines.append("Network nodes:")
        for n in nets:
            seg = Segment.from_dict(n.network)
            ports = [f"{i['name']} -> {peer[(n.id, i['name'])]}" for i in n.interfaces if (n.id, i["name"]) in peer]
            lines.append(f"- {n.name}: {seg.describe()}{' (management network)' if n.network.get('mgmt') else ''}"
                         + (f"; {'; '.join(ports)}" if ports else ""))
    if topo.annotations:
        lines.append("Canvas annotations:")
        for an in topo.annotations:
            lines.append(f"- {an['type']} “{an.get('text', '')}” at ({an['x']:.0f},{an['y']:.0f})")
    lines.append("Canvas positions: " + ", ".join(f"{n.name}({n.x:.0f},{n.y:.0f})" for n in nodes))
    return "\n".join(lines)


def t_list_device_types(db, tid, a, emit) -> str:
    local = lab_service.local_docker_images()
    out = []
    for t in db.query(NodeTemplate).order_by(NodeTemplate.vendor, NodeTemplate.name):
        k = get_kind(t.kind)
        if is_bridge(t.kind):
            avail = "available"
        elif not t.image_ref:
            avail = "NO IMAGE (can't be used)"
        else:
            avail = "available" if (t.image_ref in local or f"{t.image_ref}:latest" in local) else "image not loaded"
        out.append(f"- {t.name}: {k.label} ({k.family}), {t.interface_count} interfaces, {avail}"
                   + (f", ~{k.min_memory_mb // 1024} GB RAM" if k.min_memory_mb >= 2048 else ""))
    return "\n".join(out)


def t_get_running_config(db, tid, a, emit) -> str:
    t = _target(db, tid, a["device"])
    return _clip(asyncio.run(device_exec.running_config(t, timeout=90)))


def t_run_show(db, tid, a, emit) -> str:
    t = _target(db, tid, a["device"])
    res = asyncio.run(device_exec.run_cli(t, a["commands"], timeout=90))
    return _clip("\n\n".join(f"{t.name}# {r['command']}\n{r['output']}" for r in res))


def t_apply_config(db, tid, a, emit) -> str:
    t = _target(db, tid, a["device"])
    r = asyncio.run(device_exec.configure(t, a["config"], timeout=180))
    if r.get("ok"):
        return f"Applied to {t.name} without errors."
    raise ToolError(f"{t.name} reported errors:\n" + "\n".join(r.get("errors") or [])
                    + ("\n(IOS XR: the commit was aborted, nothing was applied.)" if t.family == "iosxr" else ""))


def t_ping(db, tid, a, emit) -> str:
    t = _target(db, tid, a["device"])
    count = a.get("count") or 5
    cmd = device_exec.ping_command(t.family, a["target"], count, a.get("source"))
    (r,) = asyncio.run(device_exec.run_cli(t, [cmd], timeout=60))
    return f"{t.name}# {cmd}\n{r['output']}"


def _template(db, name: str) -> NodeTemplate:
    q = name.strip().lower()
    ts = db.query(NodeTemplate).all()
    t = next((x for x in ts if x.name.lower() == q), None) or next((x for x in ts if q in x.name.lower()), None)
    if not t:
        raise ToolError(f"No device type {name!r}. Types: {', '.join(x.name for x in ts)}")
    return t


def t_add_devices(db, tid, a, emit) -> str:
    from app.services.topology_ops import TopologyError, create_node
    made = []
    try:
        for d in a["devices"]:
            t = _template(db, d["type"])
            if not is_bridge(t.kind) and not t.image_ref:
                raise ToolError(f"{t.name} has no image yet (build or load one under Images)")
            n = create_node(db, tid, template_id=t.id, name=d.get("name"), x=d.get("x"), y=d.get("y"),
                            interface_count=d.get("interfaces"))
            db.flush()
            made.append(f"{n.name} ({t.name}; interfaces {', '.join(i['name'] for i in n.interfaces) or 'grow as cabled'})")
    except TopologyError as e:
        db.rollback()
        raise ToolError(str(e))
    db.commit()
    lab_service.request_sync(db, tid)
    return "Added: " + "; ".join(made)


def t_connect_devices(db, tid, a, emit) -> str:
    from app.services.topology_ops import TopologyError, create_link
    done = []
    try:
        for l in a["links"]:
            na, nb = _node(db, tid, l["a"]), _node(db, tid, l["b"])
            link = create_link(db, tid, na.id, nb.id, l.get("a_interface"), l.get("b_interface"))
            db.flush()
            done.append(f"{na.name} {link.source_interface} <-> {nb.name} {link.target_interface}")
    except TopologyError as e:
        db.rollback()
        raise ToolError(f"{e} (nothing was cabled)")
    db.commit()
    lab_service.request_sync(db, tid)
    return "Cabled: " + "; ".join(done)


def t_disconnect(db, tid, a, emit) -> str:
    na, nb = _node(db, tid, a["a"]), _node(db, tid, a["b"])
    q = db.query(Link).filter(Link.topology_id == tid).filter(
        ((Link.source_node_id == na.id) & (Link.target_node_id == nb.id))
        | ((Link.source_node_id == nb.id) & (Link.target_node_id == na.id)))
    def a_side(l: Link) -> str:
        return l.source_interface if l.source_node_id == na.id else l.target_interface
    links = [l for l in q if not a.get("a_interface") or a_side(l) == a["a_interface"]]
    if not links:
        raise ToolError(f"No cable between {na.name} and {nb.name}")
    for l in links:
        db.delete(l)
    db.commit()
    lab_service.request_sync(db, tid)
    return f"Removed {len(links)} cable(s) between {na.name} and {nb.name}"


def t_remove_device(db, tid, a, emit) -> str:
    from app.services.lab_network import release_node
    n = _node(db, tid, a["device"])
    dep = db.query(LabDeployment).filter(LabDeployment.topology_id == tid).first()
    if n.network.get("mgmt") and dep and dep.status != "idle":
        raise ToolError(f"{n.name} is the lab's management network; destroy the lab before removing it")
    release_node(db, n.id)
    db.query(Link).filter((Link.source_node_id == n.id) | (Link.target_node_id == n.id)).delete(synchronize_session=False)
    name = n.name
    db.delete(n)
    db.commit()
    lab_service.request_sync(db, tid)
    return f"Removed {name}"


def t_add_annotation(db, tid, a, emit) -> str:
    topo = db.get(Topology, tid)
    kind = a["kind"]
    color = a.get("color") or ("yellow" if kind == "note" else "blue")
    if kind == "note" and color not in ("yellow", "blue", "teal", "purple", "gray"):
        color = "yellow"
    if kind != "note" and color == "yellow":
        color = "orange"
    if kind == "group" and a.get("around"):
        ns = [_node(db, tid, x) for x in a["around"]]
        x0, y0 = min(n.x for n in ns) - 70, min(n.y for n in ns) - 70
        x1, y1 = max(n.x for n in ns) + 56 + 70, max(n.y for n in ns) + 56 + 80
        geo = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
    else:
        size = {"group": (360, 220), "note": (220, 120), "text": (0, 0)}[kind]
        geo = {"x": a.get("x", 80), "y": a.get("y", 40), "w": size[0], "h": size[1]}
    item = {"id": secrets.token_hex(4), "type": kind, **{k: float(v) for k, v in geo.items()},
            "text": a["text"], "color": color, "locked": False}
    topo.annotations = topo.annotations + [item]
    db.commit()
    return f"Drew {kind} “{a['text']}”"


def t_start_devices(db, tid, a, emit) -> str:
    names = a.get("devices") or []
    dep = lab_service.get_deployment_state(db, tid)
    deployed = dep.get("status") in ("running", "syncing", "deploying", "failed")
    if not names:
        emit({"type": "status", "text": "Deploying the lab"})
        lab_service.deploy_lab(db, tid, start_all=True)
        return "Lab deployed; all devices are starting. Use wait_for_devices."
    nodes = [_node(db, tid, x) for x in names]
    if not deployed:
        # Only these devices come up; the rest stay off.
        for n in db.query(NodeInstance).filter(NodeInstance.topology_id == tid):
            n.enabled = 1 if n.id in {x.id for x in nodes} else 0
        db.commit()
        emit({"type": "status", "text": "Deploying " + ", ".join(n.name for n in nodes)})
        lab_service.deploy_lab(db, tid)
    else:
        for n in nodes:
            lab_service.start_node(db, tid, n.id)
    return f"Starting {', '.join(n.name for n in nodes)}. Use wait_for_devices."


def t_wait_for_devices(db, tid, a, emit) -> str:
    deadline = time.time() + (a.get("timeout_s") or 600)
    want = {n.name for n in (_node(db, tid, x) for x in a["devices"])} if a.get("devices") else None
    if want:
        off = sorted(n.name for n in db.query(NodeInstance).filter(NodeInstance.topology_id == tid)
                     if n.name in want and n.enabled == 0)
        if off:
            raise ToolError(f"{', '.join(off)} is off; start it with start_devices first")
    cancel = getattr(_tool_ctx, "cancel", None)
    last = ""
    while True:
        if cancel is not None and cancel.is_set():
            raise ToolError("Stopped by the user")
        db.expire_all()
        nodes = [n for n in db.query(NodeInstance).filter(NodeInstance.topology_id == tid)
                 if not is_bridge(n.kind) and n.enabled != 0 and (want is None or n.name in want)]
        rt = lab_service.get_deployment_state(db, tid).get("nodes", {})
        st = {n.name: _rt(rt, n.id).get("ready_state", "absent") for n in nodes}
        pending = [k for k, v in st.items() if v != "up"]
        summary = ", ".join(f"{k} {v}" for k, v in sorted(st.items()))
        if not pending:
            return f"All up: {summary}"
        if time.time() > deadline:
            return f"Timed out; current state: {summary}"
        if summary != last:
            emit({"type": "status", "text": f"Waiting for {', '.join(pending)} to boot"})
            last = summary
        time.sleep(5)


def t_fetch_cisco_doc(db, tid, a, emit) -> str:
    import re
    from html import unescape
    from urllib.parse import urlparse
    import requests
    from urllib.parse import urljoin
    url = a["url"]
    for _ in range(6):
        # Every hop (redirects included) must stay on a Cisco documentation site.
        u = urlparse(url)
        host = u.hostname or ""
        if u.scheme not in ("http", "https") or not any(host == d or host.endswith("." + d) for d in CISCO_DOMAINS):
            raise ToolError(f"Only Cisco documentation sites are allowed ({', '.join(CISCO_DOMAINS)})")
        r = requests.get(url, timeout=30, headers={"User-Agent": "PhaneroLabs-docs/1.0"}, allow_redirects=False)
        if not r.is_redirect:
            break
        url = urljoin(url, r.headers.get("location", ""))
    else:
        raise ToolError("Too many redirects")
    r.raise_for_status()
    text = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", r.text)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return _clip(re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip(), 40000)


_IMPL: dict[str, Callable[..., str]] = {
    "get_lab_state": t_get_lab_state, "list_device_types": t_list_device_types,
    "get_running_config": t_get_running_config, "run_show": t_run_show, "apply_config": t_apply_config,
    "ping": t_ping, "add_devices": t_add_devices, "connect_devices": t_connect_devices,
    "disconnect": t_disconnect, "remove_device": t_remove_device, "add_annotation": t_add_annotation,
    "start_devices": t_start_devices, "wait_for_devices": t_wait_for_devices,
    "fetch_cisco_doc": t_fetch_cisco_doc,
}

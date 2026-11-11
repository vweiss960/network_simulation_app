"""Service module AI assistant — agent for authoring the Jinja
templates of a Service.

Mirrors routes/config_ai.py (same server-side agent loop + SSE event protocol:
token / tool_status / set_body / render_result / done) but for the Service
module's *Direct* variable model: templates render against the plain variable
dict the operator supplies at deploy time (plus a `device` namespace), not
topology/interface context. Reduced toolset accordingly.

Gated behind PHANERO_SERVICE_ENABLED at registration time in main.py.
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.service import Service
from app.models.service_template import ServiceTemplate
from app.services.service_renderer import render_service_template
from app.services.service_deploy import effective_variables
from app.services import llm


router = APIRouter(prefix="/api/services/ai", tags=["service"])
logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a network configuration template author embedded in Phanero's Service module. You write and iterate on Jinja2 templates that produce real Cisco IOS-XR device configuration.

**How the Service module works (important — different from the Config Design module):**
- A *Service* bundles templates + declared variables. At DEPLOY time the operator supplies, per target device, a plain JSON dict of variable values. Your template renders directly against that dict — this is the "Direct" variable model.
- So a template references variables by the names the operator will supply, e.g. `router bgp {{ asn }}` expects the deploy payload to include `"asn"`. There is NO automatic topology/interface context here.
- A small `device` namespace is also available: `device.name`, `device.mgmt_ip`, `device.kind` — the box the config will be pushed to.

**Your tools & workflow:**
- Call `describe_variables` to see the variables this service already declares (name, required, default). Prefer those names so the operator's payload lines up. You may introduce new variables the operator will then supply.
- Use `set_template_body` to put your template into the editor (the operator reviews it as a pending change and Applies/Discards — so lean toward focused edits, not full rewrites).
- After every `set_template_body`, call `render_template` with a sample `variables` object to verify it renders (catches undefined variables, bad loops, syntax errors). Fix and retry until it renders clean.
- Use `list_templates` / `read_template` to reference other templates in this service.
- Use `create_template` to add a new focused template to the service.

**Jinja notes:**
- The renderer uses StrictUndefined — `{{ foo }}` with no `foo` in the supplied variables is a render error. Provide those variables in your `render_template` sample.
- `trim_blocks` and `lstrip_blocks` are on, so block tags don't leave blank lines.
- Loop with `{% for x in items %} ... {% endfor %}`; guard with `{% if ... %}`.

**Output rules:**
- Real IOS-XR config syntax only inside the template body — no markdown, no prose. Cisco comments use `!`.
- Keep templates focused on exactly what the user asked. Final text response: a short summary; the body is already in the pending-edit pane."""


TOOLS = [
    {
        "name": "set_template_body",
        "description": "Replace the Jinja template body in the editor with new content. Applied live as a pending edit the operator reviews. Always follow with render_template to verify.",
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "The new, complete Jinja2 template body (not a diff)."},
            },
            "required": ["body"],
        },
    },
    {
        "name": "render_template",
        "description": "Render the current template body against a sample variable dict (the Direct model — you supply the values the operator would supply at deploy). Returns the rendered config or a Jinja error. Use after every set_template_body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "variables": {
                    "type": "object",
                    "description": "Sample values for the variables your template references, e.g. {\"asn\": 65001, \"loopback\": \"1.1.1.1\"}. Merged over the service's declared defaults.",
                },
                "device_name": {"type": "string", "description": "Optional value for device.name in the render context."},
            },
            "required": [],
        },
    },
    {
        "name": "describe_variables",
        "description": "List the variables this service declares: name, required, default, description. Prefer these names so the operator's deploy payload matches.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_templates",
        "description": "List the templates in this service (id, name, position, body length).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_template",
        "description": "Return the full Jinja body of a template in this service. Pair with list_templates to find ids.",
        "input_schema": {
            "type": "object",
            "properties": {"template_id": {"type": "integer", "description": "Template id from list_templates."}},
            "required": ["template_id"],
        },
    },
    {
        "name": "create_template",
        "description": "Create a new template in this service. Use to add a focused section rather than overloading one template.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name (unique within the service)."},
                "body": {"type": "string", "description": "The Jinja body — a focused snippet."},
            },
            "required": ["name", "body"],
        },
    },
]


# ---------------------- Request schema ----------------------

class AIMessage(BaseModel):
    role: str
    content: str


class TemplateMeta(BaseModel):
    id: Optional[int] = None
    name: str = ""


class AIRequest(BaseModel):
    service_id: int
    current_body: str = ""
    messages: list[AIMessage]
    template_meta: Optional[TemplateMeta] = None


# ---------------------- Tool executor ----------------------

def _load_service(db: Session, service_id: int) -> Optional[Service]:
    return db.query(Service).filter(Service.id == service_id).first()


def _execute_tool(name: str, args: dict, service_id: int, current_body_ref: list, db: Session) -> tuple[str, Optional[dict]]:
    """Run a tool server-side. Returns (text_for_agent, optional_sse_event).
    current_body_ref is a one-element mutable cell holding the in-flight body."""
    service = _load_service(db, service_id)
    if service is None:
        return (f"Error: service {service_id} not found.", None)

    if name == "set_template_body":
        new_body = args.get("body", "")
        current_body_ref[0] = new_body
        return (
            "Template body updated in the editor. Call render_template with sample variables to verify.",
            {"type": "set_body", "body": new_body},
        )

    if name == "render_template":
        supplied = args.get("variables") or {}
        variables, _missing = effective_variables(service, supplied)
        device_meta = {"name": args.get("device_name") or "sample-device", "mgmt_ip": "", "kind": "cisco_xrd"}
        outcome = render_service_template(current_body_ref[0], variables, device_meta)
        if outcome["ok"]:
            rendered = outcome["rendered"]
            preview = rendered if len(rendered) <= 2000 else rendered[:2000] + f"\n... (truncated, total {len(rendered)} chars)"
            return (
                f"Render OK. Output:\n```\n{preview}\n```",
                {"type": "render_result", "node_name": device_meta["name"], "ok": True, "rendered": rendered},
            )
        return (
            f"Render FAILED. Error: {outcome['error']}"
            + (f" (line {outcome.get('line')})" if outcome.get("line") else ""),
            {"type": "render_result", "node_name": device_meta["name"], "ok": False,
             "error": outcome["error"], "line": outcome.get("line")},
        )

    if name == "describe_variables":
        rows = [{"name": v.name, "required": v.required,
                 "default": (json.loads(v.default_json) if v.default_json else None),
                 "description": v.description or ""} for v in service.variables]
        return (json.dumps(rows, indent=2, default=str) if rows else "This service has no declared variables yet.", None)

    if name == "list_templates":
        rows = [{"id": t.id, "name": t.name, "position": t.position, "body_length": len(t.body or "")}
                for t in service.templates]
        return (json.dumps(rows, indent=2), None)

    if name == "read_template":
        tid = args.get("template_id")
        t = next((t for t in service.templates if t.id == tid), None)
        if not t:
            return (f"Error: template {tid} not found in this service.", None)
        body = t.body or ""
        if len(body) > 6000:
            body = body[:6000] + f"\n... (truncated, total {len(t.body or '')} chars)"
        return (f"Template '{t.name}' (id={t.id}):\n```\n{body}\n```", None)

    if name == "create_template":
        tname = (args.get("name") or "").strip()
        if not tname:
            return ("Error: template name is required.", None)
        if any(t.name == tname for t in service.templates):
            return (f"Error: template '{tname}' already exists in this service. Use set_template_body to update it.", None)
        t = ServiceTemplate(service_id=service_id, name=tname, body=args.get("body") or "",
                            position=len(service.templates))
        db.add(t)
        db.commit()
        db.refresh(t)
        return (f"Created template '{t.name}' (id={t.id}) in this service.", None)

    return (f"Unknown tool: {name}", None)


# ---------------------- Route ----------------------

@router.post("")
def ai_edit(req: AIRequest, db: Session = Depends(get_db)):
    err = llm.config_error()
    if err:
        def no_key():
            msg = f"AI assistant is not configured. {err}"
            yield f"data: {json.dumps({'type': 'token', 'text': msg})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(no_key(), media_type="text/event-stream")

    def generate():
        current_body_ref = [req.current_body]
        conv = llm.Conversation([m.model_dump() for m in req.messages], TOOLS)

        def _system_for_turn() -> str:
            tm = req.template_meta
            if not tm or tm.id is None:
                return SYSTEM_PROMPT
            extra = ("\n\n**Currently focused template (set_template_body acts on THIS one):**\n"
                     f"- id: {tm.id}\n- name: {tm.name or '(unnamed)'}\n")
            return SYSTEM_PROMPT + extra

        max_iterations = 12
        try:
            for _ in range(max_iterations):
                turn = conv.step(_system_for_turn(), max_tokens=8192)
                for i in range(0, len(turn.text), 32):
                    yield f"data: {json.dumps({'type': 'token', 'text': turn.text[i:i+32]})}\n\n"

                if not turn.tool_calls:
                    break
                tool_results = []
                for call in turn.tool_calls:
                    yield f"data: {json.dumps({'type': 'tool_status', 'tool': call.name, 'input': call.input})}\n\n"
                    text, sse_event = _execute_tool(call.name, call.input, req.service_id, current_body_ref, db)
                    if sse_event:
                        yield f"data: {json.dumps(sse_event)}\n\n"
                    tool_results.append((call.id, text))
                conv.add_tool_results(tool_results)
        except Exception as e:
            logger.exception("Service AI failed")
            yield f"data: {json.dumps({'type': 'token', 'text': f'AI assistant error: {e}'})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'final_body': current_body_ref[0]})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

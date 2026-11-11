"""Config Design AI assistant — agent for generating and iterating on Jinja
templates.

Separate from the main /chat endpoint because the toolset is much narrower
(it only needs to edit a template body and test renders) and the model is
always the main (more capable) model — Jinja correctness matters more than
turn-around speed. Provider/model come from app.services.llm.

Server-side agent loop pattern:
    1. Call the model with the conversation + tools
    2. Resolve tool calls:
         - set_template_body  → emit SSE event so the frontend applies the
                                edit to the CodeMirror editor live, and
                                update the in-flight "current body" so the
                                next render_template call uses it
         - render_template    → renders body against a node, returns output
                                or error
         - list_devices       → compact device list for the agent to pick
                                from when choosing a render target
         - describe_variables → variable namespace exposed by the renderer
    3. Loop until end_turn, streaming text along the way
    4. Emit `done` event
"""
import json
import logging
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.config_template import ConfigTemplate
from app.models.config_template_group import ConfigTemplateGroup
from app.services.config_renderer import build_context, render_for_node, demote_default_owners
from app.services import llm


def _rule_str(obj) -> str:
    """Human-readable default rule ('attr=value' or 'none') — single format
    for every place the AI is shown a template/group's rule."""
    if not obj.platform_default:
        return "none"
    return f"{obj.match_attr or 'platform'}={obj.platform_default}"


router = APIRouter(prefix="/api/config-templates/ai", tags=["config_design"])

logger = logging.getLogger(__name__)

# Uses the main model (llm.MODEL), not the fast tool model — Jinja correctness
# and per-platform syntax matter more than tool-selection speed here, and the
# agent often emits long, structured config bodies that benefit from it.


SYSTEM_PROMPT = """You are a network configuration template author embedded in Phanero. Your job is to generate and iterate on Jinja2 templates that produce real device configurations for the network platforms the user describes.

**How you work:**
- The user describes a platform (IOS-XR, JunOS, EOS, NX-OS, IOS-XE, FRR, Linux, etc.) and features they want configured (OSPF, BGP, MPLS, interfaces, etc.).
- You write a Jinja2 template that produces a valid config for that platform.
- You use `set_template_body` to put your template into the editor.
- You use `render_template` to verify your template actually renders against a real device in the user's topology — catch undefined variables, broken loops, syntax errors.
- If render fails, you fix the template and try again. Do not stop until rendering succeeds against at least one device.

**Variable namespace available in templates:**
- `device` — a dict with the following keys:
    - Core: name, vendor, model, platform, capacity_gbps, mgmt_ip, role, location, prometheus_id, color, is_planned, mtbf_hours, mttr_hours, latitude, longitude
    - Route reflection: is_route_reflector (bool), is_super_route_reflector (bool — top tier of a 2-tier hierarchy)
    - RR peering lists (each item is a compact peer dict with id, name, platform, vendor, mgmt_ip, is_route_reflector, is_super_route_reflector, location, PLUS every admin-defined custom device attribute — e.g. if a topology has a unique-per-topology integer attribute `v4_index`, each peer entry exposes `peer.v4_index` the same way `device.v4_index` works on the local node):
      - rr_peers — RRs this device explicitly peers with. For a regular client: its direct RRs. For a non-super RR: its super-RR parents. Empty for super RRs (they auto-mesh).
      - rr_clients — devices pointing AT this device as their RR (only populated when this device is an RR). Each item has is_route_reflector/is_super_route_reflector flags so you can split mid-tier RR clients from regular clients with a Jinja `if`.
      - all_route_reflectors — every RR in this topology (super + non-super), excluding self. Use for flat full-mesh RR config.
      - super_route_reflectors — every super RR in this topology, excluding self. Use for super-RR mesh in hierarchical mode.
    - Custom fields: every admin-defined device attribute appears as `device.<key>`; every admin-defined interface-ref appears as `interface.<key>` resolving to the full iface dict (device.<key> is a legacy alias).
- `interfaces` — a list of dicts, one per interface on the device. Each interface has:
    - name, speed_gbps, description (from the device definition)
    - is_connected (bool — true if the interface is wired to a link)
    - For connected interfaces: ip (IPv4), ipv6, neighbor_device, neighbor_interface, neighbor_ip (IPv4), neighbor_ipv6, circuit_id, circuit_cost, link_id, bandwidth_gbps, latency_ms, carrier, color

**Route-reflection patterns:**
- Flat mode (no super RRs exist): every RR meshes with every other RR. On any RR, iterate `device.all_route_reflectors`. On a non-RR client, iterate `device.rr_peers` for its assigned RRs.
- Hierarchical mode (super RRs exist): super RRs mesh with each other (iterate `device.super_route_reflectors`), and have non-super RRs as clients (iterate `device.rr_clients`). Non-super RRs peer up to super RRs (iterate `device.rr_peers`) and have regular clients (iterate `device.rr_clients`, filter with `if not c.is_route_reflector`). Use the `is_super_route_reflector` / `is_route_reflector` flags on `device` to branch.

When you're unsure what's populated for a specific device, call `describe_variables` with that device's id — it returns the actual context dict as the renderer will see it.

**Jinja syntax notes:**
- Use `{% for iface in interfaces %} ... {% endfor %}` to loop interfaces.
- Use `{% if iface.is_connected %}` to skip dark interfaces.
- The renderer uses StrictUndefined — `{{ device.foo }}` where `foo` doesn't exist will be a render error. Stick to the documented fields.
- The renderer has `trim_blocks=True` and `lstrip_blocks=True`, so block tags don't leave blank lines.

**Template groups (default mode):**
Configurations are organized as groups of small focused templates ("files"):
banner, interfaces, OSPF, BGP, etc. — concatenated in order to form the
full device config. Treat a group like a project directory and each
template like a file.
- Call `list_groups` to see what groups exist. Call `read_group` to see
  a group's members in order and the body of each.
- When asked to add or change a feature in a device that has a group, find
  the relevant member template and edit just that one via set_template_body.
  Don't rewrite the whole group's worth of templates in one go.
- When asked to create new functionality, prefer creating a small new
  template (call `create_template`) and adding it to the group at an
  appropriate position (call `add_to_group`).
- When the user asks to build a config for a platform that has no group
  yet, call `create_group` first (give it a clear name like
  "Cisco IOS-XR Base", set a default rule if it should auto-apply:
  platform_default holds the VALUE and match_attr picks the device
  attribute it compares against — platform (default), vendor, model,
  role, location, device_template, or a custom device-attribute key.
  When a device matches several defaults, the more specific attribute
  wins: custom attr > location > device_template > model > vendor >
  role > platform), then create_template + add_to_group each focused
  section in render order.

**Workflow rules:**
- Always call `list_devices` first if you don't already know what devices the user has — picking a platform-correct render target is critical.
- When the user references a group or says "the BGP template in the iosxr group", call `list_groups` + `read_group` to find the right member, then set_template_body on its id.
- When the user says "like template X" or "match the FRR template", call `list_templates` to find ids and `read_template` to read the relevant body before writing your own — mimic structure where it makes sense.
- When the user's edit affects an EXISTING template, your set_template_body output replaces only the current draft — the operator reviews it as a pending change and explicitly Applies/Discards it. So your changes are reviewable; lean toward smaller focused edits rather than rewriting the whole template.
- After every `set_template_body` call, immediately call `render_template` against a device with the matching platform to verify.
- If rendering fails, read the error, fix the template, set_template_body again, render again.
- Keep templates focused — generate exactly what the user asked for. Don't add unrelated features unless asked.
- Use comments (`! ...` for Cisco, `# ...` for FRR/Linux, `/* ... */` for JunOS) sparingly. Real config syntax only — no markdown, no prose inside the template body.
- Final text response: short summary of what you built and any caveats. The template body is in the pending-edit pane; you don't need to paste it again."""


TOOLS = [
    {
        "name": "set_template_body",
        "description": "Replace the Jinja template body in the editor with new content. The change is applied live to the editor. The previous body is snapshotted client-side so the user can undo. Always follow this with a render_template call to verify the new body works.",
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "description": "The new Jinja template body. Should be a complete, valid Jinja2 template — not a partial edit or a diff.",
                },
            },
            "required": ["body"],
        },
    },
    {
        "name": "render_template",
        "description": "Render the current template body against a device in the topology. Returns the rendered config text on success, or a Jinja error message on failure. Use this after every set_template_body to verify the edit. Pass the node_id of a device whose platform matches the template's target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "integer",
                    "description": "The id of a device in the topology to render against.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "list_devices",
        "description": "List all devices in the user's current topology with their id, name, and platform. Use this to pick a render target whose platform matches what you're templating for.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "describe_variables",
        "description": "Show the exact variable namespace a template will see when rendered for a specific device — useful when you need to know what interface fields are populated for that device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "integer",
                    "description": "The id of a device. Returns the full context dict (device + interfaces).",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "list_templates",
        "description": "List every existing Jinja template in the library, with id, name, description, platform_default, and body length. Use this when the user says 'like the FRR template' or 'similar to template X' — read those for reference before writing your own.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_template",
        "description": "Return the full Jinja body of an existing template. Pair with list_templates to discover ids first. Use to mimic structure or extract reusable patterns from other templates the user has already authored.",
        "input_schema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template id from list_templates."},
            },
            "required": ["template_id"],
        },
    },
    {
        "name": "list_groups",
        "description": "List every template group with id, name, platform_default, and the ordered member list (template ids + names + body lengths). Groups are how multi-section configs are organized — each member is a focused template that renders in order to form a complete device config.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_group",
        "description": "Return a group's metadata and the body of every member template, in render order. Use this when the user asks to change a feature (BGP, OSPF, banner, etc.) in a device that uses a group — find the member to edit and set_template_body on just that one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer", "description": "Group id from list_groups."},
            },
            "required": ["group_id"],
        },
    },
    {
        "name": "create_template",
        "description": "Create a new ConfigTemplate row. Use when the user asks to add a feature that doesn't fit any existing template — e.g. a new section in a group. Pair with add_to_group to slot it into a group at a specific position.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique template name."},
                "description": {"type": "string", "description": "Short description of what this template generates."},
                "body": {"type": "string", "description": "The Jinja body. Should be a focused single-purpose snippet."},
                "platform_default": {"type": "string", "description": "Optional default-rule VALUE (field name is historical). With match_attr it makes this template the default for matching devices. Leave empty if the template is used as a group member.", "default": ""},
                "match_attr": {"type": "string", "description": "Which device attribute the default-rule value compares against: platform (default), vendor, model, role, location, device_template (NodeTemplate name), or a custom device-attribute key.", "default": "platform"},
            },
            "required": ["name", "body"],
        },
    },
    {
        "name": "add_to_group",
        "description": "Add an existing template to a group at a specific position (smaller = earlier). Position null appends to the end. Use after create_template to slot the new template into the render order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer", "description": "Target group id."},
                "template_id": {"type": "integer", "description": "Template id to add."},
                "position": {"type": "integer", "description": "0-based slot for the new member. Omit to append.", "default": None},
            },
            "required": ["group_id", "template_id"],
        },
    },
    {
        "name": "create_group",
        "description": "Create a new empty template group ('project of templates'). Use when the user asks to build a config from scratch for a platform that doesn't have a group yet — then create_template + add_to_group each focused section into the new group in render order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique group name (e.g. 'Cisco IOS-XR Base', 'FRR PE Standard')."},
                "description": {"type": "string", "description": "Short description of what this group's composite config does."},
                "platform_default": {"type": "string", "description": "Optional default-rule VALUE (e.g. 'iosxr', 'DC1'). With match_attr it makes this group the default for matching devices. A matching group trumps any matching single template.", "default": ""},
                "match_attr": {"type": "string", "description": "Which device attribute the default-rule value compares against: platform (default), vendor, model, role, location, device_template, or a custom device-attribute key. When a device matches several defaults, specificity wins: custom attr > location > device_template > model > vendor > role > platform.", "default": "platform"},
            },
            "required": ["name"],
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
    description: str = ""
    platform_default: str = ""
    match_attr: str = "platform"


class AIRequest(BaseModel):
    topology_id: int
    current_body: str = ""
    messages: list[AIMessage]
    # Metadata for the currently-focused template in the user's editor.
    # The conversation persists across template switches, so we re-inject
    # this on every turn — the agent always knows the latest focus.
    template_meta: Optional[TemplateMeta] = None


# ---------------------- Tool executor ----------------------

def _execute_tool(name: str, args: dict, topology_id: int, current_body_ref: list, db: Session) -> tuple[str, Optional[dict]]:
    """Run a tool server-side. Returns (text_for_agent, optional_sse_event).

    current_body_ref is a one-element list used as a mutable cell so we can
    update the in-flight body when the agent calls set_template_body.
    """
    if name == "set_template_body":
        new_body = args.get("body", "")
        current_body_ref[0] = new_body
        # The actual editor update happens client-side via the SSE event below.
        return (
            "Template body updated in the editor. Call render_template next to verify it works.",
            {"type": "set_body", "body": new_body},
        )

    if name == "render_template":
        node_id = args.get("node_id")
        node = db.query(NodeInstance).filter(NodeInstance.id == node_id).first()
        if not node:
            return (f"Error: node {node_id} not found in the topology.", None)
        if node.topology_id != topology_id:
            return (f"Error: node {node_id} is not in the current topology.", None)
        links = db.query(Link).filter(
            (Link.source_node_id == node.id) | (Link.target_node_id == node.id)
        ).all()
        outcome = render_for_node(current_body_ref[0], node, links, db=db)
        if outcome["ok"]:
            rendered = outcome["rendered"]
            # Cap what we send back to the agent — Sonnet doesn't need 50KB
            # of router config in its context window for verification.
            preview = rendered if len(rendered) <= 2000 else rendered[:2000] + f"\n... (truncated, total {len(rendered)} chars)"
            return (
                f"Render OK against device '{node.name}' (platform={node.platform}). Output:\n```\n{preview}\n```",
                {"type": "render_result", "node_id": node.id, "node_name": node.name, "ok": True, "rendered": rendered},
            )
        else:
            return (
                f"Render FAILED against device '{node.name}'. Error: {outcome['error']}"
                + (f" (line {outcome.get('line')})" if outcome.get("line") else ""),
                {"type": "render_result", "node_id": node.id, "node_name": node.name, "ok": False, "error": outcome["error"], "line": outcome.get("line")},
            )

    if name == "list_devices":
        nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
        rows = [
            {"id": n.id, "name": n.name, "platform": n.platform or "", "vendor": n.vendor or "", "role": n.role or "router"}
            for n in nodes
        ]
        return (json.dumps(rows, indent=2), None)

    if name == "describe_variables":
        node_id = args.get("node_id")
        node = db.query(NodeInstance).filter(NodeInstance.id == node_id).first()
        if not node:
            return (f"Error: node {node_id} not found.", None)
        links = db.query(Link).filter(
            (Link.source_node_id == node.id) | (Link.target_node_id == node.id)
        ).all()
        ctx = build_context(node, links, db=db)
        return (json.dumps(ctx, indent=2, default=str), None)

    if name == "list_templates":
        rows = db.query(ConfigTemplate).order_by(ConfigTemplate.name).all()
        items = [
            {
                "id": r.id, "name": r.name,
                "description": r.description or "",
                "platform_default": r.platform_default or "",
                "match_attr": r.match_attr or "platform",
                "body_length": len(r.body or ""),
            }
            for r in rows
        ]
        return (json.dumps(items, indent=2), None)

    if name == "read_template":
        template_id = args.get("template_id")
        t = db.query(ConfigTemplate).filter(ConfigTemplate.id == template_id).first()
        if not t:
            return (f"Error: template {template_id} not found.", None)
        body = t.body or ""
        # Cap response size — Sonnet doesn't need megabytes in context.
        if len(body) > 6000:
            body = body[:6000] + f"\n... (truncated, total {len(t.body or '')} chars)"
        return (
            f"Template '{t.name}' (id={t.id}, default rule: {_rule_str(t)}):\n```\n{body}\n```",
            None,
        )

    if name == "list_groups":
        from sqlalchemy.orm import selectinload
        from app.models.config_template_group import ConfigTemplateGroupMember
        groups = (
            db.query(ConfigTemplateGroup)
            .options(selectinload(ConfigTemplateGroup.members).selectinload(ConfigTemplateGroupMember.template))
            .order_by(ConfigTemplateGroup.name)
            .all()
        )
        items = []
        for g in groups:
            members = sorted(g.members, key=lambda m: m.position)
            items.append({
                "id": g.id, "name": g.name, "description": g.description or "",
                "platform_default": g.platform_default or "",
                "match_attr": g.match_attr or "platform",
                "members": [
                    {
                        "template_id": m.template_id,
                        "template_name": m.template.name if m.template else f"#{m.template_id}",
                        "position": m.position,
                        "body_length": len(m.template.body or "") if m.template else 0,
                    }
                    for m in members
                ],
            })
        return (json.dumps(items, indent=2), None)

    if name == "read_group":
        from sqlalchemy.orm import selectinload
        from app.models.config_template_group import ConfigTemplateGroupMember
        group_id = args.get("group_id")
        g = (
            db.query(ConfigTemplateGroup)
            .options(selectinload(ConfigTemplateGroup.members).selectinload(ConfigTemplateGroupMember.template))
            .filter(ConfigTemplateGroup.id == group_id).first()
        )
        if not g:
            return (f"Error: group {group_id} not found.", None)
        out_lines = [
            f"Group '{g.name}' (id={g.id}, default rule: {_rule_str(g)})",
            f"Description: {g.description or '(none)'}",
            "",
        ]
        for idx, m in enumerate(sorted(g.members, key=lambda x: x.position)):
            t = m.template
            if not t:
                out_lines.append(f"--- position {m.position}: (missing template #{m.template_id}) ---")
                continue
            body = t.body or ""
            if len(body) > 3000:
                body = body[:3000] + f"\n... (truncated, total {len(t.body or '')} chars)"
            out_lines.append(f"--- position {m.position}: template '{t.name}' (id={t.id}) ---")
            out_lines.append(body)
            out_lines.append("")
        return ("\n".join(out_lines), None)

    if name == "create_template":
        tname = args.get("name") or ""
        body = args.get("body") or ""
        if db.query(ConfigTemplate).filter(ConfigTemplate.name == tname).first():
            return (f"Error: template '{tname}' already exists. Use set_template_body to update it.", None)
        platform_default = args.get("platform_default") or ""
        match_attr = args.get("match_attr") or "platform"
        # One owner per (match_attr, value) pair — same rule as the REST routes.
        demote_default_owners(db, ConfigTemplate, match_attr, platform_default)
        t = ConfigTemplate(
            name=tname,
            description=args.get("description") or "",
            body=body,
            platform_default=platform_default,
            match_attr=match_attr,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return (f"Created template '{t.name}' (id={t.id}).", None)

    if name == "add_to_group":
        from app.models.config_template_group import ConfigTemplateGroupMember
        group_id = args.get("group_id")
        template_id = args.get("template_id")
        position = args.get("position")
        g = db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.id == group_id).first()
        if not g:
            return (f"Error: group {group_id} not found.", None)
        t = db.query(ConfigTemplate).filter(ConfigTemplate.id == template_id).first()
        if not t:
            return (f"Error: template {template_id} not found.", None)
        existing = db.query(ConfigTemplateGroupMember).filter(
            ConfigTemplateGroupMember.group_id == group_id,
            ConfigTemplateGroupMember.template_id == template_id,
        ).first()
        if existing:
            return (f"Template {template_id} is already in group {group_id}.", None)
        if position is None:
            position = db.query(ConfigTemplateGroupMember).filter(
                ConfigTemplateGroupMember.group_id == group_id
            ).count()
        db.add(ConfigTemplateGroupMember(group_id=group_id, template_id=template_id, position=int(position)))
        db.commit()
        return (f"Added template '{t.name}' to group '{g.name}' at position {position}.", None)

    if name == "create_group":
        gname = (args.get("name") or "").strip()
        if not gname:
            return ("Error: group name is required.", None)
        if db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.name == gname).first():
            return (f"Error: group '{gname}' already exists. Use add_to_group to extend it.", None)
        platform_default = args.get("platform_default") or ""
        match_attr = args.get("match_attr") or "platform"
        # Only one group can own a given (match_attr, value) pair — demote any
        # existing claimant of the SAME pair.
        demote_default_owners(db, ConfigTemplateGroup, match_attr, platform_default)
        g = ConfigTemplateGroup(
            name=gname,
            description=args.get("description") or "",
            platform_default=platform_default,
            match_attr=match_attr,
        )
        db.add(g)
        db.commit()
        db.refresh(g)
        suffix = f" (default for {match_attr}='{platform_default}')" if platform_default else ""
        return (f"Created group '{g.name}' (id={g.id}){suffix}. Use create_template + add_to_group to populate it.", None)

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
        # Mutable reference so tool executor can update the in-flight body.
        current_body_ref = [req.current_body]

        conv = llm.Conversation([m.model_dump() for m in req.messages], TOOLS)

        # Build a per-turn system prompt that pins the agent to whatever
        # template the user has focused right now. Re-evaluated each loop
        # iteration so renders/edits land on the right target even if the
        # conversation history references multiple templates.
        def _system_for_turn() -> str:
            tm = req.template_meta
            if not tm or tm.id is None:
                return SYSTEM_PROMPT
            extra = (
                "\n\n**Currently focused template (set_template_body acts on THIS one by default):**\n"
                f"- id: {tm.id}\n"
                f"- name: {tm.name or '(unnamed)'}\n"
                f"- default rule: {_rule_str(tm)}\n"
            )
            if tm.description:
                extra += f"- description: {tm.description}\n"
            return SYSTEM_PROMPT + extra

        max_iterations = 12  # generous — agent often iterates render → fix → render
        try:
            for _ in range(max_iterations):
                turn = conv.step(_system_for_turn(), max_tokens=8192)

                # Surface any text the agent produced this turn before processing tools.
                chunk_size = 32
                for i in range(0, len(turn.text), chunk_size):
                    yield f"data: {json.dumps({'type': 'token', 'text': turn.text[i:i+chunk_size]})}\n\n"

                if not turn.tool_calls:
                    break  # agent is done

                tool_results = []
                for call in turn.tool_calls:
                    yield f"data: {json.dumps({'type': 'tool_status', 'tool': call.name, 'input': call.input})}\n\n"
                    text, sse_event = _execute_tool(call.name, call.input, req.topology_id, current_body_ref, db)
                    if sse_event:
                        yield f"data: {json.dumps(sse_event)}\n\n"
                    tool_results.append((call.id, text))
                conv.add_tool_results(tool_results)
        except Exception as e:
            logger.exception("Config AI failed")
            yield f"data: {json.dumps({'type': 'token', 'text': f'AI assistant error: {e}'})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'final_body': current_body_ref[0]})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

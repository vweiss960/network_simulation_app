"""Config Design — Jinja2 rendering against device + link attributes.

All rendering happens in a SandboxedEnvironment so a template body can't
execute arbitrary Python (e.g. importing os, opening files). The variable
namespace passed to each render is:

    device       — dict of NodeInstance attrs (name, vendor, model, platform,
                   mgmt_ip, role, capacity_gbps, location, prometheus_id, ...)
    interface    — dict of custom interface references: interface.<key> is
                   the FULL interface dict the device designated for that
                   definition (e.g. interface.oob_interface.ip), or None.
                   device.<key> remains a legacy alias.
    interfaces   — list of dicts, one per interface on the device, augmented
                   with neighbor/link info if the interface is wired:
                     name, speed_gbps, description,
                     ip, neighbor_device, neighbor_interface, neighbor_ip,
                     circuit_id, circuit_cost, link_id, bandwidth_gbps,
                     latency_ms, carrier, color

A future phase will add a `globals` namespace (truly global key/value pairs).
"""
from typing import Optional
from sqlalchemy import func
from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

from app.models.config_template import ConfigTemplate
from app.models.config_template_group import ConfigTemplateGroup
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.attribute_definitions import (
    DeviceAttributeDefinition, InterfaceAttributeDefinition,
)


def _make_env() -> SandboxedEnvironment:
    # StrictUndefined surfaces typos (e.g. `{{ device.nmae }}`) as render errors
    # rather than silently emitting empty strings.
    env = SandboxedEnvironment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    return env


def build_context(
    node: NodeInstance,
    links: list[Link],
    db=None,
    iface_name_map: Optional[dict] = None,
) -> dict:
    """Build the per-device template context.

    `db` is optional — when provided, the function will look up custom device
    attribute definitions and route-reflector peer details. Callers that don't
    have a Session handy (or don't need those fields) can omit it; the
    template will still see device/interfaces, just without `device.rr_peers`
    resolved beyond ids and without custom-attribute defaults applied.

    `iface_name_map` is the design→lab interface translation produced by
    lab_view. When present, each interface dict's `name` field is rewritten
    to the lab-side name; link matching still uses the original names
    internally, so the substitution is transparent to the caller. Templates
    that loop `interfaces` therefore generate lab-correct config without any
    template change.
    """
    device = {
        "id": node.id,
        "name": node.name,
        "vendor": node.vendor or "",
        "model": node.model or "",
        "platform": node.platform or "",
        "capacity_gbps": node.capacity_gbps or 0,
        "mgmt_ip": node.mgmt_ip or "",
        "role": node.role or "router",
        "location": node.location or "",
        "prometheus_id": node.prometheus_id or "",
        "color": node.color or "",
        "is_planned": bool(node.is_planned),
        "mtbf_hours": node.mtbf_hours,
        "mttr_hours": node.mttr_hours,
        "latitude": node.latitude,
        "longitude": node.longitude,
        "is_route_reflector": bool(node.is_route_reflector),
        "is_super_route_reflector": bool(getattr(node, "is_super_route_reflector", 0)),
    }

    # Start from the device's interface list; layer in link info per interface.
    by_iface: dict[str, dict] = {}
    for iface in node.interfaces:
        name = iface.get("name", "")
        if not name:
            continue
        by_iface[name] = {
            "name": name,
            "speed_gbps": iface.get("speed_gbps", 0),
            "description": iface.get("description", ""),
            # Link-derived fields default to empty so templates don't need
            # to check for `is defined` constantly.
            "ip": "",
            "ipv6": "",
            "neighbor_device": "",
            "neighbor_interface": "",
            "neighbor_ip": "",
            "neighbor_ipv6": "",
            "circuit_id": "",
            "circuit_cost": 0.0,
            "link_id": "",
            "bandwidth_gbps": iface.get("speed_gbps", 0),
            "latency_ms": 0.0,
            "latency_source": "unknown",
            "carrier": "",
            "color": "",
            "is_connected": False,
        }

    # Effective latency per link. With a session, estimates can use every node
    # in the topology chain (site coordinates); without one, only known values.
    from app.services.latency import LatencyContext, build_context as latency_context, effective_latency
    if db is not None:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, node.topology_id)
        lat_ctx = latency_context(db, db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all())
    else:
        lat_ctx = LatencyContext()

    def lat_ctx_info(link):
        return effective_latency(link, lat_ctx)

    for link in links:
        if link.source_node_id == node.id:
            local_iface = link.source_interface
            local_ip = link.source_ip
            local_ipv6 = link.source_ipv6
            remote_node = link.target_node
            remote_iface = link.target_interface
            remote_ip = link.target_ip
            remote_ipv6 = link.target_ipv6
        elif link.target_node_id == node.id:
            local_iface = link.target_interface
            local_ip = link.target_ip
            local_ipv6 = link.target_ipv6
            remote_node = link.source_node
            remote_iface = link.source_interface
            remote_ip = link.source_ip
            remote_ipv6 = link.source_ipv6
        else:
            continue

        if local_iface and local_iface in by_iface:
            by_iface[local_iface].update({
                "ip": local_ip or "",
                "ipv6": local_ipv6 or "",
                "neighbor_device": (remote_node.name if remote_node else "") or "",
                "neighbor_interface": remote_iface or "",
                "neighbor_ip": remote_ip or "",
                "neighbor_ipv6": remote_ipv6 or "",
                "circuit_id": link.circuit_id or "",
                "circuit_cost": link.circuit_cost or 0.0,
                "link_id": link.link_id_label or "",
                "bandwidth_gbps": link.bandwidth_gbps or 0,
                # Effective latency: measured, else estimated, else 0 (unknown);
                # latency_source lets templates tell them apart.
                "latency_ms": lat_ctx_info(link).ms or 0.0,
                "latency_source": lat_ctx_info(link).source,
                "carrier": link.carrier or "",
                "color": link.color or "",
                "is_connected": True,
            })

    # If a lab translation map is supplied, rewrite each iface dict's `name`
    # field (and keep the original as `original_name` for templates that
    # need to reference the design-side identifier). Sort order stays on
    # the original key so encounter order is deterministic; the visible
    # name on the dict is the lab-translated one.
    if iface_name_map:
        for orig_name, entry in by_iface.items():
            mapped = iface_name_map.get(orig_name)
            if mapped:
                entry["original_name"] = entry.get("name", orig_name)
                entry["name"] = mapped

    interfaces = [by_iface[k] for k in sorted(by_iface.keys())]

    # ---- Route-reflector peers ----
    # Resolve rr_peer_ids to a list of compact peer dicts the template can
    # iterate. Requires the db so we can look up peers; if db is None, we
    # surface ids only (template can still iterate but won't have names/IPs).
    # Cache attribute definitions once — _rr_dict layers them onto each peer
    # so templates can access `peer.<custom_key>` (e.g. `peer.v4_index`) the
    # same way they access `device.<custom_key>` on the local node.
    _attr_defs_cache: list = []
    if db is not None:
        _attr_defs_cache = list(db.query(DeviceAttributeDefinition).all())

    def _rr_dict(p):
        out = {
            "id": p.id, "name": p.name, "platform": p.platform or "",
            "vendor": p.vendor or "", "mgmt_ip": p.mgmt_ip or "",
            "is_route_reflector": bool(p.is_route_reflector),
            "is_super_route_reflector": bool(getattr(p, "is_super_route_reflector", 0)),
            "location": p.location or "",
        }
        # Flatten every defined custom device attribute (typed) onto the
        # peer dict — falls back to the definition's default when this peer
        # hasn't set a value. Built-in keys never get overwritten.
        for d in _attr_defs_cache:
            if d.key in out:
                continue
            raw = p.custom_attrs.get(d.key, d.default_value)
            out[d.key] = _coerce_attr_value(raw, d.type)
        return out

    if db is not None and node.rr_peer_ids:
        peers = db.query(NodeInstance).filter(NodeInstance.id.in_(node.rr_peer_ids)).all()
        # Safety net: only CURRENT route reflectors render as peers — a stale
        # reference to a demoted device must not leak into generated config.
        device["rr_peers"] = [_rr_dict(p) for p in peers if p.is_route_reflector]
    else:
        device["rr_peers"] = [{"id": pid} for pid in node.rr_peer_ids]

    # ---- Route-reflector clients (inverse of rr_peers) ----
    # For RR devices, expose the list of clients pointing AT this RR — i.e.
    # every node in the same topology whose rr_peer_ids includes this node.
    # Non-RR devices always get an empty list so templates can use
    # `{% if device.rr_clients %}` uniformly. Requires db; without it, we
    # fall back to an empty list.
    device["rr_clients"] = []
    # Topology-wide RR lists — handy for flat full-mesh and for super-RR
    # auto-mesh in hierarchical mode. Always populated when db is provided;
    # the device's own entry is filtered out so templates can iterate safely.
    device["all_route_reflectors"] = []
    device["super_route_reflectors"] = []
    if db is not None:
        peers_in_topo = db.query(NodeInstance).filter(
            NodeInstance.topology_id == node.topology_id,
            NodeInstance.id != node.id,
        ).all()
        if node.is_route_reflector:
            clients = [p for p in peers_in_topo if node.id in (p.rr_peer_ids or [])]
            device["rr_clients"] = [_rr_dict(c) for c in clients]
        device["all_route_reflectors"] = [_rr_dict(p) for p in peers_in_topo if p.is_route_reflector]
        device["super_route_reflectors"] = [
            _rr_dict(p) for p in peers_in_topo
            if getattr(p, "is_super_route_reflector", 0)
        ]

    # ---- Custom device attributes ----
    # For every globally-defined device attribute, expose `device.<key>` with
    # either the per-device value or the definition's default (typed). Reuses
    # the cache populated above for _rr_dict so we don't hit the DB twice.
    if db is not None:
        for d in _attr_defs_cache:
            key = d.key
            if key in {"rr_peers", "is_route_reflector"} or key in device:
                continue  # don't let a custom def shadow built-ins
            raw = node.custom_attrs.get(key, d.default_value)
            device[key] = _coerce_attr_value(raw, d.type)

    # ---- Custom interface references ----
    # Each definition maps to ONE interface on the device. The template sees
    # `interface.<key>` as the full interface dict (or None if unset / the
    # interface was renamed away); `device.<key>` remains as a legacy alias.
    # Lookup against by_iface so we share field shape.
    iface_ns: dict = {}
    if db is not None:
        refs = node.custom_iface_refs
        for d in db.query(InterfaceAttributeDefinition).all():
            key = d.key
            iface_name = refs.get(key) or ""
            resolved = by_iface.get(iface_name)  # None if unset / not on device
            # Canonical home: interface.<key> — these are interface designations,
            # not device properties.
            iface_ns[key] = resolved
            # Legacy alias: device.<key> (pre-namespace templates). A device
            # attribute with the same key wins the device namespace.
            if key not in device:
                device[key] = resolved

    return {"device": device, "interfaces": interfaces, "interface": iface_ns}


def _coerce_attr_value(raw, attr_type: str):
    """Convert a stored string value into the template-facing native type."""
    if raw is None or raw == "":
        if attr_type == "boolean":
            return False
        if attr_type == "integer":
            return 0
        return ""
    if attr_type == "boolean":
        return str(raw).lower() in {"1", "true", "yes", "on"}
    if attr_type == "integer":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
    # string + select: pass through as-is
    return str(raw)


def validate_template(body: str) -> dict:
    """Compile-check a template body. Returns {ok, error, line}."""
    env = _make_env()
    try:
        env.from_string(body or "")
        return {"ok": True, "error": "", "line": None}
    except TemplateSyntaxError as e:
        return {"ok": False, "error": str(e.message), "line": e.lineno}


def render_for_node(
    body: str,
    node: NodeInstance,
    links: list[Link],
    db=None,
    iface_name_map: Optional[dict] = None,
) -> dict:
    """Render a template body for one device. Returns:
        {ok, rendered, error, line}
    Errors are returned (not raised) so the UI can show them inline.
    Pass `db` to expose custom attributes and resolved RR peers to the
    template; callers that don't need those can omit it. Pass
    `iface_name_map` to render with lab-translated interface names — see
    build_context for the semantics.
    """
    env = _make_env()
    try:
        tmpl = env.from_string(body or "")
    except TemplateSyntaxError as e:
        return {"ok": False, "rendered": "", "error": f"Syntax error: {e.message}", "line": e.lineno}

    ctx = build_context(node, links, db=db, iface_name_map=iface_name_map)
    try:
        output = tmpl.render(**ctx)
        return {"ok": True, "rendered": output, "error": "", "line": None}
    except UndefinedError as e:
        return {"ok": False, "rendered": "", "error": f"Undefined variable: {e.message}", "line": None}
    except Exception as e:
        return {"ok": False, "rendered": "", "error": str(e), "line": None}


# ---------------------------------------------------------------------------
# Default-rule matching. A template/group declares ONE rule: match_attr
# (which device attribute) + platform_default (the value — column name kept
# for API compatibility). match_attr is a builtin column, "device_template"
# (the NodeTemplate name), or a custom DeviceAttributeDefinition key.
# ---------------------------------------------------------------------------

BUILTIN_MATCH_ATTRS = {"platform", "vendor", "model", "role", "location", "device_template"}
# Builtin specificity, narrow -> broad. Custom attributes (anything not in
# BUILTIN_MATCH_ATTRS) rank above all builtins.
_SPECIFICITY = ["location", "device_template", "model", "vendor", "role", "platform"]


def demote_default_owners(db, model_cls, match_attr: str, value: str,
                          exclude_id: Optional[int] = None) -> None:
    """Enforce one owner per default-rule (match_attr, value) pair: clear
    platform_default on every OTHER row of model_cls (ConfigTemplate or
    ConfigTemplateGroup) currently claiming the same pair. No commit — the
    caller owns the transaction."""
    if not value:
        return
    q = db.query(model_cls).filter(
        model_cls.platform_default == value,
        func.coalesce(model_cls.match_attr, "platform") == (match_attr or "platform"),
    )
    if exclude_id is not None:
        q = q.filter(model_cls.id != exclude_id)
    for existing in q.all():
        existing.platform_default = ""


def _match_rank(attr: str) -> int:
    """Lower = more specific = wins when a device matches several defaults."""
    if attr not in BUILTIN_MATCH_ATTRS:
        return 0  # custom attribute — most specific
    return 1 + _SPECIFICITY.index(attr)


def _node_match_value(db, node: NodeInstance, attr: str):
    """The device-side value for a match attribute, or None when unset.
    Custom attributes come back coerced to their declared type."""
    if attr == "device_template":
        if not node.template_id or db is None:
            return None
        from app.models.node_template import NodeTemplate
        t = db.query(NodeTemplate).filter(NodeTemplate.id == node.template_id).first()
        return t.name if t else None
    if attr in BUILTIN_MATCH_ATTRS:
        return getattr(node, attr, None) or None
    return _custom_attr_value(db, node, attr)[1]


def _custom_attr_value(db, node: NodeInstance, attr: str):
    """(definition, coerced value|None) for a custom device attribute — the
    single read path (default_value fallback, ''/None = unset, type coercion)
    shared by _node_match_value and _rule_matches so unset semantics can't
    drift between specificity checks and actual matching."""
    if db is None:
        return None, None
    d = db.query(DeviceAttributeDefinition).filter(DeviceAttributeDefinition.key == attr).first()
    if not d:
        return None, None
    raw = node.custom_attrs.get(attr, d.default_value)
    if raw is None or raw == "":
        return d, None
    return d, _coerce_attr_value(raw, d.type)


def _rule_matches(db, node: NodeInstance, attr: str, value: str) -> bool:
    """Does `node.<attr> == value`? Custom-attr comparison coerces BOTH sides
    through the definition's type so '5'/5 and 'true'/True compare equal."""
    if attr in BUILTIN_MATCH_ATTRS:
        node_val = _node_match_value(db, node, attr)
        return node_val is not None and node_val == value
    d, node_val = _custom_attr_value(db, node, attr)
    if d is None or node_val is None:
        return False
    return node_val == _coerce_attr_value(value, d.type)


def _resolve_default(db, node: NodeInstance, rows):
    """Among rows (templates OR groups) with a default value set, return the
    one matching this node with the highest specificity; ties within a rank
    break on name (stable/documented). None when nothing matches."""
    matches = [r for r in rows
               if (r.platform_default or "") and _rule_matches(db, node, r.match_attr or "platform", r.platform_default)]
    if not matches:
        return None
    return min(matches, key=lambda r: (_match_rank(r.match_attr or "platform"), r.name or ""))


def resolve_template_for_node(db, node: NodeInstance, rows=None) -> Optional[ConfigTemplate]:
    """Pick the single ConfigTemplate that applies, in priority order:
        1. Explicit per-device override (node.config_template_id)
        2. Best-matching default rule (match_attr + value, by specificity)
    Returns None if neither resolves. NOTE: prefer resolve_for_node() which
    also handles template groups; this helper exists for legacy paths and
    callers that explicitly want a single template (not a group).
    """
    if node.config_template_id:
        t = db.query(ConfigTemplate).filter(ConfigTemplate.id == node.config_template_id).first()
        if t:
            return t
    if rows is None:
        rows = db.query(ConfigTemplate).filter(ConfigTemplate.platform_default != "").all()
    return _resolve_default(db, node, rows)


def resolve_group_for_node(db, node: NodeInstance, rows=None) -> Optional[ConfigTemplateGroup]:
    """Same idea as resolve_template_for_node but for groups: per-device
    override first, then the best-matching default rule (by specificity).
    Eager-loads members so the renderer can iterate without N+1.
    """
    from sqlalchemy.orm import selectinload
    q = db.query(ConfigTemplateGroup).options(selectinload(ConfigTemplateGroup.members))
    if node.config_template_group_id:
        g = q.filter(ConfigTemplateGroup.id == node.config_template_group_id).first()
        if g:
            return g
    if rows is None:
        rows = q.filter(ConfigTemplateGroup.platform_default != "").all()
    return _resolve_default(db, node, rows)


def resolve_for_node(db, node: NodeInstance, template_rows=None, group_rows=None):
    """Top-level resolver. Returns one of:
        ("group", ConfigTemplateGroup)   — group takes precedence
        ("template", ConfigTemplate)     — fallback to single-template path
        None                              — nothing resolves; device has no config
    """
    g = resolve_group_for_node(db, node, rows=group_rows)
    if g:
        return ("group", g)
    t = resolve_template_for_node(db, node, rows=template_rows)
    if t:
        return ("template", t)
    return None


def render_group_for_node(
    group: ConfigTemplateGroup,
    node: NodeInstance,
    links: list[Link],
    db=None,
    iface_name_map: Optional[dict] = None,
) -> dict:
    """Render each member template in order and concatenate with a single
    blank line between sections. Returns the same shape as render_for_node
    so callers can treat the two paths uniformly.

    On the first member error, the group render aborts and returns the
    failing member's name in the error message — partial output is dropped
    so the caller never sees a half-rendered config. Pass iface_name_map
    to render with lab-translated interface names.
    """
    parts: list[str] = []
    members = sorted(group.members, key=lambda m: m.position)
    for idx, member in enumerate(members):
        tmpl = member.template
        if not tmpl:
            continue
        outcome = render_for_node(tmpl.body or "", node, links, db=db, iface_name_map=iface_name_map)
        if not outcome["ok"]:
            return {
                "ok": False,
                "rendered": "",
                "error": f"Group '{group.name}' member '{tmpl.name}' (#{idx + 1}): {outcome['error']}",
                "line": outcome.get("line"),
                "failed_template_id": tmpl.id,
                "failed_template_name": tmpl.name,
            }
        parts.append(outcome["rendered"].rstrip())
    return {"ok": True, "rendered": "\n\n".join(p for p in parts if p) + ("\n" if parts else ""), "error": "", "line": None}

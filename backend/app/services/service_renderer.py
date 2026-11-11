"""Service module — Jinja2 rendering against caller-supplied variables.

Unlike config_renderer (which builds its context from topology device/interface
data), a Service template renders against the plain variable dict the caller
sends in the deploy request (the "Direct" variable model). We reuse
config_renderer's sandboxed environment so a template body still can't execute
arbitrary Python, and StrictUndefined still turns typos into render errors.

The render context is:
    <variables>   — every key from the deploy payload's `variables` dict,
                    referenced directly, e.g. {{ asn }}, {{ loopback }}
    device        — {name, mgmt_ip, kind, container} for the resolved target,
                    so templates can reference the box they're being pushed to
"""
from typing import Optional
from jinja2 import TemplateSyntaxError, UndefinedError

# Reuse the exact sandboxed env + syntax check from Config Design.
from app.services.config_renderer import _make_env, validate_template  # noqa: F401


def render_service_template(
    body: str,
    variables: dict,
    device_meta: Optional[dict] = None,
) -> dict:
    """Render one service template against a plain variable dict.

    Returns {ok, rendered, error, line}; errors are returned (not raised) so the
    caller can surface them per-device/per-template. `device_meta` is exposed as
    the `device` namespace; the caller-supplied `variables` are top-level.
    """
    env = _make_env()
    try:
        tmpl = env.from_string(body or "")
    except TemplateSyntaxError as e:
        return {"ok": False, "rendered": "", "error": f"Syntax error: {e.message}", "line": e.lineno}

    ctx = dict(variables or {})
    ctx["device"] = device_meta or {}
    try:
        return {"ok": True, "rendered": tmpl.render(**ctx), "error": "", "line": None}
    except UndefinedError as e:
        return {"ok": False, "rendered": "", "error": f"Undefined variable: {e.message}", "line": None}
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "rendered": "", "error": str(e), "line": None}

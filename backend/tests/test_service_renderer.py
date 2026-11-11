"""Service module — renderer + variable-resolution unit tests.

Pure logic (no lab/SSH): the Direct variable model renders the caller's variable
dict through the sandboxed Jinja env, and effective_variables merges declared
defaults + reports missing required variables.
"""
import json

from app.models.service import Service
from app.models.service_variable import ServiceVariable
from app.services.service_renderer import render_service_template
from app.services.service_deploy import effective_variables


def test_render_substitutes_variables_and_device():
    r = render_service_template(
        "router bgp {{ asn }}\n bgp router-id {{ loopback }}\n !! on {{ device.name }}",
        {"asn": 65001, "loopback": "1.1.1.1"},
        {"name": "MT-RR-1", "kind": "cisco_xrd"},
    )
    assert r["ok"], r["error"]
    assert "router bgp 65001" in r["rendered"]
    assert "bgp router-id 1.1.1.1" in r["rendered"]
    assert "on MT-RR-1" in r["rendered"]


def test_render_strict_undefined_is_an_error():
    # A typo'd variable must surface as an error, not a silent blank.
    r = render_service_template("hostname {{ naem }}", {"name": "R1"}, {})
    assert not r["ok"]
    assert "Undefined variable" in r["error"]


def test_render_syntax_error_is_returned_not_raised():
    r = render_service_template("{% for x in %}", {}, {})
    assert not r["ok"]
    assert "Syntax error" in r["error"]


def test_effective_variables_applies_defaults_and_flags_missing_required(db):
    s = Service(name="svc", target_mode="lab")
    db.add(s)
    db.flush()
    db.add(ServiceVariable(service_id=s.id, name="asn", required=True, default_json=""))
    db.add(ServiceVariable(service_id=s.id, name="mtu", required=False, default_json=json.dumps(1500)))
    db.commit()
    db.refresh(s)

    # supplied asn; mtu falls back to its declared default
    merged, missing = effective_variables(s, {"asn": 65001})
    assert merged["asn"] == 65001
    assert merged["mtu"] == 1500
    assert missing == []

    # required asn omitted and has no default → reported missing
    _merged2, missing2 = effective_variables(s, {})
    assert missing2 == ["asn"]

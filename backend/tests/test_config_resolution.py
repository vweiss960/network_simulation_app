"""Config Design default-rule resolution (attribute-based matching).

A template/group declares one default rule: match_attr (which device attribute)
+ platform_default (the value — column name is historical). Resolution:
explicit per-node assignment > matching group > matching template; among
matching defaults, attribute specificity wins:
custom attr > location > device_template > model > vendor > role > platform.
"""
import json

from app.models.config_template import ConfigTemplate
from app.models.config_template_group import ConfigTemplateGroup
from app.models.attribute_definitions import DeviceAttributeDefinition
from app.services.config_renderer import (
    resolve_template_for_node, resolve_group_for_node, resolve_for_node,
)
from tests.factories import make_topology, make_node


def _template(db, name, value="", attr="platform", body="!"):
    t = ConfigTemplate(name=name, body=body, platform_default=value, match_attr=attr)
    db.add(t)
    db.flush()
    return t


def test_platform_default_still_resolves(db):
    # Back-compat: default match_attr behaves exactly like the old platform rule.
    topo = make_topology(db, "T")
    n = make_node(db, topo, "R1", platform="iosxr")
    t = _template(db, "xr-base", value="iosxr")
    db.commit()
    assert resolve_template_for_node(db, n).id == t.id
    other = make_node(db, topo, "R2", platform="junos")
    assert resolve_template_for_node(db, other) is None


def test_location_rule_matches_only_that_location(db):
    topo = make_topology(db, "T")
    dc1 = make_node(db, topo, "R1", platform="iosxr", location="DC1")
    dc2 = make_node(db, topo, "R2", platform="iosxr", location="DC2")
    t = _template(db, "dc1-overrides", value="DC1", attr="location")
    db.commit()
    assert resolve_template_for_node(db, dc1).id == t.id
    assert resolve_template_for_node(db, dc2) is None


def test_device_template_rule_matches_node_template_name(db):
    from app.models.node_template import NodeTemplate
    topo = make_topology(db, "T")
    nt = NodeTemplate(name="ASR-9903", vendor="Cisco", model="9903", capacity_gbps=100)
    db.add(nt)
    db.flush()
    n = make_node(db, topo, "R1", template_id=nt.id)
    other = make_node(db, topo, "R2")
    t = _template(db, "asr-base", value="ASR-9903", attr="device_template")
    db.commit()
    assert resolve_template_for_node(db, n).id == t.id
    assert resolve_template_for_node(db, other) is None


def test_custom_attr_rule_matches_with_type_coercion(db):
    topo = make_topology(db, "T")
    db.add(DeviceAttributeDefinition(key="is_pe", label="PE", type="boolean"))
    db.add(DeviceAttributeDefinition(key="tier", label="Tier", type="integer"))
    db.flush()
    pe = make_node(db, topo, "R1", custom_attrs_json=json.dumps({"is_pe": "true", "tier": "2"}))
    non_pe = make_node(db, topo, "R2", custom_attrs_json=json.dumps({"is_pe": "false"}))
    t_bool = _template(db, "pe-config", value="true", attr="is_pe")
    db.commit()
    assert resolve_template_for_node(db, pe).id == t_bool.id
    assert resolve_template_for_node(db, non_pe) is None
    # Integer coercion: "2" matches 2.
    t_bool.platform_default = ""  # retire the bool rule
    t_int = _template(db, "tier2-config", value="2", attr="tier")
    db.commit()
    assert resolve_template_for_node(db, pe).id == t_int.id


def test_specificity_order(db):
    # location beats platform; custom attr beats location.
    topo = make_topology(db, "T")
    db.add(DeviceAttributeDefinition(key="svc_class", label="Class", type="string"))
    db.flush()
    n = make_node(db, topo, "R1", platform="iosxr", location="DC1",
                  custom_attrs_json=json.dumps({"svc_class": "gold"}))
    t_plat = _template(db, "by-platform", value="iosxr", attr="platform")
    t_loc = _template(db, "by-location", value="DC1", attr="location")
    db.commit()
    assert resolve_template_for_node(db, n).id == t_loc.id  # location > platform
    t_custom = _template(db, "by-class", value="gold", attr="svc_class")
    db.commit()
    assert resolve_template_for_node(db, n).id == t_custom.id  # custom > location


def test_override_and_group_precedence_unchanged(db):
    topo = make_topology(db, "T")
    n = make_node(db, topo, "R1", platform="iosxr", location="DC1")
    t_loc = _template(db, "by-location", value="DC1", attr="location")
    override = _template(db, "explicit", value="", attr="platform")
    n.config_template_id = override.id
    db.commit()
    # Explicit assignment beats even a more specific default rule.
    assert resolve_template_for_node(db, n).id == override.id
    # A matching group beats a matching template.
    n.config_template_id = None
    g = ConfigTemplateGroup(name="xr-group", platform_default="iosxr", match_attr="platform")
    db.add(g)
    db.commit()
    kind, obj = resolve_for_node(db, n)
    assert kind == "group" and obj.id == g.id
    assert resolve_template_for_node(db, n).id == t_loc.id  # template path still resolves


def test_demotion_is_scoped_to_the_attr_value_pair(db):
    # (route functions called directly — the config-design routers aren't
    # registered in the default test app since the module flag is off)
    from app.routes.config_templates import create_template, ConfigTemplateCreate
    _template(db, "xr-base", value="iosxr", attr="platform")
    _template(db, "dc1-base", value="DC1", attr="location")
    db.commit()
    # A new template claiming location=DC1 demotes only the DC1 owner.
    create_template(ConfigTemplateCreate(
        name="dc1-new", body="!", platform_default="DC1", match_attr="location"), db)
    rows = {t.name: (t.match_attr, t.platform_default) for t in db.query(ConfigTemplate).all()}
    assert rows["dc1-base"] == ("location", "")        # demoted
    assert rows["xr-base"] == ("platform", "iosxr")    # untouched
    assert rows["dc1-new"] == ("location", "DC1")


def test_ai_create_template_demotes_existing_owner(db):
    # The AI drawer's create_template tool must enforce the same one-owner-
    # per-(attr, value) rule as the REST routes (it used to skip demotion).
    from app.routes.config_ai import _execute_tool
    _template(db, "xr-old", value="iosxr", attr="platform")
    db.commit()
    msg, _ = _execute_tool("create_template",
                           {"name": "xr-new", "body": "!",
                            "platform_default": "iosxr", "match_attr": "platform"},
                           topology_id=0, current_body_ref=[None], db=db)
    assert "Created template" in msg
    rows = {t.name: t.platform_default for t in db.query(ConfigTemplate).all()}
    assert rows["xr-old"] == ""       # demoted
    assert rows["xr-new"] == "iosxr"  # sole owner


def test_get_generated_reports_group_resolution(db):
    from app.routes.config_templates import get_generated
    topo = make_topology(db, "T")
    n = make_node(db, topo, "R1", platform="iosxr")
    g = ConfigTemplateGroup(name="xr-group", platform_default="iosxr", match_attr="platform")
    db.add(g)
    db.commit()
    body = get_generated(n.id, db)
    assert body["resolved_kind"] == "group"
    assert body["resolved_name"] == "xr-group"


def test_interface_refs_live_in_interface_namespace_with_device_alias(db):
    # Custom interface references are exposed as interface.<key> (canonical)
    # with device.<key> kept as a legacy alias for older templates.
    from app.models.attribute_definitions import InterfaceAttributeDefinition
    from app.services.config_renderer import render_for_node
    topo = make_topology(db, "T")
    n = make_node(db, topo, "R1", interfaces=[{"name": "Gi0/0", "speed_gbps": 10}])
    db.add(InterfaceAttributeDefinition(key="oob", label="OOB"))
    db.flush()
    n.custom_iface_refs_json = json.dumps({"oob": "Gi0/0"})
    db.commit()

    r = render_for_node("{{ interface.oob.name }} / {{ device.oob.name }}", n, [], db=db)
    assert r["ok"], r["error"]
    assert r["rendered"] == "Gi0/0 / Gi0/0"

    # Unset ref renders as None-guardable (no crash, StrictUndefined-safe).
    n.custom_iface_refs_json = "{}"
    db.commit()
    r2 = render_for_node("{% if interface.oob %}set{% else %}unset{% endif %}", n, [], db=db)
    assert r2["ok"] and r2["rendered"] == "unset"

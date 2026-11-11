"""Schema-drift guard: EVERY persistent column of Node/Link/Flow must survive a
snapshot -> restore round-trip (the same machinery behind version restore and
edit-session discard/auto-discard).

Two layers:
  1. Coverage manifest — every column is classified as DENY (intentionally not
     persisted), REFERENTIAL (preserved by name/identity, not raw id), or PLAIN
     (raw value round-trips). Add a column and forget it → this test fails by
     name, forcing a conscious decision. This is what stops silent data loss
     (e.g. the route-reflector attributes) from recurring unnoticed.
  2. Behavioral — set a distinctive value on every column, snapshot, restore,
     and assert it comes back (raw for PLAIN, resolved identity for REFERENTIAL).
"""
import json

from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.flow import Flow
from app.models.customer import Customer
from app.models.config_template import ConfigTemplate
from app.routes.topologies import _serialize_topology, _restore_into_topology
from tests.factories import make_topology, make_node, make_link, make_flow


# id/topology_id are identity; generated_* are derived (regenerated on demand);
# availability_report_json is a derived Monte Carlo result.
NODE_DENY = {"id", "topology_id", "generated_config", "generated_lab_config"}
LINK_DENY = {"id", "topology_id"}
# flow.customer_id is legacy: a flow's customer is derived from its endpoint
# nodes (services/customer_flows.py), so it is neither exported nor restored.
FLOW_DENY = {"id", "topology_id", "availability_report_json", "customer_id"}

# Preserved by NAME/identity because the referenced row is recreated on restore.
NODE_REF = {"template_id", "customer_id", "rr_peer_ids_json"}
LINK_REF = {"source_node_id", "target_node_id"}
FLOW_REF = {"source_node_id", "dest_node_id",
            "explicit_path_json", "explicit_link_ids_json"}

NODE_PLAIN = {
    "name", "vendor", "model", "platform", "x", "y", "latitude", "longitude",
    "geo_latitude", "geo_longitude", "interfaces_json", "mtbf_hours", "mttr_hours",
    "capacity_gbps", "location", "prometheus_id", "mgmt_ip", "is_planned", "role",
    "color", "config_template_id", "config_template_group_id",
    "is_route_reflector", "is_super_route_reflector",
    "custom_attrs_json", "custom_iface_refs_json",
}
LINK_PLAIN = {
    "source_interface", "target_interface", "bandwidth_gbps", "cost", "latency_ms",
    "carrier", "mtbf_hours", "mttr_hours", "sr_attributes_json", "circuit_cost",
    "circuit_id", "link_id_label", "source_ip", "target_ip", "source_ipv6",
    "target_ipv6", "is_planned", "color", "geo_path_json", "latency_source", "transport", "orbit",
}
FLOW_PLAIN = {
    "name", "description", "bandwidth_gbps", "routing_behavior",
    "source_interface", "dest_interface", "ecmp_enabled",
}


# PatchPanel: identity/timestamps are not persisted; ports_json carries node/
# link refs that are exported by name/link key (see test_patch_panels.py for
# the behavioral round-trip).
PANEL_DENY = {"id", "topology_id", "created_at", "updated_at"}
PANEL_REF = {"ports_json", "in_front_of_id"}  # chain exported by panel name
PANEL_PLAIN = {"location", "name", "rack", "start_u", "height_u", "rows",
               "ports_per_row", "notes"}


def _cols(model):
    return {c.name for c in model.__table__.columns}


def test_patch_panel_manifest_matches_schema():
    from app.models.patch_panel import PatchPanel
    assert _cols(PatchPanel) == PANEL_DENY | PANEL_REF | PANEL_PLAIN


def test_coverage_manifest_matches_schema():
    """Drift alarm — a new column that isn't classified fails here. If this
    breaks: add the column to serialize/import + PLAIN (or REFERENTIAL), or to
    the DENY set with a reason."""
    assert _cols(NodeInstance) == NODE_DENY | NODE_REF | NODE_PLAIN
    assert _cols(Link) == LINK_DENY | LINK_REF | LINK_PLAIN
    assert _cols(Flow) == FLOW_DENY | FLOW_REF | FLOW_PLAIN


def test_node_columns_round_trip(db):
    topo = make_topology(db, "T")
    cust = Customer(topology_id=topo.id, name="Cust")
    ct = ConfigTemplate(name="ct-roundtrip")
    db.add_all([cust, ct])
    db.flush()
    a = make_node(db, topo, "A")
    b = make_node(db, topo, "B")
    # Distinctive value on every PLAIN column.
    a.vendor, a.model, a.platform = "Cisco-X", "M9", "iosxr"
    a.x, a.y, a.latitude, a.longitude = 123.5, -45.0, 10.0, 20.0
    a.geo_latitude, a.geo_longitude = 1.5, 2.5
    a.interfaces_json = json.dumps([{"name": "Gi0"}])
    a.mtbf_hours, a.mttr_hours, a.capacity_gbps = 5000.0, 3.0, 400.0
    a.location, a.prometheus_id, a.mgmt_ip = "DC1", "pid1", "10.0.0.9"
    a.is_planned, a.role, a.color = 1, "pe", "#abcdef"
    a.config_template_id, a.config_template_group_id = ct.id, None
    a.is_route_reflector, a.is_super_route_reflector = 1, 1
    a.custom_attrs_json = json.dumps({"asn": 65001})
    a.custom_iface_refs_json = json.dumps({"oob": "Gi0"})
    # Referential.
    a.customer_id = cust.id
    a.rr_peer_ids = [b.id]
    db.commit()
    before = {c: getattr(a, c) for c in _cols(NodeInstance)}

    _restore_into_topology(_serialize_topology(topo.id, db), topo.id, db)

    a2 = db.query(NodeInstance).filter_by(topology_id=topo.id, name="A").first()
    for col in NODE_PLAIN:
        assert getattr(a2, col) == before[col], f"node.{col} not preserved: {getattr(a2, col)!r} != {before[col]!r}"
    # Referential: re-resolved by identity (ids changed).
    b2 = db.query(NodeInstance).filter_by(topology_id=topo.id, name="B").first()
    assert a2.rr_peer_ids == [b2.id]
    assert db.query(Customer).get(a2.customer_id).name == "Cust"


def test_link_columns_round_trip(db):
    topo = make_topology(db, "T")
    a = make_node(db, topo, "A")
    b = make_node(db, topo, "B")
    l = make_link(db, topo, a, b)
    l.source_interface, l.target_interface = "Gi0/1", "Gi0/2"
    l.bandwidth_gbps, l.cost, l.latency_ms = 40.0, 7.0, 12.0
    # Non-defaults: the factory makes "measured" / "terrestrial" links.
    l.latency_source, l.transport, l.orbit = "unknown", "satellite", "geo"
    l.carrier, l.circuit_id, l.link_id_label = "Lumen", "CID-9", "L-9"
    l.mtbf_hours, l.mttr_hours, l.circuit_cost = 8000.0, 2.0, 99.0
    l.sr_attributes_json = json.dumps([{"sid": 16001}])
    l.source_ip, l.target_ip = "10.0.0.1", "10.0.0.2"
    l.source_ipv6, l.target_ipv6 = "2001:db8::1", "2001:db8::2"
    l.is_planned, l.color = 1, "#112233"
    l.geo_path_json = json.dumps([[[1.0, 2.0], [3.0, 4.0]]])
    db.commit()
    before = {c: getattr(l, c) for c in _cols(Link)}

    _restore_into_topology(_serialize_topology(topo.id, db), topo.id, db)

    l2 = db.query(Link).filter_by(topology_id=topo.id).first()
    for col in LINK_PLAIN:
        assert getattr(l2, col) == before[col], f"link.{col} not preserved: {getattr(l2, col)!r} != {before[col]!r}"
    a2 = db.query(NodeInstance).filter_by(topology_id=topo.id, name="A").first()
    b2 = db.query(NodeInstance).filter_by(topology_id=topo.id, name="B").first()
    assert {l2.source_node_id, l2.target_node_id} == {a2.id, b2.id}  # endpoints re-linked


def test_flow_columns_round_trip(db):
    topo = make_topology(db, "T")
    a = make_node(db, topo, "A")
    b = make_node(db, topo, "B")
    l = make_link(db, topo, a, b)
    f = make_flow(db, topo, "F1", a, b, bw=5.0)
    f.description, f.routing_behavior = "primary", "explicit_path"
    f.source_interface, f.dest_interface, f.ecmp_enabled = "Gi0/1", "Gi0/2", 1
    f.explicit_path_json = json.dumps([a.id, b.id])
    f.explicit_link_ids_json = json.dumps([l.id])
    db.commit()
    before = {c: getattr(f, c) for c in _cols(Flow)}

    _restore_into_topology(_serialize_topology(topo.id, db), topo.id, db)

    f2 = db.query(Flow).filter_by(topology_id=topo.id, name="F1").first()
    for col in FLOW_PLAIN:
        assert getattr(f2, col) == before[col], f"flow.{col} not preserved: {getattr(f2, col)!r} != {before[col]!r}"
    a2 = db.query(NodeInstance).filter_by(topology_id=topo.id, name="A").first()
    b2 = db.query(NodeInstance).filter_by(topology_id=topo.id, name="B").first()
    l2 = db.query(Link).filter_by(topology_id=topo.id).first()
    assert json.loads(f2.explicit_path_json) == [a2.id, b2.id]   # nodes re-resolved
    assert json.loads(f2.explicit_link_ids_json) == [l2.id]      # pinned link re-resolved

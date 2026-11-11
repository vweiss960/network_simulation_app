import pytest

from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.services import topology_io
from app.services.topology_ops import TopologyError
from tests.factories import make_link, make_node, make_topology


def test_export_import_round_trip(db):
    t = make_topology(db, "RT")
    a = make_node(db, t, name="P1", kind="cisco_xrd", image="xrd", startup_config="hostname P1")
    b = make_node(db, t, name="H1", icon="host", interface_count=1)
    make_link(db, t, a, b)
    text = topology_io.export_yaml(db, t.id)
    t2 = topology_io.import_topology(db, text, name="RT2")
    nodes = {n.name: n for n in db.query(NodeInstance).filter_by(topology_id=t2.id)}
    assert nodes["P1"].kind == "cisco_xrd" and nodes["P1"].startup_config == "hostname P1"
    assert len(nodes["H1"].interfaces) == 1
    (l,) = db.query(Link).filter_by(topology_id=t2.id).all()
    assert (l.source_interface, l.target_interface) == ("GigabitEthernet0/0/0/0", "eth1")


def test_import_containerlab_file_maps_kernel_names(db):
    clab = """
name: demo
topology:
  kinds:
    cisco_xrd: {image: ios-xr/xrd-control-plane:25.4.2}
  nodes:
    r1: {kind: cisco_xrd}
    r2: {kind: cisco_c8000v, image: vrnetlab/c8000v:17}
  links:
    - endpoints: ["r1:Gi0-0-0-5", "r2:eth2"]
"""
    t = topology_io.import_topology(db, clab)
    nodes = {n.name: n for n in db.query(NodeInstance).filter_by(topology_id=t.id)}
    assert nodes["r1"].image_ref == "ios-xr/xrd-control-plane:25.4.2"
    assert len(nodes["r1"].interfaces) == 6
    (l,) = db.query(Link).filter_by(topology_id=t.id).all()
    assert (l.source_interface, l.target_interface) == ("GigabitEthernet0/0/0/5", "GigabitEthernet3")


def test_import_rejects_unknown_kind(db):
    with pytest.raises(TopologyError, match="unknown kind"):
        topology_io.import_topology(db, "name: x\nnodes:\n  - {name: a, kind: bogus}\n")

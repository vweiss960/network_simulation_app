import pytest

from app.services.topology_ops import TopologyError, set_interface_count
from tests.factories import make_link, make_node, make_topology


def test_auto_names_by_icon(db):
    t = make_topology(db)
    assert make_node(db, t).name == "R1"
    assert make_node(db, t).name == "R2"
    assert make_node(db, t, icon="host").name == "H1"


def test_duplicate_name_rejected(db):
    t = make_topology(db)
    make_node(db, t, name="P1")
    with pytest.raises(TopologyError):
        make_node(db, t, name="P1")


def test_link_autopicks_next_free_interface(db):
    t = make_topology(db)
    a = make_node(db, t, kind="cisco_xrd", image="x")
    b = make_node(db, t, kind="cisco_xrd", image="x")
    c = make_node(db, t, kind="cisco_xrd", image="x")
    l1 = make_link(db, t, a, b)
    l2 = make_link(db, t, a, c)
    assert (l1.source_interface, l1.target_interface) == ("GigabitEthernet0/0/0/0", "GigabitEthernet0/0/0/0")
    assert l2.source_interface == "GigabitEthernet0/0/0/1"


def test_link_rejects_used_or_missing_interface(db):
    t = make_topology(db)
    a, b, c = make_node(db, t), make_node(db, t), make_node(db, t)
    make_link(db, t, a, b, "eth1", "eth1")
    with pytest.raises(TopologyError, match="already cabled"):
        make_link(db, t, a, c, "eth1", None)
    with pytest.raises(TopologyError, match="no interface"):
        make_link(db, t, a, c, "eth99", None)


def test_self_link_rejected(db):
    t = make_topology(db)
    a = make_node(db, t)
    with pytest.raises(TopologyError):
        make_link(db, t, a, a)


def test_link_fails_when_out_of_ports(db):
    t = make_topology(db)
    a = make_node(db, t, interface_count=1)
    b, c = make_node(db, t), make_node(db, t)
    make_link(db, t, a, b)
    with pytest.raises(TopologyError, match="no free interfaces"):
        make_link(db, t, a, c)


def test_shrinking_interfaces_blocked_when_cabled(db):
    t = make_topology(db)
    a, b = make_node(db, t, interface_count=4), make_node(db, t)
    make_link(db, t, a, b, "eth3", None)
    with pytest.raises(TopologyError, match="eth3"):
        set_interface_count(db, a, 2)
    set_interface_count(db, a, 3)
    assert len(a.interfaces) == 3


def test_nodes_without_position_are_spread_on_a_grid(db):
    t = make_topology(db)
    pos = {(n.x, n.y) for n in (make_node(db, t) for _ in range(6))}
    assert len(pos) == 6

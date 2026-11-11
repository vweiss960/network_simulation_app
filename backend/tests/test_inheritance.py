"""topology_chain.get_merged_topology_data: parent/child planning layers.

The merged view = inherited (parent) items + local (child) items, with
same-name children shadowed by the parent (parent wins) and endpoints remapped.
"""
from app.services.topology_chain import get_merged_topology_data
from tests.factories import make_topology, make_node, make_link


def test_child_inherits_parent_and_adds_local(db):
    parent = make_topology(db, "parent")
    a = make_node(db, parent, "A")
    b = make_node(db, parent, "B")
    make_link(db, parent, a, b)

    child = make_topology(db, "child", parent_id=parent.id)
    c = make_node(db, child, "C")
    make_link(db, child, b, c)  # local link from inherited B to local C

    nodes, links, flows, remap = get_merged_topology_data(db, child.id)

    assert {n.name for n in nodes} == {"A", "B", "C"}
    assert len(links) == 2          # parent A-B + child B-C
    assert remap == {}              # no name collisions


def test_same_name_child_node_is_shadowed_and_remapped(db):
    parent = make_topology(db, "parent")
    a = make_node(db, parent, "A")
    parent_b = make_node(db, parent, "B")

    child = make_topology(db, "child", parent_id=parent.id)
    child_b = make_node(db, child, "B")   # same name -> shadowed by parent's B
    c = make_node(db, child, "C")
    child_link = make_link(db, child, child_b, c)  # references the shadowed child B

    nodes, links, flows, remap = get_merged_topology_data(db, child.id)

    # Only one "B" survives, and it's the parent's (root wins).
    assert {n.name for n in nodes} == {"A", "B", "C"}
    b_nodes = [n for n in nodes if n.name == "B"]
    assert len(b_nodes) == 1 and b_nodes[0].id == parent_b.id

    # The child's shadowed B is remapped to the parent's B...
    assert remap.get(child_b.id) == parent_b.id
    # ...and the child link's endpoint is rewritten to the surviving B.
    merged_link = next(l for l in links if l.id == child_link.id)
    assert merged_link.source_node_id == parent_b.id
    assert merged_link.target_node_id == c.id


def test_grandparent_link_absorbs_middle_layer_link_in_grandchild(db):
    """A child link later duplicated in the grandparent is hidden in the child
    AND in the grandchild — the highest ancestor's link always wins."""
    from app.services.topology_chain import get_nullified_child_link_ids
    root = make_topology(db, "root")
    a, b = make_node(db, root, "A"), make_node(db, root, "B")
    child = make_topology(db, "child", parent_id=root.id)
    child_ab = make_link(db, child, a, b)         # planning link in the child
    root_ab = make_link(db, root, a, b)           # root later adds the official one
    grandchild = make_topology(db, "grandchild", parent_id=child.id)
    c = make_node(db, grandchild, "C")
    make_link(db, grandchild, b, c)

    assert child_ab.id in get_nullified_child_link_ids(db, child.id)
    assert child_ab.id in get_nullified_child_link_ids(db, grandchild.id)
    _, links, _, _ = get_merged_topology_data(db, grandchild.id)
    ab = [l for l in links if {l.source_node_id, l.target_node_id} == {a.id, b.id}]
    assert [l.id for l in ab] == [root_ab.id]
    assert len(links) == 2   # root A-B + grandchild B-C


def test_middle_layer_link_absorbs_grandchild_duplicate(db):
    """The rule applies at every level: a grandchild link duplicating its
    parent's (the middle layer's) link is hidden; the middle link stays."""
    root = make_topology(db, "root")
    a, b = make_node(db, root, "A"), make_node(db, root, "B")
    child = make_topology(db, "child", parent_id=root.id)
    child_ab = make_link(db, child, a, b)
    grandchild = make_topology(db, "grandchild", parent_id=child.id)
    make_link(db, grandchild, a, b)
    _, links, _, _ = get_merged_topology_data(db, grandchild.id)
    assert [l.id for l in links] == [child_ab.id]


def test_absorbed_link_keeps_its_ports_reserved_in_grandchild(db):
    """A hidden (absorbed) middle-layer link still holds its interfaces, so a
    grandchild can't double-book them if the absorbing link is later removed."""
    from app.routes.links import _get_used_interfaces
    root = make_topology(db, "root")
    ifaces = [{"name": "eth1", "speed_gbps": 10.0}, {"name": "eth2", "speed_gbps": 10.0}]
    a = make_node(db, root, "A", interfaces=ifaces)
    b = make_node(db, root, "B", interfaces=ifaces)
    mid = make_topology(db, "mid", parent_id=root.id)
    make_link(db, mid, a, b, source_interface="eth1", target_interface="eth1")
    make_link(db, root, a, b, source_interface="eth2", target_interface="eth2")   # absorbs mid's
    leaf = make_topology(db, "leaf", parent_id=mid.id)
    assert _get_used_interfaces(leaf.id, a.id, db) == {"eth1", "eth2"}


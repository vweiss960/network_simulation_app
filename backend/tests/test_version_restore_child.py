"""Version snapshot/restore must not drop a CHILD topology's links/flows that
reference inherited (ancestor) nodes.

Regression: _serialize_topology built its node-name map from the child's OWN
nodes only, so a child link/flow touching an inherited parent node serialized
with an empty endpoint name and was dropped on restore (e.g. 157 links -> 131).
"""
from app.routes.topologies import _serialize_topology, _restore_into_topology
from tests.factories import make_topology, make_node, make_link, make_flow


def _parent_child_with_inherited_links(db):
    parent = make_topology(db, "parent")
    a = make_node(db, parent, "A")
    b = make_node(db, parent, "B")
    child = make_topology(db, "child", parent_id=parent.id)
    x = make_node(db, child, "X")
    # Child-local links that reference INHERITED parent nodes:
    make_link(db, child, a, x)   # inherited A -> local X
    make_link(db, child, a, b)   # inherited A -> inherited B
    make_link(db, child, x, x)   # local -> local
    make_flow(db, child, "Fch", a, x)  # flow from inherited node to local node
    db.commit()
    return parent, child, {a.id, b.id}


def test_serialize_child_keeps_inherited_endpoint_names(db):
    parent, child, parent_ids = _parent_child_with_inherited_links(db)
    snap = _serialize_topology(child.id, db)
    # No link/flow endpoint serialized as an empty name.
    assert all(l["source_node"] and l["target_node"] for l in snap["links"])
    assert all(f["source_node"] and f["dest_node"] for f in snap["flows"])
    pairs = {(l["source_node"], l["target_node"]) for l in snap["links"]}
    assert ("A", "X") in pairs and ("A", "B") in pairs


def test_restore_child_preserves_rr_peer_to_inherited_node(db):
    """A child RR client peering with an INHERITED (parent) route reflector must
    survive snapshot/restore — the peer is serialized by name and re-resolved via
    the inherited fallback, exactly like inherited link endpoints."""
    from app.models.node_instance import NodeInstance
    parent = make_topology(db, "p")
    a = make_node(db, parent, "A")
    a.is_route_reflector = 1
    child = make_topology(db, "c", parent_id=parent.id)
    x = make_node(db, child, "X")
    x.rr_peer_ids = [a.id]  # child client peers with the inherited parent RR
    db.commit()
    a_id = a.id  # inherited parent node is NOT deleted, so its id is stable

    _restore_into_topology(_serialize_topology(child.id, db), child.id, db)

    x2 = db.query(NodeInstance).filter_by(topology_id=child.id, name="X").first()
    assert x2.rr_peer_ids == [a_id]  # re-resolved to the inherited parent RR


def test_restore_child_preserves_view_membership_and_exclusions(db):
    """A child view mixing an INHERITED member (stable id) and a LOCAL member
    (id changes on restore) must survive snapshot/restore, along with the
    default-view exclusion list. Members are serialized by NAME; a decoy row in
    another topology forces the local node's id to actually shift (SQLite would
    otherwise reuse the freed rowid and mask a broken remap)."""
    import json
    from app.models.node_instance import NodeInstance
    from app.models.topology_view import TopologyView
    parent = make_topology(db, "p")
    a = make_node(db, parent, "A")
    child = make_topology(db, "c", parent_id=parent.id)
    x = make_node(db, child, "X")
    db.add(TopologyView(topology_id=child.id, name="MIX",
                        node_ids_json=json.dumps([a.id, x.id])))
    child.default_view_excluded_node_ids = [x.id]
    db.commit()
    old_x = x.id

    snap = _serialize_topology(child.id, db)
    other = make_topology(db, "other")
    make_node(db, other, "decoy")  # advance the global rowid counter
    db.commit()

    _restore_into_topology(snap, child.id, db)

    x2 = db.query(NodeInstance).filter_by(topology_id=child.id, name="X").first()
    assert x2.id != old_x  # the id really shifted — remap was exercised
    v = db.query(TopologyView).filter_by(topology_id=child.id).first()
    assert set(v.node_ids) == {a.id, x2.id}
    assert db.query(type(child)).get(child.id).default_view_excluded_node_ids == [x2.id]


def test_restore_view_resolves_shadowed_names_to_rendered_parent(db):
    """A full-copy child contains local duplicates of parent nodes (same
    names); the canvas renders the PARENT row (parent wins) and the view
    stores the parent id. Restore must resolve those members back to the
    parent (rendered) row — resolving to the shadowed local copy produces
    members that exist but never render, i.e. an empty-looking view."""
    import json
    from app.models.node_instance import NodeInstance
    from app.models.topology_view import TopologyView
    parent = make_topology(db, "p")
    a = make_node(db, parent, "A")
    child = make_topology(db, "c", parent_id=parent.id)
    make_node(db, child, "A")  # shadowed local copy (parent wins on render)
    db.add(TopologyView(topology_id=child.id, name="V",
                        node_ids_json=json.dumps([a.id])))  # rendered = parent id
    db.commit()

    _restore_into_topology(_serialize_topology(child.id, db), child.id, db)

    v = db.query(TopologyView).filter_by(topology_id=child.id).first()
    assert v.node_ids == [a.id]  # still the parent (rendered) row
    shadow = db.query(NodeInstance).filter_by(topology_id=child.id, name="A").first()
    assert shadow.id not in v.node_ids  # never the invisible shadow


def test_duplicate_carries_views(client, db):
    """Duplicating a topology must copy its views with membership remapped to
    the duplicate's own (new) node ids — the old hand-rolled duplicate dropped
    views entirely."""
    import json
    from app.models.node_instance import NodeInstance
    from app.models.topology_view import TopologyView
    t = make_topology(db, "orig")
    a = make_node(db, t, "A")
    make_node(db, t, "B")
    db.add(TopologyView(topology_id=t.id, name="V", node_ids_json=json.dumps([a.id])))
    db.commit()

    r = client.post(f"/api/topologies/{t.id}/duplicate")
    assert r.status_code == 200
    new_id = r.json()["id"]
    v = db.query(TopologyView).filter_by(topology_id=new_id).first()
    assert v is not None and v.name == "V"
    new_a = db.query(NodeInstance).filter_by(topology_id=new_id, name="A").first()
    assert v.node_ids == [new_a.id]


def test_restore_child_preserves_inherited_links_and_flows(db):
    from app.models.link import Link
    from app.models.flow import Flow
    parent, child, parent_ids = _parent_child_with_inherited_links(db)
    snap = _serialize_topology(child.id, db)
    before = db.query(Link).filter(Link.topology_id == child.id).count()

    _restore_into_topology(snap, child.id, db)

    after = db.query(Link).filter(Link.topology_id == child.id).count()
    assert after == before == 3  # no links dropped
    # The inherited-referencing links re-link to the EXISTING parent nodes.
    restored = db.query(Link).filter(Link.topology_id == child.id).all()
    inherited_refs = [l for l in restored
                      if l.source_node_id in parent_ids or l.target_node_id in parent_ids]
    assert len(inherited_refs) == 2
    # The flow from an inherited node survived too.
    assert db.query(Flow).filter(Flow.topology_id == child.id).count() == 1


def test_restore_grandchild_preserves_links_to_grandparent_and_parent_nodes(db):
    """Grandchild topologies: links / flows / link IDs that reference a
    GRANDPARENT node and a PARENT (middle layer) node survive a snapshot +
    restore (the round-trip behind version restore and protected-topology
    discard) and re-link to the existing ancestor nodes."""
    from app.models.link import Link
    from app.models.flow import Flow
    root = make_topology(db, "root")
    g1 = make_node(db, root, "G1")
    mid = make_topology(db, "mid", parent_id=root.id)
    p1 = make_node(db, mid, "P1")
    leaf = make_topology(db, "leaf", parent_id=mid.id)
    c1 = make_node(db, leaf, "C1")
    make_link(db, leaf, g1, c1, link_id_label="LID-G1-C1", circuit_id="CKT-1")   # grandparent ↔ local
    make_link(db, leaf, p1, c1, link_id_label="LID-P1-C1")                       # parent ↔ local
    make_link(db, leaf, g1, p1, link_id_label="LID-G1-P1")                       # grandparent ↔ parent
    make_flow(db, leaf, "F-g1", g1, c1)
    db.commit()

    _restore_into_topology(_serialize_topology(leaf.id, db), leaf.id, db)

    links = db.query(Link).filter(Link.topology_id == leaf.id).all()
    by_label = {l.link_id_label: l for l in links}
    assert set(by_label) == {"LID-G1-C1", "LID-P1-C1", "LID-G1-P1"}       # none dropped, IDs kept
    assert by_label["LID-G1-C1"].circuit_id == "CKT-1"
    c1_new = next(l for l in links if l.link_id_label == "LID-P1-C1").target_node_id
    assert {by_label["LID-G1-C1"].source_node_id, by_label["LID-G1-C1"].target_node_id} == {g1.id, c1_new}
    assert {by_label["LID-G1-P1"].source_node_id, by_label["LID-G1-P1"].target_node_id} == {g1.id, p1.id}
    flow = db.query(Flow).filter(Flow.topology_id == leaf.id).one()
    assert flow.source_node_id == g1.id

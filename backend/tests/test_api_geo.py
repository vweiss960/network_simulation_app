"""Geographic coordinates: editable on local nodes, parent-only for inherited.

Geo (real-world map) coords differ from logical lat/long. A node local to the
requesting topology can set them; an inherited node's geo is owned by the parent,
so a child can't change it (and never sees a per-child geo override).
"""
from tests.factories import make_topology, make_node


def test_local_node_geo_edit_persists(client, db):
    t = make_topology(db, "geo")
    a = make_node(db, t, "A")
    db.commit()

    r = client.put(f"/api/topologies/{t.id}/nodes/{a.id}", json={"geo_latitude": 40.7128, "geo_longitude": -74.006})
    assert r.status_code == 200
    assert r.json()["geo_latitude"] == 40.7128 and r.json()["geo_longitude"] == -74.006

    nodes = {n["id"]: n for n in client.get(f"/api/topologies/{t.id}/nodes/").json()}
    assert nodes[a.id]["geo_latitude"] == 40.7128


def test_inherited_node_geo_edit_rejected(client, db):
    parent = make_topology(db, "parent")
    p = make_node(db, parent, "P", geo_latitude=10.0, geo_longitude=20.0)
    child = make_topology(db, "child", parent_id=parent.id)
    db.commit()

    # The node is inherited in the child → geo edit is parent-only → 403.
    r = client.put(f"/api/topologies/{child.id}/nodes/{p.id}", json={"geo_latitude": 99.0, "geo_longitude": 88.0})
    assert r.status_code == 403


def test_inherited_node_logical_override_still_works(client, db):
    # Logical (x/y) layout overrides on inherited nodes are unchanged.
    parent = make_topology(db, "parent")
    p = make_node(db, parent, "P", x=1.0, y=2.0)
    child = make_topology(db, "child", parent_id=parent.id)
    db.commit()

    r = client.put(f"/api/topologies/{child.id}/nodes/{p.id}", json={"x": 500.0, "y": 600.0})
    assert r.status_code == 200

    nodes = {n["id"]: n for n in client.get(f"/api/topologies/{child.id}/nodes/").json()}
    assert nodes[p.id]["x"] == 500.0 and nodes[p.id]["y"] == 600.0  # child override applied


def test_inherited_node_shows_parent_geo_ignoring_override(client, db):
    # Even a pre-existing per-child geo override must not surface — an inherited
    # node's geo always reflects the parent.
    from app.models.node_position_override import NodePositionOverride
    parent = make_topology(db, "parent")
    p = make_node(db, parent, "P", geo_latitude=10.0, geo_longitude=20.0)
    child = make_topology(db, "child", parent_id=parent.id)
    db.add(NodePositionOverride(topology_id=child.id, node_id=p.id, geo_latitude=99.0, geo_longitude=88.0))
    db.commit()

    nodes = {n["id"]: n for n in client.get(f"/api/topologies/{child.id}/nodes/").json()}
    assert nodes[p.id]["is_inherited"] is True
    assert nodes[p.id]["geo_latitude"] == 10.0 and nodes[p.id]["geo_longitude"] == 20.0

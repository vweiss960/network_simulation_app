"""Customer listing must be inheritance-aware.

A child topology's merged nodes/flows can reference ancestor customers, so
GET /customers/ returns the whole chain's customers (child + ancestors). The
parent's own list is unaffected, and mutation stays own-topology-scoped: a
child cannot edit/delete an ancestor customer (404).
"""
from app.models.customer import Customer
from tests.factories import make_topology


def _seed(db):
    parent = make_topology(db, "p")
    child = make_topology(db, "c", parent_id=parent.id)
    pc = Customer(topology_id=parent.id, name="ParentCust")
    cc = Customer(topology_id=child.id, name="ChildCust")
    db.add_all([pc, cc])
    db.commit()
    return parent, child, pc, cc


def test_child_list_includes_ancestor_customers(client, db):
    parent, child, pc, cc = _seed(db)
    names = {c["name"]: c["topology_id"] for c in client.get(f"/api/topologies/{child.id}/customers/").json()}
    assert set(names) == {"ParentCust", "ChildCust"}
    # topology_id lets the UI mark ancestor customers read-only.
    assert names["ParentCust"] == parent.id and names["ChildCust"] == child.id


def test_parent_list_stays_own_only(client, db):
    parent, child, pc, cc = _seed(db)
    names = {c["name"] for c in client.get(f"/api/topologies/{parent.id}/customers/").json()}
    assert names == {"ParentCust"}  # child customers never leak upward


def test_child_cannot_mutate_ancestor_customer(client, db):
    parent, child, pc, cc = _seed(db)
    assert client.put(f"/api/topologies/{child.id}/customers/{pc.id}", json={"name": "X"}).status_code == 404
    assert client.delete(f"/api/topologies/{child.id}/customers/{pc.id}").status_code == 404

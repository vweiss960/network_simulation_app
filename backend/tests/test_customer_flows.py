"""Flow -> customer membership is derived from the flow's endpoint nodes."""
from app.models.customer import Customer
from app.services.customer_flows import node_customer_map, flow_customer_ids
from app.services.cost_allocation import run_cost_allocation
from tests.factories import make_topology, make_node, make_link, make_flow


def _seed(db):
    """CA - A - B - CB: CA owned by Acme, CB by Globex; one link with a circuit cost."""
    t = make_topology(db, "custFlows")
    acme = Customer(topology_id=t.id, name="Acme")
    globex = Customer(topology_id=t.id, name="Globex")
    db.add_all([acme, globex])
    db.flush()
    ca = make_node(db, t, "CA", role="customer", customer_id=acme.id)
    cb = make_node(db, t, "CB", role="customer", customer_id=globex.id)
    a = make_node(db, t, "A")
    b = make_node(db, t, "B")
    make_link(db, t, ca, a, bw=100.0)
    make_link(db, t, a, b, bw=100.0, circuit_cost=1000.0)
    make_link(db, t, b, cb, bw=100.0)
    return t, acme, globex, (ca, cb, a, b)


def test_membership_from_either_endpoint(db):
    t, acme, globex, (ca, cb, a, b) = _seed(db)
    to_acme = make_flow(db, t, "toAcme", a, ca)       # dest node is Acme's
    between = make_flow(db, t, "between", ca, cb)     # A-end Acme, Z-end Globex
    core = make_flow(db, t, "core", a, b)             # no customer node
    loop = make_flow(db, t, "loop", ca, ca)           # same customer both ends
    db.commit()
    nc = node_customer_map([ca, cb, a, b])
    assert flow_customer_ids(to_acme, nc) == [acme.id]
    assert flow_customer_ids(between, nc) == [acme.id, globex.id]
    assert flow_customer_ids(core, nc) == []
    assert flow_customer_ids(loop, nc) == [acme.id]


def test_legacy_flow_customer_id_is_ignored(db):
    t, acme, globex, (ca, cb, a, b) = _seed(db)
    f = make_flow(db, t, "core", a, b)
    f.customer_id = acme.id  # stale manual assignment
    db.commit()
    assert flow_customer_ids(f, node_customer_map([ca, cb, a, b])) == []


def test_cost_split_evenly_for_flow_between_two_customers(db):
    t, acme, globex, (ca, cb, a, b) = _seed(db)
    make_flow(db, t, "between", ca, cb, bw=10.0)
    db.commit()
    r = run_cost_allocation(t.id, db, "proportional_used")
    costs = {c["customer_name"]: c["total_cost"] for c in r["customers"]}
    assert costs == {"Acme": 500.0, "Globex": 500.0}  # no double counting
    assert r["summary"]["flows_assigned"] == 1
    assert r["summary"]["total_allocated_cost"] == 1000.0


def test_stale_customer_id_on_router_node_is_ignored(db):
    t, acme, globex, (ca, cb, a, b) = _seed(db)
    a.customer_id = acme.id  # left over after switching the node back to router
    f = make_flow(db, t, "core", a, b)
    db.commit()
    assert flow_customer_ids(f, node_customer_map([ca, cb, a, b])) == []

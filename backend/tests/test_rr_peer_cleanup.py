"""Demoting or deleting a route reflector must strip it from every device's
rr_peer_ids — including nodes in CHILD topologies peering with an inherited
ancestor RR. Stale references otherwise inflate peer counts in the UI and
(pre-safety-net) leaked demoted peers into rendered configs.
"""
from app.routes.nodes import update_node, delete_node
from app.schemas.schemas import NodeInstanceUpdate
from app.services.config_renderer import build_context
from tests.factories import make_topology, make_node


def _seed(db):
    parent = make_topology(db, "p")
    rr = make_node(db, parent, "RR1")
    rr.is_route_reflector = 1
    client_node = make_node(db, parent, "C1")
    client_node.rr_peer_ids = [rr.id]
    child = make_topology(db, "c", parent_id=parent.id)
    child_client = make_node(db, child, "CC1")
    child_client.rr_peer_ids = [rr.id]  # child peering with inherited RR
    db.commit()
    return parent, rr, client_node, child_client


def test_demoting_rr_strips_peer_references_everywhere(db):
    parent, rr, client_node, child_client = _seed(db)
    update_node(parent.id, rr.id, NodeInstanceUpdate(is_route_reflector=False), db)
    db.expire_all()
    assert client_node.rr_peer_ids == []
    assert child_client.rr_peer_ids == []   # child topology cleaned too


def test_deleting_rr_strips_peer_references(db):
    parent, rr, client_node, child_client = _seed(db)
    delete_node(parent.id, rr.id, db)
    db.expire_all()
    assert client_node.rr_peer_ids == []
    assert child_client.rr_peer_ids == []


def test_renderer_never_exposes_non_rr_peers(db):
    # Safety net: even if a stale reference exists, device.rr_peers only
    # contains CURRENT route reflectors.
    parent, rr, client_node, child_client = _seed(db)
    rr.is_route_reflector = 0   # stale by direct write (bypasses the route)
    db.commit()
    ctx = build_context(client_node, [], db=db)
    assert ctx["device"]["rr_peers"] == []

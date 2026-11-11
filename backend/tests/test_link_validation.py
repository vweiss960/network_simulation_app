"""_validate_link_interfaces: the link endpoint-interface rules."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routes.links import _validate_link_interfaces
from tests.factories import make_topology, make_node, make_link

IF_10G = [{"name": "e0", "speed_gbps": 10.0}, {"name": "e1", "speed_gbps": 10.0}]
IF_1G = [{"name": "e0", "speed_gbps": 1.0}]


def test_both_interfaces_required(db):
    t = make_topology(db, "t")
    a = make_node(db, t, "A", interfaces=IF_10G)
    b = make_node(db, t, "B", interfaces=IF_10G)
    data = SimpleNamespace(source_interface="e0", target_interface="")
    with pytest.raises(HTTPException) as ei:
        _validate_link_interfaces(data, t.id, db, a, b)
    assert ei.value.status_code == 400


def test_speed_mismatch_rejected(db):
    t = make_topology(db, "t")
    a = make_node(db, t, "A", interfaces=IF_10G)   # e0 @ 10G
    b = make_node(db, t, "B", interfaces=IF_1G)     # e0 @ 1G
    data = SimpleNamespace(source_interface="e0", target_interface="e0")
    with pytest.raises(HTTPException) as ei:
        _validate_link_interfaces(data, t.id, db, a, b)
    assert ei.value.status_code == 400
    assert "speed" in str(ei.value.detail).lower()


def test_valid_pair_returns_matched_speed(db):
    t = make_topology(db, "t")
    a = make_node(db, t, "A", interfaces=IF_10G)
    b = make_node(db, t, "B", interfaces=IF_10G)
    data = SimpleNamespace(source_interface="e0", target_interface="e0")
    assert _validate_link_interfaces(data, t.id, db, a, b) == ("e0", "e0", 10.0)


def test_interface_already_in_use_rejected(db):
    t = make_topology(db, "t")
    a = make_node(db, t, "A", interfaces=IF_10G)
    b = make_node(db, t, "B", interfaces=IF_10G)
    make_link(db, t, a, b, source_interface="e0", target_interface="e0")  # claims a.e0
    data = SimpleNamespace(source_interface="e0", target_interface="e1")   # a.e0 already used
    with pytest.raises(HTTPException) as ei:
        _validate_link_interfaces(data, t.id, db, a, b)
    assert ei.value.status_code == 400
    assert "use" in str(ei.value.detail).lower()


@pytest.mark.parametrize("depth", [1, 2])  # child, grandchild
def test_changing_interfaces_on_link_to_inherited_node(client, db, depth):
    """A child / grandchild link from a local node to an ancestor's node can
    have its interfaces changed (it used to 404 "Source or target not found")."""
    root = make_topology(db, "root")
    upstream = make_node(db, root, "Up", interfaces=IF_10G)
    topo = root
    for i in range(depth):
        topo = make_topology(db, f"layer{i}", parent_id=topo.id)
    local = make_node(db, topo, "Local", interfaces=IF_10G)
    link = make_link(db, topo, local, upstream, source_interface="e0", target_interface="e0")
    db.commit()

    r = client.put(f"/api/topologies/{topo.id}/links/{link.id}",
                   json={"source_interface": "e1", "target_interface": "e1"})
    assert r.status_code == 200, r.text
    assert (r.json()["source_interface"], r.json()["target_interface"]) == ("e1", "e1")
    # The ancestor's ports are still checked: e0 is taken by a root link.
    other = make_node(db, root, "Other", interfaces=IF_10G)
    make_link(db, root, upstream, other, source_interface="e0", target_interface="e0")
    db.commit()
    r = client.put(f"/api/topologies/{topo.id}/links/{link.id}", json={"target_interface": "e0"})
    assert r.status_code == 400 and "already in use" in r.json()["detail"]

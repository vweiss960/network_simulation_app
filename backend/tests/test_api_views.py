"""Sub-topology views API: the Default view exclusion set + saved views.

The Default view is modeled as an EXCLUSION set on the topology (all nodes minus
these), so newly-created and inherited nodes appear automatically — the frontend
computes `allNodes - excluded`. These tests cover the backend contract: the
exclusion set defaults empty, round-trips, dedupes/sorts, and is per-topology.
"""
from tests.factories import make_topology, make_node


def test_default_view_excludes_nothing_initially(client, db):
    t = make_topology(db, "v")
    make_node(db, t, "A")
    make_node(db, t, "B")
    db.commit()

    r = client.get(f"/api/topologies/{t.id}/views/default")
    assert r.status_code == 200
    assert r.json()["excluded_node_ids"] == []  # Default view = every node


def test_default_view_exclusion_roundtrips_sorted_deduped(client, db):
    t = make_topology(db, "v")
    a = make_node(db, t, "A")
    b = make_node(db, t, "B")
    db.commit()

    r = client.put(
        f"/api/topologies/{t.id}/views/default",
        json={"excluded_node_ids": [b.id, a.id, b.id]},  # unsorted + duplicate
    )
    assert r.status_code == 200
    assert r.json()["excluded_node_ids"] == sorted([a.id, b.id])

    # Persisted: a fresh GET returns the same set.
    assert client.get(f"/api/topologies/{t.id}/views/default").json()["excluded_node_ids"] == sorted([a.id, b.id])


def test_new_node_is_not_auto_excluded(client, db):
    # Removing a node from Default must not pull in later-added nodes — they
    # join the Default view automatically because they're not in the exclusion set.
    t = make_topology(db, "v")
    a = make_node(db, t, "A")
    db.commit()
    client.put(f"/api/topologies/{t.id}/views/default", json={"excluded_node_ids": [a.id]})

    # Add a node after the exclusion was set.
    rn = client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "C"})
    assert rn.status_code == 200
    new_id = rn.json()["id"]

    excluded = client.get(f"/api/topologies/{t.id}/views/default").json()["excluded_node_ids"]
    assert excluded == [a.id]
    assert new_id not in excluded  # auto-included in Default


def test_default_view_exclusion_is_per_topology(client, db):
    # A child topology's removals are scoped to the child — the parent's Default
    # view is untouched (so inherited nodes can be removed locally only).
    parent = make_topology(db, "parent")
    pa = make_node(db, parent, "PA")
    child = make_topology(db, "child", parent_id=parent.id)
    db.commit()

    client.put(f"/api/topologies/{child.id}/views/default", json={"excluded_node_ids": [pa.id]})

    assert client.get(f"/api/topologies/{child.id}/views/default").json()["excluded_node_ids"] == [pa.id]
    assert client.get(f"/api/topologies/{parent.id}/views/default").json()["excluded_node_ids"] == []


def test_saved_view_crud_roundtrip(client, db):
    t = make_topology(db, "v")
    a = make_node(db, t, "A")
    b = make_node(db, t, "B")
    db.commit()

    created = client.post(
        f"/api/topologies/{t.id}/views/",
        json={"name": "Core", "node_ids": [a.id, b.id]},
    ).json()
    assert created["name"] == "Core"
    assert created["node_ids"] == sorted([a.id, b.id])

    listed = client.get(f"/api/topologies/{t.id}/views/").json()
    assert [v["id"] for v in listed] == [created["id"]]

    client.put(f"/api/topologies/{t.id}/views/{created['id']}", json={"node_ids": [a.id]})
    assert client.get(f"/api/topologies/{t.id}/views/").json()[0]["node_ids"] == [a.id]

    client.delete(f"/api/topologies/{t.id}/views/{created['id']}")
    assert client.get(f"/api/topologies/{t.id}/views/").json() == []


def test_saved_view_color_roundtrips(client, db):
    t = make_topology(db, "v")
    a = make_node(db, t, "A")
    db.commit()

    created = client.post(
        f"/api/topologies/{t.id}/views/",
        json={"name": "Edge", "node_ids": [a.id], "color": "#22d3ee"},
    ).json()
    assert created["color"] == "#22d3ee"

    # Recolor via update.
    client.put(f"/api/topologies/{t.id}/views/{created['id']}", json={"color": "#f59e0b"})
    assert client.get(f"/api/topologies/{t.id}/views/").json()[0]["color"] == "#f59e0b"

    # Default (no color) comes back as empty string, not null.
    plain = client.post(f"/api/topologies/{t.id}/views/", json={"name": "Plain", "node_ids": []}).json()
    assert plain["color"] == ""

"""Protected topologies: admin-only edit sessions + commit/discard versioning.

The `client` fixture authenticates as id=1/username='tester' (auth override), so
these tests seed a real `tester` User (id 1) that is an admin — entering edit
mode is admin-only and requires no password.
"""
from datetime import datetime, timedelta

import pytest

from tests.factories import make_topology, make_node, make_link, make_flow


@pytest.fixture
def tester(db):
    from app.models.user import User
    from app.auth.service import hash_password
    u = User(username="tester", hashed_password=hash_password("secret"), is_admin=True)
    db.add(u)
    db.commit()
    assert u.id == 1  # matches the auth override (also is_admin)
    return u


def _protect(db, topo):
    topo.is_protected = 1
    db.commit()


def test_unprotected_topology_edits_without_session(client, db, tester):
    t = make_topology(db, "open")
    db.commit()
    r = client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "A"})
    assert r.status_code == 200  # no session needed


def test_protected_blocks_mutation_without_session(client, db, tester):
    t = make_topology(db, "prot")
    _protect(db, t)
    r = client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "A"})
    assert r.status_code == 403


def test_begin_admin_only_no_password(client, db, tester):
    # Entering edit mode needs no password, but requires admin.
    t = make_topology(db, "prot")
    _protect(db, t)
    r = client.post(f"/api/topologies/{t.id}/edit-session/begin")
    assert r.status_code == 200 and r.json()["held_by_me"] is True


def test_begin_requires_admin(client, db, tester):
    from types import SimpleNamespace
    from app.auth.dependencies import get_current_user
    from app.main import app
    t = make_topology(db, "prot")
    _protect(db, t)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=9, username="bob", is_admin=False)
    try:
        assert client.post(f"/api/topologies/{t.id}/edit-session/begin").status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="tester", is_admin=True)


def test_session_allows_mutation_then_commit_blocks_again(client, db, tester):
    t = make_topology(db, "prot")
    _protect(db, t)
    client.post(f"/api/topologies/{t.id}/edit-session/begin")
    assert client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "A"}).status_code == 200

    c = client.post(f"/api/topologies/{t.id}/edit-session/commit", json={"message": "added A"})
    assert c.status_code == 200
    # Version name is "<user> — <ts>"; the commit message lives in the description.
    versions = client.get(f"/api/topologies/{t.id}/versions/").json()
    assert versions and versions[0]["created_by"] == "tester"
    assert versions[0]["name"].startswith("tester — ")
    assert versions[0]["description"] == "added A"
    # Session is over → mutations blocked again.
    assert client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "B"}).status_code == 403


def test_protected_allows_readonly_analytics(client, db, tester):
    # Read-only analytics (compute a flow's path, per-flow availability) must work
    # on a protected topology even without an edit session; mutations stay blocked.
    t = make_topology(db, "prot")
    a = make_node(db, t, "A")
    b = make_node(db, t, "B")
    make_link(db, t, a, b)
    f = make_flow(db, t, "F", a, b)
    _protect(db, t)
    db.commit()

    assert client.post(f"/api/topologies/{t.id}/flows/{f.id}/compute-path").status_code == 200
    assert client.post(f"/api/topologies/{t.id}/flows/{f.id}/analyze-availability", json={}).status_code != 403
    assert client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "C"}).status_code == 403


def test_exclusive_lock_blocks_second_user(client, db, tester):
    t = make_topology(db, "prot")
    t.is_protected = 1
    t.edit_session_user_id = 2  # someone else
    t.edit_session_username = "other"
    t.edit_session_expires_at = datetime.utcnow() + timedelta(hours=1)
    db.commit()
    r = client.post(f"/api/topologies/{t.id}/edit-session/begin")
    assert r.status_code == 409  # locked by another


def test_discard_reverts_all_changes_full_fidelity(client, db, tester):
    from app.models.topology_view import TopologyView
    import json as _json
    t = make_topology(db, "prot")
    a = make_node(db, t, "A", geo_latitude=10.0, geo_longitude=20.0)
    view = TopologyView(topology_id=t.id, name="V", color="#111111", node_ids_json=_json.dumps([a.id]))
    db.add(view)
    _protect(db, t)

    client.post(f"/api/topologies/{t.id}/edit-session/begin")
    # Change geo, recolor the view, and add a node — all in the session.
    client.put(f"/api/topologies/{t.id}/nodes/{a.id}", json={"geo_latitude": 99.0, "geo_longitude": 88.0})
    vid = client.get(f"/api/topologies/{t.id}/views/").json()[0]["id"]
    client.put(f"/api/topologies/{t.id}/views/{vid}", json={"color": "#222222"})
    client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "B"})

    assert client.post(f"/api/topologies/{t.id}/edit-session/discard").status_code == 200

    nodes = {n["name"]: n for n in client.get(f"/api/topologies/{t.id}/nodes/").json()}
    assert set(nodes) == {"A"}  # added node B reverted
    assert nodes["A"]["geo_latitude"] == 10.0 and nodes["A"]["geo_longitude"] == 20.0  # geo reverted
    assert client.get(f"/api/topologies/{t.id}/views/").json()[0]["color"] == "#111111"  # view color reverted


def test_expired_session_auto_discards(client, db, tester):
    from app.models.topology import Topology
    t = make_topology(db, "prot")
    make_node(db, t, "A")
    _protect(db, t)
    client.post(f"/api/topologies/{t.id}/edit-session/begin")
    client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "B"})  # 2 nodes now

    # Force the session to expire, then hit the status endpoint (lazy expiry).
    db.query(Topology).filter(Topology.id == t.id).update({"edit_session_expires_at": datetime.utcnow() - timedelta(minutes=1)})
    db.commit()
    status = client.get(f"/api/topologies/{t.id}/edit-session/").json()
    assert status["active"] is False
    # Auto-discard restored the baseline (B gone), and edits are blocked again.
    assert {n["name"] for n in client.get(f"/api/topologies/{t.id}/nodes/").json()} == {"A"}
    assert client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "C"}).status_code == 403


def test_admin_protection_toggle(client, db, tester):
    # Protection is toggled via the dedicated admin endpoint (the `client` user
    # is an admin), independent of any edit session.
    t = make_topology(db, "open")
    db.commit()
    r = client.put(f"/api/topologies/{t.id}/protection", json={"is_protected": True})
    assert r.status_code == 200 and r.json()["is_protected"] is True
    # Now protected → content edits are blocked without a session.
    assert client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "A"}).status_code == 403
    # Admin can unprotect directly (no edit session needed).
    r = client.put(f"/api/topologies/{t.id}/protection", json={"is_protected": False})
    assert r.status_code == 200 and r.json()["is_protected"] is False
    assert client.post(f"/api/topologies/{t.id}/nodes/", json={"name": "A"}).status_code == 200


def test_protection_toggle_requires_admin(client, db):
    # A non-admin user can't change protection.
    from types import SimpleNamespace
    from app.auth.dependencies import get_current_user
    from app.main import app
    t = make_topology(db, "open")
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=9, username="bob", is_admin=False)
    try:
        assert client.put(f"/api/topologies/{t.id}/protection", json={"is_protected": True}).status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="tester", is_admin=True)


def test_protected_topology_cannot_be_deleted_even_in_edit_mode(client, db, tester):
    from app.models.topology import Topology
    t = make_topology(db, "P")
    _protect(db, t)
    # Without a session: refused by the edit-session guard.
    assert client.delete(f"/api/topologies/{t.id}").status_code == 403
    # Even holding the edit session: still refused — unprotect first.
    assert client.post(f"/api/topologies/{t.id}/edit-session/begin").status_code == 200
    r = client.delete(f"/api/topologies/{t.id}")
    assert r.status_code == 409 and "unprotect" in r.json()["detail"]
    db.expire_all()
    assert db.get(Topology, t.id) is not None

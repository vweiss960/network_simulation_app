"""Bulk flow import (POST /topologies/{id}/flows/bulk): rows whose name already
exists in the topology — or repeats within the same import — are skipped, so
re-importing a file never creates duplicates and never changes existing flows.
Names inherited from a parent topology still reject the import (409)."""
from app.models.flow import Flow
from tests.factories import make_topology, make_node, make_flow


def _setup(db, parent=None):
    t = make_topology(db, "T", parent_id=parent.id if parent else None)
    a, b = make_node(db, t, "A"), make_node(db, t, "B")
    db.commit()
    return t, a, b


def _post(client, t, rows):
    return client.post(f"/api/topologies/{t.id}/flows/bulk", json={"flows": rows})


def _row(name, a, b, bw=1.0):
    return {"name": name, "source_node_id": a.id, "dest_node_id": b.id, "bandwidth_gbps": bw}


def _count(db, t):
    db.expire_all()
    return db.query(Flow).filter(Flow.topology_id == t.id).count()


def test_reimporting_the_same_file_creates_flows_once(client, db):
    t, a, b = _setup(db)
    rows = [_row("F1", a, b), _row("F2", b, a)]
    r1 = _post(client, t, rows)
    assert r1.status_code == 200 and len(r1.json()) == 2
    r2 = _post(client, t, rows)
    assert r2.status_code == 200 and r2.json() == []
    assert _count(db, t) == 2


def test_repeated_name_within_one_import_is_created_once(client, db):
    t, a, b = _setup(db)
    r = _post(client, t, [_row("Dup", a, b), _row("dup ", b, a), _row("Other", a, b)])
    assert sorted(f["name"] for f in r.json()) == ["Dup", "Other"]
    assert _count(db, t) == 2


def test_case_and_space_variants_of_existing_names_are_skipped(client, db):
    t, a, b = _setup(db)
    make_flow(db, t, "Core Flow", a, b)
    db.commit()
    r = _post(client, t, [_row("  core flow ", a, b), _row("CORE FLOW", b, a)])
    assert r.json() == []
    assert _count(db, t) == 1


def test_reimport_does_not_change_existing_flow(client, db):
    t, a, b = _setup(db)
    make_flow(db, t, "F1", a, b, bw=5.0)
    db.commit()
    _post(client, t, [_row("F1", a, b, bw=99.0)])
    db.expire_all()
    assert db.query(Flow).filter_by(topology_id=t.id, name="F1").one().bandwidth_gbps == 5.0


def test_imported_names_are_trimmed(client, db):
    t, a, b = _setup(db)
    r = _post(client, t, [_row("  Spaced  ", a, b)])
    assert r.json()[0]["name"] == "Spaced"


def test_child_topology_parent_names_rejected_and_new_names_created(client, db):
    parent, a, b = _setup(db)
    make_flow(db, parent, "Parent Flow", a, b)
    child = make_topology(db, "child", parent_id=parent.id)
    db.commit()
    r = _post(client, child, [_row(" parent flow", a, b)])
    assert r.status_code == 409
    r = _post(client, child, [_row("Child Flow", a, b)])
    assert r.status_code == 200 and len(r.json()) == 1
    # And a second import into the child skips it.
    assert _post(client, child, [_row("Child Flow", a, b)]).json() == []
    assert _count(db, child) == 1


def test_update_existing_option_updates_matching_flows(client, db):
    t, a, b = _setup(db)
    make_flow(db, t, "F1", a, b, bw=5.0)
    db.commit()
    r = client.post(f"/api/topologies/{t.id}/flows/bulk",
                    json={"flows": [_row(" f1 ", a, b, bw=10.0), _row("F2", a, b)], "update_existing": True})
    assert r.status_code == 200 and len(r.json()) == 2
    db.expire_all()
    assert db.query(Flow).filter_by(topology_id=t.id, name="F1").one().bandwidth_gbps == 10.0
    assert _count(db, t) == 2


def test_blank_names_are_rejected(client, db):
    t, a, b = _setup(db)
    r = _post(client, t, [_row("   ", a, b), _row("OK", a, b)])
    assert r.status_code == 422
    assert _count(db, t) == 0


def test_single_create_and_rename_follow_the_name_rule(client, db):
    t, a, b = _setup(db)
    base = f"/api/topologies/{t.id}/flows/"
    assert client.post(base, json=_row("Core Flow", a, b)).status_code == 200
    assert client.post(base, json=_row(" core flow ", a, b)).status_code == 409
    other = client.post(base, json=_row("  Other  ", a, b)).json()
    assert other["name"] == "Other"
    assert client.put(f"{base}{other['id']}", json={"name": "CORE FLOW"}).status_code == 409
    # Re-casing your own name is fine.
    assert client.put(f"{base}{other['id']}", json={"name": "other"}).status_code == 200


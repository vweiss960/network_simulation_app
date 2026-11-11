"""Route-level CRUD round-trips through the API (auth bypassed by `client`)."""
from tests.factories import make_topology, make_node, make_link


def _two_node_topo(db):
    t = make_topology(db, "api")
    a = make_node(db, t, "A")
    b = make_node(db, t, "B")
    return t, a, b


def test_create_topology_and_node_via_api(client):
    r = client.post("/api/topologies/", json={"name": "apitopo"})
    assert r.status_code == 200
    tid = r.json()["id"]

    rn = client.post(
        f"/api/topologies/{tid}/nodes/",
        json={"name": "A", "interfaces": [{"name": "e0", "speed_gbps": 10.0}]},
    )
    assert rn.status_code == 200

    nodes = client.get(f"/api/topologies/{tid}/nodes/").json()
    assert any(n["name"] == "A" for n in nodes)


def test_links_listed_after_seeding(client, db):
    t, a, b = _two_node_topo(db)
    link = make_link(db, t, a, b)
    db.commit()  # request handlers use separate sessions; must be committed

    links = client.get(f"/api/topologies/{t.id}/links/").json()
    assert [l["id"] for l in links] == [link.id]


def test_geo_path_multipart_roundtrips(client, db):
    t, a, b = _two_node_topo(db)
    link = make_link(db, t, a, b)
    db.commit()

    parts = [[[-1.0, 2.0], [-1.1, 2.1]], [[-2.0, 3.0], [-2.1, 3.1]]]  # 2 disjoint parts
    r = client.put(f"/api/topologies/{t.id}/links/{link.id}", json={"geo_path": parts})
    assert r.status_code == 200
    assert r.json()["geo_path"] == parts  # nested shape preserved through store + reparse

    # Empty list clears it.
    r2 = client.put(f"/api/topologies/{t.id}/links/{link.id}", json={"geo_path": []})
    assert r2.status_code == 200
    assert r2.json()["geo_path"] is None


def test_bulk_update_nodes_applies_color(client, db):
    # Bulk-color a selection; only listed ids change, only allowed fields apply.
    t, a, b = _two_node_topo(db)
    c = make_node(db, t, "C")
    db.commit()

    r = client.put(
        f"/api/topologies/{t.id}/nodes/bulk-update",
        json={"node_ids": [a.id, b.id], "updates": {"color": "#ff0000", "name": "HACK"}},
    )
    assert r.status_code == 200
    returned = {n["id"]: n for n in r.json()}
    assert set(returned) == {a.id, b.id}  # c untouched (not listed)
    assert all(n["color"] == "#ff0000" for n in returned.values())
    assert returned[a.id]["name"] == "A"  # name not in allowed_fields → ignored

    # c stayed uncolored.
    nodes = {n["id"]: n for n in client.get(f"/api/topologies/{t.id}/nodes/").json()}
    assert nodes[c.id].get("color", "") in ("", None)

    # Empty color clears it.
    client.put(f"/api/topologies/{t.id}/nodes/bulk-update", json={"node_ids": [a.id], "updates": {"color": ""}})
    nodes = {n["id"]: n for n in client.get(f"/api/topologies/{t.id}/nodes/").json()}
    assert nodes[a.id].get("color", "") in ("", None)


def test_version_diff(client, db):
    t, a, b = _two_node_topo(db)
    db.commit()
    v1 = client.post(f"/api/topologies/{t.id}/versions/", json={"name": "v1"}).json()
    client.put(f"/api/topologies/{t.id}/nodes/{a.id}", json={"model": "CHANGED-MODEL"})
    v2 = client.post(f"/api/topologies/{t.id}/versions/", json={"name": "v2"}).json()

    d = client.get(f"/api/topologies/{t.id}/versions/diff?from={v1['id']}&to={v2['id']}").json()
    assert d["identical"] is False and "CHANGED-MODEL" in d["diff"]

    same = client.get(f"/api/topologies/{t.id}/versions/diff?from={v1['id']}&to={v1['id']}").json()
    assert same["identical"] is True and same["diff"] == ""

    cur = client.get(f"/api/topologies/{t.id}/versions/diff?from={v1['id']}&to=current").json()
    assert cur["identical"] is False  # current reflects the model change


def test_bulk_update_nodes_requires_ids_and_updates(client, db):
    t, a, b = _two_node_topo(db)
    db.commit()
    assert client.put(f"/api/topologies/{t.id}/nodes/bulk-update", json={"node_ids": [], "updates": {"color": "#fff"}}).status_code == 400
    assert client.put(f"/api/topologies/{t.id}/nodes/bulk-update", json={"node_ids": [a.id], "updates": {}}).status_code == 400


def test_blank_interface_link_edits_nonendpoint_field(client, db):
    # Links imported without interfaces are common; editing a non-endpoint field
    # must NOT trip the "both interfaces required" check (regression).
    t, a, b = _two_node_topo(db)
    link = make_link(db, t, a, b)  # blank source/target interfaces
    db.commit()

    ok = client.put(f"/api/topologies/{t.id}/links/{link.id}", json={"latency_ms": 7.0})
    assert ok.status_code == 200
    assert ok.json()["latency_ms"] == 7.0

    # Actually changing an endpoint interface still validates (both required).
    bad = client.put(f"/api/topologies/{t.id}/links/{link.id}", json={"source_interface": "e0"})
    assert bad.status_code == 400

def _topo(client, name="Lab"):
    return client.post("/api/topologies/", json={"name": name}).json()


def _tmpl_id(client, name):
    return next(t["id"] for t in client.get("/api/node-templates/").json() if t["name"] == name)


def test_templates_are_seeded(client, db):
    from app.main import SEED_TEMPLATES
    from app.models.node_template import NodeTemplate
    for t in SEED_TEMPLATES:
        db.add(NodeTemplate(**t))
    db.commit()
    names = {t["name"] for t in client.get("/api/node-templates/").json()}
    assert {"Cisco XRd Control Plane", "Cisco Catalyst 8000v", "Linux Host"} <= names


def test_node_link_crud_triggers_reconcile(client, db):
    from app.main import SEED_TEMPLATES
    from app.models.node_template import NodeTemplate
    for t in SEED_TEMPLATES:
        db.add(NodeTemplate(**t))
    db.commit()
    t = _topo(client)
    xrd = _tmpl_id(client, "Cisco XRd Control Plane")
    r1 = client.post(f"/api/topologies/{t['id']}/nodes/", json={"template_id": xrd, "x": 10, "y": 20}).json()
    r2 = client.post(f"/api/topologies/{t['id']}/nodes/", json={"template_id": xrd}).json()
    assert r1["name"] == "R1" and r1["kind"] == "cisco_xrd" and len(r1["interfaces"]) == 4
    assert r1["interfaces"][0] == {"name": "GigabitEthernet0/0/0/0", "kernel": "Gi0-0-0-0"}

    link = client.post(f"/api/topologies/{t['id']}/links/",
                       json={"source_node_id": r1["id"], "target_node_id": r2["id"]}).json()
    assert link["source_interface"] == "GigabitEthernet0/0/0/0"

    # position-only update must NOT reconcile; a deploy-relevant one must
    client.syncs.clear()
    client.patch(f"/api/topologies/{t['id']}/nodes/{r1['id']}", json={"x": 99})
    assert client.syncs == []
    client.patch(f"/api/topologies/{t['id']}/nodes/{r1['id']}", json={"startup_config": "hostname R1"})
    assert client.syncs == [t["id"]]

    # deleting a node removes its links
    assert client.delete(f"/api/topologies/{t['id']}/nodes/{r2['id']}").status_code == 200
    assert client.get(f"/api/topologies/{t['id']}/links/").json() == []
    summary = client.get(f"/api/topologies/{t['id']}").json()
    assert summary["node_count"] == 1 and summary["link_count"] == 0


def test_link_errors_are_400(client):
    t = _topo(client)
    a = client.post(f"/api/topologies/{t['id']}/nodes/", json={"kind": "linux", "image_ref": "x"}).json()
    r = client.post(f"/api/topologies/{t['id']}/links/", json={"source_node_id": a["id"], "target_node_id": a["id"]})
    assert r.status_code == 400


def test_free_interfaces(client):
    t = _topo(client)
    a = client.post(f"/api/topologies/{t['id']}/nodes/", json={"kind": "linux", "image_ref": "x"}).json()
    b = client.post(f"/api/topologies/{t['id']}/nodes/", json={"kind": "linux", "image_ref": "x"}).json()
    client.post(f"/api/topologies/{t['id']}/links/", json={"source_node_id": a["id"], "target_node_id": b["id"]})
    ifs = client.get(f"/api/topologies/{t['id']}/links/free-interfaces/{a['id']}").json()["interfaces"]
    assert [i["used"] for i in ifs] == [True, False, False, False]


def test_kinds_catalog(client):
    kinds = {k["kind"]: k for k in client.get("/api/lab/kinds").json()["kinds"]}
    assert kinds["cisco_c8000v"]["is_vm"] is True
    assert kinds["cisco_xrd"]["example_interfaces"][0]["kernel"] == "Gi0-0-0-0"


def test_export_and_duplicate(client):
    t = _topo(client, "Orig")
    client.post(f"/api/topologies/{t['id']}/nodes/", json={"kind": "linux", "image_ref": "x", "name": "A"})
    text = client.get(f"/api/topologies/{t['id']}/export").text
    assert "name: A" in text
    dup = client.post(f"/api/topologies/{t['id']}/duplicate").json()
    assert dup["name"] == "Orig (copy)" and dup["node_count"] == 1


def test_lab_view_has_no_ports(client):
    t = _topo(client)
    client.post(f"/api/topologies/{t['id']}/nodes/", json={"kind": "cisco_xrd", "image_ref": "x"})
    y = client.get(f"/api/topologies/{t['id']}/lab/yaml").json()["yaml"]
    assert "ports" not in y

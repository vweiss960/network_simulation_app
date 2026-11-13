import yaml

from app.services.lab_service import _recreate_plan, _startup_changed


def doc(nodes, links):
    return yaml.safe_dump({"name": "t", "topology": {
        "nodes": nodes, "links": [{"endpoints": list(e)} for e in links]}})


XRD = {"kind": "cisco_xrd", "image": "xrd:1"}
C8K = {"kind": "cisco_c8000v", "image": "vrnetlab/c8k:1"}
FRR = {"kind": "linux", "image": "frr:1"}


def test_new_link_recreates_only_non_live_kinds():
    old = doc({"R1": XRD, "R2": XRD, "F1": FRR}, [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0")])
    new = doc({"R1": XRD, "R2": XRD, "F1": FRR, "R3": C8K},
              [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0"), ("R3:eth1", "R1:Gi0-0-0-1"), ("F1:eth1", "R2:Gi0-0-0-1")])
    # R3 is new (containerlab creates it); F1 is linux → link added live
    assert sorted(_recreate_plan(old, new)) == ["R1", "R2"]


def test_removed_link_and_node():
    old = doc({"R1": XRD, "R2": XRD, "R3": C8K, "F1": FRR},
              [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0"), ("R3:eth1", "R1:Gi0-0-0-1"), ("F1:eth1", "R3:eth2")])
    new = doc({"R1": XRD, "R2": XRD, "F1": FRR}, [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0")])
    assert _recreate_plan(old, new) == ["R1"]


def test_unchanged_and_startup_only():
    old = doc({"R1": {**XRD, "startup-config": "configs/R1-aaaa.cfg"}, "B": {"kind": "bridge"}},
              [("R1:Gi0-0-0-0", "B:p1")])
    new = doc({"R1": {**XRD, "startup-config": "configs/R1-bbbb.cfg"}, "B": {"kind": "bridge"}},
              [("R1:Gi0-0-0-0", "B:p1")])
    assert _recreate_plan(old, old) == []
    assert _recreate_plan(old, new) == []           # handled by _startup_changed
    assert _startup_changed(old, new) == ["R1"]


def test_image_change_recreates_any_kind():
    old = doc({"F1": FRR}, [])
    new = doc({"F1": {**FRR, "image": "frr:2"}}, [])
    assert _recreate_plan(old, new) == ["F1"]


def test_cabling_never_changes_the_yaml(db):
    """Ports are pre-provisioned, so adding / removing cables only changes the
    patch list: no device is recreated."""
    from app.models.topology import Topology
    from app.services import topology_ops
    from app.services.clab_yaml import generate_clab_yaml
    t = Topology(name="w"); db.add(t); db.commit()
    r1 = topology_ops.create_node(db, t.id, kind="cisco_xrd", image_ref="xrd:1")
    r2 = topology_ops.create_node(db, t.id, kind="cisco_xrd", image_ref="xrd:1")
    f1 = topology_ops.create_node(db, t.id, kind="linux", image_ref="frr:1")
    db.commit()
    before = generate_clab_yaml(db, t.id)
    l1 = topology_ops.create_link(db, t.id, r1.id, r2.id)
    topology_ops.create_link(db, t.id, f1.id, r1.id)
    db.commit()
    after = generate_clab_yaml(db, t.id)
    assert after["yaml"] == before["yaml"]
    assert _recreate_plan(before["yaml"], after["yaml"]) == []
    assert len(after["patches"]) == 2 and before["patches"] == []

    # adding a device recreates nobody else
    r3 = topology_ops.create_node(db, t.id, kind="cisco_xrd", image_ref="xrd:1")
    topology_ops.create_link(db, t.id, r3.id, r1.id)
    db.commit()
    assert _recreate_plan(after["yaml"], generate_clab_yaml(db, t.id)["yaml"]) == []

    # more ports on a device: only that device changes
    topology_ops.set_interface_count(db, r2, 6)
    db.commit()
    assert _recreate_plan(after["yaml"], generate_clab_yaml(db, t.id)["yaml"]) == [r2.name]


def test_day0_config_strips_unsafe_xe_lines():
    from app.services.clab_yaml import day0_config
    run = """Building configuration...

Current configuration : 6270 bytes
!
version 17.18
platform sslvpn use-pd
hostname C1
!
crypto pki trustpoint TP-self-signed-123
 enrollment selfsigned
 revocation-check none
!
crypto pki certificate chain TP-self-signed-123
 certificate self-signed 01
  30820330 30820218
  \tquit
interface Loopback0
 ip address 5.5.5.5 255.255.255.255
interface GigabitEthernet3
 shutdown
license udi pid C8000V sn X
end
"""
    out = day0_config("iosxe", run)
    assert "crypto pki" not in out and "30820330" not in out and "quit" not in out
    assert "version" not in out and "platform" not in out and "license udi" not in out
    assert "hostname C1" in out and " ip address 5.5.5.5 255.255.255.255" in out
    assert " ip address 5.5.5.5 255.255.255.255\n no shutdown\ninterface GigabitEthernet3\n shutdown\n" in out + "\n"
    assert out.count("no shutdown") == 1 and out.rstrip().endswith("\nend")
    assert day0_config("iosxr", run) == run


def test_day0_leaves_empty_config_empty():
    from app.services.clab_yaml import day0_config
    assert day0_config("iosxe", "") == ""


def test_remove_for_recreate_wipes_only_new_startup(tmp_path, monkeypatch):
    from app.services import lab_service
    monkeypatch.setattr(lab_service, "_lab_dir_for", lambda name: tmp_path / name)
    monkeypatch.setattr(lab_service, "_run", lambda *a, **k: (0, "", ""))
    for n in ("C1", "R1"):
        (tmp_path / "lab" / "clab-lab" / n / "config").mkdir(parents=True)
    lab_service._remove_for_recreate("lab", ["C1", "R1"], wipe={"C1"})
    assert not (tmp_path / "lab" / "clab-lab" / "C1").exists()
    assert (tmp_path / "lab" / "clab-lab" / "R1" / "config").exists()   # XRd keeps xr-storage on link changes


def test_defer_saves_changes_for_next_restart(client, db, monkeypatch):
    from app.models.lab_deployment import LabDeployment
    from app.services import lab_service
    tid = client.post("/api/topologies/", json={"name": "d"}).json()["id"]
    nid = client.post(f"/api/topologies/{tid}/nodes/", json={"kind": "cisco_xrd", "image_ref": "xrd:1"}).json()["id"]
    db.add(LabDeployment(topology_id=tid, status="running", lab_name="plabs-x")); db.commit()

    r = client.patch(f"/api/topologies/{tid}/nodes/{nid}",
                     json={"image_ref": "xrd:2", "interface_count": 6, "startup_config": "hostname X", "defer": True})
    n = r.json()
    assert n["image_ref"] == "xrd:1" and len(n["interfaces"]) == 4 and n["startup_config"] == ""
    assert n["pending"] == {"image_ref": "xrd:2", "interface_count": 6, "startup_config": "hostname X"}
    # setting a field back to its current value drops it from pending
    n = client.patch(f"/api/topologies/{tid}/nodes/{nid}", json={"image_ref": "xrd:1", "defer": True}).json()
    assert "image_ref" not in n["pending"]
    # an impossible deferred interface count is refused up front
    nid2 = client.post(f"/api/topologies/{tid}/nodes/", json={"kind": "linux", "image_ref": "frr"}).json()["id"]
    client.post(f"/api/topologies/{tid}/links/", json={"source_node_id": nid, "target_node_id": nid2,
                                                         "source_interface": "GigabitEthernet0/0/0/3"})
    assert client.patch(f"/api/topologies/{tid}/nodes/{nid}", json={"interface_count": 2, "defer": True}).status_code == 400

    # restart applies them through a reconcile
    scheduled = []
    monkeypatch.setattr(lab_service, "_schedule_sync", lambda t: scheduled.append(t))
    res = lab_service.restart_node(db, tid, nid)
    assert res["action"] == "recreate" and scheduled == [tid]
    n = client.get(f"/api/topologies/{tid}/nodes/{nid}").json()
    assert len(n["interfaces"]) == 6 and n["startup_config"] == "hostname X" and n["pending"] == {}


def test_discard_pending_and_apply_now_clears(client, db):
    from app.models.lab_deployment import LabDeployment
    tid = client.post("/api/topologies/", json={"name": "d2"}).json()["id"]
    nid = client.post(f"/api/topologies/{tid}/nodes/", json={"kind": "linux", "image_ref": "frr:1"}).json()["id"]
    db.add(LabDeployment(topology_id=tid, status="running", lab_name="plabs-y")); db.commit()
    client.patch(f"/api/topologies/{tid}/nodes/{nid}", json={"image_ref": "frr:2", "defer": True})
    assert client.delete(f"/api/topologies/{tid}/nodes/{nid}/pending").json()["pending"] == {}
    client.patch(f"/api/topologies/{tid}/nodes/{nid}", json={"image_ref": "frr:2", "defer": True})
    n = client.patch(f"/api/topologies/{tid}/nodes/{nid}", json={"image_ref": "frr:3"}).json()
    assert n["image_ref"] == "frr:3" and n["pending"] == {}


def test_annotations_roundtrip_and_export(client):
    tid = client.post("/api/topologies/", json={"name": "ann"}).json()["id"]
    assert client.get(f"/api/topologies/{tid}/annotations").json() == []
    items = [{"id": "g1", "type": "group", "x": 10, "y": 20, "w": 300, "h": 200, "text": "Area 0", "color": "blue", "locked": True},
             {"id": "t1", "type": "text", "x": 5, "y": 5, "text": "OSPF lab"},
             {"id": "n1", "type": "note", "x": 50, "y": 60, "w": 180, "h": 100, "text": "Configure R1", "color": "yellow"}]
    r = client.put(f"/api/topologies/{tid}/annotations", json=items)
    assert r.status_code == 200 and r.json()[0]["locked"] is True
    assert client.put(f"/api/topologies/{tid}/annotations", json=[{"id": "x", "type": "blob"}]).status_code == 422
    dup = client.post(f"/api/topologies/{tid}/duplicate").json()["id"]
    assert [a["id"] for a in client.get(f"/api/topologies/{dup}/annotations").json()] == ["g1", "t1", "n1"]
    assert "Area 0" in client.get(f"/api/topologies/{tid}/export").text


def test_start_single_devices(client, db, monkeypatch):
    """Start one device on an idle lab → only it deploys; starting another on
    the running lab adds just that one (its cables patch in); Deploy-all
    powers everything on."""
    from app.models.lab_deployment import LabDeployment
    from app.models.node_instance import NodeInstance
    from app.services import lab_service
    from app.services.clab_yaml import generate_clab_yaml
    tid = client.post("/api/topologies/", json={"name": "solo"}).json()["id"]
    a = client.post(f"/api/topologies/{tid}/nodes/", json={"kind": "cisco_xrd", "image_ref": "xrd:1"}).json()["id"]
    b = client.post(f"/api/topologies/{tid}/nodes/", json={"kind": "cisco_xrd", "image_ref": "xrd:1"}).json()["id"]
    client.post(f"/api/topologies/{tid}/links/", json={"source_node_id": a, "target_node_id": b})

    deployed = []
    monkeypatch.setattr(lab_service, "deploy_lab", lambda db_, t, start_all=False: deployed.append(
        sorted(n["id"] for n in generate_clab_yaml(db_, t)["included_nodes"])) or {"ok": True})
    lab_service.start_node(db, tid, a)
    assert deployed == [[a]]
    spec = generate_clab_yaml(db, tid)
    assert spec["patches"] == []                         # B is off: A's port stays unplugged
    assert [s["name"] for s in spec["skipped_nodes"] if s.get("off")] == [db.get(NodeInstance, b).name]
    assert client.get(f"/api/topologies/{tid}/nodes/{b}").json()["enabled"] is False

    dep = db.query(LabDeployment).filter_by(topology_id=tid).first() or LabDeployment(topology_id=tid)
    dep.status, dep.lab_name = "running", "plabs-s"
    db.add(dep); db.commit()
    scheduled = []
    monkeypatch.setattr(lab_service, "_schedule_sync", lambda t: scheduled.append(t))
    assert lab_service.start_node(db, tid, b)["action"] == "reconcile" and scheduled == [tid]
    assert len(generate_clab_yaml(db, tid)["patches"]) == 1

    lab_service._set_enabled(db, tid, only=a)
    monkeypatch.undo()
    monkeypatch.setattr(lab_service, "_lock_for", lambda t: __import__("contextlib").nullcontext())
    monkeypatch.setattr(lab_service, "_ensure_fresh_yaml", lambda *a_: (_ for _ in ()).throw(RuntimeError("stop")))
    try:
        lab_service.deploy_lab(db, tid, start_all=True)
    except RuntimeError:
        pass
    assert all(n.enabled == 1 for n in db.query(NodeInstance).filter(NodeInstance.topology_id == tid))

"""Stop lab (save configs on the devices, keep the containers) and starting
a stopped lab again — containerlab and the devices are stubbed."""
import pytest

from app.models.lab_deployment import LabDeployment
from app.models.topology import Topology
from app.services import lab_service


@pytest.fixture
def running_lab(db, monkeypatch, tmp_path):
    t = Topology(name="stop-lab")
    db.add(t)
    db.commit()
    db.add(LabDeployment(topology_id=t.id, status="running", lab_name="plabs-stop", last_yaml="name: plabs-stop\n"))
    db.commit()
    calls = []
    monkeypatch.setattr(lab_service, "_yaml_path_for", lambda name: tmp_path / f"{name}.clab.yml")
    (tmp_path / "plabs-stop.clab.yml").write_text("name: plabs-stop\n")
    monkeypatch.setattr(lab_service, "_run", lambda cmd, timeout=600, cwd=None: (calls.append(cmd), (0, "", ""))[1])
    monkeypatch.setattr(lab_service, "generate_clab_yaml", lambda db_, tid, assign_ips=False: {
        "yaml": "name: plabs-stop\n", "included_nodes": [{}], "ports": [], "patches": [],
        "resource_estimate": {"memory_mb": 0, "bridges": []}})
    monkeypatch.setattr(lab_service, "_apply_patches", lambda spec: [])
    monkeypatch.setattr(lab_service, "_assert_capacity", lambda *a: None)
    monkeypatch.setattr(lab_service, "_start_addons", lambda *a: None)
    synced = []
    monkeypatch.setattr(lab_service, "_schedule_sync", lambda tid: synced.append(tid))
    return t, calls, synced


def _status(db, tid):
    db.expire_all()
    return db.query(LabDeployment).filter_by(topology_id=tid).one().status


def test_stop_saves_then_stops(db, running_lab, monkeypatch):
    t, calls, _ = running_lab
    monkeypatch.setattr(lab_service, "save_device_configs", lambda db_, tid: {
        "saved": [{"device": "R1", "how": "write memory"}], "failed": [], "skipped": []})
    r = lab_service.stop_lab(db, t.id)
    assert r["stopped"] and r["saved"][0]["device"] == "R1"
    assert calls[-1][:2] == ["containerlab", "stop"]
    assert _status(db, t.id) == "stopped"
    # Edits on a stopped lab don't reconcile (they apply on Start).
    assert lab_service.request_sync(db, t.id) is False


def test_stop_refuses_when_a_device_cant_save(db, running_lab, monkeypatch):
    t, calls, _ = running_lab
    monkeypatch.setattr(lab_service, "save_device_configs", lambda db_, tid: {
        "saved": [], "failed": [{"device": "R4", "error": "SSH refused"}], "skipped": []})
    r = lab_service.stop_lab(db, t.id)
    assert r["stopped"] is False and r["failed"][0]["device"] == "R4"
    assert not any(c[:2] == ["containerlab", "stop"] for c in calls)
    assert _status(db, t.id) == "running"
    assert lab_service.stop_lab(db, t.id, force=True)["stopped"]
    assert _status(db, t.id) == "stopped"


def test_deploy_on_stopped_lab_starts_it(db, running_lab, monkeypatch):
    t, calls, synced = running_lab
    monkeypatch.setattr(lab_service, "save_device_configs", lambda db_, tid: {"saved": [], "failed": [], "skipped": []})
    monkeypatch.setattr(lab_service, "_prepare_host_network", lambda *a: None)
    lab_service.stop_lab(db, t.id)
    r = lab_service.deploy_lab(db, t.id)
    assert r["started"] and any(c[:2] == ["containerlab", "start"] for c in calls)
    assert not any(c[:2] == ["containerlab", "deploy"] for c in calls)      # same containers, not recreated
    assert _status(db, t.id) == "running"
    assert synced == [t.id]                     # edits made while stopped get applied


def test_stop_route(client, db, running_lab, monkeypatch):
    t, _, _ = running_lab
    monkeypatch.setattr(lab_service, "save_device_configs", lambda db_, tid: {"saved": [], "failed": [], "skipped": []})
    r = client.post(f"/api/topologies/{t.id}/lab/stop")
    assert r.status_code == 200 and r.json()["stopped"]
    assert client.post(f"/api/topologies/{t.id}/lab/stop").status_code == 400     # not running any more


def test_starting_a_device_added_while_stopped_starts_the_lab(db, running_lab, monkeypatch):
    """A device added to a stopped lab has no container yet: Start brings the
    lab up and its reconcile creates the device (not `docker start`)."""
    from tests.factories import make_node
    t, calls, synced = running_lab
    monkeypatch.setattr(lab_service, "save_device_configs", lambda db_, tid: {"saved": [], "failed": [], "skipped": []})
    monkeypatch.setattr(lab_service, "_prepare_host_network", lambda *a: None)
    lab_service.stop_lab(db, t.id)
    tg = make_node(db, t, name="TG1", kind="linux", image="plabs/traffic:latest")
    db.commit()
    monkeypatch.setattr(lab_service, "_container_exists", lambda db_, tid, nid: False)
    lab_service.start_node(db, t.id, tg.id)
    assert any(c[:2] == ["containerlab", "start"] for c in calls)
    assert not any(c[:2] == ["docker", "start"] for c in calls)
    assert _status(db, t.id) == "running" and t.id in synced


APPLIED = """name: plabs-stop
topology:
  nodes:
    R1: {kind: cisco_xrd, image: x}
    H1: {kind: linux, image: plabs/host:latest}
  links:
  - endpoints: ['R1:Gi0-0-0-0', 'host:pp9n1p0']
  - endpoints: ['R1:Gi0-0-0-1', 'host:pp9n1p1']
  - endpoints: ['H1:eth1', 'host:pp9n2p0']
"""


def test_parked_ports_come_from_the_deployed_yaml():
    assert lab_service.parked_ports(APPLIED) == {
        "R1": [("Gi0-0-0-0", "pp9n1p0"), ("Gi0-0-0-1", "pp9n1p1")], "H1": [("eth1", "pp9n2p0")]}


def test_repair_rebuilds_only_stopped_devices(monkeypatch):
    from app.services import host_net
    monkeypatch.setattr(lab_service, "_lab_containers", lambda lab: {
        "clab-plabs-stop-R1": "exited", "clab-plabs-stop-H1": "running"})
    done = []
    monkeypatch.setattr(host_net, "rebuild_parked", lambda c, node, ports, mtu: done.append((c, ports, mtu)) or "ok")
    assert lab_service.repair_parked_ports("plabs-stop", APPLIED) == ["R1"]
    assert done == [("clab-plabs-stop-R1", [("Gi0-0-0-0", "pp9n1p0"), ("Gi0-0-0-1", "pp9n1p1")], 9500)]


def test_startup_marks_a_rebooted_lab_stopped_and_rebuilds_it(db, monkeypatch):
    t = Topology(name="rebooted")
    db.add(t)
    db.commit()
    db.add(LabDeployment(topology_id=t.id, status="running", lab_name="plabs-rb", last_yaml=APPLIED))
    db.commit()
    monkeypatch.setattr(lab_service, "_lab_containers", lambda lab: {"clab-plabs-rb-R1": "exited", "clab-plabs-rb-H1": "exited"})
    rebuilt = []
    monkeypatch.setattr(lab_service, "repair_parked_ports", lambda lab, y: rebuilt.append(lab) or [])
    lab_service.restore_host_network(db)
    assert _status(db, t.id) == "stopped" and rebuilt == ["plabs-rb"]


def test_startup_leaves_a_partly_running_lab_running(db, monkeypatch):
    t = Topology(name="half")
    db.add(t)
    db.commit()
    db.add(LabDeployment(topology_id=t.id, status="running", lab_name="plabs-half", last_yaml=APPLIED))
    db.commit()
    monkeypatch.setattr(lab_service, "_lab_containers", lambda lab: {"clab-plabs-half-R1": "running", "clab-plabs-half-H1": "exited"})
    monkeypatch.setattr(lab_service, "_ensure_fresh_yaml", lambda db_, tid: (_ for _ in ()).throw(RuntimeError("stub")))
    lab_service.restore_host_network(db)
    assert _status(db, t.id) == "running"

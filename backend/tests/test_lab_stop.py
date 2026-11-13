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
    assert r["started"] and calls[-1][:2] == ["containerlab", "start"]
    assert not any(c[:2] == ["containerlab", "deploy"] for c in calls)      # same containers, not recreated
    assert _status(db, t.id) == "running"
    assert synced == [t.id]                     # edits made while stopped get applied


def test_stop_route(client, db, running_lab, monkeypatch):
    t, _, _ = running_lab
    monkeypatch.setattr(lab_service, "save_device_configs", lambda db_, tid: {"saved": [], "failed": [], "skipped": []})
    r = client.post(f"/api/topologies/{t.id}/lab/stop")
    assert r.status_code == 200 and r.json()["stopped"]
    assert client.post(f"/api/topologies/{t.id}/lab/stop").status_code == 400     # not running any more

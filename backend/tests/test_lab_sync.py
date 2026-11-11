"""request_sync / reconcile scheduling with containerlab stubbed out."""
from app.models.lab_deployment import LabDeployment
from app.services import lab_service
from tests.factories import make_node, make_topology


def _dep(db, t, status):
    d = LabDeployment(topology_id=t.id, status=status, lab_name=f"plabs-{t.id}-T")
    db.add(d)
    db.commit()
    return d


def test_no_sync_when_lab_not_deployed(db, monkeypatch):
    t = make_topology(db)
    db.commit()
    started = []
    monkeypatch.setattr(lab_service, "_schedule_sync", lambda tid: started.append(tid))
    assert lab_service.request_sync(db, t.id) is False
    _dep(db, t, "idle")
    assert lab_service.request_sync(db, t.id) is False
    assert started == []


def test_sync_scheduled_for_running_lab(db, monkeypatch):
    t = make_topology(db)
    _dep(db, t, "running")
    started = []
    monkeypatch.setattr(lab_service, "_schedule_sync", lambda tid: started.append(tid))
    assert lab_service.request_sync(db, t.id) is True
    assert started == [t.id]


def test_sync_worker_reconciles_with_plain_deploy(db, monkeypatch, tmp_path):
    t = make_topology(db)
    make_node(db, t, name="A")
    _dep(db, t, "running")
    calls = []

    def fake_run(cmd, timeout=600, cwd=None):
        calls.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(lab_service, "_run", fake_run)
    monkeypatch.setattr(lab_service, "LAB_STATE_DIR", tmp_path)
    lab_service._sync_pending.add(t.id)
    lab_service._sync_worker(t.id)
    deploys = [c for c in calls if c[:2] == ["containerlab", "deploy"]]
    assert len(deploys) == 1 and "--reconfigure" not in deploys[0] and "--node-filter" not in deploys[0]
    db.expire_all()
    assert db.query(LabDeployment).filter_by(topology_id=t.id).one().status == "running"


def test_sync_worker_records_failure(db, monkeypatch, tmp_path):
    t = make_topology(db)
    make_node(db, t, name="A")
    _dep(db, t, "running")
    monkeypatch.setattr(lab_service, "_run", lambda cmd, timeout=600, cwd=None: (1, "", "boom"))
    monkeypatch.setattr(lab_service, "LAB_STATE_DIR", tmp_path)
    lab_service._sync_pending.add(t.id)
    lab_service._sync_worker(t.id)
    db.expire_all()
    d = db.query(LabDeployment).filter_by(topology_id=t.id).one()
    assert d.status == "failed" and "boom" in d.last_error

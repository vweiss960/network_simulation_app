import pytest

from app.models.node_template import NodeTemplate
from app.services import qcow_images


@pytest.fixture
def qcow_dir(tmp_path, monkeypatch):
    d = tmp_path / "qcow"
    d.mkdir()
    monkeypatch.setattr(qcow_images, "QCOW_DIR", d)
    monkeypatch.setattr(qcow_images, "DEVICE_IMAGES_DIR", tmp_path)
    monkeypatch.setattr(qcow_images, "ensure_dirs", lambda: None)
    return d


def test_mismatched_platform_refused_at_upload_start(client):
    r = client.post("/api/lab/uploads/", json=dict(
        filename="c8000v-universalk9_8G_serial.17.18.02.qcow2", size=10, last_modified=1,
        purpose="qcow", platform="cat9kv"))
    assert r.status_code == 400 and "Catalyst 8000v" in r.json()["detail"]


def test_unrecognised_name_accepts_any_platform(qcow_dir, db):
    (qcow_dir / "my-image-17.1.1.qcow2").write_bytes(b"x")
    img = qcow_images.register_existing(db, "my-image-17.1.1.qcow2", "cat9kv")
    assert img.clab_kind == "cisco_cat9kv"


def test_reclassify_discards_build(client, qcow_dir, db):
    name = "c8000v-universalk9_8G_serial.17.18.02.qcow2"
    (qcow_dir / name).write_bytes(b"x")
    img = qcow_images.DeviceImage(filename=name, size_bytes=1, sha256="", vendor="cisco",
                                  platform="cat9kv", clab_kind="cisco_cat9kv", status="built",
                                  built_image_tag="ghcr.io/srl-labs/vrnetlab-base:0.3.0")
    db.add(img)
    db.add(NodeTemplate(name="Cat9kv", kind="cisco_cat9kv", image_ref=img.built_image_tag))
    db.commit()

    # can't go to another wrong platform
    assert client.patch(f"/api/lab/qcow-images/{img.id}", json={"platform": "xrv9k"}).status_code == 400
    r = client.patch(f"/api/lab/qcow-images/{img.id}", json={"platform": "c8000v"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": img.id, "platform": "c8000v", "clab_kind": "cisco_c8000v", "status": "uploaded"}
    db.expire_all()
    assert db.query(NodeTemplate).filter_by(name="Cat9kv").one().image_ref == ""
    assert qcow_images.get_image(db, img.id).built_image_tag == ""


def test_build_without_vrnetlab_tag_is_a_failure(qcow_dir, db, tmp_path, monkeypatch):
    name = "c8000v-universalk9_8G_serial.17.18.02.qcow2"
    (qcow_dir / name).write_bytes(b"x")
    vr = tmp_path / "vrnetlab"
    (vr / "cisco" / "c8000v").mkdir(parents=True)
    monkeypatch.setattr(qcow_images, "VRNETLAB_DIR", vr)
    monkeypatch.setattr(qcow_images, "LOG_DIR", tmp_path)
    monkeypatch.setattr(qcow_images, "ensure_vrnetlab_repo", lambda: None)
    monkeypatch.setattr(qcow_images, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    tags = iter([set(), {"ghcr.io/srl-labs/vrnetlab-base:0.3.0"}])
    monkeypatch.setattr(qcow_images, "_list_docker_image_tags", lambda: next(tags))
    monkeypatch.setattr(qcow_images.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 0})())
    img = qcow_images.DeviceImage(filename=name, size_bytes=1, sha256="", vendor="cisco",
                                  platform="c8000v", clab_kind="cisco_c8000v", status="uploaded")
    db.add(img)
    db.commit()
    qcow_images._run_build(img.id)
    img = qcow_images.get_image(db, img.id)
    assert img.status == "failed" and "no image" in img.error_message and img.built_image_tag == ""

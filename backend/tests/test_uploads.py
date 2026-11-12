import pytest

from app.services import qcow_images, uploads


@pytest.fixture
def images_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "DEVICE_IMAGES_DIR", tmp_path)
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path / ".uploads")
    monkeypatch.setattr(qcow_images, "QCOW_DIR", tmp_path / "qcow")
    monkeypatch.setattr(qcow_images, "DEVICE_IMAGES_DIR", tmp_path)
    return tmp_path


def test_resumable_qcow_upload_flow(client, images_dir):
    data = bytes(range(256)) * 4000               # 1,024,000 bytes
    start = dict(filename="cat9kv-prd-17.18.02.qcow2", size=len(data), last_modified=123,
                 purpose="qcow", platform="cat9kv")
    s = client.post("/api/lab/uploads/", json=start).json()
    uid = s["id"]
    assert s["received"] == 0 and not s["complete"]

    def put(offset, chunk):
        return client.put(f"/api/lab/uploads/{uid}?offset={offset}", content=chunk)

    assert put(0, data[:400_000]).json()["received"] == 400_000
    # retransmitting part of what we have (lost ack) truncates, no duplication
    assert put(300_000, data[300_000:500_000]).json()["received"] == 500_000
    # a gap is refused and tells the client where to resume
    r = put(700_000, data[700_000:800_000])
    assert r.status_code == 409 and "resume from 500000" in r.json()["detail"]
    # "page reload": starting again with the same file finds the same session
    again = client.post("/api/lab/uploads/", json=start).json()
    assert again["id"] == uid and again["received"] == 500_000
    assert [u["id"] for u in client.get("/api/lab/uploads/").json()["uploads"]] == [uid]
    # completing early is refused
    assert client.post(f"/api/lab/uploads/{uid}/complete").status_code == 409
    assert put(500_000, data[500_000:]).json()["complete"] is True
    done = client.post(f"/api/lab/uploads/{uid}/complete").json()
    assert done["ok"] and done["platform"] == "cat9kv"
    assert (images_dir / "qcow" / "cat9kv-prd-17.18.02.qcow2").read_bytes() == data
    assert client.get("/api/lab/uploads/").json()["uploads"] == []
    imgs = client.get("/api/lab/qcow-images").json()["images"]
    assert imgs[0]["filename"] == "cat9kv-prd-17.18.02.qcow2" and imgs[0]["status"] == "uploaded"


def test_upload_validation_and_cancel(client, images_dir):
    assert client.post("/api/lab/uploads/", json=dict(filename="x.qcow2", size=10, purpose="qcow",
                                                     platform="nope")).status_code == 400
    assert client.post("/api/lab/uploads/", json=dict(filename="../etc/passwd", size=10, purpose="tarball")).json()["filename"] == "passwd"
    s = client.post("/api/lab/uploads/", json=dict(filename="img.tgz", size=10, purpose="tarball")).json()
    client.put(f"/api/lab/uploads/{s['id']}?offset=0", content=b"12345")
    assert client.delete(f"/api/lab/uploads/{s['id']}").json()["ok"]
    assert client.get(f"/api/lab/uploads/{s['id']}").status_code == 404
    assert client.get("/api/lab/uploads/zzz").status_code == 404

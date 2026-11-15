"""Modules: the bundled SPCOR module, validation, pack round trip (with its
lessons), import modes, the learner's progress and the routes."""
import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from app.models.lesson import Lesson, LessonAttempt
from app.models.module import Module
from app.models.topology import Topology
from app.services import lessons as lesson_svc, modules as svc

CURRICULUM = Path(__file__).resolve().parents[2] / "curriculum"


@pytest.fixture
def seeded(db):
    lesson_svc.seed_bundled(db, CURRICULUM)
    assert svc.seed_bundled(db, CURRICULUM) == ["spcor-networking"]
    return db.query(Module).filter_by(slug="spcor-networking").one()


def test_bundled_module(db, seeded):
    m = seeded
    assert m.track == "service_provider" and m.blueprint.startswith("CCNP SPCOR")
    assert [(it["section"], it["lesson"]) for it in m.items] == [
        ("2.1", "isis-sp-levels-multitopology"), ("2.2", "ospf-xr-multiarea-area-types"),
        ("2.4", "bgp-sp-core"), ("2.3 · 2.5", "bgp-path-selection-policy"), ("2.4", "bgp-addpaths-pic")]
    assert m.items[0]["objectives"][2] == "2.1.c Multitopology"
    assert svc.seed_bundled(db, CURRICULUM) == []                   # same version: left alone
    p = svc.progress(db, m, "learner")
    assert p["completed"] == 0 and p["total"] == 5 and p["minutes"] == 555
    assert {it["status"]["state"] for it in p["items"]} == {"not_started"}
    isis = db.query(Lesson).filter_by(slug="isis-sp-levels-multitopology").one()
    assert p["next_lesson_id"] == isis.id
    assert svc.modules_for_lesson(db, isis.slug) == [
        {"id": m.id, "slug": m.slug, "title": m.title, "section": "2.1"}]


def test_validation():
    assert svc.normalize_meta({"title": "Core IGPs"})["slug"] == "core-igps"      # slugified title
    assert svc.validate(svc.normalize_meta({"title": "Mod"}), [{"lesson": "a-b"}]) == []
    errs = svc.validate(svc.normalize_meta({"title": "Mod", "track": "nope"}),
                        svc.normalize_items(["a-b", {"lesson": "a-b"}, {"lesson": "Bad Slug"}]))
    assert any("track" in e for e in errs) and any("twice" in e for e in errs) and any("item 3" in e for e in errs)


def test_progress_follows_attempts(db, seeded):
    isis = db.query(Lesson).filter_by(slug="isis-sp-levels-multitopology").one()
    ospf = db.query(Lesson).filter_by(slug="ospf-xr-multiarea-area-types").one()
    t = Topology(name="lab")
    db.add(t)
    db.flush()
    db.add(LessonAttempt(lesson_id=isis.id, lesson_version=isis.version, username="learner", topology_id=t.id,
                         completed_at=datetime.utcnow()))
    db.commit()
    p = svc.progress(db, seeded, "learner")
    assert p["completed"] == 1 and p["next_lesson_id"] == ospf.id
    assert p["items"][0]["status"]["state"] == "completed"
    assert svc.progress(db, seeded, "someone-else")["completed"] == 0


def test_pack_round_trip_installs_lessons(db, seeded):
    data = svc.export_zip(db, seeded)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "spcor-networking/module.yaml" in names
    assert "spcor-networking/lessons/isis-sp-levels-multitopology/tasks.yaml" in names
    # A fresh installation: no lessons, no module.
    db.query(Module).delete()
    db.query(Lesson).delete()
    db.commit()
    meta, items, overview, packs = svc.read_zip(data)
    assert set(packs) == {it["lesson"] for it in seeded.items}
    m, report = svc.import_pack(db, meta, items, overview, packs)
    db.commit()
    assert sorted(report["imported"]) == sorted(packs) and m.items == seeded.items
    assert "## About this module" in m.overview_md
    # Same slug again: an error unless replace / copy; existing lessons are kept.
    meta, items, overview, packs = svc.read_zip(data)
    with pytest.raises(svc.ModuleExists):
        svc.import_pack(db, meta, items, overview, packs)
    c, report = svc.import_pack(db, meta, items, overview, packs, mode="copy")
    assert c.slug == "spcor-networking-2" and len(report["kept"]) == 5


def test_routes(client, db, seeded):
    lst = client.get("/api/modules").json()
    assert [m["slug"] for m in lst] == ["spcor-networking"] and lst[0]["lesson_count"] == 5
    d = client.get(f"/api/modules/{seeded.id}").json()
    assert [i["lesson_title"] for i in d["items"]][0].startswith("IS-IS")
    assert d["items"][1]["task_count"] == 10 and d["sections"][0]["title"] == "About this module"
    isis = db.query(Lesson).filter_by(slug="isis-sp-levels-multitopology").one()
    assert client.get(f"/api/lessons/{isis.id}").json()["modules"][0]["section"] == "2.1"
    r = client.get(f"/api/modules/{seeded.id}/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    up = client.post("/api/modules/import", files={"file": ("m.zip", r.content, "application/zip")})
    assert up.status_code == 409
    up = client.post("/api/modules/import?mode=replace", files={"file": ("m.zip", r.content, "application/zip")})
    assert up.status_code == 200 and up.json()["lessons"]["kept"]
    bad = client.post("/api/modules", json={"meta": {"title": "X"}, "items": ["nope nope"]})
    assert bad.status_code == 400
    new = client.post("/api/modules", json={"meta": {"title": "My module", "track": "enterprise"},
                                            "items": [{"lesson": isis.slug, "section": "1"}], "overview_md": "## Hi"})
    assert new.status_code == 200 and new.json()["lesson_count"] == 1
    assert db.get(Module, new.json()["id"]).edited_in_app == 1
    assert client.delete(f"/api/modules/{new.json()['id']}").json() == {"ok": True}
    assert db.query(Lesson).filter_by(slug=isis.slug).count() == 1     # lessons stay

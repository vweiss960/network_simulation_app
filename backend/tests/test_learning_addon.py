"""The learning add-on switch (PLABS_LEARNING_ENABLED): off → its API is
404, nothing is seeded, lesson labs look and behave like ordinary labs."""
from app import config
from app.models.lesson import Lesson
from app.models.topology import Topology
from app.services import lab_agent


def test_learning_off(client, db, monkeypatch):
    l = Lesson(slug="x-lesson", title="X", ai_policy="off")
    db.add(l)
    db.flush()
    t = Topology(name="lesson lab", lesson_id=l.id, lesson_role="attempt")
    db.add(t)
    db.commit()
    assert client.get("/api/features").json()["learning_enabled"] is True
    assert client.get("/api/lessons").status_code == 200
    out = next(x for x in client.get("/api/topologies/").json() if x["id"] == t.id)
    assert out["lesson_role"] == "attempt" and out["lesson_ai_policy"] == "off"
    assert lab_agent.lesson_policy(t.id) == "off"

    monkeypatch.setattr(config, "LEARNING_ENABLED", False)
    assert client.get("/api/features").json()["learning_enabled"] is False
    for path in ("/api/lessons", "/api/modules", f"/api/lessons/{l.id}", f"/api/lesson-attempts/by-topology/{t.id}"):
        r = client.get(path)
        assert r.status_code == 404 and "PLABS_LEARNING_ENABLED" in r.text
    out = next(x for x in client.get("/api/topologies/").json() if x["id"] == t.id)
    assert out["lesson_id"] is None and out["lesson_role"] is None and out["lesson_ai_policy"] is None
    assert lab_agent.lesson_policy(t.id) is None and lab_agent.lesson_prompt(t.id) == ""

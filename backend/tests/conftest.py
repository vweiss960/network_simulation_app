"""Pytest setup. Uses a throwaway SQLite DB bound before the app is imported."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="plabs-test-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "test.db")
os.environ["LAB_STATE_DIR"] = os.path.join(_tmp, "lab_state")
for _k in [k for k in list(os.environ) if k.startswith("PLABS_")]:
    del os.environ[_k]

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
import app.main  # noqa: E402,F401 — registers every model + router
from app.models.database import Base, engine, SessionLocal  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db, monkeypatch):
    """TestClient with auth bypassed and containerlab calls stubbed out."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth.dependencies import get_current_user
    from app.services import lab_service

    syncs = []
    monkeypatch.setattr(lab_service, "request_sync", lambda db, tid: syncs.append(tid) or False)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="tester", is_admin=True)
    c = TestClient(app)
    c.syncs = syncs
    try:
        yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)

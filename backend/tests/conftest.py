"""Pytest setup for backend logic tests.

Uses a throwaway file-backed SQLite DB (set before any app import so the
engine binds to it) and the real SQLAlchemy models/services — no mocking of
the graph engine, which is the whole point of these tests.
"""
import os
import tempfile

# Bind the app's engine to a disposable DB *before* importing anything that
# reads DATABASE_URL (app.models.database creates the engine at import time).
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="phanero-test-"), "test.db"
)

# Hermetic feature flags: clear any PHANERO_*_ENABLED inherited from the
# container/.env so the default app under test has every optional module OFF
# regardless of the dev environment. Gating tests opt specific modules back on
# via reload.
for _k in [k for k in list(os.environ) if k.startswith("PHANERO_")]:
    del os.environ[_k]

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
import app.main  # noqa: E402,F401 — registers every SQLAlchemy model (and routers); no DB writes at import
from app.models.database import Base, engine, SessionLocal  # noqa: E402


@pytest.fixture
def db():
    """A fresh schema + session per test (created, then dropped)."""
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    """FastAPI TestClient against the default app (optional modules OFF — no
    PHANERO_* flags set), with auth bypassed via a dependency override. Request
    handlers open their own sessions on the same test DB, so seed data created
    through the `db` fixture must be committed for requests to see it.

    Used WITHOUT a context manager so startup events (init_db) don't fire — the
    `db` fixture already created the schema.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, username="tester", is_admin=True,
    )
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_current_user, None)

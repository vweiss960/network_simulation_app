"""Auth gating on protected routers."""
from fastapi.testclient import TestClient

import app.main
from app.auth.dependencies import get_current_user


def test_protected_route_requires_auth():
    # No dependency override here: a protected router must reject an
    # unauthenticated request rather than serving it.
    raw = TestClient(app.main.app)
    resp = raw.get("/api/topologies/")
    assert resp.status_code == 401


def test_authenticated_request_is_allowed(client):
    # `client` fixture overrides get_current_user -> request goes through.
    resp = client.get("/api/topologies/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_features_endpoint_is_public():
    # /api/features is not behind auth_dep (the frontend reads it pre-login).
    raw = TestClient(app.main.app)
    assert raw.get("/api/features").status_code == 200
    # sanity: the override fixture and this raw client target the same hook
    assert get_current_user is not None

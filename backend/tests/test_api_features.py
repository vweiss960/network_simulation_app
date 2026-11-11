"""Feature-module gating via /api/features + router registration.

The test process sets no PHANERO_* vars, so the default app has every optional
module OFF. The 'enabled' case reloads app.main with a flag set, then restores.
"""
import importlib
import os

from fastapi.testclient import TestClient

import app.main

OPTIONAL_FLAGS = [
    "geo_enabled", "ai_enabled", "discovery_enabled",
    "prometheus_enabled", "cost_enabled", "integrations_enabled",
]


def test_features_lists_all_module_flags_off_by_default():
    feats = TestClient(app.main.app).get("/api/features").json()
    for key in OPTIONAL_FLAGS + ["lab_enabled", "config_design_enabled"]:
        assert key in feats, f"missing flag {key}"
        assert feats[key] is False


def test_disabled_module_route_returns_404():
    # GEO is off by default -> the map router isn't registered -> 404 (not 401),
    # i.e. the path doesn't exist rather than being auth-blocked.
    resp = TestClient(app.main.app).get("/api/map/submarine-cables")
    assert resp.status_code == 404


def test_enabling_a_module_registers_its_routes():
    saved = {k: os.environ.get(k) for k in ("PHANERO_GEO_ENABLED",)}
    os.environ["PHANERO_GEO_ENABLED"] = "1"
    try:
        importlib.reload(app.main)
        paths = {r.path for r in app.main.app.routes}
        assert any(p.startswith("/api/map") for p in paths)
        assert app.main.features()["geo_enabled"] is True
    finally:
        # Restore env + reload back to the default (all-off) app so other tests
        # see the unmodified application.
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(app.main)

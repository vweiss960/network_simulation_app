"""Effective link latency (services/latency.py): known values, geographic
estimates for unknown links, the unknown-link routing penalty, and backward
compatibility for links written without a latency source."""
import json

from app.models.global_settings import GlobalSettings
from app.models.link import Link
from app.routes.topologies import _serialize_topology, _restore_into_topology
from app.services.latency import (
    build_context, effective_latency, infer_latency_source, latency_infos,
)
from app.services.simulation import compute_flow_paths
from tests.factories import make_topology, make_node, make_link, make_flow

IFACES = [{"name": "e0", "speed_gbps": 10.0}, {"name": "e1", "speed_gbps": 10.0}]


def _info(db, t, link):
    from app.models.node_instance import NodeInstance
    nodes = db.query(NodeInstance).filter_by(topology_id=t.id).all()
    return effective_latency(link, build_context(db, nodes))


def test_measured_value_wins_over_estimate(db):
    t = make_topology(db)
    a = make_node(db, t, "A", geo_latitude=0.0, geo_longitude=0.0)
    b = make_node(db, t, "B", geo_latitude=0.0, geo_longitude=10.0)
    l = make_link(db, t, a, b, latency=7.5)
    assert _info(db, t, l) == (7.5, "measured", "measured")


def test_straight_line_estimate_uses_route_factor_setting(db):
    t = make_topology(db)
    a = make_node(db, t, "A", geo_latitude=0.0, geo_longitude=0.0)
    b = make_node(db, t, "B", geo_latitude=0.0, geo_longitude=1.0)   # ≈ 111.2 km
    l = make_link(db, t, a, b, latency=None)
    info = _info(db, t, l)
    assert info.source == "estimated" and info.method == "straight line"
    assert abs(info.ms - (111.195 * 1.5 / 200 + 0.1)) < 0.005
    db.add(GlobalSettings(key="latency_route_factor", value=2.0))
    db.flush()
    db.info.pop("latency_settings", None)  # settings are cached per session
    assert abs(_info(db, t, l).ms - (111.195 * 2.0 / 200 + 0.1)) < 0.005


def test_node_without_coordinates_borrows_its_site(db):
    t = make_topology(db)
    make_node(db, t, "DEN-1", location="Denver", geo_latitude=39.74, geo_longitude=-104.99)
    den2 = make_node(db, t, "DEN-2", location="Denver")                    # no coords
    chi = make_node(db, t, "CHI-1", location="Chicago", geo_latitude=41.88, geo_longitude=-87.63)
    info = _info(db, t, make_link(db, t, den2, chi, latency=None))
    assert info.source == "estimated" and info.method == "straight line"
    assert 10 < info.ms < 15   # ~1,480 km × 1.5 ÷ 200 ≈ 11 ms


def test_same_site_link_is_overhead_only(db):
    t = make_topology(db)
    a = make_node(db, t, "A", location="NYC")
    b = make_node(db, t, "B", location="NYC")
    assert _info(db, t, make_link(db, t, a, b, latency=None)) == (0.1, "estimated", "same site")


def test_fiber_route_estimate_uses_drawn_route_length(db):
    t = make_topology(db)
    a = make_node(db, t, "A", geo_latitude=0.0, geo_longitude=0.0)
    b = make_node(db, t, "B", geo_latitude=0.0, geo_longitude=1.0)
    # A detour route: 0,0 → 0,2 → 0,1 (≈ 333.6 km), no route factor applied.
    l = make_link(db, t, a, b, latency=None, geo_path_json=json.dumps([[0, 0], [2, 0], [1, 0]]))
    info = _info(db, t, l)
    assert info.method == "fiber route"
    assert abs(info.ms - (333.585 / 200 + 0.1)) < 0.005


def test_no_coordinates_is_unknown(db):
    t = make_topology(db)
    a, b = make_node(db, t, "A"), make_node(db, t, "B")
    assert _info(db, t, make_link(db, t, a, b, latency=None)) == (None, "unknown", "unknown")


def test_infer_latency_source_rules():
    assert infer_latency_source(None) == "unknown"
    assert infer_latency_source(0) == "unknown"            # 0 has always meant "not set"
    assert infer_latency_source(1.0) == "unknown"          # the old new-link default
    assert infer_latency_source(4.2) == "measured"
    assert infer_latency_source(1.0, "measured") == "measured"
    assert infer_latency_source(9.0, "unknown") == "unknown"
    assert infer_latency_source(9.0, "bogus") == "measured"


def test_lowest_latency_flow_routes_on_estimates(db):
    # A–B–Z unknown but close together (estimated ≪ 1 ms); A–C–Z measured 50 ms each.
    t = make_topology(db)
    a = make_node(db, t, "A", geo_latitude=0.0, geo_longitude=0.0)
    b = make_node(db, t, "B", geo_latitude=0.0, geo_longitude=0.01)
    z = make_node(db, t, "Z", geo_latitude=0.0, geo_longitude=0.02)
    c = make_node(db, t, "C")
    ab, bz = make_link(db, t, a, b, latency=None), make_link(db, t, b, z, latency=None)
    make_link(db, t, a, c, latency=50)
    make_link(db, t, c, z, latency=50)
    f = make_flow(db, t, "F", a, z, routing="lowest_latency")
    fp, _ = compute_flow_paths([a, b, z, c], db.query(Link).filter_by(topology_id=t.id).all(), [f], db)
    assert fp[0]["link_ids_per_path"] == [[ab.id, bz.id]]
    assert 0 < fp[0]["latency_per_path_ms"][0] < 1


def test_unknown_penalty_steers_routing_but_not_totals(db):
    # A–X–Z: two unknown links (no coords). A–Y–Z: measured 7.5 + 7.5 = 15 ms.
    t = make_topology(db)
    a, x, y, z = (make_node(db, t, n) for n in "AXYZ")
    ax, xz = make_link(db, t, a, x, latency=None), make_link(db, t, x, z, latency=None)
    make_link(db, t, a, y, latency=7.5)
    make_link(db, t, y, z, latency=7.5)
    links = db.query(Link).filter_by(topology_id=t.id).all()
    f = make_flow(db, t, "F", a, z, routing="lowest_latency")
    # Default penalty 10 ms/link → unknown path "costs" 20 > 15: take the measured one.
    fp, _ = compute_flow_paths([a, x, y, z], links, [f], db)
    assert fp[0]["latency_per_path_ms"] == [15.0]
    db.add(GlobalSettings(key="latency_unknown_penalty_ms", value=1.0))
    db.flush()
    db.info.pop("latency_settings", None)
    fp, _ = compute_flow_paths([a, x, y, z], links, [f], db)
    assert fp[0]["link_ids_per_path"] == [[ax.id, xz.id]]
    assert fp[0]["latency_per_path_ms"] == [0]   # unknown links add nothing


def test_import_without_latency_source_still_works(db):
    """Older export files (no latency_source / transport) must still import."""
    t = make_topology(db)
    a, b, c = make_node(db, t, "A"), make_node(db, t, "B"), make_node(db, t, "C")
    make_link(db, t, a, b, latency=7.0)
    make_link(db, t, b, c, latency=1.0)
    make_link(db, t, a, c, latency=3.0)
    db.commit()
    data = _serialize_topology(t.id, db)
    for l in data["links"]:
        l.pop("latency_source", None)
        l.pop("transport", None)
    data["links"][2].pop("latency_ms")   # a link with no latency at all
    _restore_into_topology(data, t.id, db)
    got = {round(l.latency_ms, 1): (l.latency_source, l.transport)
           for l in db.query(Link).filter_by(topology_id=t.id).all()}
    assert got == {7.0: ("measured", "terrestrial"), 1.0: ("unknown", "terrestrial"),
                   0.0: ("unknown", "terrestrial")}


def test_api_create_and_update_set_latency_source(client, db):
    t = make_topology(db)
    a = make_node(db, t, "A", interfaces=IFACES)
    b = make_node(db, t, "B", interfaces=IFACES)
    db.commit()
    base = f"/api/topologies/{t.id}/links/"
    r = client.post(base, json={"source_node_id": a.id, "target_node_id": b.id,
                                "source_interface": "e0", "target_interface": "e0"})
    assert r.status_code == 200, r.text
    assert (r.json()["latency_source"], r.json()["latency_effective_source"]) == ("unknown", "unknown")
    r = client.post(base, json={"source_node_id": a.id, "target_node_id": b.id,
                                "source_interface": "e1", "target_interface": "e1", "latency_ms": 6.0})
    lid = r.json()["id"]
    assert (r.json()["latency_source"], r.json()["latency_effective_ms"]) == ("measured", 6.0)
    r = client.put(f"{base}{lid}", json={"latency_source": "unknown"})
    assert r.json()["latency_source"] == "unknown"
    r = client.put(f"{base}{lid}", json={"latency_ms": 9.0})
    assert (r.json()["latency_source"], r.json()["latency_effective_ms"]) == ("measured", 9.0)
    assert client.put(f"{base}{lid}", json={"latency_source": "guess"}).status_code == 400
    listed = {l["id"]: l for l in client.get(base).json()}
    assert listed[lid]["latency_effective_source"] == "measured"


def test_measured_zero_is_treated_as_unknown(db):
    t = make_topology(db)
    a = make_node(db, t, "A", location="NYC")
    b = make_node(db, t, "B", location="NYC")
    l = make_link(db, t, a, b, latency=0, latency_source="measured")
    assert _info(db, t, l).source == "estimated"   # same site → estimated, not "0 ms known"


def test_bulk_update_latency_null_means_unknown(client, db):
    t = make_topology(db)
    a, b = make_node(db, t, "A"), make_node(db, t, "B")
    l = make_link(db, t, a, b, latency=5)
    db.commit()
    url = f"/api/topologies/{t.id}/links/bulk-update"
    r = client.put(url, json={"link_ids": [l.id], "updates": {"latency_ms": None}})
    assert r.status_code == 200 and r.json()[0]["latency_source"] == "unknown"
    r = client.put(url, json={"link_ids": [l.id], "updates": {"latency_ms": 4.5}})
    assert r.json()[0]["latency_source"] == "measured"
    assert client.put(url, json={"link_ids": [l.id], "updates": {"latency_ms": "fast"}}).status_code == 400


def test_sync_payload_only_sends_known_latency(db):
    from app.routes.links import _link_sync_payload
    t = make_topology(db)
    a, b = make_node(db, t, "A"), make_node(db, t, "B")
    assert _link_sync_payload(make_link(db, t, a, b, latency=6), db)["latency_ms"] == 6
    assert _link_sync_payload(make_link(db, t, a, b, latency=None), db)["latency_ms"] is None

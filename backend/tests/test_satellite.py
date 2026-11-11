"""Satellite dishes / PoPs / satellite links (services/satellite.py) and the
satellite latency estimator (services/latency.py)."""
import pytest

from app.models.link import Link
from app.routes.topologies import _serialize_topology, _restore_into_topology
from app.services.latency import build_context, effective_latency
from app.services.path_analysis import analyze_paths
from app.services.satellite import validate_link_roles, SatelliteLinkError
from tests.factories import make_topology, make_node, make_link

IFACES = [{"name": "e0", "speed_gbps": 10.0}]


def test_role_rules():
    assert validate_link_roles("sat_dish", "sat_pop", "satellite", "leo") == "satellite"
    # Every dish ↔ PoP link is a satellite link, even if sent as terrestrial…
    assert validate_link_roles("sat_pop", "sat_dish", None, "geo") == "satellite"
    # …and needs an orbit.
    with pytest.raises(SatelliteLinkError, match="orbit"):
        validate_link_roles("sat_dish", "sat_pop", None, "")
    with pytest.raises(SatelliteLinkError, match="each other"):
        validate_link_roles("sat_dish", "sat_dish", None, None)
    # A satellite link must be dish ↔ PoP.
    with pytest.raises(SatelliteLinkError, match="Dish to a Satellite PoP"):
        validate_link_roles("router", "sat_pop", "satellite", "leo")
    # A dish's site link and a PoP's network link are ordinary links.
    assert validate_link_roles("sat_dish", "router", None, None) == "terrestrial"
    assert validate_link_roles("sat_pop", "router", None, None) == "terrestrial"


def _sat_topology(db):
    t = make_topology(db)
    dish = make_node(db, t, "Dish", role="sat_dish", interfaces=IFACES)
    pop = make_node(db, t, "PoP", role="sat_pop", interfaces=IFACES)
    rtr = make_node(db, t, "R", interfaces=IFACES)
    db.commit()
    return t, dish, pop, rtr


def test_api_satellite_link_needs_no_interfaces(client, db):
    t, dish, pop, _ = _sat_topology(db)
    r = client.post(f"/api/topologies/{t.id}/links/", json={
        "source_node_id": dish.id, "target_node_id": pop.id,
        "transport": "satellite", "orbit": "leo", "carrier": "Starlink",
        "bandwidth_gbps": 0.2, "cost": 50,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["transport"], body["orbit"], body["carrier"]) == ("satellite", "leo", "Starlink")
    assert (body["bandwidth_gbps"], body["cost"]) == (0.2, 50)
    assert body["latency_effective_source"] == "estimated" and body["latency_method"] == "LEO estimate"
    # Changing the orbit re-estimates; dropping it is refused.
    lid = body["id"]
    r = client.put(f"/api/topologies/{t.id}/links/{lid}", json={"orbit": "geo"})
    assert r.json()["latency_method"] == "GEO estimate"
    assert client.put(f"/api/topologies/{t.id}/links/{lid}", json={"orbit": ""}).status_code == 400


def test_api_rejects_invalid_pairs(client, db):
    t, dish, pop, rtr = _sat_topology(db)
    base = f"/api/topologies/{t.id}/links/"
    r = client.post(base, json={"source_node_id": dish.id, "target_node_id": pop.id})   # no orbit
    assert r.status_code == 400 and "orbit" in r.text
    r = client.post(base, json={"source_node_id": rtr.id, "target_node_id": pop.id,
                                "transport": "satellite", "orbit": "leo"})
    assert r.status_code == 400
    # A dish's ordinary site link still works (with interfaces).
    r = client.post(base, json={"source_node_id": dish.id, "target_node_id": rtr.id,
                                "source_interface": "e0", "target_interface": "e0"})
    assert r.status_code == 200 and r.json()["transport"] == "terrestrial"


@pytest.mark.parametrize("orbit,lo,hi", [("leo", 12, 20), ("meo", 60, 85), ("geo", 260, 300)])
def test_satellite_estimate_magnitudes(db, orbit, lo, hi):
    t = make_topology(db)
    dish = make_node(db, t, "Dish", role="sat_dish", geo_latitude=47.6, geo_longitude=-122.3)
    pop = make_node(db, t, "PoP", role="sat_pop", geo_latitude=45.5, geo_longitude=-122.7)
    l = make_link(db, t, dish, pop, latency=None, transport="satellite", orbit=orbit)
    info = effective_latency(l, build_context(db, [dish, pop]))
    assert info.source == "estimated" and info.method == f"{orbit.upper()} estimate"
    assert lo < info.ms < hi


def test_satellite_estimate_without_coordinates_and_measured_wins(db):
    t = make_topology(db)
    dish, pop = make_node(db, t, "Dish", role="sat_dish"), make_node(db, t, "PoP", role="sat_pop")
    geo = make_link(db, t, dish, pop, latency=None, transport="satellite", orbit="geo")
    ctx = build_context(db, [dish, pop])
    assert 270 < effective_latency(geo, ctx).ms < 300   # 30° elevation assumed
    known = make_link(db, t, dish, pop, latency=31.0, transport="satellite", orbit="geo")
    assert effective_latency(known, ctx) == (31.0, "measured", "measured")


def test_path_analysis_routes_site_dish_pop_core(db):
    t = make_topology(db)
    site = make_node(db, t, "SiteRtr")
    dish = make_node(db, t, "Dish", role="sat_dish")
    pop = make_node(db, t, "PoP", role="sat_pop")
    core = make_node(db, t, "Core")
    make_link(db, t, site, dish, latency=0.1)
    sat = make_link(db, t, dish, pop, latency=None, transport="satellite", orbit="leo", bw=0.2)
    make_link(db, t, pop, core, latency=2)
    p = analyze_paths(db, t.id, {"node_id": site.id}, {"node_id": core.id})["shortest"]["lowest_cost"]
    assert p["node_names"] == ["SiteRtr", "Dish", "PoP", "Core"]
    assert sat.id in p["link_ids"] and p["bottleneck_gbps"] == 0.2
    assert p["latency_estimated_links"] == 1 and p["latency_ms"] > 10


def test_orbit_survives_snapshot_restore(db):
    t = make_topology(db)
    dish, pop = make_node(db, t, "Dish", role="sat_dish"), make_node(db, t, "PoP", role="sat_pop")
    make_link(db, t, dish, pop, latency=None, transport="satellite", orbit="meo", carrier="SES")
    db.commit()
    _restore_into_topology(_serialize_topology(t.id, db), t.id, db)
    l = db.query(Link).filter_by(topology_id=t.id).one()
    assert (l.transport, l.orbit, l.carrier) == ("satellite", "meo", "SES")


def test_update_cannot_point_link_at_another_topology(client, db):
    t, dish, pop, _ = _sat_topology(db)
    other = make_topology(db, "other")
    foreign_pop = make_node(db, other, "ForeignPoP", role="sat_pop")
    db.commit()
    r = client.post(f"/api/topologies/{t.id}/links/", json={
        "source_node_id": dish.id, "target_node_id": pop.id, "transport": "satellite", "orbit": "leo", "bandwidth_gbps": 1})
    lid = r.json()["id"]
    r = client.put(f"/api/topologies/{t.id}/links/{lid}", json={"target_node_id": foreign_pop.id})
    assert r.status_code == 404


def test_role_change_that_breaks_links_is_refused(client, db):
    t = make_topology(db)
    a = make_node(db, t, "A", interfaces=IFACES)
    b = make_node(db, t, "B", interfaces=IFACES)
    make_link(db, t, a, b)
    db.commit()
    assert client.put(f"/api/topologies/{t.id}/nodes/{a.id}", json={"role": "sat_dish"}).status_code == 200
    # B → PoP would turn the terrestrial A–B link into a dish↔PoP link: refused.
    r = client.put(f"/api/topologies/{t.id}/nodes/{b.id}", json={"role": "sat_pop"})
    assert r.status_code == 400 and "A" in r.json()["detail"]


def test_geo_out_of_view_is_unknown(db):
    t = make_topology(db)
    dish = make_node(db, t, "Dish", role="sat_dish", geo_latitude=0.0, geo_longitude=-100.0)
    pop = make_node(db, t, "PoP", role="sat_pop", geo_latitude=0.0, geo_longitude=70.0)
    l = make_link(db, t, dish, pop, latency=None, transport="satellite", orbit="geo")
    assert effective_latency(l, build_context(db, [dish, pop])).source == "unknown"

"""A-loc → Z-loc path analysis (services/path_analysis.py): lowest-cost vs
lowest-latency paths, site ends, and link / node / SRLG-disjoint path sets."""
import pytest

from app.models.srlg import SrlgGroup, SrlgMember
from app.services.path_analysis import analyze_paths, PathAnalysisError
from tests.factories import make_topology, make_node, make_link


def _end(n):
    return {"node_id": n.id}


def test_lowest_cost_and_lowest_latency_differ(db):
    t = make_topology(db)
    a, b, c, z = (make_node(db, t, x) for x in "ABCZ")
    ab = make_link(db, t, a, b, cost=1, latency=50)
    bz = make_link(db, t, b, z, cost=1, latency=50)
    ac = make_link(db, t, a, c, cost=5, latency=1)
    cz = make_link(db, t, c, z, cost=5, latency=1)

    r = analyze_paths(db, t.id, _end(a), _end(z))["shortest"]
    assert r["lowest_cost"]["link_ids"] == [ab.id, bz.id]
    assert (r["lowest_cost"]["cost"], r["lowest_cost"]["latency_ms"]) == (2, 100)
    assert r["lowest_latency"]["link_ids"] == [ac.id, cz.id]
    assert (r["lowest_latency"]["cost"], r["lowest_latency"]["latency_ms"]) == (10, 2)
    assert r["same_path"] is False
    assert r["lowest_cost"]["node_names"] == ["A", "B", "Z"]


def test_customer_nodes_are_never_transit(db):
    t = make_topology(db)
    a, b, z = (make_node(db, t, x) for x in "ABZ")
    cust = make_node(db, t, "Cust", role="customer")
    make_link(db, t, a, cust, latency=1)
    make_link(db, t, cust, z, latency=1)
    make_link(db, t, a, b, latency=40)
    make_link(db, t, b, z, latency=40)
    r = analyze_paths(db, t.id, _end(a), _end(z))["shortest"]
    assert r["lowest_latency"]["node_names"] == ["A", "B", "Z"]
    # ...but a customer node is fine as an end.
    to_cust = analyze_paths(db, t.id, _end(a), _end(cust))["shortest"]["lowest_cost"]
    assert to_cust["node_names"] == ["A", "Cust"]


def test_latency_breakdown_measured_estimated_unknown(db):
    # A→B measured 4 ms; B→C estimated from coordinates; C→Z unknown (no coords).
    t = make_topology(db)
    a = make_node(db, t, "A")
    b = make_node(db, t, "B", geo_latitude=0.0, geo_longitude=0.0)
    c = make_node(db, t, "C", geo_latitude=0.0, geo_longitude=1.0)
    z = make_node(db, t, "Z")
    make_link(db, t, a, b, latency=4)
    make_link(db, t, b, c, latency=None)
    make_link(db, t, c, z, latency=None)
    p = analyze_paths(db, t.id, _end(a), _end(z))["shortest"]["lowest_cost"]
    assert p["latency_measured_ms"] == 4
    assert p["latency_estimated_links"] == 1 and p["latency_unknown_links"] == 1
    # ~111 km × 1.5 route factor ÷ 200 km/ms + 0.1 ms overhead ≈ 0.93 ms
    assert abs(p["latency_estimated_ms"] - 0.934) < 0.01
    assert p["latency_ms"] == round(p["latency_measured_ms"] + p["latency_estimated_ms"], 3)


def test_site_end_uses_best_node_at_the_site(db):
    t = make_topology(db)
    a1 = make_node(db, t, "A1", location="NYC")
    a2 = make_node(db, t, "A2", location="NYC")
    z = make_node(db, t, "Z", location="LAX")
    make_link(db, t, a1, z, cost=10)
    best = make_link(db, t, a2, z, cost=3)
    r = analyze_paths(db, t.id, {"location": "NYC"}, {"location": "LAX"})
    assert r["a_node_ids"] == sorted([a1.id, a2.id])
    assert r["shortest"]["lowest_cost"]["link_ids"] == [best.id]
    assert r["shortest"]["lowest_cost"]["node_ids"] == [a2.id, z.id]


def test_link_disjoint_vs_node_disjoint_through_shared_transit_node(db):
    # Two parallel links A–M and M–Z: two link-disjoint paths, but both
    # transit M, so only one node-disjoint path.
    t = make_topology(db)
    a, m, z = (make_node(db, t, x) for x in "AMZ")
    for _ in range(2):
        make_link(db, t, a, m)
        make_link(db, t, m, z)
    link = analyze_paths(db, t.id, _end(a), _end(z), mode="disjoint", disjoint="link")["disjoint"]
    assert len(link["paths"]) == 2
    assert not set(link["paths"][0]["link_ids"]) & set(link["paths"][1]["link_ids"])
    node = analyze_paths(db, t.id, _end(a), _end(z), mode="disjoint", disjoint="node")["disjoint"]
    assert len(node["paths"]) == 1
    assert node["method"] == "optimal"


def test_disjoint_minimises_total_weight(db):
    # Three A→Z branches costing 2, 4 and 20: the best two are returned, cheapest first.
    t = make_topology(db)
    a, z = make_node(db, t, "A"), make_node(db, t, "Z")
    for name, cost in (("B", 1), ("C", 2), ("D", 10)):
        x = make_node(db, t, name)
        make_link(db, t, a, x, cost=cost)
        make_link(db, t, x, z, cost=cost)
    d = analyze_paths(db, t.id, _end(a), _end(z), mode="disjoint", disjoint="node", max_paths=2)["disjoint"]
    assert [p["cost"] for p in d["paths"]] == [2, 4]


def test_srlg_disjoint_drops_path_sharing_a_risk_group(db):
    t = make_topology(db)
    a, b, c, z = (make_node(db, t, x) for x in "ABCZ")
    ab = make_link(db, t, a, b)
    make_link(db, t, b, z)
    ac = make_link(db, t, a, c, cost=2)
    make_link(db, t, c, z, cost=2)
    g = SrlgGroup(topology_id=t.id, name="conduit-1")
    db.add(g)
    db.flush()
    db.add_all([SrlgMember(srlg_id=g.id, member_type="link", member_id=ab.id),
                SrlgMember(srlg_id=g.id, member_type="link", member_id=ac.id)])
    db.flush()

    link = analyze_paths(db, t.id, _end(a), _end(z), mode="disjoint", disjoint="link")["disjoint"]
    assert len(link["paths"]) == 2
    assert link["shared_srlgs"] == [{"paths": [0, 1], "srlgs": ["conduit-1"]}]

    srlg = analyze_paths(db, t.id, _end(a), _end(z), mode="disjoint", disjoint="srlg")["disjoint"]
    assert len(srlg["paths"]) == 1
    assert srlg["method"] == "greedy"
    assert srlg["shared_srlgs"] == []


def test_unreachable_and_overlapping_ends_raise(db):
    t = make_topology(db)
    a, b, z = (make_node(db, t, x) for x in "ABZ")
    make_link(db, t, a, b)
    with pytest.raises(PathAnalysisError, match="No path"):
        analyze_paths(db, t.id, _end(a), _end(z))
    with pytest.raises(PathAnalysisError, match="overlap"):
        analyze_paths(db, t.id, _end(a), _end(a))
    with pytest.raises(PathAnalysisError, match="no usable nodes"):
        analyze_paths(db, t.id, {"location": "nowhere"}, _end(z))


def test_planned_links_excluded_unless_included(db):
    t = make_topology(db)
    a, b, c, z = (make_node(db, t, x) for x in "ABCZ")
    make_link(db, t, a, b)
    make_link(db, t, b, z)
    make_link(db, t, a, c, planned=True)
    make_link(db, t, c, z, planned=True)
    kw = dict(mode="disjoint", disjoint="node")
    assert len(analyze_paths(db, t.id, _end(a), _end(z), **kw)["disjoint"]["paths"]) == 1
    assert len(analyze_paths(db, t.id, _end(a), _end(z), include_planned=True, **kw)["disjoint"]["paths"]) == 2


def test_latency_ranking_counts_unknown_links_at_the_penalty(db):
    # Path via X: 2 unknown links (reported 0 ms, routed at 2 × 10 ms penalty).
    # Path via Y: measured 6 + 6 = 12 ms. Y must rank first.
    t = make_topology(db)
    a, x, y, z = (make_node(db, t, n) for n in "AXYZ")
    make_link(db, t, a, x, latency=None)
    make_link(db, t, x, z, latency=None)
    make_link(db, t, a, y, latency=6)
    make_link(db, t, y, z, latency=6)
    d = analyze_paths(db, t.id, _end(a), _end(z), mode="disjoint", metric="latency", disjoint="node")["disjoint"]
    assert [p["node_names"] for p in d["paths"]] == [["A", "Y", "Z"], ["A", "X", "Z"]]

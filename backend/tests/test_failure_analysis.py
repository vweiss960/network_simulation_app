"""run_simulation: SPOF detection, baseline-isolated exclusion, on-demand flag.

These pin the behaviours fixed this session (baseline-isolated nodes must not
inflate every scenario; failure analysis is skippable for fast util-only runs).
"""
from app.services.simulation import run_simulation
from tests.factories import make_topology, make_node, make_link, make_flow


def _linear(db, name="lin"):
    """A — B — C — D, returns (topology, (a, b, c, d))."""
    t = make_topology(db, name)
    a = make_node(db, t, "A"); b = make_node(db, t, "B")
    c = make_node(db, t, "C"); d = make_node(db, t, "D")
    make_link(db, t, a, b); make_link(db, t, b, c); make_link(db, t, c, d)
    return t, (a, b, c, d)


def _spof_node_ids(result):
    return {s["id"] for s in result["single_points_of_failure"] if s["type"] == "node"}


def test_cut_vertices_are_spofs(db):
    # A-B-C-D: removing B or C disconnects the graph; the leaves A/D do not.
    t, (a, b, c, d) = _linear(db)
    spofs = _spof_node_ids(run_simulation(t.id, db))
    assert b.id in spofs and c.id in spofs
    assert a.id not in spofs and d.id not in spofs


def test_ring_has_no_spof(db):
    # A-B-C-D-A ring: any single node/link failure leaves the rest connected.
    t = make_topology(db, "ring")
    a = make_node(db, t, "A"); b = make_node(db, t, "B")
    c = make_node(db, t, "C"); d = make_node(db, t, "D")
    make_link(db, t, a, b); make_link(db, t, b, c)
    make_link(db, t, c, d); make_link(db, t, d, a)
    assert run_simulation(t.id, db)["single_points_of_failure"] == []


def test_baseline_isolated_node_excluded_from_every_scenario(db):
    # A-B-C-D plus an orphan X (no links). X is disconnected with nothing
    # failed, so it must never appear as "isolated" in any failure scenario.
    t, (a, b, c, d) = _linear(db)
    x = make_node(db, t, "X")  # orphan
    r = run_simulation(t.id, db)
    for fs in r["failure_scenarios"]:
        assert x.id not in (fs.get("isolated_node_ids") or [])
    # The orphan must not turn unrelated failures into SPOFs: leaf A failing
    # still isolates nothing.
    a_scen = next(fs for fs in r["failure_scenarios"]
                  if fs["failed_element_type"] == "node" and fs["failed_element_id"] == a.id)
    assert a_scen["isolated_node_ids"] == []


def test_failure_analysis_flag_skips_the_expensive_pass(db):
    t, (a, _b, _c, d) = _linear(db)
    make_flow(db, t, "f", a, d, bw=2.0)
    r = run_simulation(t.id, db, failure_analysis=False)
    assert r["failure_scenarios"] == []
    assert r["single_points_of_failure"] == []
    # Utilization/flow paths are still computed on the fast path.
    assert r["link_utilizations"] and r["flow_paths"]

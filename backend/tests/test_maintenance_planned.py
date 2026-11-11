"""Maintenance-window baseline must match the canvas simulation's network.

Regression: run_maintenance_window computed its baseline on the UNfiltered
merged topology — planned links included — while the canvas simulation excludes
them by default. A cheap planned link would absorb a flow in the baseline, so
failing the real link the canvas showed carrying that traffic reported
"no change" instead of "rerouted".
"""
from app.services.simulation import run_maintenance_window, run_simulation
from tests.factories import make_topology, make_node, make_link, make_flow


def _topo_with_planned_shortcut(db):
    """A->B direct (real, cost 1), A->C->B detour (real, cost 2), plus a
    cheaper PLANNED A-B parallel link (cost 0.1) that must not attract traffic
    unless planned links are explicitly included."""
    t = make_topology(db, "T")
    a = make_node(db, t, "A")
    b = make_node(db, t, "B")
    c = make_node(db, t, "C")
    real_ab = make_link(db, t, a, b, cost=1.0)
    make_link(db, t, a, c, cost=1.0)
    make_link(db, t, c, b, cost=1.0)
    planned_ab = make_link(db, t, a, b, cost=0.1, planned=True)
    make_flow(db, t, "F", a, b, bw=1.0)
    db.commit()
    return t, real_ab, planned_ab


def test_maintenance_baseline_excludes_planned_and_reports_reroute(db):
    t, real_ab, planned_ab = _topo_with_planned_shortcut(db)

    # Baseline must agree with the canvas sim: flow rides the REAL direct link.
    r = run_maintenance_window(t.id, db, [], [real_ab.id])
    baseline_links = set()
    for fp in r["baseline_flow_paths"]:
        for lids in fp["link_ids_per_path"]:
            baseline_links.update(lids)
    assert planned_ab.id not in baseline_links
    assert real_ab.id in baseline_links

    # Failing the in-use real link must be reported as a reroute (via C),
    # not silently absorbed by the planned link.
    assert [f["status"] for f in r["affected_flows"]] == ["rerouted"]
    failed_links = set()
    for fp in r["failed_flow_paths"]:
        for lids in fp["link_ids_per_path"]:
            failed_links.update(lids)
    assert planned_ab.id not in failed_links

    # Consistency with the canvas: same baseline path as run_simulation.
    sim = run_simulation(t.id, db, include_planned=False, failure_analysis=False)
    sim_links = set()
    for fp in sim["flow_paths"]:
        for lids in fp["link_ids_per_path"]:
            sim_links.update(lids)
    assert sim_links == baseline_links


def test_maintenance_include_planned_opts_into_planned_capacity(db):
    t, real_ab, planned_ab = _topo_with_planned_shortcut(db)
    # With planned explicitly included, the cheap planned link carries the flow
    # and failing the real link is correctly a no-op — a consistent view of the
    # "with planned build-out" network.
    r = run_maintenance_window(t.id, db, [], [real_ab.id], include_planned=True)
    baseline_links = set()
    for fp in r["baseline_flow_paths"]:
        for lids in fp["link_ids_per_path"]:
            baseline_links.update(lids)
    assert planned_ab.id in baseline_links
    assert r["affected_flows"] == []

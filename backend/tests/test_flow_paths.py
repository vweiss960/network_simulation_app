"""compute_flow_paths: routing + utilization."""
from app.services.simulation import compute_flow_paths
from tests.factories import make_topology, make_node, make_link, make_flow


def _linear(db):
    """A — B — C — D (single path)."""
    t = make_topology(db, "lin")
    a = make_node(db, t, "A"); b = make_node(db, t, "B")
    c = make_node(db, t, "C"); d = make_node(db, t, "D")
    links = [make_link(db, t, a, b), make_link(db, t, b, c), make_link(db, t, c, d)]
    return t, [a, b, c, d], links


def test_shortest_path_traverses_chain(db):
    t, nodes, links = _linear(db)
    a, b, c, d = nodes
    f = make_flow(db, t, "f", a, d, bw=2.0)
    flow_paths, _ = compute_flow_paths(nodes, links, [f], db)

    assert len(flow_paths) == 1
    fp = flow_paths[0]
    assert fp["paths"] == [[a.id, b.id, c.id, d.id]]


def test_utilization_reflects_flow_bandwidth(db):
    t, nodes, links = _linear(db)
    a, _, _, d = nodes
    f = make_flow(db, t, "f", a, d, bw=2.0)  # 2G over 10G links -> 20%
    _, link_utils = compute_flow_paths(nodes, links, [f], db)

    by_id = {lu["link_id"]: lu for lu in link_utils}
    for l in links:
        assert by_id[l.id]["used_gbps"] == 2.0
        assert by_id[l.id]["utilization_pct"] == 20.0


def test_no_path_when_disconnected(db):
    t = make_topology(db, "split")
    a = make_node(db, t, "A"); b = make_node(db, t, "B")
    c = make_node(db, t, "C"); d = make_node(db, t, "D")
    links = [make_link(db, t, a, b), make_link(db, t, c, d)]  # two islands
    f = make_flow(db, t, "f", a, d, bw=1.0)  # A and D are in different islands
    flow_paths, _ = compute_flow_paths([a, b, c, d], links, [f], db)
    assert flow_paths[0]["paths"] == []


def test_ecmp_splits_across_equal_cost_paths(db):
    # Diamond: A-B-D and A-C-D, both cost 2 -> two equal paths.
    t = make_topology(db, "diamond")
    a = make_node(db, t, "A"); b = make_node(db, t, "B")
    c = make_node(db, t, "C"); d = make_node(db, t, "D")
    links = [
        make_link(db, t, a, b), make_link(db, t, b, d),
        make_link(db, t, a, c), make_link(db, t, c, d),
    ]
    f = make_flow(db, t, "f", a, d, bw=4.0, ecmp=True)
    flow_paths, _ = compute_flow_paths([a, b, c, d], links, [f], db)

    fp = flow_paths[0]
    assert len(fp["paths"]) == 2
    # bandwidth is divided across the equal-cost paths
    assert abs(sum(fp["bandwidth_per_path_gbps"]) - 4.0) < 1e-6

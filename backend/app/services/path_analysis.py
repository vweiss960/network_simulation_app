"""A-loc → Z-loc path analysis without creating a flow.

- shortest: the lowest-cost path (the IGP default) and the lowest-latency path.
- disjoint: up to N mutually disjoint paths — link-disjoint, node-disjoint
  (optimal, via min-cost flow) or SRLG-disjoint (greedy).

Each end is a node or a site (every node at a `location`); a site end lets a
path start/finish at any of its nodes. Works on the merged inheritance view.
Customer nodes are endpoints, never transit: they're only usable as an end.
"""
import networkx as nx
from sqlalchemy.orm import Session

from app.models.srlg import SrlgGroup
from app.services.latency import latency_infos, routing_latencies, summarize, unknown_penalty_ms
from app.services.simulation import _build_multigraph, _find_shortest_path, _without_planned
from app.services.topology_chain import get_merged_topology_data, get_topology_chain

# Virtual super-source / super-sink joined to every node of a (site) end with
# zero-weight edges. Real node ids are positive.
_SRC, _DST = -1, -2
_VIRTUAL_LINK = -1
METRIC_ATTR = {"cost": "cost", "latency": "latency_ms"}


class PathAnalysisError(ValueError):
    """A request that can't be answered (bad end, A = Z, no path)."""


def _resolve_end(end: dict, nodes, label: str) -> set[int]:
    node_id = end.get("node_id")
    location = (end.get("location") or "").strip()
    if node_id is not None:
        ids = {n.id for n in nodes if n.id == node_id}
        what = f"node {node_id}"
    elif location:
        ids = {n.id for n in nodes if (n.location or "").strip() == location}
        what = f"site '{location}'"
    else:
        raise PathAnalysisError(f"Choose a node or site for {label}")
    if not ids:
        raise PathAnalysisError(f"{label}: no usable nodes for {what} (planned items are excluded unless included)")
    return ids


def _graph(nodes, links, db, metric, a_ids, z_ids, exclude_nodes=None, exclude_links=None):
    G = _build_multigraph(nodes, links, db, weight=METRIC_ATTR[metric],
                          exclude_node_ids=exclude_nodes, exclude_link_ids=exclude_links)
    for a in a_ids:
        if a in G:
            G.add_edge(_SRC, a, id=_VIRTUAL_LINK, weight=0)
    for z in z_ids:
        if z in G:
            G.add_edge(z, _DST, id=_VIRTUAL_LINK, weight=0)
    return G


def _shortest(G) -> tuple[list[int], list[int]] | None:
    try:
        path, lids = _find_shortest_path(G, _SRC, _DST)
    except (nx.NodeNotFound, nx.NetworkXNoPath):
        return None
    return path[1:-1], [l for l in lids if l != _VIRTUAL_LINK]


def _stats(node_ids, link_ids, node_by_id, link_by_id, lat) -> dict:
    ls = [link_by_id[l] for l in link_ids]
    return {
        "node_ids": node_ids,
        "node_names": [node_by_id[n].name for n in node_ids],
        "link_ids": link_ids,
        "hops": len(ls),
        "cost": round(sum(l.cost or 0 for l in ls), 3),
        # latency_ms = measured + estimated; unknown links aren't counted.
        **summarize([lat[l] for l in link_ids]),
        "bottleneck_gbps": min((l.bandwidth_gbps or 0 for l in ls), default=0),
    }


def _srlg_maps(db, topology_id, node_remap):
    """(srlg names, link → srlg ids, node → srlg ids) over the topology chain."""
    chain = get_topology_chain(db, topology_id)
    names, by_link, by_node = {}, {}, {}
    for g in db.query(SrlgGroup).filter(SrlgGroup.topology_id.in_(chain)).all():
        names[g.id] = g.name
        for m in g.members:
            if m.member_type == "node":
                by_node.setdefault(node_remap.get(m.member_id, m.member_id), set()).add(g.id)
            else:
                by_link.setdefault(m.member_id, set()).add(g.id)
    return names, by_link, by_node


def _path_srlgs(p, end_ids, by_link, by_node) -> set[int]:
    s = set()
    for l in p["link_ids"]:
        s |= by_link.get(l, set())
    for n in p["node_ids"]:
        if n not in end_ids:
            s |= by_node.get(n, set())
    return s


def _disjoint_flow(db, nodes, links, metric, a_ids, z_ids, max_paths, node_disjoint, no_transit):
    """Min-cost flow: up to max_paths link- (or node-) disjoint paths with the
    lowest total weight. Each link is a capacity-1 arc between its own in/out
    vertices (keeps parallel links distinct; an undirected link is used once);
    node-disjoint also splits transit nodes into capacity-1 in/out pairs."""
    end_ids = a_ids | z_ids
    # Latency routes on the effective value (estimates; unknown → penalty).
    lat_w = routing_latencies(db, nodes, links) if metric == "latency" else None
    node_set = {n.id for n in nodes} - no_transit

    def n_in(x):
        return ("N", x) if (not node_disjoint or x in end_ids) else ("N", x, "in")

    def n_out(x):
        return ("N", x) if (not node_disjoint or x in end_ids) else ("N", x, "out")

    D = nx.DiGraph()
    if node_disjoint:
        for x in node_set - end_ids:
            D.add_edge(n_in(x), n_out(x), capacity=1, weight=0)
    for l in links:
        u, v = l.source_node_id, l.target_node_id
        if u not in node_set or v not in node_set:
            continue
        # min-cost flow needs integer weights; scale to keep 3 decimals.
        w = int(round((lat_w[l.id] if lat_w is not None else (l.cost or 0)) * 1000))
        lin, lout = ("L", l.id, "in"), ("L", l.id, "out")
        D.add_edge(lin, lout, capacity=1, weight=w)
        for x in (u, v):
            D.add_edge(n_out(x), lin, capacity=1, weight=0)
            D.add_edge(lout, n_in(x), capacity=1, weight=0)
    D.add_edge("S0", "S", capacity=max_paths, weight=0)
    for a in a_ids:
        D.add_edge("S", n_in(a), capacity=max_paths, weight=0)
    for z in z_ids:
        D.add_edge(n_out(z), "T", capacity=max_paths, weight=0)

    flow = nx.max_flow_min_cost(D, "S0", "T")
    # Decompose the integral flow into S0 → T walks (cutting any zero-cost cycles).
    paths = []
    while True:
        walk, seen, cur = ["S0"], {"S0": 0}, "S0"
        while cur != "T":
            nxt = next((v for v, f in flow[cur].items() if f > 0), None)
            if nxt is None:
                break
            flow[cur][nxt] -= 1
            if nxt in seen:
                walk = walk[:seen[nxt] + 1]
                seen = {v: i for i, v in enumerate(walk)}
            else:
                seen[nxt] = len(walk)
                walk.append(nxt)
            cur = nxt
        if cur != "T":
            break
        node_ids, link_ids = [], []
        for v in walk:
            if isinstance(v, tuple) and v[0] == "N" and (not node_ids or node_ids[-1] != v[1]):
                node_ids.append(v[1])
            elif isinstance(v, tuple) and v[0] == "L" and v[2] == "in":
                link_ids.append(v[1])
        paths.append((node_ids, link_ids))
    return paths


def analyze_paths(db: Session, topology_id: int, a: dict, z: dict, mode: str = "shortest",
                  metric: str = "cost", disjoint: str = "link", max_paths: int = 2,
                  include_planned: bool = False) -> dict:
    nodes, links, _flows, node_remap = get_merged_topology_data(db, topology_id)
    if not include_planned:
        nodes, links = _without_planned(nodes, links)
    a_ids = _resolve_end(a, nodes, "A-end")
    z_ids = _resolve_end(z, nodes, "Z-end")
    if a_ids & z_ids:
        raise PathAnalysisError("A-end and Z-end overlap — pick different nodes or sites")
    node_by_id = {n.id: n for n in nodes}
    link_by_id = {l.id: l for l in links}
    lat = latency_infos(db, nodes, links)
    # Customer sites terminate traffic; they never carry it between A and Z.
    no_transit = {n.id for n in nodes if n.role == "customer"} - a_ids - z_ids
    result: dict = {"mode": mode, "a_node_ids": sorted(a_ids), "z_node_ids": sorted(z_ids)}

    if mode == "shortest":
        out = {}
        for m in ("cost", "latency"):
            found = _shortest(_graph(nodes, links, db, m, a_ids, z_ids, exclude_nodes=no_transit))
            out[m] = _stats(*found, node_by_id, link_by_id, lat) if found else None
        if out["cost"] is None:
            raise PathAnalysisError("No path between A-end and Z-end")
        result["shortest"] = {
            "lowest_cost": out["cost"],
            "lowest_latency": out["latency"],
            "same_path": out["latency"] is not None and out["cost"]["link_ids"] == out["latency"]["link_ids"],
        }
        return result

    if mode != "disjoint":
        raise PathAnalysisError(f"Unknown mode '{mode}'")
    if metric not in METRIC_ATTR:
        raise PathAnalysisError(f"Unknown metric '{metric}'")
    max_paths = max(2, min(int(max_paths), 4))
    end_ids = a_ids | z_ids
    srlg_names, by_link, by_node = _srlg_maps(db, topology_id, node_remap)

    if disjoint in ("link", "node"):
        found = _disjoint_flow(db, nodes, links, metric, a_ids, z_ids, max_paths, disjoint == "node", no_transit)
        method = "optimal"
    elif disjoint == "srlg":
        # Greedy: take the best path, then remove its links, transit nodes and
        # everything sharing an SRLG with them; repeat. Exact SRLG-disjoint
        # routing is NP-hard, so this can miss some diverse combinations.
        found, ex_nodes, ex_links = [], set(no_transit), set()
        for _ in range(max_paths):
            p = _shortest(_graph(nodes, links, db, metric, a_ids, z_ids, ex_nodes, ex_links))
            if not p:
                break
            found.append(p)
            ex_links |= set(p[1])
            ex_nodes |= set(p[0]) - end_ids
            hit = _path_srlgs({"node_ids": p[0], "link_ids": p[1]}, end_ids, by_link, by_node)
            ex_links |= {l for l, gs in by_link.items() if gs & hit}
            ex_nodes |= {n for n, gs in by_node.items() if gs & hit} - end_ids
        method = "greedy"
    else:
        raise PathAnalysisError(f"Unknown independence '{disjoint}'")

    paths = [_stats(n, l, node_by_id, link_by_id, lat) for n, l in found]
    # Rank by what the search optimized: for latency, unknown links count at
    # the penalty (they're excluded from the reported latency_ms).
    penalty = unknown_penalty_ms(db)
    paths.sort(key=lambda p: (p["cost"] if metric == "cost"
                              else p["latency_ms"] + p["latency_unknown_links"] * penalty, p["hops"]))
    if not paths:
        raise PathAnalysisError("No path between A-end and Z-end")
    # Risk groups shared between the found paths (always empty for SRLG mode).
    shared = []
    groups = [_path_srlgs(p, end_ids, by_link, by_node) for p in paths]
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            common = groups[i] & groups[j]
            if common:
                shared.append({"paths": [i, j], "srlgs": sorted(srlg_names[g] for g in common)})
    result["disjoint"] = {
        "independence": disjoint, "metric": metric, "method": method,
        "requested": max_paths, "paths": paths, "shared_srlgs": shared,
    }
    return result

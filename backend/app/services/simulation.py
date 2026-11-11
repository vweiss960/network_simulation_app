import networkx as nx
import numpy as np
import heapq
from typing import Optional
from sqlalchemy.orm import Session
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.flow import Flow
from app.models.global_settings import GlobalSettings
from app.models.srlg import SrlgGroup, SrlgMember


def _get_setting(db: Session, key: str, default: float) -> float:
    setting = db.query(GlobalSettings).filter(GlobalSettings.key == key).first()
    return setting.value if setting else default


def _latency_weights(db, nodes, links, weight: str) -> dict[int, float] | None:
    """Effective latency per link (measured, else estimated, else the unknown
    penalty) when routing on latency; None for any other weight."""
    if weight != "latency_ms" or db is None:
        return None
    from app.services.latency import routing_latencies
    return routing_latencies(db, nodes, links)


def _build_multigraph(
    nodes: list[NodeInstance],
    links: list[Link],
    db: Session,
    exclude_node_ids: set[int] | None = None,
    exclude_link_ids: set[int] | None = None,
    weight: str = "cost",
) -> nx.MultiGraph:
    exclude_node_ids = exclude_node_ids or set()
    exclude_link_ids = exclude_link_ids or set()
    lat_w = _latency_weights(db, nodes, links, weight)
    G = nx.MultiGraph()
    for node in nodes:
        if node.id not in exclude_node_ids:
            G.add_node(node.id, name=node.name, location=node.location or "")
    for link in links:
        if link.id in exclude_link_ids:
            continue
        if link.source_node_id in exclude_node_ids or link.target_node_id in exclude_node_ids:
            continue
        w = lat_w[link.id] if lat_w is not None else getattr(link, weight if weight in ("cost", "latency_ms") else "cost", link.cost)
        G.add_edge(
            link.source_node_id, link.target_node_id,
            key=link.id, id=link.id,
            bandwidth=link.bandwidth_gbps, cost=link.cost,
            latency=link.latency_ms, weight=w,
        )
    return G


def _build_graph(
    nodes: list[NodeInstance],
    links: list[Link],
    db: Session,
    exclude_node_ids: set[int] | None = None,
    exclude_link_ids: set[int] | None = None,
    weight: str = "cost",
) -> nx.Graph:
    exclude_node_ids = exclude_node_ids or set()
    exclude_link_ids = exclude_link_ids or set()
    lat_w = _latency_weights(db, nodes, links, weight)
    G = nx.Graph()
    for node in nodes:
        if node.id not in exclude_node_ids:
            G.add_node(node.id, name=node.name, location=node.location or "")
    for link in links:
        if link.id in exclude_link_ids:
            continue
        if link.source_node_id in exclude_node_ids or link.target_node_id in exclude_node_ids:
            continue
        w = lat_w[link.id] if lat_w is not None else getattr(link, weight if weight in ("cost", "latency_ms") else "cost", link.cost)
        if G.has_edge(link.source_node_id, link.target_node_id):
            existing_w = G[link.source_node_id][link.target_node_id].get("weight", float("inf"))
            if w < existing_w:
                G[link.source_node_id][link.target_node_id].update(
                    id=link.id, bandwidth=link.bandwidth_gbps, cost=link.cost,
                    latency=link.latency_ms, weight=w,
                )
        else:
            G.add_edge(
                link.source_node_id, link.target_node_id,
                id=link.id, bandwidth=link.bandwidth_gbps, cost=link.cost,
                latency=link.latency_ms, weight=w,
            )
    return G


def _without_planned(nodes, links):
    """Drop planned nodes, planned links, and links touching a planned node.

    The ONE planned-filter every analysis entry point (simulation, maintenance
    window, SRLG, Monte Carlo) must share: a baseline computed on a different
    graph than the canvas simulation silently routes flows over links that
    don't exist yet, so failure reports show "no change" for links the canvas
    shows carrying traffic.
    """
    planned_node_ids = {n.id for n in nodes if n.is_planned}
    nodes = [n for n in nodes if not n.is_planned]
    links = [l for l in links if not l.is_planned
             and l.source_node_id not in planned_node_ids
             and l.target_node_id not in planned_node_ids]
    return nodes, links


def _isolated_in_graph(G: nx.Graph) -> set[int]:
    """Node ids NOT in the largest connected component — i.e. already
    disconnected. Used to compute the baseline-isolated set so failure
    scenarios only report nodes newly isolated by the failure."""
    components = list(nx.connected_components(G))
    if len(components) <= 1:
        return set()
    largest = max(components, key=len)
    return {nid for comp in components if comp is not largest for nid in comp}


def _compute_dynamic_cost(link: Link, current_util_pct: float) -> float:
    base = link.cost
    if current_util_pct < 50:
        return base
    elif current_util_pct < 75:
        return base * 2
    elif current_util_pct < 90:
        return base * 5
    else:
        return base * 20


def _find_shortest_path(G: nx.MultiGraph, source: int, target: int) -> tuple[list[int], list[int]]:
    """Find single shortest path in multigraph. Returns (node_path, link_ids)."""
    if source not in G or target not in G:
        raise nx.NodeNotFound(f"Source {source} or target {target} not in G")

    # Dijkstra tracking best edge per hop. Ties break on VALUES (node id, then
    # the node-id path) — never on object identity (a previous id()-based
    # tie-break made equal-cost path choice depend on memory addresses, so
    # repeated maintenance runs flip-flopped between "rerouted" and "no
    # change"). With this ordering the lexicographically-smallest equal-cost
    # path always wins, deterministically across runs.
    heap = [(0, source, [source], [])]
    visited = set()

    while heap:
        dist, node, path, link_ids = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == target:
            return path, link_ids

        for neighbor in G[node]:
            if neighbor in visited:
                continue
            best_w = float("inf")
            best_lid = None
            for edge_key, edge_data in G[node][neighbor].items():
                w = edge_data.get("weight", 1)
                if w < best_w:
                    best_w = w
                    best_lid = edge_data.get("id", edge_key)
            if best_lid is not None:
                heapq.heappush(heap, (dist + best_w, neighbor, path + [neighbor], link_ids + [best_lid]))

    raise nx.NetworkXNoPath(f"No path between {source} and {target}")


def _find_all_ecmp_paths(G: nx.MultiGraph, source: int, target: int) -> list[tuple[list[int], list[int]]]:
    """Find ALL equal-cost paths in a multigraph, including parallel-link variants.
    Returns list of (node_path, link_ids) tuples."""
    if source not in G or target not in G:
        raise nx.NodeNotFound(f"Source {source} or target {target} not in G")

    # Phase 1: Dijkstra to find shortest distance to each node
    dist = {source: 0}
    heap = [(0, source)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, float("inf")):
            continue
        for neighbor in G[node]:
            # Best weight among parallel edges
            best_w = min(ed.get("weight", 1) for ed in G[node][neighbor].values())
            nd = d + best_w
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                heapq.heappush(heap, (nd, neighbor))

    if target not in dist:
        raise nx.NetworkXNoPath(f"No path between {source} and {target}")

    shortest_dist = dist[target]

    # Phase 2: DFS to enumerate all paths with total weight == shortest_dist
    # At each hop, consider ALL parallel edges that have the best weight for that hop
    results: list[tuple[list[int], list[int]]] = []

    def dfs(node: int, current_dist: float, node_path: list[int], link_path: list[int]):
        if node == target:
            if abs(current_dist - shortest_dist) < 1e-9:
                results.append((list(node_path), list(link_path)))
            return

        for neighbor in G[node]:
            if neighbor in node_path:  # avoid cycles
                continue
            if neighbor not in dist:
                continue

            # Find best weight among parallel edges to this neighbor
            best_w = min(ed.get("weight", 1) for ed in G[node][neighbor].values())
            new_dist = current_dist + best_w

            # Prune: can't possibly reach target within shortest distance
            if new_dist > shortest_dist + 1e-9:
                continue
            # Check this hop is on a shortest path
            if abs(dist.get(node, 0) + best_w - dist.get(neighbor, float("inf"))) > 1e-9:
                continue

            # Enumerate all parallel edges with this best weight
            equal_cost_edges = [
                (eid, ed.get("id", eid))
                for eid, ed in G[node][neighbor].items()
                if abs(ed.get("weight", 1) - best_w) < 1e-9
            ]

            for _, lid in equal_cost_edges:
                node_path.append(neighbor)
                link_path.append(lid)
                dfs(neighbor, new_dist, node_path, link_path)
                node_path.pop()
                link_path.pop()

    dfs(source, 0, [source], [])

    if not results:
        # Fallback to single path
        path, lids = _find_shortest_path(G, source, target)
        return [(path, lids)]

    return results


def compute_flow_paths(
    nodes: list[NodeInstance],
    links: list[Link],
    flows: list[Flow],
    db: Session,
    exclude_node_ids: set[int] | None = None,
    exclude_link_ids: set[int] | None = None,
) -> tuple[list[dict], list[dict]]:
    link_usage: dict[int, float] = {}
    link_flows: dict[int, list[int]] = {}
    flow_paths = []
    link_by_id = {link.id: link for link in links}
    # Effective per-link latency for the reported path totals.
    from app.services.latency import latency_infos
    lat_infos = latency_infos(db, nodes, links) if (db is not None and flows) else {}
    # Within a single compute_flow_paths call the exclusion set is fixed, so
    # the routing graph is identical for every flow sharing a weight metric.
    # Build each variant once and reuse it across flows instead of rebuilding
    # per flow (the old behavior, which made the whole simulation ~F× more
    # graph construction than necessary). Keyed by routing metric:
    #   "cost"        — shortest_path
    #   "latency_ms"  — lowest_latency
    #   "optimized"   — its own graph; every optimized flow fully rewrites all
    #                   edge weights from live utilization before routing, so
    #                   reusing one instance across optimized flows is correct
    #                   and can't leak mutated weights into the static graphs.
    graph_cache: dict[str, nx.MultiGraph] = {}

    sorted_flows = sorted(flows, key=lambda f: 0 if f.routing_behavior == "explicit" else 1)

    for flow in sorted_flows:
        if exclude_node_ids and (flow.source_node_id in exclude_node_ids or flow.dest_node_id in exclude_node_ids):
            flow_paths.append({
                "flow_id": flow.id, "flow_name": flow.name,
                "paths": [], "link_ids_per_path": [], "bandwidth_per_path_gbps": [],
            })
            continue

        if flow.routing_behavior == "explicit" and flow.explicit_path:
            # Validate the explicit path — check all nodes and links still exist
            explicit_valid = True
            ep_nodes = flow.explicit_path
            ep_links = flow.explicit_link_ids if hasattr(flow, 'explicit_link_ids') and flow.explicit_link_ids else []

            active_node_ids = {n.id for n in nodes} - (exclude_node_ids or set())
            active_link_ids = {l.id for l in links} - (exclude_link_ids or set())

            # Check all nodes in path exist and aren't excluded
            for nid in ep_nodes:
                if nid not in active_node_ids:
                    explicit_valid = False
                    break

            # Check all links in path exist and aren't excluded
            if explicit_valid and ep_links:
                for lid in ep_links:
                    if lid not in active_link_ids:
                        explicit_valid = False
                        break

            if explicit_valid:
                paths_with_links = [(ep_nodes, ep_links)]
            else:
                paths_with_links = []  # Path is broken
        else:
            # Pick the cache key + the metric to build the graph's static
            # "weight" attribute from. optimized builds off cost but gets its
            # own cache slot since it mutates weights per flow.
            if flow.routing_behavior == "optimized":
                cache_key, build_weight = "optimized", "cost"
            elif flow.routing_behavior == "lowest_latency":
                cache_key = build_weight = "latency_ms"
            else:
                cache_key = build_weight = "cost"

            G = graph_cache.get(cache_key)
            if G is None:
                G = _build_multigraph(nodes, links, db, exclude_node_ids, exclude_link_ids, build_weight)
                graph_cache[cache_key] = G

            if flow.routing_behavior == "optimized":
                for u, v, key, data in G.edges(data=True, keys=True):
                    lid = data["id"]
                    current_util = (link_usage.get(lid, 0) / data["bandwidth"] * 100) if data["bandwidth"] > 0 else 0
                    lnk = link_by_id.get(lid)
                    if lnk:
                        data["weight"] = _compute_dynamic_cost(lnk, current_util)

            try:
                if flow.ecmp_enabled:
                    paths_with_links = _find_all_ecmp_paths(G, flow.source_node_id, flow.dest_node_id)
                else:
                    node_path, path_link_ids = _find_shortest_path(G, flow.source_node_id, flow.dest_node_id)
                    paths_with_links = [(node_path, path_link_ids)]
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                paths_with_links = []

        paths = [p for p, _ in paths_with_links]
        link_ids_per_path = [l for _, l in paths_with_links]

        num_paths = max(len(paths), 1)
        bw_per_path = flow.bandwidth_gbps / num_paths
        bw_list = [bw_per_path] * len(paths)

        for pi, (path, lids) in enumerate(paths_with_links):
            if lids:
                for lid in lids:
                    link_usage[lid] = link_usage.get(lid, 0) + bw_per_path
                    if lid not in link_flows:
                        link_flows[lid] = []
                    if flow.id not in link_flows[lid]:
                        link_flows[lid].append(flow.id)
            else:
                # Fallback for explicit paths
                for i in range(len(path) - 1):
                    for link in links:
                        if (link.source_node_id == path[i] and link.target_node_id == path[i + 1]) or \
                           (link.target_node_id == path[i] and link.source_node_id == path[i + 1]):
                            link_usage[link.id] = link_usage.get(link.id, 0) + bw_per_path
                            if link.id not in link_flows:
                                link_flows[link.id] = []
                            if flow.id not in link_flows[link.id]:
                                link_flows[link.id].append(flow.id)
                            break

        # Compute total latency per path
        latency_per_path = []
        for lids in link_ids_per_path:
            # Known + estimated latency; unknown links don't add to the total.
            total_latency = sum(lat_infos[lid].ms or 0 for lid in lids if lid in lat_infos)
            latency_per_path.append(round(total_latency, 2))

        flow_paths.append({
            "flow_id": flow.id, "flow_name": flow.name,
            "paths": paths, "link_ids_per_path": link_ids_per_path,
            "bandwidth_per_path_gbps": bw_list,
            "latency_per_path_ms": latency_per_path,
        })

    link_utilizations = []
    for link in links:
        if (exclude_link_ids and link.id in exclude_link_ids) or \
           (exclude_node_ids and (link.source_node_id in exclude_node_ids or link.target_node_id in exclude_node_ids)):
            continue
        used = link_usage.get(link.id, 0)
        util_pct = (used / link.bandwidth_gbps * 100) if link.bandwidth_gbps > 0 else 0
        # Cost per Gbps = circuit_cost / used_gbps on this link
        circuit_cost = getattr(link, 'circuit_cost', 0) or 0
        if circuit_cost > 0 and used > 0:
            cost_per_gb = round(circuit_cost / used, 2)
        else:
            cost_per_gb = 0
        link_utilizations.append({
            "link_id": link.id,
            "source_node_id": link.source_node_id,
            "target_node_id": link.target_node_id,
            "bandwidth_gbps": link.bandwidth_gbps,
            "used_gbps": round(used, 4),
            "utilization_pct": round(util_pct, 2),
            "flow_ids": link_flows.get(link.id, []),
            "cost_per_gb": round(cost_per_gb, 4),
        })

    return flow_paths, link_utilizations


def run_simulation(topology_id: int, db: Session,
                    exclude_node_ids: list[int] | None = None,
                    exclude_link_ids: list[int] | None = None,
                    include_planned: bool = False,
                    failure_analysis: bool = True) -> dict:
    from app.services.topology_chain import get_merged_topology_data
    nodes, links, flows, _ = get_merged_topology_data(db, topology_id)

    # Filter out planned nodes/links unless explicitly included
    if not include_planned:
        nodes, links = _without_planned(nodes, links)

    # Filter out maintenance/excluded nodes and links
    if exclude_node_ids:
        exc_set = set(exclude_node_ids)
        nodes = [n for n in nodes if n.id not in exc_set]
        links = [l for l in links if l.source_node_id not in exc_set and l.target_node_id not in exc_set]
        flows = [f for f in flows if f.source_node_id not in exc_set and f.dest_node_id not in exc_set]
    if exclude_link_ids:
        exc_set = set(exclude_link_ids)
        links = [l for l in links if l.id not in exc_set]

    flow_paths, link_utilizations = compute_flow_paths(nodes, links, flows, db)

    spof_list = []
    failure_scenarios = []

    # The failure (SPOF) analysis re-solves flow paths for every node/link
    # failure and is ~98% of simulation time. Skip it unless requested, so plain
    # utilization runs (e.g. auto-simulate) stay fast; the Failures tab runs it
    # on demand.
    if not failure_analysis:
        return {
            "link_utilizations": link_utilizations,
            "flow_paths": flow_paths,
            "failure_scenarios": [],
            "single_points_of_failure": [],
        }

    # Build the full graph to find connected components under failure
    all_node_ids = {n.id for n in nodes}
    node_name_map = {n.id: n.name for n in nodes}
    # Customer nodes are by-design endpoints — their failure is the customer's
    # problem, not a network SPOF. Exclude them from the SPOF determination.
    customer_node_ids = {n.id for n in nodes if n.role == "customer"}

    def _find_isolated_nodes(remaining_node_ids: set, remaining_G: nx.Graph) -> list[int]:
        """Find the smaller partition(s) of nodes that are isolated from the main network.
        Returns node IDs in the smaller component(s). A node that was removed is NOT isolated
        (it failed, it didn't get isolated by someone else's failure)."""
        if len(remaining_node_ids) <= 1:
            return []
        components = list(nx.connected_components(remaining_G))
        if len(components) <= 1:
            return []  # Network is still connected
        # Find the largest component — everything else is "isolated"
        largest = max(components, key=len)
        isolated = []
        for comp in components:
            if comp is not largest:
                isolated.extend(comp)
        return sorted(isolated)

    # Nodes already disconnected in the BASELINE (nothing failed) — an orphan
    # node or a separate network island. They are isolated regardless of any
    # failure, so they must not be attributed to every failure scenario (which
    # would inflate isolation counts and flag every element as a SPOF). Each
    # scenario reports only nodes NEWLY isolated by that failure.
    baseline_isolated = set(_find_isolated_nodes(all_node_ids, _build_graph(nodes, links, db)))

    for node in nodes:
        fp, lu = compute_flow_paths(nodes, links, flows, db, exclude_node_ids={node.id})
        affected = []
        for orig, new in zip(flow_paths, fp):
            if orig["paths"] and not new["paths"]:
                affected.append(orig["flow_id"])

        # Check if removing this node splits the graph and isolates OTHER nodes
        remaining_ids = all_node_ids - {node.id}
        G_fail = _build_graph(nodes, links, db, exclude_node_ids={node.id})
        isolated = [nid for nid in _find_isolated_nodes(remaining_ids, G_fail) if nid not in baseline_isolated]
        # SPOF rule: a customer node failing is never a SPOF; nor is any failure
        # whose only victims are customer nodes (the customer disconnection is
        # by-design, not a network gap to fix).
        non_customer_isolated = [nid for nid in isolated if nid not in customer_node_ids]
        is_spof = (node.id not in customer_node_ids) and len(non_customer_isolated) > 0

        failure_scenarios.append({
            "failed_element_type": "node", "failed_element_id": node.id,
            "affected_flows": affected, "link_utilizations": lu, "spof": is_spof,
            "isolated_node_ids": isolated,
            "isolated_node_names": [node_name_map.get(nid, f"Node {nid}") for nid in isolated],
        })
        if is_spof:
            spof_list.append({
                "type": "node", "id": node.id, "name": node.name,
                "affected_flows": len(affected),
                "isolated_node_ids": isolated,
                "isolated_count": len(isolated),
            })

    for link in links:
        fp, lu = compute_flow_paths(nodes, links, flows, db, exclude_link_ids={link.id})
        affected = []
        for orig, new in zip(flow_paths, fp):
            if orig["paths"] and not new["paths"]:
                affected.append(orig["flow_id"])

        # Check if removing this link splits the graph and isolates nodes
        G_fail = _build_graph(nodes, links, db, exclude_link_ids={link.id})
        isolated = [nid for nid in _find_isolated_nodes(all_node_ids, G_fail) if nid not in baseline_isolated]
        non_customer_isolated = [nid for nid in isolated if nid not in customer_node_ids]
        is_spof = len(non_customer_isolated) > 0

        failure_scenarios.append({
            "failed_element_type": "link", "failed_element_id": link.id,
            "affected_flows": affected, "link_utilizations": lu, "spof": is_spof,
            "isolated_node_ids": isolated,
            "isolated_node_names": [node_name_map.get(nid, f"Node {nid}") for nid in isolated],
        })
        if is_spof:
            src_name = node_name_map.get(link.source_node_id, f"Node {link.source_node_id}")
            tgt_name = node_name_map.get(link.target_node_id, f"Node {link.target_node_id}")
            spof_list.append({
                "type": "link", "id": link.id,
                "name": f"{src_name} \u2194 {tgt_name}",
                "affected_flows": len(affected),
                "isolated_node_ids": isolated,
                "isolated_count": len(isolated),
            })

    return {
        "link_utilizations": link_utilizations,
        "flow_paths": flow_paths,
        "failure_scenarios": failure_scenarios,
        "single_points_of_failure": spof_list,
    }


def run_maintenance_window(
    topology_id: int, db: Session,
    failed_node_ids: list[int], failed_link_ids: list[int],
    include_planned: bool = False,
) -> dict:
    """Simulate taking specific nodes/links out of service.
    Compares healthy vs. failed state to identify affected flows and capacity issues.

    include_planned must match the setting used for the canvas simulation —
    the baseline here has to be the SAME network the user is looking at, or
    the rerouted/no-change verdicts are judged against paths they never saw."""
    from app.services.topology_chain import get_merged_topology_data
    nodes, links, flows, _ = get_merged_topology_data(db, topology_id)
    if not include_planned:
        nodes, links = _without_planned(nodes, links)

    node_name_map = {n.id: n.name for n in nodes}
    link_name_map = {l.id: f"{node_name_map.get(l.source_node_id, '?')} - {node_name_map.get(l.target_node_id, '?')}" for l in links}
    all_node_ids = {n.id for n in nodes}

    # Baseline: healthy network
    baseline_fp, baseline_lu = compute_flow_paths(nodes, links, flows, db)

    # Failed state
    exclude_nodes = set(failed_node_ids)
    exclude_links = set(failed_link_ids)
    failed_fp, failed_lu = compute_flow_paths(nodes, links, flows, db,
                                               exclude_node_ids=exclude_nodes,
                                               exclude_link_ids=exclude_links)

    # Compare flow states
    affected_flows = []
    for orig, new in zip(baseline_fp, failed_fp):
        had_path = len(orig["paths"]) > 0
        has_path = len(new["paths"]) > 0
        orig_links = set()
        for lids in orig.get("link_ids_per_path", []):
            orig_links.update(lids)
        new_links = set()
        for lids in new.get("link_ids_per_path", []):
            new_links.update(lids)

        if had_path and not has_path:
            affected_flows.append({
                "flow_id": orig["flow_id"], "flow_name": orig["flow_name"],
                "status": "broken",
            })
        elif had_path and has_path and orig_links != new_links:
            affected_flows.append({
                "flow_id": orig["flow_id"], "flow_name": orig["flow_name"],
                "status": "rerouted",
            })

    # Check for capacity issues in failed state
    # Only links NEWLY overloaded by this window — links already >100% in the
    # healthy baseline are a pre-existing condition, not a consequence of the
    # maintenance event, so they'd be noise here. baseline_utilization_pct is
    # included so the UI can show the jump.
    baseline_pct = {lu["link_id"]: lu["utilization_pct"] for lu in baseline_lu}
    capacity_exceeded = False
    overloaded_links = []
    for lu in failed_lu:
        if lu["utilization_pct"] > 100 and baseline_pct.get(lu["link_id"], 0) <= 100:
            capacity_exceeded = True
            overloaded_links.append({
                "link_id": lu["link_id"],
                "name": link_name_map.get(lu["link_id"], f"Link {lu['link_id']}"),
                "utilization_pct": lu["utilization_pct"],
                "baseline_utilization_pct": baseline_pct.get(lu["link_id"], 0),
                "used_gbps": lu["used_gbps"],
                "bandwidth_gbps": lu["bandwidth_gbps"],
            })

    # Check for isolated nodes — only those NEWLY isolated by this window, not
    # nodes already disconnected in the baseline (orphans / separate islands).
    G_fail = _build_graph(nodes, links, db, exclude_node_ids=exclude_nodes, exclude_link_ids=exclude_links)
    remaining_ids = all_node_ids - exclude_nodes
    baseline_isolated = _isolated_in_graph(_build_graph(nodes, links, db))
    isolated = []
    if len(remaining_ids) > 1:
        components = list(nx.connected_components(G_fail))
        if len(components) > 1:
            largest = max(components, key=len)
            for comp in components:
                if comp is not largest:
                    isolated.extend(nid for nid in comp if nid not in baseline_isolated)

    return {
        "failed_node_ids": failed_node_ids,
        "failed_link_ids": failed_link_ids,
        "failed_node_names": [node_name_map.get(nid, f"Node {nid}") for nid in failed_node_ids],
        "failed_link_names": [link_name_map.get(lid, f"Link {lid}") for lid in failed_link_ids],
        "affected_flows": affected_flows,
        "total_flows": len(flows),
        "broken_count": sum(1 for f in affected_flows if f["status"] == "broken"),
        "rerouted_count": sum(1 for f in affected_flows if f["status"] == "rerouted"),
        "unaffected_count": len(flows) - len(affected_flows),
        "capacity_exceeded": capacity_exceeded,
        "overloaded_links": overloaded_links,
        "isolated_node_ids": sorted(isolated),
        "isolated_node_names": [node_name_map.get(nid, f"Node {nid}") for nid in sorted(isolated)],
        "baseline_utilizations": baseline_lu,
        "failed_utilizations": failed_lu,
        "baseline_flow_paths": baseline_fp,
        "failed_flow_paths": failed_fp,
    }


def run_srlg_analysis(topology_id: int, db: Session, include_planned: bool = False) -> list[dict]:
    """Test each SRLG group as a correlated failure event. include_planned
    must match the canvas simulation setting (see run_maintenance_window)."""
    from app.services.topology_chain import get_merged_topology_data, get_topology_chain
    nodes, links, flows, _ = get_merged_topology_data(db, topology_id)
    if not include_planned:
        nodes, links = _without_planned(nodes, links)
    chain = get_topology_chain(db, topology_id)
    srlgs = db.query(SrlgGroup).filter(SrlgGroup.topology_id.in_(chain)).all()

    node_name_map = {n.id: n.name for n in nodes}
    all_node_ids = {n.id for n in nodes}
    # Customer nodes don't count as SPOF victims (see run_simulation rationale).
    customer_node_ids = {n.id for n in nodes if n.role == "customer"}

    # Baseline
    baseline_fp, baseline_lu = compute_flow_paths(nodes, links, flows, db)
    # Nodes already disconnected with nothing failed — excluded from each SRLG's
    # isolation count so they aren't blamed on every group.
    baseline_isolated = _isolated_in_graph(_build_graph(nodes, links, db))

    results = []
    for srlg in srlgs:
        exclude_nodes = set()
        exclude_links = set()
        for m in srlg.members:
            if m.member_type == "node":
                exclude_nodes.add(m.member_id)
            else:
                exclude_links.add(m.member_id)

        fp, lu = compute_flow_paths(nodes, links, flows, db,
                                     exclude_node_ids=exclude_nodes,
                                     exclude_link_ids=exclude_links)

        affected = []
        for orig, new in zip(baseline_fp, fp):
            if orig["paths"] and not new["paths"]:
                affected.append(orig["flow_id"])

        # Check isolated nodes
        G_fail = _build_graph(nodes, links, db, exclude_node_ids=exclude_nodes, exclude_link_ids=exclude_links)
        remaining_ids = all_node_ids - exclude_nodes
        isolated = []
        if len(remaining_ids) > 1:
            components = list(nx.connected_components(G_fail))
            if len(components) > 1:
                largest = max(components, key=len)
                for comp in components:
                    if comp is not largest:
                        isolated.extend(nid for nid in comp if nid not in baseline_isolated)

        max_util = max((l["utilization_pct"] for l in lu), default=0)

        non_customer_isolated = [nid for nid in isolated if nid not in customer_node_ids]
        results.append({
            "srlg_id": srlg.id,
            "srlg_name": srlg.name,
            "srlg_type": srlg.srlg_type,
            "member_count": len(srlg.members),
            "affected_flow_ids": affected,
            "affected_flow_count": len(affected),
            "max_utilization_pct": round(max_util, 2),
            "capacity_exceeded": max_util > 100,
            "isolated_node_ids": sorted(isolated),
            "isolated_node_names": [node_name_map.get(nid, f"Node {nid}") for nid in sorted(isolated)],
            "is_spof": len(non_customer_isolated) > 0 or len(affected) > 0,
            "link_utilizations": lu,
        })

    return results


def _resolve_location_to_nodes(location_str: str, nodes: list[NodeInstance]) -> list[NodeInstance]:
    try:
        node_id = int(location_str)
        matches = [n for n in nodes if n.id == node_id]
        if matches:
            return matches
    except ValueError:
        pass
    matches = [n for n in nodes if n.name.lower() == location_str.lower()]
    if matches:
        return matches
    matches = [n for n in nodes if (n.location or "").lower() == location_str.lower()]
    if matches:
        return matches
    matches = [n for n in nodes if location_str.lower() in n.name.lower() or location_str.lower() in (n.location or "").lower()]
    return matches


def run_monte_carlo(
    topology_id: int, db: Session,
    a_location: str, z_location: str,
    a_node_ids: list[int] | None = None,
    z_node_ids: list[int] | None = None,
    iterations: int = 10000, connection_type: str = "both",
    exclude_node_ids: list[int] | None = None,
    exclude_link_ids: list[int] | None = None,
    **kwargs,
) -> dict:
    """Monte Carlo availability simulation using temporary user nodes.

    Creates temporary 'user' nodes at A and Z ends, connected to the real network
    devices with perfect (never-fail) links. Availability is measured between these
    user nodes, not the network devices directly.

    - Single-homed: user node connects to exactly 1 device at each location
    - Multi-homed: user node connects to all selected devices at each location

    The user nodes and their links have MTTR=0 (perfect availability), so only
    real network infrastructure failures affect the result.

    Nodes/links in maintenance (exclude_*_ids) are removed before simulation.
    If include_planned is False (default), planned nodes/links are excluded.
    """
    from app.services.topology_chain import get_merged_topology_data
    nodes, links, _flows, _ = get_merged_topology_data(db, topology_id)

    # Exclude planned nodes/links unless explicitly included
    include_planned = kwargs.get("include_planned", False)
    if not include_planned:
        nodes, links = _without_planned(nodes, links)

    # Exclude maintenance nodes/links
    if exclude_node_ids:
        exc_set = set(exclude_node_ids)
        nodes = [n for n in nodes if n.id not in exc_set]
        links = [l for l in links if l.source_node_id not in exc_set and l.target_node_id not in exc_set]
    if exclude_link_ids:
        exc_set = set(exclude_link_ids)
        links = [l for l in links if l.id not in exc_set]

    default_node_mtbf = _get_setting(db, "default_node_mtbf_hours", 50000)
    default_node_mttr = _get_setting(db, "default_node_mttr_hours", 4)
    default_link_mtbf = _get_setting(db, "default_link_mtbf_hours", 80000)
    default_link_mttr = _get_setting(db, "default_link_mttr_hours", 2)
    hours_per_year = 8760

    # Resolve A and Z locations to node lists
    a_nodes = _resolve_location_to_nodes(a_location, nodes)
    z_nodes = _resolve_location_to_nodes(z_location, nodes)

    # If specific node IDs were provided, filter to those
    if a_node_ids:
        a_nodes = [n for n in a_nodes if n.id in a_node_ids]
    if z_node_ids:
        z_nodes = [n for n in z_nodes if n.id in z_node_ids]

    _err = lambda msg: {
        "error": msg, "iterations": 0, "a_location": a_location, "z_location": z_location,
        "connection_type": connection_type, "scenarios": [],
        "a_devices": [], "z_devices": [],
    }

    if not a_nodes:
        return _err(f"No nodes found matching A-location '{a_location}'")
    if not z_nodes:
        return _err(f"No nodes found matching Z-location '{z_location}'")

    a_ids = [n.id for n in a_nodes]
    z_ids = [n.id for n in z_nodes]

    if set(a_ids) & set(z_ids):
        return _err("A and Z locations share common nodes — cannot measure availability to self")

    # Gather MTBF/MTTR for each component.
    # Use explicit None checks so that an intentionally-zero MTTR (e.g. on a
    # customer node, meaning "doesn't contribute to outage time") is honored
    # instead of being silently replaced by the default via `or`-fallback.
    customer_ids = {n.id for n in nodes if n.role == "customer"}

    node_mtbf = {}
    node_mttr = {}
    for node in nodes:
        node_mtbf[node.id] = node.mtbf_hours if node.mtbf_hours is not None else default_node_mtbf
        if node.id in customer_ids:
            # Customer nodes are by-design endpoints; their reliability is the
            # customer's domain, not the network's. Override to zero so that
            # availability measurements reflect only network performance.
            node_mttr[node.id] = 0
        else:
            node_mttr[node.id] = node.mttr_hours if node.mttr_hours is not None else default_node_mttr

    link_mtbf_map = {}
    link_mttr_map = {}
    for link in links:
        link_mtbf_map[link.id] = link.mtbf_hours if link.mtbf_hours is not None else default_link_mtbf
        is_access_tail = (link.source_node_id in customer_ids or link.target_node_id in customer_ids)
        if is_access_tail:
            # Access tail between a customer and the network — same rationale
            # as the customer node above: not part of network reliability for
            # this measurement.
            link_mttr_map[link.id] = 0
        else:
            link_mttr_map[link.id] = link.mttr_hours if link.mttr_hours is not None else default_link_mttr

    # Temp user node IDs
    max_real_id = max(n.id for n in nodes) if nodes else 0
    TEMP_A_USER = max_real_id + 1
    TEMP_Z_USER = max_real_id + 2

    a_conns = a_ids
    z_conns = z_ids

    rng = np.random.default_rng()
    node_ids = [n.id for n in nodes]
    link_ids = [l.id for l in links]

    # Pre-compute arrays for vectorized event generation
    all_comp_ids = node_ids + link_ids
    all_mtbf = np.array([node_mtbf[nid] for nid in node_ids] + [link_mtbf_map[lid] for lid in link_ids])
    all_mttr = np.array([node_mttr[nid] for nid in node_ids] + [link_mttr_map[lid] for lid in link_ids])
    n_components = len(all_comp_ids)
    n_nodes = len(node_ids)

    # Build adjacency info once for fast graph reconstruction
    # For each node, store its links; for each link, store its endpoints
    link_endpoints = {}  # link_id -> (src, tgt)
    for link in links:
        link_endpoints[link.id] = (link.source_node_id, link.target_node_id)

    # Adjacency: for connectivity checking without rebuilding nx.Graph each time
    # We'll use a simple BFS/DFS on adjacency sets, which is much faster
    def _check_connected(failed_node_set, failed_link_set):
        """Fast BFS from TEMP_A_USER to TEMP_Z_USER without building nx.Graph."""
        # Build adjacency on the fly (only surviving components)
        adj = {nid: set() for nid in node_ids if nid not in failed_node_set}
        adj[TEMP_A_USER] = set()
        adj[TEMP_Z_USER] = set()

        for lid in link_ids:
            if lid in failed_link_set:
                continue
            src, tgt = link_endpoints[lid]
            if src in failed_node_set or tgt in failed_node_set:
                continue
            if src in adj and tgt in adj:
                adj[src].add(tgt)
                adj[tgt].add(src)

        # Connect temp user nodes
        for a_id in a_conns:
            if a_id not in failed_node_set and a_id in adj:
                adj[TEMP_A_USER].add(a_id)
                adj[a_id].add(TEMP_A_USER)
        for z_id in z_conns:
            if z_id not in failed_node_set and z_id in adj:
                adj[TEMP_Z_USER].add(z_id)
                adj[z_id].add(TEMP_Z_USER)

        if not adj[TEMP_A_USER] or not adj[TEMP_Z_USER]:
            return False

        # BFS
        visited = {TEMP_A_USER}
        queue = [TEMP_A_USER]
        qi = 0
        while qi < len(queue):
            node = queue[qi]
            qi += 1
            for neighbor in adj.get(node, ()):
                if neighbor == TEMP_Z_USER:
                    return True
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return False

    # Time-based simulation: each iteration simulates 1 year (8760 hours)
    # Event-driven: generate failure/repair events, check connectivity at each state change
    downtime_hours_per_iter = np.zeros(iterations)

    for it in range(iterations):
        failed_set = set()  # currently failed component indices

        # Generate all failure/repair events for the year
        # Events: (time, component_index, is_fail)  — is_fail=True for failure, False for repair
        events = []
        for ci in range(n_components):
            # Components with MTBF <= 0 are pathological — treat as never-fail.
            # (Customer nodes typically have MTTR=0 with a real MTBF; that's fine.)
            if all_mtbf[ci] <= 0:
                continue
            t = rng.exponential(all_mtbf[ci])
            while t < hours_per_year:
                repair_duration = rng.exponential(all_mttr[ci])
                repair_end = min(t + repair_duration, hours_per_year)
                events.append((t, ci, True))
                events.append((repair_end, ci, False))
                t = repair_end + rng.exponential(all_mtbf[ci])

        if not events:
            continue  # No failures — 100% uptime

        events.sort(key=lambda e: e[0])

        prev_time = 0.0
        was_connected = True
        year_downtime = 0.0

        for evt_time, ci, is_fail in events:
            if not was_connected:
                year_downtime += evt_time - prev_time
            prev_time = evt_time

            if is_fail:
                failed_set.add(ci)
            else:
                failed_set.discard(ci)

            # Convert failed component indices to node/link ID sets
            failed_nodes = set()
            failed_links = set()
            for fci in failed_set:
                if fci < n_nodes:
                    failed_nodes.add(node_ids[fci])
                else:
                    failed_links.add(link_ids[fci - n_nodes])

            was_connected = _check_connected(failed_nodes, failed_links)

        if not was_connected:
            year_downtime += hours_per_year - prev_time

        downtime_hours_per_iter[it] = year_downtime

    # Compute results
    avail_per_iter = 1.0 - (downtime_hours_per_iter / hours_per_year)
    mean_avail = float(np.mean(avail_per_iter))
    std_avail = float(np.std(avail_per_iter, ddof=1)) if iterations > 1 else 0.0
    mean_downtime_min = float(np.mean(downtime_hours_per_iter)) * 60
    std_downtime_min = float(np.std(downtime_hours_per_iter, ddof=1)) * 60 if iterations > 1 else 0.0
    outage_iters = int(np.sum(downtime_hours_per_iter > 0))

    # Distribution stats + histogram of per-iteration downtime (minutes/year).
    # Downtime is the more intuitive axis than availability here — most
    # iterations cluster at zero and the tail is what matters for SLA risk.
    downtime_min_per_iter = downtime_hours_per_iter * 60.0
    if iterations > 0:
        pct = np.percentile(downtime_min_per_iter, [50, 90, 95, 99])
        downtime_percentiles_min = {
            "p50": round(float(pct[0]), 2),
            "p90": round(float(pct[1]), 2),
            "p95": round(float(pct[2]), 2),
            "p99": round(float(pct[3]), 2),
            "max": round(float(np.max(downtime_min_per_iter)), 2),
        }
        # Availability percentiles mirror the downtime ones (inverted order:
        # the 50th-pct downtime corresponds to the 50th-pct availability).
        apct = np.percentile(avail_per_iter, [1, 5, 10, 50])
        avail_percentiles = {
            "p1": round(float(apct[0]), 10),
            "p5": round(float(apct[1]), 10),
            "p10": round(float(apct[2]), 10),
            "p50": round(float(apct[3]), 10),
            "min": round(float(np.min(avail_per_iter)), 10),
        }
        # Histogram: 30 bins. If every iteration was a perfect year (no
        # downtime anywhere), np.histogram over an all-zero array still
        # produces a valid single-loaded bin — handle the degenerate range
        # so bin_end != bin_start.
        max_dt = float(np.max(downtime_min_per_iter))
        hist_range = (0.0, max_dt if max_dt > 0 else 1.0)
        counts, edges = np.histogram(downtime_min_per_iter, bins=30, range=hist_range)
        histogram = [
            {"bin_start": round(float(edges[i]), 2),
             "bin_end": round(float(edges[i + 1]), 2),
             "count": int(counts[i])}
            for i in range(len(counts))
        ]
        zero_downtime_iters = int(np.sum(downtime_min_per_iter == 0))
    else:
        downtime_percentiles_min = {}
        avail_percentiles = {}
        histogram = []
        zero_downtime_iters = 0

    # Count disjoint paths in healthy network
    full_G = _build_graph(nodes, links, None)
    full_G.add_node(TEMP_A_USER)
    full_G.add_node(TEMP_Z_USER)
    for a_id in a_conns:
        full_G.add_edge(TEMP_A_USER, a_id, weight=0)
    for z_id in z_conns:
        full_G.add_edge(TEMP_Z_USER, z_id, weight=0)
    try:
        disjoint_paths = nx.node_connectivity(full_G, TEMP_A_USER, TEMP_Z_USER)
    except (nx.NetworkXError, nx.NodeNotFound):
        disjoint_paths = 0

    return {
        "iterations": iterations, "a_location": a_location, "z_location": z_location,
        "a_devices": [{"id": n.id, "name": n.name} for n in a_nodes],
        "z_devices": [{"id": n.id, "name": n.name} for n in z_nodes],
        "availability": round(mean_avail, 10),
        "downtime_min_yr": round(mean_downtime_min, 2),
        "std_dev": round(std_avail, 10),
        "downtime_std_min_yr": round(std_downtime_min, 2),
        "disjoint_paths": disjoint_paths,
        "outage_iterations": outage_iters,
        # Distribution detail — consumed by the per-flow availability report.
        # The Monte Carlo panel ignores these extra keys harmlessly.
        "zero_downtime_iterations": zero_downtime_iters,
        "downtime_percentiles_min": downtime_percentiles_min,
        "availability_percentiles": avail_percentiles,
        "downtime_histogram_min": histogram,
    }

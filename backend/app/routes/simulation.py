from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.flow import Flow
from app.schemas.schemas import MonteCarloRequest, MaintenanceWindowRequest
from app.services.simulation import run_simulation, run_monte_carlo, compute_flow_paths, run_maintenance_window, run_srlg_analysis
from app.models.srlg import SrlgGroup, SrlgMember

router = APIRouter(prefix="/api/topologies/{topology_id}/simulate", tags=["simulation"])


class SimulateRequest(BaseModel):
    exclude_node_ids: list[int] = []
    exclude_link_ids: list[int] = []
    include_planned: bool = False
    failure_analysis: bool = True  # run the (expensive) SPOF/failure scenarios


@router.post("/")
def simulate(topology_id: int, req: SimulateRequest = SimulateRequest(), db: Session = Depends(get_db)):
    return run_simulation(topology_id, db,
                          exclude_node_ids=req.exclude_node_ids or None,
                          exclude_link_ids=req.exclude_link_ids or None,
                          include_planned=req.include_planned,
                          failure_analysis=req.failure_analysis)


@router.post("/export-csv")
def export_simulation_csv(topology_id: int, req: SimulateRequest = SimulateRequest(), db: Session = Depends(get_db)):
    """Run simulation and return results as downloadable CSV."""
    result = run_simulation(topology_id, db,
                            exclude_node_ids=req.exclude_node_ids or None,
                            exclude_link_ids=req.exclude_link_ids or None,
                            include_planned=req.include_planned)

    from app.services.topology_chain import get_merged_topology_data
    nodes, links_db, _flows, _ = get_merged_topology_data(db, topology_id)
    node_map = {n.id: n.name for n in nodes}
    link_obj_map = {l.id: l for l in links_db}

    # Build human-readable link names: "NodeA - NodeB" or "NodeA - NodeB (2)" for parallel links
    pair_count: dict[str, int] = {}
    pair_seen: dict[str, int] = {}
    for l in links_db:
        pair_key = " - ".join(sorted([node_map.get(l.source_node_id, "?"), node_map.get(l.target_node_id, "?")]))
        pair_count[pair_key] = pair_count.get(pair_key, 0) + 1

    link_name_map: dict[int, str] = {}
    for l in links_db:
        pair_key = " - ".join(sorted([node_map.get(l.source_node_id, "?"), node_map.get(l.target_node_id, "?")]))
        if pair_count[pair_key] > 1:
            idx = pair_seen.get(pair_key, 0) + 1
            pair_seen[pair_key] = idx
            link_name_map[l.id] = f"{pair_key} ({idx})"
        else:
            link_name_map[l.id] = pair_key

    lines = []

    # Link Utilization
    lines.append("=== LINK UTILIZATION ===")
    lines.append("Link Name,Source Node,Target Node,Source Interface,Target Interface,Bandwidth (Gbps),Used (Gbps),Utilization (%),Cost/GB,Circuit Cost,Status")
    for lu in sorted(result.get("link_utilizations", []), key=lambda x: -x["utilization_pct"]):
        link = link_obj_map.get(lu["link_id"])
        name = link_name_map.get(lu["link_id"], "?")
        src = node_map.get(lu["source_node_id"], "?")
        tgt = node_map.get(lu["target_node_id"], "?")
        src_if = link.source_interface if link else ""
        tgt_if = link.target_interface if link else ""
        cc = link.circuit_cost if link else 0
        status = "PLANNED" if (link and link.is_planned) else "ACTIVE"
        lines.append(f'"{name}","{src}","{tgt}","{src_if}","{tgt_if}",{lu["bandwidth_gbps"]},{lu["used_gbps"]},{lu["utilization_pct"]},{lu.get("cost_per_gb", 0)},{cc or 0},{status}')

    lines.append("")

    # Flow Paths
    lines.append("=== FLOW PATHS ===")
    lines.append("Flow Name,Path,Bandwidth (Gbps),Latency (ms)")
    for fp in result.get("flow_paths", []):
        for i, path in enumerate(fp.get("paths", [])):
            path_str = " -> ".join(node_map.get(nid, str(nid)) for nid in path)
            bw = fp["bandwidth_per_path_gbps"][i] if i < len(fp["bandwidth_per_path_gbps"]) else 0
            lat = fp.get("latency_per_path_ms", [0])[i] if i < len(fp.get("latency_per_path_ms", [])) else ""
            lines.append(f'"{fp["flow_name"]}","{path_str}",{bw},{lat}')

    lines.append("")

    # Single Points of Failure — ranked by impact (flows broken, then nodes isolated)
    lines.append("=== SINGLE POINTS OF FAILURE ===")
    lines.append("Rank,Type,Name,Affected Flows,Nodes Isolated,Isolated Nodes")
    spofs_sorted = sorted(
        result.get("single_points_of_failure", []),
        key=lambda s: (-s.get("affected_flows", 0), -s.get("isolated_count", 0)),
    )
    for i, spof in enumerate(spofs_sorted, 1):
        iso_ids = spof.get("isolated_node_ids", [])
        iso_names = "; ".join(node_map.get(nid, str(nid)) for nid in iso_ids)
        lines.append(
            f'{i},"{spof["type"]}","{spof["name"]}",{spof["affected_flows"]},'
            f'{spof.get("isolated_count", len(iso_ids))},"{iso_names}"'
        )

    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="simulation_report.csv"'},
    )


@router.post("/preview/{routing_behavior}")
def preview_routing(topology_id: int, routing_behavior: str, db: Session = Depends(get_db)):
    """Preview what all flow paths would look like under a given routing behavior,
    without modifying any flow data. Returns flow_paths and link_utilizations."""
    if routing_behavior not in ("shortest_path", "lowest_latency", "optimized"):
        return {"error": f"Invalid routing behavior: {routing_behavior}"}

    from app.services.topology_chain import get_merged_topology_data
    nodes, links, flows, _ = get_merged_topology_data(db, topology_id)

    # Create temporary flow copies with overridden routing behavior
    class TempFlow:
        pass

    temp_flows = []
    for f in flows:
        tf = TempFlow()
        tf.id = f.id
        tf.name = f.name
        tf.source_node_id = f.source_node_id
        tf.dest_node_id = f.dest_node_id
        tf.bandwidth_gbps = f.bandwidth_gbps
        tf.routing_behavior = routing_behavior
        tf.explicit_path = []
        tf.ecmp_enabled = f.ecmp_enabled
        temp_flows.append(tf)

    flow_paths, link_utilizations = compute_flow_paths(nodes, links, temp_flows, db)

    # Build readable node names
    node_map = {n.id: n.name for n in nodes}

    return {
        "routing_behavior": routing_behavior,
        "flow_paths": flow_paths,
        "link_utilizations": link_utilizations,
    }


class ShortestPathsRequest(BaseModel):
    source_node_id: int
    target_node_ids: list[int]
    weight: str = "cost"  # "cost" or "latency_ms"
    include_planned: bool = False  # match the rest of the simulation API


@router.post("/shortest-paths")
def compute_shortest_paths(topology_id: int, req: ShortestPathsRequest, db: Session = Depends(get_db)):
    """Compute the shortest path from one source node to each of N targets.
    Used by the canvas to visualise things like route-reflector peering paths
    without going through the full simulation pipeline.

    Planned nodes/links are excluded by default — set include_planned=true to
    consider hypothetical infrastructure too. Returns a single FlowPath-shaped
    object so the frontend can drop it straight into highlightedFlowPaths and
    re-use the existing path renderer.
    """
    import networkx as nx
    from app.services.simulation import _build_multigraph, _find_shortest_path
    from app.services.topology_chain import get_merged_topology_data

    nodes, links, _flows, _customers = get_merged_topology_data(db, topology_id)
    # Drop planned topology unless the caller explicitly asks for it. Same
    # filter rule used by run_simulation: a link to/from a planned node is
    # itself dropped even if the link wasn't flagged planned.
    if not req.include_planned:
        planned_node_ids = {n.id for n in nodes if n.is_planned}
        nodes = [n for n in nodes if not n.is_planned]
        links = [
            l for l in links
            if not l.is_planned
            and l.source_node_id not in planned_node_ids
            and l.target_node_id not in planned_node_ids
        ]
    G = _build_multigraph(nodes, links, db, weight=req.weight if req.weight in ("cost", "latency_ms") else "cost")

    paths: list[list[int]] = []
    link_ids_per_path: list[list[int]] = []
    unreachable: list[int] = []
    for target in req.target_node_ids:
        if req.source_node_id == target:
            continue
        try:
            node_path, lids = _find_shortest_path(G, req.source_node_id, target)
            paths.append(node_path)
            link_ids_per_path.append(lids)
        except (nx.NodeNotFound, nx.NetworkXNoPath, Exception):
            unreachable.append(target)

    return {
        "flow_id": -1,
        "flow_name": "shortest-paths",
        "paths": paths,
        "link_ids_per_path": link_ids_per_path,
        "bandwidth_per_path_gbps": [0 for _ in paths],
        "unreachable_target_ids": unreachable,
    }


class PathEnd(BaseModel):
    node_id: int | None = None
    location: str | None = None


class PathAnalysisRequest(BaseModel):
    a: PathEnd
    z: PathEnd
    mode: str = "shortest"       # "shortest" | "disjoint"
    metric: str = "cost"         # disjoint only: "cost" | "latency"
    disjoint: str = "link"       # disjoint only: "link" | "node" | "srlg"
    max_paths: int = 2           # disjoint only: 2–4
    include_planned: bool = False


@router.post("/path-analysis")
def path_analysis(topology_id: int, req: PathAnalysisRequest, db: Session = Depends(get_db)):
    """A-loc → Z-loc paths without creating a flow: lowest-cost and
    lowest-latency paths, or a set of link / node / SRLG-disjoint paths.
    See services/path_analysis.py."""
    from fastapi import HTTPException
    from app.services.path_analysis import analyze_paths, PathAnalysisError
    try:
        return analyze_paths(
            db, topology_id, req.a.model_dump(), req.z.model_dump(), mode=req.mode,
            metric=req.metric, disjoint=req.disjoint, max_paths=req.max_paths,
            include_planned=req.include_planned,
        )
    except PathAnalysisError as e:
        raise HTTPException(400, str(e))


@router.get("/resolve-location")
def resolve_location(topology_id: int, location: str = Query(...),
                     include_planned: bool = Query(False), db: Session = Depends(get_db)):
    """Resolve a location string to matching nodes for device selection."""
    from app.services.simulation import _resolve_location_to_nodes
    from app.services.topology_chain import get_merged_topology_data
    nodes, _l, _f, _ = get_merged_topology_data(db, topology_id)
    if not include_planned:
        nodes = [n for n in nodes if not n.is_planned]
    matches = _resolve_location_to_nodes(location, nodes)
    return [{"id": n.id, "name": n.name, "location": n.location or ""} for n in matches]


@router.post("/maintenance-window")
def maintenance_window(topology_id: int, req: MaintenanceWindowRequest, db: Session = Depends(get_db)):
    """Simulate taking specific nodes/links/SRLGs out of service."""
    failed_node_ids = list(req.failed_node_ids)
    failed_link_ids = list(req.failed_link_ids)

    # Resolve SRLG IDs to their member nodes/links
    if req.srlg_ids:
        members = db.query(SrlgMember).filter(
            SrlgMember.srlg_id.in_(req.srlg_ids)
        ).all()
        for m in members:
            if m.member_type == "node" and m.member_id not in failed_node_ids:
                failed_node_ids.append(m.member_id)
            elif m.member_type == "link" and m.member_id not in failed_link_ids:
                failed_link_ids.append(m.member_id)

    return run_maintenance_window(topology_id, db, failed_node_ids, failed_link_ids,
                                  include_planned=req.include_planned)


@router.post("/srlg-analysis")
def srlg_analysis(topology_id: int, include_planned: bool = Query(False), db: Session = Depends(get_db)):
    """Analyze impact of each SRLG group failing as a correlated event."""
    return run_srlg_analysis(topology_id, db, include_planned=include_planned)


@router.post("/monte-carlo")
def monte_carlo(topology_id: int, req: MonteCarloRequest, db: Session = Depends(get_db)):
    return run_monte_carlo(
        topology_id, db,
        a_location=req.a_location,
        z_location=req.z_location,
        a_node_ids=req.a_node_ids,
        z_node_ids=req.z_node_ids,
        iterations=req.iterations,
        connection_type=req.connection_type,
        exclude_node_ids=req.exclude_node_ids,
        exclude_link_ids=req.exclude_link_ids,
        include_planned=req.include_planned,
    )

import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.flow import Flow
from app.schemas.schemas import FlowCreate, FlowUpdate, FlowOut, BulkFlowImport


class FlowAvailabilityRequest(BaseModel):
    # Active maintenance-window exclusions to apply to the analysis. Optional:
    # an empty/absent body analyzes the full as-built network. These mirror
    # what the Monte Carlo panel passes so the two paths agree when a
    # maintenance window is active.
    exclude_node_ids: Optional[list[int]] = None
    exclude_link_ids: Optional[list[int]] = None
    include_planned: bool = False

router = APIRouter(prefix="/api/topologies/{topology_id}/flows", tags=["flows"])


def _flow_to_dict(f: Flow, is_inherited: bool = False) -> dict:
    return {
        "id": f.id, "topology_id": f.topology_id, "name": f.name,
        "description": f.description or "",
        "source_node_id": f.source_node_id, "dest_node_id": f.dest_node_id,
        "source_interface": f.source_interface or "",
        "dest_interface": f.dest_interface or "",
        "bandwidth_gbps": f.bandwidth_gbps, "routing_behavior": f.routing_behavior,
        "explicit_path": f.explicit_path, "explicit_link_ids": f.explicit_link_ids,
        "ecmp_enabled": bool(f.ecmp_enabled),
        "is_inherited": is_inherited,
        "availability_report": f.availability_report,
    }


@router.get("/", response_model=list[FlowOut])
def list_flows(topology_id: int, db: Session = Depends(get_db)):
    """List flows for a topology, including those inherited from parent topologies.

    Two kinds of shadowing apply:
      - Node shadowing: a flow's source/dest node IDs are remapped if the node
        was shadowed by a parent's same-named node.
      - Flow shadowing: a child-local flow whose name was later claimed by a
        parent addition is hidden in favor of the parent's version.
    """
    from app.services.topology_chain import get_topology_chain, merge_by_name
    from app.models.node_instance import NodeInstance
    chain = get_topology_chain(db, topology_id)

    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    _, node_remap = merge_by_name(all_nodes, chain)

    all_flows = db.query(Flow).filter(Flow.topology_id.in_(chain)).all()
    visible_flows, _ = merge_by_name(all_flows, chain)

    out = []
    for f in visible_flows:
        d = _flow_to_dict(f, is_inherited=(f.topology_id != topology_id))
        if d["source_node_id"] in node_remap:
            d["source_node_id"] = node_remap[d["source_node_id"]]
        if d["dest_node_id"] in node_remap:
            d["dest_node_id"] = node_remap[d["dest_node_id"]]
        out.append(d)
    return out


def _parent_chain_ids(db: Session, topology_id: int) -> list[int]:
    """Return ancestor topology IDs (root → parent), excluding topology_id itself."""
    from app.services.topology_chain import get_topology_chain
    return [tid for tid in get_topology_chain(db, topology_id) if tid != topology_id]


# Flow names are unique per topology and can't reuse a parent topology's flow
# name. Names compare trimmed and case-insensitively (the import wizard applies
# the same rule client-side) and are stored trimmed.
def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _check_flow_name(db: Session, topology_id: int, name: str, exclude_id: int | None = None) -> None:
    """Raise 422 for a blank name, 409 if it's taken here or inherited."""
    key = _norm(name)
    if not key:
        raise HTTPException(422, "Flow name can't be blank")
    parent_ids = _parent_chain_ids(db, topology_id)
    if parent_ids and any(_norm(n) == key for (n,) in
                          db.query(Flow.name).filter(Flow.topology_id.in_(parent_ids))):
        raise HTTPException(409, f"A flow named '{name.strip()}' is inherited from a parent topology — pick a different name")
    if any(_norm(n) == key and i != exclude_id for i, n in
           db.query(Flow.id, Flow.name).filter(Flow.topology_id == topology_id)):
        raise HTTPException(409, f"A flow named '{name.strip()}' already exists in this topology — pick a different name")


@router.post("/", response_model=FlowOut)
def create_flow(topology_id: int, data: FlowCreate, db: Session = Depends(get_db)):
    _check_flow_name(db, topology_id, data.name)

    flow = Flow(
        topology_id=topology_id, name=data.name.strip(), description=data.description,
        source_node_id=data.source_node_id, dest_node_id=data.dest_node_id,
        source_interface=data.source_interface, dest_interface=data.dest_interface,
        bandwidth_gbps=data.bandwidth_gbps, routing_behavior=data.routing_behavior,
        explicit_path_json=json.dumps(data.explicit_path),
        explicit_link_ids_json=json.dumps(data.explicit_link_ids),
        ecmp_enabled=1 if data.ecmp_enabled else 0,
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return _flow_to_dict(flow)


@router.post("/bulk", response_model=list[FlowOut])
def bulk_create_flows(topology_id: int, data: BulkFlowImport, db: Session = Depends(get_db)):
    """Import flows. A row whose name already exists in this topology is
    skipped — or, with update_existing, updates that flow. A name repeated
    within the import is used once. Names inherited from a parent topology
    reject the whole import (409); a blank name rejects it too (422).
    Returns the flows created or updated (skipped rows aren't included)."""
    blank = sum(1 for fd in data.flows if not _norm(fd.name))
    if blank:
        raise HTTPException(422, f"Cannot import: {blank} row(s) have a blank flow name.")

    parent_ids = _parent_chain_ids(db, topology_id)
    if parent_ids:
        import_names = {_norm(fd.name) for fd in data.flows}
        inherited_names = sorted({
            n for (n,) in db.query(Flow.name).filter(Flow.topology_id.in_(parent_ids))
            if _norm(n) in import_names
        })
        if inherited_names:
            names_str = ", ".join(inherited_names[:10])
            raise HTTPException(
                409,
                f"Cannot import: {len(inherited_names)} flow name(s) conflict with flows inherited from a parent topology: {names_str}"
                + ("…" if len(inherited_names) > 10 else "")
                + ". Rename or remove those rows and re-import."
            )

    # Existing flows here by normalized name (full rows only needed to update).
    existing = ({_norm(f.name): f for f in db.query(Flow).filter(Flow.topology_id == topology_id)}
                if data.update_existing else
                {_norm(n): None for (n,) in db.query(Flow.name).filter(Flow.topology_id == topology_id)})
    seen: set[str] = set()   # names already handled in this import

    results = []
    for fd in data.flows:
        key = _norm(fd.name)
        if key in seen:
            continue
        seen.add(key)
        if key in existing:
            match = existing[key]
            if match is None:
                continue  # skip: already exists
            # Update: keep interface associations when the import doesn't
            # specify them and that side's node hasn't changed.
            match.source_interface = fd.source_interface or (
                match.source_interface if match.source_node_id == fd.source_node_id else "")
            match.dest_interface = fd.dest_interface or (
                match.dest_interface if match.dest_node_id == fd.dest_node_id else "")
            match.description = fd.description
            match.source_node_id = fd.source_node_id
            match.dest_node_id = fd.dest_node_id
            match.bandwidth_gbps = fd.bandwidth_gbps
            match.routing_behavior = fd.routing_behavior
            match.explicit_path_json = json.dumps(fd.explicit_path)
            match.explicit_link_ids_json = json.dumps(fd.explicit_link_ids)
            match.ecmp_enabled = 1 if fd.ecmp_enabled else 0
            db.flush()
            results.append(_flow_to_dict(match))
            continue
        flow = Flow(
            topology_id=topology_id, name=fd.name.strip(), description=fd.description,
            source_node_id=fd.source_node_id, dest_node_id=fd.dest_node_id,
            source_interface=fd.source_interface, dest_interface=fd.dest_interface,
            bandwidth_gbps=fd.bandwidth_gbps, routing_behavior=fd.routing_behavior,
            explicit_path_json=json.dumps(fd.explicit_path),
            explicit_link_ids_json=json.dumps(fd.explicit_link_ids),
            ecmp_enabled=1 if fd.ecmp_enabled else 0,
        )
        db.add(flow)
        db.flush()
        results.append(_flow_to_dict(flow))
    db.commit()
    return results


@router.put("/{flow_id}", response_model=FlowOut)
def update_flow(topology_id: int, flow_id: int, data: FlowUpdate, db: Session = Depends(get_db)):
    flow = db.query(Flow).filter(Flow.id == flow_id, Flow.topology_id == topology_id).first()
    if not flow:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        inherited = db.query(Flow).filter(Flow.id == flow_id, Flow.topology_id.in_(chain)).first()
        if inherited:
            raise HTTPException(403, "This flow is inherited from a parent topology and is read-only here. Edit it in the root topology.")
        raise HTTPException(404, "Flow not found")

    # Shadowed-by-parent case: parent later added a flow with the same name.
    # Allow rename-only (escape hatch); block any other update.
    from app.services.topology_chain import get_topology_chain, merge_by_name
    chain = get_topology_chain(db, topology_id)
    all_flows = db.query(Flow).filter(Flow.topology_id.in_(chain)).all()
    _, flow_remap = merge_by_name(all_flows, chain)
    if flow.id in flow_remap:
        fields_set = data.model_dump(exclude_unset=True)
        is_rename_only = (
            len(fields_set) == 1 and "name" in fields_set
            and data.name and data.name != flow.name
        )
        if not is_rename_only:
            raise HTTPException(
                403,
                f"A flow named '{flow.name}' was added in a parent topology — this local copy has been absorbed and is now read-only. "
                f"Either edit the parent's version, or rename this one (name only) to keep it as a separate local flow."
            )

    # A rename must follow the flow-name rule (not blank, not taken here or
    # inherited); the stored name is trimmed.
    if data.name is not None:
        if data.name.strip() != flow.name:
            _check_flow_name(db, topology_id, data.name, exclude_id=flow.id)
        data.name = data.name.strip()

    # If node changes, clear the interface association for that side
    if data.source_node_id is not None and data.source_node_id != flow.source_node_id:
        flow.source_interface = ""
    if data.dest_node_id is not None and data.dest_node_id != flow.dest_node_id:
        flow.dest_interface = ""
    for field in ["name", "description", "source_node_id", "dest_node_id", "source_interface", "dest_interface", "bandwidth_gbps", "routing_behavior"]:
        val = getattr(data, field)
        if val is not None:
            setattr(flow, field, val)
    if data.explicit_path is not None:
        flow.explicit_path_json = json.dumps(data.explicit_path)
    if data.explicit_link_ids is not None:
        flow.explicit_link_ids_json = json.dumps(data.explicit_link_ids)
    if data.ecmp_enabled is not None:
        flow.ecmp_enabled = 1 if data.ecmp_enabled else 0

    db.commit()
    db.refresh(flow)
    return _flow_to_dict(flow)


@router.delete("/", status_code=200)
def delete_all_flows(topology_id: int, db: Session = Depends(get_db)):
    count = db.query(Flow).filter(Flow.topology_id == topology_id).delete()
    db.commit()
    return {"ok": True, "deleted": count}


@router.post("/{flow_id}/analyze-availability")
def analyze_flow_availability(
    topology_id: int, flow_id: int,
    req: FlowAvailabilityRequest = FlowAvailabilityRequest(),
    db: Session = Depends(get_db),
):
    """Run a Monte Carlo availability analysis between this flow's source and
    destination nodes, using the same engine as the Simulation panel's Monte
    Carlo tab. Defaults to the app-wide monte_carlo_iterations setting.

    Honors the active maintenance window: any exclude_node_ids /
    exclude_link_ids passed in are removed before the simulation, matching
    the MC panel. The exclusions (with resolved names) are stamped into the
    cached report so a saved report is never read out of context once the
    maintenance window is cleared.

    The result is cached on the flow row (availability_report_json) so the
    user can reopen the report without re-running, and is also returned.

    Inherited flows are analyzed against the current topology's merged network
    view, and the report is persisted on the flow's own (ancestor) row.
    """
    from app.services.topology_chain import get_topology_chain
    from app.services.simulation import run_monte_carlo
    from app.models.node_instance import NodeInstance
    from app.models.link import Link
    from datetime import datetime, timezone

    chain = get_topology_chain(db, topology_id)
    flow = db.query(Flow).filter(Flow.id == flow_id, Flow.topology_id.in_(chain)).first()
    if not flow:
        raise HTTPException(404, "Flow not found")

    # Default iteration count from global settings (matches the MC panel).
    iterations = int(_get_iterations_default(db))

    excl_nodes = req.exclude_node_ids or None
    excl_links = req.exclude_link_ids or None

    # Use the flow endpoints as the A/Z single-homed nodes. Passing the node
    # id as both the location string and the node-id filter pins the analysis
    # to exactly those two devices.
    src = db.query(NodeInstance).filter(NodeInstance.id == flow.source_node_id).first()
    dst = db.query(NodeInstance).filter(NodeInstance.id == flow.dest_node_id).first()
    result = run_monte_carlo(
        topology_id, db,
        a_location=str(flow.source_node_id),
        z_location=str(flow.dest_node_id),
        a_node_ids=[flow.source_node_id],
        z_node_ids=[flow.dest_node_id],
        iterations=iterations,
        exclude_node_ids=excl_nodes,
        exclude_link_ids=excl_links,
        include_planned=req.include_planned,
    )
    # Decorate with flow-friendly labels + a timestamp before caching.
    result["flow_id"] = flow.id
    result["flow_name"] = flow.name
    result["source_node_name"] = src.name if src else str(flow.source_node_id)
    result["dest_node_name"] = dst.name if dst else str(flow.dest_node_id)
    result["analyzed_at"] = datetime.now(timezone.utc).isoformat()

    # Stamp the applied maintenance exclusions (with names) so the cached
    # report stays honest after the maintenance window is cleared.
    excluded_nodes_named = []
    excluded_links_named = []
    if excl_nodes:
        rows = db.query(NodeInstance).filter(NodeInstance.id.in_(excl_nodes)).all()
        name_by_id = {n.id: n.name for n in rows}
        excluded_nodes_named = [{"id": nid, "name": name_by_id.get(nid, str(nid))} for nid in excl_nodes]
    if excl_links:
        rows = db.query(Link).filter(Link.id.in_(excl_links)).all()
        present = {l.id for l in rows}
        excluded_links_named = [{"id": lid} for lid in excl_links if lid in present]
    result["excluded_nodes"] = excluded_nodes_named
    result["excluded_links"] = excluded_links_named

    # IMPORTANT: run_monte_carlo() -> get_merged_topology_data() expunges
    # every flow in the topology chain from the session (a defensive measure
    # so its in-memory edits can't persist). That detaches the `flow` object
    # we fetched above, so writing to it and committing would silently no-op
    # — which is exactly why reports weren't persisting. Re-query the flow
    # fresh (now attached) and persist on that instance.
    flow = db.query(Flow).filter(Flow.id == flow_id, Flow.topology_id.in_(chain)).first()
    if flow:
        flow.availability_report = result
        db.commit()
    return result


def _get_iterations_default(db) -> float:
    """Read the app-wide Monte Carlo iteration count (global setting)."""
    from app.services.simulation import _get_setting
    return _get_setting(db, "monte_carlo_iterations", 10000)


@router.post("/{flow_id}/compute-path")
def compute_single_flow_path(topology_id: int, flow_id: int, db: Session = Depends(get_db)):
    """Compute the routed path for a single flow without running a full
    simulation. Used by the canvas/Flows panel to preview a flow's path
    on click when the user hasn't (yet) clicked Run Simulation — saves
    them the wall-time of routing every flow in the topology just to
    look at one.

    Returns the same shape as one entry in simulationResult.flow_paths,
    so the frontend's existing highlightedFlowPaths consumer doesn't
    care which path it came from.
    """
    from app.services.simulation import compute_flow_paths
    from app.services.topology_chain import get_merged_topology_data
    nodes, links, flows, _remap = get_merged_topology_data(db, topology_id)
    flow = next((f for f in flows if f.id == flow_id), None)
    if not flow:
        raise HTTPException(404, "Flow not found in this topology")
    flow_paths, _link_utils = compute_flow_paths(nodes, links, [flow], db)
    if not flow_paths:
        # compute_flow_paths always returns one entry per flow, but be
        # defensive — fall through to a no-path payload the UI already
        # understands rather than 500ing.
        return {
            "flow_id": flow.id, "flow_name": flow.name,
            "paths": [], "link_ids_per_path": [], "bandwidth_per_path_gbps": [],
        }
    return flow_paths[0]


@router.delete("/{flow_id}")
def delete_flow(topology_id: int, flow_id: int, db: Session = Depends(get_db)):
    flow = db.query(Flow).filter(Flow.id == flow_id, Flow.topology_id == topology_id).first()
    if not flow:
        from app.services.topology_chain import get_topology_chain
        chain = get_topology_chain(db, topology_id)
        inherited = db.query(Flow).filter(Flow.id == flow_id, Flow.topology_id.in_(chain)).first()
        if inherited:
            raise HTTPException(403, "This flow is inherited from a parent topology and cannot be deleted here. Delete it in the root topology.")
        raise HTTPException(404, "Flow not found")
    db.delete(flow)
    db.commit()
    return {"ok": True}

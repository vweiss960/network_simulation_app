"""Link metrics — import per-interface peak utilization + link delay, read
the derived per-link view, and export a circuit report CSV.

Measurement data, so the router sits behind auth only. The exception is the
import, which also writes measured delay into link latency_ms (design data),
so it alone requires an edit session on protected topologies.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.topology import Topology
from app.routes.edit_session import require_edit_session
from app.services.actual_utilization import (
    parse_metrics_csv, import_metrics, clear_metrics,
    compute_actual_link_utilization, export_links_csv, apply_link_delays,
)

router = APIRouter(prefix="/api/topologies/{topology_id}/actual-utilization", tags=["actual-utilization"])


class MetricsCsvImport(BaseModel):
    csv: str
    replace: bool = True


def _require_topology(db: Session, topology_id: int) -> Topology:
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    if not topo:
        raise HTTPException(404, "Topology not found")
    return topo


@router.get("/")
def get_actual_utilization(topology_id: int, db: Session = Depends(get_db)):
    _require_topology(db, topology_id)
    return compute_actual_link_utilization(db, topology_id)


@router.post("/import-csv", dependencies=[Depends(require_edit_session)])
def import_actual_utilization_csv(topology_id: int, data: MetricsCsvImport, db: Session = Depends(get_db)):
    _require_topology(db, topology_id)
    rows, errors = parse_metrics_csv(data.csv)
    if not rows:
        raise HTTPException(400, errors[0] if errors else "No metric rows found in CSV")
    stored = import_metrics(db, topology_id, rows, replace=data.replace)
    result = compute_actual_link_utilization(db, topology_id)
    applied = apply_link_delays(db, topology_id, result["links"])
    result["latency_updated_links"] = applied["updated"]
    result["latency_skipped_inherited"] = applied["skipped_inherited"]
    result["imported_rows"] = len(rows)
    result["stored_rows"] = stored
    result["errors"] = errors[:50]
    result["error_count"] = len(errors)
    return result


@router.delete("/")
def delete_actual_utilization(topology_id: int, db: Session = Depends(get_db)):
    _require_topology(db, topology_id)
    return {"deleted": clear_metrics(db, topology_id)}


@router.get("/export-csv")
def export_actual_utilization_csv(topology_id: int, db: Session = Depends(get_db)):
    topo = _require_topology(db, topology_id)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in topo.name) or "topology"
    return Response(
        content=export_links_csv(db, topology_id),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe}_link_utilization.csv"'},
    )

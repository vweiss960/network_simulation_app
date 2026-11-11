"""Cost allocation API routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.schemas.schemas import CostAllocationRequest
from app.services.cost_allocation import run_cost_allocation

router = APIRouter(prefix="/api/topologies/{topology_id}/cost-allocation", tags=["cost-allocation"])


@router.post("/")
def run_allocation(topology_id: int, req: CostAllocationRequest, db: Session = Depends(get_db)):
    """Run cost allocation analysis with the specified model."""
    return run_cost_allocation(topology_id, db, allocation_model=req.allocation_model)

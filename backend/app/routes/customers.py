"""Customer management API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.customer import Customer
from app.models.flow import Flow
from app.schemas.schemas import CustomerCreate, CustomerUpdate, CustomerOut

router = APIRouter(prefix="/api/topologies/{topology_id}/customers", tags=["customers"])


@router.get("/", response_model=list[CustomerOut])
def list_customers(topology_id: int, db: Session = Depends(get_db)):
    """Customers across the WHOLE inheritance chain. A child topology's merged
    nodes/flows can reference ancestor customers, so the panel (and the export
    dialog's customer filter) must see them too. Ancestor customers stay owned
    by their topology — update/delete below remain own-topology-scoped, and the
    frontend shows them read-only (topology_id != current topology)."""
    from app.services.topology_chain import get_topology_chain
    chain = get_topology_chain(db, topology_id)
    return db.query(Customer).filter(Customer.topology_id.in_(chain)).all()


@router.post("/", response_model=CustomerOut)
def create_customer(topology_id: int, data: CustomerCreate, db: Session = Depends(get_db)):
    customer = Customer(topology_id=topology_id, **data.dict())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(topology_id: int, customer_id: int, data: CustomerUpdate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id, Customer.topology_id == topology_id).first()
    if not customer:
        raise HTTPException(404, "Customer not found")
    for key, val in data.dict(exclude_unset=True).items():
        setattr(customer, key, val)
    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}")
def delete_customer(topology_id: int, customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id, Customer.topology_id == topology_id).first()
    if not customer:
        raise HTTPException(404, "Customer not found")
    # Unassign flows and nodes from this customer
    db.query(Flow).filter(Flow.customer_id == customer_id).update({"customer_id": None})
    from app.models.node_instance import NodeInstance
    db.query(NodeInstance).filter(NodeInstance.customer_id == customer_id).update({"customer_id": None})
    db.delete(customer)
    db.commit()
    return {"ok": True}

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from app.models.database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    name = Column(String, nullable=False)
    contact_email = Column(String, default="")
    contact_phone = Column(String, default="")
    notes = Column(String, default="")
    color = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

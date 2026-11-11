"""Service — a reusable, exportable bundle of Jinja templates + declared
variables that renders config from caller-supplied data and (optionally) pushes
it live to a device.

Unlike Config Design (which renders against topology-derived device/interface
context and caches the result for containerlab startup-config), a Service takes
its variable values directly from the deploy request and can push the rendered
config over SSH to a running lab node or a topology node's management IP.

Service module only (gated by PHANERO_SERVICE_ENABLED, which itself requires
PHANERO_LAB_ENABLED). See config_template_group.py for the relationship pattern.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from app.models.database import Base


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, default="")
    # Default push target for this service. "lab" → running containerlab node;
    # "mgmt_ip" → the topology node's management IP. Overridable per deploy.
    target_mode = Column(String, default="lab")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    templates = relationship(
        "ServiceTemplate",
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="ServiceTemplate.position",
    )
    variables = relationship(
        "ServiceVariable",
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="ServiceVariable.name",
    )

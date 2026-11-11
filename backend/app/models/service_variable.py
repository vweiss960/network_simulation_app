"""A declared variable of a Service.

These are declarations only — they document the variables a service's templates
expect, drive deploy-time validation (required vars must be supplied), seed
defaults, and are exported with the service. The actual values come inline in
the deploy request (the "Direct" variable model), not from here.
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.database import Base


class ServiceVariable(Base):
    __tablename__ = "service_variables"
    __table_args__ = (
        UniqueConstraint("service_id", "name", name="uq_service_variable_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    required = Column(Boolean, default=False)
    # JSON-encoded default value applied when a deploy omits this variable
    # (empty string = no default). Stored as text so any JSON type round-trips.
    default_json = Column(Text, default="")
    description = Column(String, default="")

    service = relationship("Service", back_populates="variables")

"""A Jinja template belonging to a Service. Rendered against the caller-supplied
variable dict (see service_renderer). Ordered within its service by `position`;
a deploy can select a subset by name or render all in order."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.database import Base


class ServiceTemplate(Base):
    __tablename__ = "service_templates"
    __table_args__ = (
        UniqueConstraint("service_id", "name", name="uq_service_template_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    body = Column(Text, default="")            # Jinja2 source
    # Render order within the service. Smaller first; gaps are fine.
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    service = relationship("Service", back_populates="templates")

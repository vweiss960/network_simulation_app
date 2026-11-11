from sqlalchemy import Column, Integer, String, ForeignKey
from app.models.database import Base


class ExternalRef(Base):
    """Tracks the external primary key for a Phanero resource."""
    __tablename__ = "external_refs"

    id = Column(Integer, primary_key=True, index=True)
    external_system_id = Column(Integer, ForeignKey("external_systems.id"), nullable=False)
    phanero_resource = Column(String, nullable=False)  # "link" or "node"
    phanero_id = Column(Integer, nullable=False)
    external_pk_value = Column(String, nullable=False)

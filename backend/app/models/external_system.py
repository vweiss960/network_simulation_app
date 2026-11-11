from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, func
from app.models.database import Base


class ExternalSystem(Base):
    """A configured external API endpoint for syncing topology data."""
    __tablename__ = "external_systems"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    name = Column(String, nullable=False)
    base_url = Column(String, nullable=False)
    auth_type = Column(String, default="api_key")  # api_key, bearer, basic, none
    auth_header_name = Column(String, default="X-API-Key")
    auth_value = Column(String, default="")
    enabled = Column(Boolean, default=False)
    verify_ssl = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from app.models.database import Base


class ConfigSnapshot(Base):
    """A saved running config of one device. Snapshots taken together for a
    whole lab share a `label`, so the lab can be restored as a set."""
    __tablename__ = "config_snapshots"

    id = Column(Integer, primary_key=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("node_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String, nullable=False)
    family = Column(String, default="")
    config = Column(Text, nullable=False)
    created_by = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

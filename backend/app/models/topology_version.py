from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.models.database import Base


class TopologyVersion(Base):
    __tablename__ = "topology_versions"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    snapshot_json = Column(Text, nullable=False)
    node_count = Column(Integer, default=0)
    link_count = Column(Integer, default=0)
    flow_count = Column(Integer, default=0)
    # Username of the committing user (protected-topology commits stamp this).
    created_by = Column(String, default="")
    # Transient pre-edit baseline snapshots (used to discard an edit session) are
    # flagged so they can be hidden from the user-facing version list.
    is_baseline = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    topology = relationship("Topology", back_populates="versions")

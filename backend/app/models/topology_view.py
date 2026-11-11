"""TopologyView — a saved "sub-topology": a named subset of a topology's
nodes. When applied in the UI, only these nodes and the links whose BOTH
endpoints are in the set are shown; everything else is hidden.

Only node ids are persisted — interconnecting links are always derived at
view time, so a saved view stays correct as links are added/removed.

Per-topology (scoped by topology_id), mirroring MaintenancePreset.
"""
import json
from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, func
from app.models.database import Base


class TopologyView(Base):
    __tablename__ = "topology_views"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    node_ids_json = Column(Text, default="[]")
    # Layer color used to tint this view's nodes/links on the canvas (hex). Empty
    # falls back to an auto palette color on the frontend.
    color = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def node_ids(self):
        return json.loads(self.node_ids_json) if self.node_ids_json else []

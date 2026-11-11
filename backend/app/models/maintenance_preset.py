import json
from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, func
from app.models.database import Base


class MaintenancePreset(Base):
    __tablename__ = "maintenance_presets"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    node_ids_json = Column(Text, default="[]")
    link_ids_json = Column(Text, default="[]")
    created_at = Column(DateTime, server_default=func.now())

    @property
    def node_ids(self):
        return json.loads(self.node_ids_json) if self.node_ids_json else []

    @property
    def link_ids(self):
        return json.loads(self.link_ids_json) if self.link_ids_json else []

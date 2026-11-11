import json
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.models.database import Base


class Flow(Base):
    __tablename__ = "flows"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    source_node_id = Column(Integer, ForeignKey("node_instances.id"), nullable=False)
    dest_node_id = Column(Integer, ForeignKey("node_instances.id"), nullable=False)
    bandwidth_gbps = Column(Float, default=1.0)
    routing_behavior = Column(String, default="shortest_path")  # shortest_path, lowest_latency, optimized, explicit
    explicit_path_json = Column(Text, default="[]")  # list of node IDs for explicit routing
    explicit_link_ids_json = Column(Text, default="[]")  # list of link IDs for explicit routing
    source_interface = Column(String, default="")
    dest_interface = Column(String, default="")
    ecmp_enabled = Column(Integer, default=0)
    # Cached availability report — the JSON result of the last Monte Carlo
    # availability analysis run between this flow's source and dest nodes
    # (see flows route /analyze-availability). Empty until the user runs it.
    availability_report_json = Column(Text, default="")

    topology = relationship("Topology", back_populates="flows")
    source_node = relationship("NodeInstance", foreign_keys=[source_node_id])
    dest_node = relationship("NodeInstance", foreign_keys=[dest_node_id])

    @property
    def explicit_path(self):
        return json.loads(self.explicit_path_json) if self.explicit_path_json else []

    @explicit_path.setter
    def explicit_path(self, value):
        self.explicit_path_json = json.dumps(value)

    @property
    def explicit_link_ids(self):
        return json.loads(self.explicit_link_ids_json) if self.explicit_link_ids_json else []

    @explicit_link_ids.setter
    def explicit_link_ids(self, value):
        self.explicit_link_ids_json = json.dumps(value)

    @property
    def availability_report(self):
        if not self.availability_report_json:
            return None
        try:
            return json.loads(self.availability_report_json)
        except (ValueError, TypeError):
            return None

    @availability_report.setter
    def availability_report(self, value):
        self.availability_report_json = json.dumps(value) if value else ""

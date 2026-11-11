import json
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.models.database import Base


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    source_node_id = Column(Integer, ForeignKey("node_instances.id"), nullable=False)
    target_node_id = Column(Integer, ForeignKey("node_instances.id"), nullable=False)
    source_interface = Column(String, default="")
    target_interface = Column(String, default="")
    bandwidth_gbps = Column(Float, default=10.0)
    cost = Column(Float, default=1.0)
    latency_ms = Column(Float, default=1.0)
    # "measured": latency_ms is a known value (entered or imported).
    # "unknown": latency_ms is ignored and an estimate is used instead
    # (services/latency.py).
    latency_source = Column(String, default="unknown")
    # How the link is carried; picks the latency estimator. Only "terrestrial"
    # for now — satellite (LEO / MEO / GEO) is planned.
    transport = Column(String, default="terrestrial")
    # Satellite links only: "leo" | "meo" | "geo" (chosen per link — a PoP may
    # serve several constellations). The provider is `carrier`.
    orbit = Column(String, default="")
    carrier = Column(String, default="")
    mtbf_hours = Column(Float, nullable=True)
    mttr_hours = Column(Float, nullable=True)
    sr_attributes_json = Column(Text, default="{}")
    circuit_cost = Column(Float, default=0.0)  # Monthly cost to keep circuit active
    circuit_id = Column(String, default="")  # Circuit ID / provider reference
    link_id_label = Column(String, default="")  # Custom link identifier
    source_ip = Column(String, default="")
    target_ip = Column(String, default="")
    source_ipv6 = Column(String, default="")
    target_ipv6 = Column(String, default="")
    is_planned = Column(Integer, default=0)  # 0=active, 1=planned (not yet built)
    color = Column(String, default="")  # Custom link color (hex, e.g. #ff0000)
    # Geographic-view only: JSON [[lng,lat],...] fiber route the link follows on
    # the globe (e.g. from a KMZ). Empty = straight great-circle line.
    geo_path_json = Column(Text, default="")

    topology = relationship("Topology", back_populates="links")
    source_node = relationship("NodeInstance", foreign_keys=[source_node_id])
    target_node = relationship("NodeInstance", foreign_keys=[target_node_id])

    @property
    def sr_attributes(self):
        return json.loads(self.sr_attributes_json) if self.sr_attributes_json else {}

    @sr_attributes.setter
    def sr_attributes(self, value):
        self.sr_attributes_json = json.dumps(value)

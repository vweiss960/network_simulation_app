import json
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.database import Base


class NodeInstance(Base):
    """A device on a topology. Carries its own kind + image (copied from the
    template it was created from) so what's on the canvas is exactly what
    deploys — there is no design→lab translation step."""
    __tablename__ = "node_instances"
    __table_args__ = (UniqueConstraint("topology_id", "name", name="uq_node_topology_name"),)

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(Integer, ForeignKey("node_templates.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="linux")
    image_ref = Column(String, default="")
    icon = Column(String, default="router")
    x = Column(Float, default=0.0)
    y = Column(Float, default=0.0)
    # [{"name": <config name>, "kernel": <clab endpoint name>}, ...]
    interfaces_json = Column(Text, default="[]")
    # Static management IPv4 on the lab mgmt network (assigned by IPAM, Phase 2).
    mgmt_ipv4 = Column(String, default="")
    # Per-node credential override {"username": .., "password": ..}; empty →
    # the kind's defaults (device_kinds).
    credentials_json = Column(Text, default="{}")
    startup_config = Column(Text, default="")
    min_memory_mb = Column(Integer, default=0)
    min_cpu_cores = Column(Integer, default=0)
    color = Column(String, default="")
    notes = Column(Text, default="")

    topology = relationship("Topology", back_populates="nodes")

    @property
    def interfaces(self) -> list[dict]:
        try:
            return list(json.loads(self.interfaces_json or "[]"))
        except Exception:
            return []

    @interfaces.setter
    def interfaces(self, value):
        self.interfaces_json = json.dumps(list(value or []))

    @property
    def credentials(self) -> dict:
        try:
            return dict(json.loads(self.credentials_json or "{}"))
        except Exception:
            return {}

    @credentials.setter
    def credentials(self, value):
        self.credentials_json = json.dumps(dict(value or {}))

    def kernel_iface(self, config_name: str) -> str:
        for i in self.interfaces:
            if i.get("name") == config_name:
                return i.get("kernel") or config_name
        return config_name

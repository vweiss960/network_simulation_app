import json
from sqlalchemy import Column, Integer, String, Float, Text
from app.models.database import Base


class NodeTemplate(Base):
    __tablename__ = "node_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    vendor = Column(String, nullable=False)  # cisco, juniper
    model = Column(String, nullable=False)
    platform = Column(String, default="")  # iosxe, iosxr, junos
    capacity_gbps = Column(Float, default=100.0)
    default_mtbf_hours = Column(Float, default=50000.0)
    default_mttr_hours = Column(Float, default=4.0)
    default_interfaces_json = Column(Text, default="[]")
    role = Column(String, default="router")  # router, customer
    created_by = Column(String, default="system")

    # --- Containerlab integration (used when deploying a topology as a real lab) ---
    clab_kind = Column(String, default="")  # containerlab "kind": e.g. cisco_xrd, linux, arista_ceos
    image_ref = Column(String, default="")  # Container image reference: localhost/xrd-vrouter:25.4.2
    min_memory_mb = Column(Integer, default=0)  # 0 = unspecified / minimal
    min_cpu_cores = Column(Integer, default=0)  # 0 = unspecified
    requires_hugepages = Column(Integer, default=0)  # 0/1 boolean
    startup_config = Column(Text, default="")  # Optional default startup config text

    @property
    def default_interfaces(self):
        return json.loads(self.default_interfaces_json) if self.default_interfaces_json else []

    @default_interfaces.setter
    def default_interfaces(self, value):
        self.default_interfaces_json = json.dumps(value)

"""PatchPanel — documentation of a physical patch panel at a location.

Purely documentary: panels never create, modify, or delete links, nodes or
interfaces, and nothing in simulation/routing reads them. A panel belongs to a
LOCATION within a (root) topology and is visible from every device whose
`location` matches. Ports reference EXISTING topology items only:

ports_json is a dict keyed by 1-based port number (row-major) holding only the
ports that carry data:
    {"7": {"label": "", "status": "cabled_used", "node_id": 12,
           "interface": "Hu0/0/0/3", "link_id": 88, "notes": "XC-12345",
           "rear_port": 5}}
status is manual: unused | cabled_free | cabled_reserved | cabled_used
(a missing entry means "unused").

Chaining: in_front_of_id points at the panel directly BEHIND this one (the
wan-facing side). Several panels may sit in front of one panel. A port's
`rear_port` is a jumper from that port to port N of the panel behind — at most
one per port on either side. Physical path: WAN link -> wan-facing port ->
jumper -> customer-facing port -> device. The device is documented on the
customer-facing port (the front of the chain); wan-facing ports document no
device, and the WAN link is mirrored on every port of a jumpered path.
"""
import json
from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, func
from app.models.database import Base

PORT_STATUSES = ("unused", "cabled_free", "cabled_reserved", "cabled_used")


class PatchPanel(Base):
    __tablename__ = "patch_panels"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False, index=True)
    location = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    rack = Column(String, default="")
    start_u = Column(Integer, nullable=True)   # lowest U the panel occupies
    height_u = Column(Integer, default=1)
    rows = Column(Integer, default=1)
    ports_per_row = Column(Integer, default=24)
    notes = Column(String, default="")
    # Panel directly behind this one (toward the WAN); None = not chained.
    in_front_of_id = Column(Integer, ForeignKey("patch_panels.id"), nullable=True)
    ports_json = Column(Text, default="{}")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def ports(self) -> dict:
        try:
            v = json.loads(self.ports_json) if self.ports_json else {}
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}

    @ports.setter
    def ports(self, value: dict):
        self.ports_json = json.dumps(value or {})

    @property
    def port_count(self) -> int:
        return (self.rows or 0) * (self.ports_per_row or 0)

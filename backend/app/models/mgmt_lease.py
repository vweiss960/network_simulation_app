from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from app.models.database import Base


class MgmtLease(Base):
    """A DHCP lease Phanero Labs holds on the bridged management network,
    either for the server itself (node_id NULL) or on behalf of a device."""
    __tablename__ = "mgmt_leases"

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("node_instances.id", ondelete="CASCADE"), nullable=True, unique=True)
    client_id = Column(String, nullable=False, unique=True)
    bridge = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    prefix = Column(Integer, nullable=False)
    router = Column(String, default="")
    dns_json = Column(Text, default="[]")
    server_id = Column(String, default="")
    lease_time = Column(Integer, default=3600)
    obtained_at = Column(Float, default=0.0)

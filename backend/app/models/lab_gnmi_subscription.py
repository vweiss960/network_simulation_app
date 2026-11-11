from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from app.models.database import Base


class LabGnmiSubscription(Base):
    """A user-defined gNMI subscription for a topology's lab telemetry collector.

    These are injected into the generated gnmic config (in addition to the
    built-in interface-counters subscription) so custom paths/timing get pulled
    into Prometheus. Per-topology; part of the lab telemetry add-on.
    """
    __tablename__ = "lab_gnmi_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False, index=True)
    name = Column(String, nullable=False)          # gnmic subscription name (config key)
    path = Column(String, nullable=False)          # gNMI path (xpath / origin:/path)
    mode = Column(String, default="sample")        # "sample" | "on-change"
    sample_interval = Column(String, default="30s")  # for sample mode (e.g. "10s")
    enabled = Column(Boolean, default=True)

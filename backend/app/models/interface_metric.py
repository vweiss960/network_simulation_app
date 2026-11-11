"""InterfaceMetric — measured ("actual") per-interface utilization.

One row per (topology, instance, interface). `instance` is the device as the
metrics source names it (matched to a node by name / prometheus_id / mgmt_ip)
and `interface` is the interface name the counters were collected on. Values
are peak rates in bits/sec over the trailing 30 and 90 days; `link_delay_us`
is the measured one-way link delay in microseconds.

Measurement data, not design data: rows are not part of topology snapshots /
version restore, and simulation never reads them. (An import does copy the
derived link delay into each local link's latency_ms — see
apply_link_delays.) Link-level actual
utilization is derived on read (services/actual_utilization.py) by matching
each link's two endpoint interfaces and taking the higher of the two.

`source` records where the rows came from ("csv" today; a live collector can
write "prometheus" etc. into the same table later).
"""
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, UniqueConstraint, func
from app.models.database import Base


class InterfaceMetric(Base):
    __tablename__ = "interface_metrics"
    __table_args__ = (UniqueConstraint("topology_id", "instance", "interface", name="uq_iface_metric"),)

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False, index=True)
    instance = Column(String, nullable=False)
    interface = Column(String, nullable=False)
    peak_30d_bps = Column(Float, nullable=True)
    peak_90d_bps = Column(Float, nullable=True)
    link_delay_us = Column(Float, nullable=True)
    source = Column(String, default="csv")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

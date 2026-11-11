from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from app.models.database import Base


class PrometheusConfig(Base):
    __tablename__ = "prometheus_configs"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), unique=True, nullable=False)
    url = Column(String, nullable=False)  # e.g. http://prometheus:9090
    auth_type = Column(String, default="none")  # none, basic, bearer
    username = Column(String, default="")
    password = Column(String, default="")
    bearer_token = Column(String, default="")
    enabled = Column(Boolean, default=False)
    # Metric configuration - which Prometheus metrics to query
    in_octets_metric = Column(String, default="ifHCInOctets")
    out_octets_metric = Column(String, default="ifHCOutOctets")
    speed_metric = Column(String, default="ifHighSpeed")
    interface_label = Column(String, default="ifName")
    instance_label = Column(String, default="instance")
    rate_interval = Column(String, default="5m")
    mode = Column(String, default="external")  # "external" or "builtin"

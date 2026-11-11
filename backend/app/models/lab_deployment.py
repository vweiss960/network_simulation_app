from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, func, UniqueConstraint
from app.models.database import Base


class LabDeployment(Base):
    """Tracks the containerlab deployment state of a topology.

    One row per topology (UniqueConstraint on topology_id). Status transitions:
      idle → deploying → running    (happy path)
      idle → deploying → failed     (deploy errored)
      running → destroying → idle   (destroy succeeded)
      running → destroying → failed (destroy errored, manual cleanup may be needed)
    """
    __tablename__ = "lab_deployments"
    __table_args__ = (
        UniqueConstraint("topology_id", name="uq_lab_deployment_topology"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, default="idle")  # idle, deploying, running, destroying, failed
    lab_name = Column(String, default="")    # containerlab-side lab name (used in CLI calls)
    lab_dir = Column(String, default="")     # filesystem path where the YAML + state live
    last_yaml = Column(Text, default="")     # most recently generated topology YAML
    last_error = Column(Text, default="")    # surfaced to the user on failed deploy/destroy
    started_at = Column(DateTime, nullable=True)   # last deploy start time
    finished_at = Column(DateTime, nullable=True)  # last terminal-state transition time
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

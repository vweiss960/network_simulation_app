from sqlalchemy import Column, Integer, Float, ForeignKey, UniqueConstraint
from app.models.database import Base


class NodePositionOverride(Base):
    """A per-child-topology override of a node's canvas/geographic position.

    Used so that when a planning (child) topology inherits a node from its
    parent, the user can drag the node around to suit the planning layout
    without modifying the parent's source-of-truth position. One row per
    (child topology, node). Absence of a row means the parent's position is
    used as-is.
    """
    __tablename__ = "node_position_overrides"
    __table_args__ = (
        UniqueConstraint("topology_id", "node_id", name="uq_position_override_topology_node"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="CASCADE"), nullable=False)
    node_id = Column(Integer, ForeignKey("node_instances.id", ondelete="CASCADE"), nullable=False)
    x = Column(Float, default=0.0)
    y = Column(Float, default=0.0)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    # Per-child-topology override of the node's geographic map position,
    # independent of the canvas x/y + logical lat/lng overrides above.
    geo_latitude = Column(Float, nullable=True)
    geo_longitude = Column(Float, nullable=True)

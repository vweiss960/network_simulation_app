from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.models.database import Base


class Link(Base):
    """A point-to-point cable between two device interfaces (a veth pair once
    deployed). Interfaces are referenced by their config (device-native) name."""
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="CASCADE"), nullable=False)
    source_node_id = Column(Integer, ForeignKey("node_instances.id", ondelete="CASCADE"), nullable=False)
    target_node_id = Column(Integer, ForeignKey("node_instances.id", ondelete="CASCADE"), nullable=False)
    source_interface = Column(String, nullable=False)
    target_interface = Column(String, nullable=False)
    label = Column(String, default="")
    color = Column(String, default="")

    topology = relationship("Topology", back_populates="links")
    source_node = relationship("NodeInstance", foreign_keys=[source_node_id])
    target_node = relationship("NodeInstance", foreign_keys=[target_node_id])

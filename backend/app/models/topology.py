import json
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import relationship
from app.models.database import Base


class Topology(Base):
    __tablename__ = "topologies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    # Optional pointer to a parent topology. When set, this topology inherits
    # all nodes and links from its parent (and the parent's parent, recursively).
    # Inherited items are read-only in the child; the child can only add.
    parent_topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=True)
    # The "Default view" is everything MINUS these node ids. Stored as an
    # exclusion set (not an inclusion list) so newly-added and inherited nodes
    # appear automatically; the user removes nodes by adding them here.
    default_view_excluded_node_ids_json = Column(Text, default="[]")
    # Protected topology: changes require an explicit, password-gated edit
    # session (sudo-style). The session is exclusive (one editor at a time) and
    # tracked inline; baseline_version_id is the pre-edit snapshot used to
    # discard / auto-discard on expiry.
    is_protected = Column(Integer, default=0)
    edit_session_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    edit_session_username = Column(String, default="")
    edit_session_expires_at = Column(DateTime, nullable=True)
    edit_session_baseline_version_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    nodes = relationship("NodeInstance", back_populates="topology", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="topology", cascade="all, delete-orphan")
    flows = relationship("Flow", back_populates="topology", cascade="all, delete-orphan")
    srlgs = relationship("SrlgGroup", back_populates="topology", cascade="all, delete-orphan")
    versions = relationship("TopologyVersion", back_populates="topology", cascade="all, delete-orphan")

    parent = relationship("Topology", remote_side=[id], backref="children")

    @property
    def default_view_excluded_node_ids(self):
        return json.loads(self.default_view_excluded_node_ids_json) if self.default_view_excluded_node_ids_json else []

    @default_view_excluded_node_ids.setter
    def default_view_excluded_node_ids(self, value):
        self.default_view_excluded_node_ids_json = json.dumps(value or [])

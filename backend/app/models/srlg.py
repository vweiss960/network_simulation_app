from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.models.database import Base


class SrlgGroup(Base):
    __tablename__ = "srlg_groups"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    srlg_type = Column(String, default="custom")  # conduit, building, power, fiber_span, custom

    topology = relationship("Topology", back_populates="srlgs")
    members = relationship("SrlgMember", back_populates="srlg", cascade="all, delete-orphan")


class SrlgMember(Base):
    __tablename__ = "srlg_members"

    id = Column(Integer, primary_key=True, index=True)
    srlg_id = Column(Integer, ForeignKey("srlg_groups.id"), nullable=False)
    member_type = Column(String, nullable=False)  # "node" or "link"
    member_id = Column(Integer, nullable=False)

    srlg = relationship("SrlgGroup", back_populates="members")

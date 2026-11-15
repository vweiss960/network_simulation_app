import json

from sqlalchemy import Column, Integer, String, DateTime, Text, func
from sqlalchemy.orm import relationship
from app.models.database import Base


class Topology(Base):
    """A topology *is* a lab: every node on it is a deployable device."""
    __tablename__ = "topologies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    # Canvas annotations (group boxes, text, notes) — drawing only, never
    # deployed. [{id, type, x, y, w, h, text, color, locked}]
    annotations_json = Column(Text, default="[]")
    # Labs made from a lesson: a learner's attempt, or the lesson's authoring
    # lab (where its starting topology is edited).
    lesson_id = Column(Integer, nullable=True, index=True)
    lesson_role = Column(String, nullable=True)          # attempt | authoring | troubleshoot | troubleshoot_test
    # Troubleshooting labs (lesson_role troubleshoot / troubleshoot_test).
    scenario_id = Column(Integer, nullable=True, index=True)

    @property
    def annotations(self) -> list[dict]:
        try:
            return list(json.loads(self.annotations_json or "[]"))
        except Exception:
            return []

    @annotations.setter
    def annotations(self, value):
        self.annotations_json = json.dumps(list(value or []))

    nodes = relationship("NodeInstance", back_populates="topology", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="topology", cascade="all, delete-orphan")

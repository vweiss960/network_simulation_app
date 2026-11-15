"""Modules: an ordered collection of lessons built around one topic — e.g. a
section of a certification blueprint. Lessons are referenced by slug, so a
module survives export / import and lessons stay usable on their own.
A learner's module progress is derived from their lesson attempts."""
import json

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.models.database import Base
from app.models.lesson import _loads


class Module(Base):
    __tablename__ = "modules"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, default="")
    level = Column(String, default="intermediate")      # same values as lessons
    track = Column(String, default="general")
    blueprint = Column(String, default="")              # e.g. "CCNP SPCOR 350-501 · 2.0 Networking"
    tags_json = Column(Text, default="[]")
    status = Column(String, default="draft")            # draft | published
    version = Column(String, default="1.0")
    author = Column(String, default="")
    # Ordered [{lesson: slug, section: "2.1", title: "Implement IS-IS", objectives: ["2.1.a …"]}]
    items_json = Column(Text, default="[]")
    overview_md = Column(Text, default="")
    edited_in_app = Column(Integer, default=0)          # bundled packs never overwrite it then
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def tags(self) -> list:
        return _loads(self.tags_json, [])

    @tags.setter
    def tags(self, v):
        self.tags_json = json.dumps(list(v or []))

    @property
    def items(self) -> list:
        return _loads(self.items_json, [])

    @items.setter
    def items(self, v):
        self.items_json = json.dumps(list(v or []))

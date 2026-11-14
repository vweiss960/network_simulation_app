"""Lessons: a topology (starting state) + graded tasks + written content.
A LessonAttempt is one learner's run of a lesson in its own lab."""
import json

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.models.database import Base


def _loads(text: str | None, default):
    try:
        v = json.loads(text) if text else default
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, default="")
    level = Column(String, default="beginner")          # beginner | intermediate | advanced
    track = Column(String, default="general")           # enterprise | service_provider | data_center | automation | security | general
    topic = Column(String, default="")
    tags_json = Column(Text, default="[]")
    minutes = Column(Integer, default=30)
    ai_policy = Column(String, default="hints")         # teach | hints | off
    hints_mode = Column(String, default="on_request")   # open | on_request | none
    status = Column(String, default="draft")            # draft | published
    version = Column(String, default="1.0")
    author = Column(String, default="")
    topology_yaml = Column(Text, default="")            # topology_io native format
    tasks_json = Column(Text, default="[]")
    content_md = Column(Text, default="")
    # Set when edited in the app: bundled packs never overwrite such a lesson.
    edited_in_app = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def tags(self) -> list:
        return _loads(self.tags_json, [])

    @tags.setter
    def tags(self, v):
        self.tags_json = json.dumps(list(v or []))

    @property
    def tasks(self) -> list:
        return _loads(self.tasks_json, [])

    @tasks.setter
    def tasks(self, v):
        self.tasks_json = json.dumps(list(v or []))


class LessonAttempt(Base):
    __tablename__ = "lesson_attempts"

    id = Column(Integer, primary_key=True, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_version = Column(String, default="")
    username = Column(String, default="", index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="SET NULL"), nullable=True, index=True)
    results_json = Column(Text, default="{}")            # task id → {passed, checked_at, checks: [...]}
    hints_json = Column(Text, default="{}")              # task id → hints revealed (count)
    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    @property
    def results(self) -> dict:
        return _loads(self.results_json, {})

    @results.setter
    def results(self, v):
        self.results_json = json.dumps(dict(v or {}))

    @property
    def hints(self) -> dict:
        return _loads(self.hints_json, {})

    @hints.setter
    def hints(self, v):
        self.hints_json = json.dumps(dict(v or {}))

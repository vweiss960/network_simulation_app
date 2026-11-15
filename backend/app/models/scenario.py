"""Troubleshooting scenarios (learning add-on).

A Scenario is a known-good network (baseline topology + health checks), its
network documentation, and a catalog of faults. "Chaos" breaks the healthy
network with a few faults from the catalog; the learner restores it.
A ScenarioAttempt is one learner's lab for a scenario and its current round.
"""
import json

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.models.database import Base
from app.models.lesson import _loads


class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, default="")
    level = Column(String, default="advanced")
    track = Column(String, default="service_provider")
    tags_json = Column(Text, default="[]")
    protocols_json = Column(Text, default="[]")       # e.g. ["isis", "bgp"]
    max_faults = Column(Integer, default=4)
    status = Column(String, default="published")
    version = Column(String, default="1.0")
    author = Column(String, default="")
    topology_yaml = Column(Text, default="")            # the healthy baseline
    docs_md = Column(Text, default="")                  # network documentation (Guided)
    health_json = Column(Text, default="[]")            # checks that pass on a healthy network
    faults_json = Column(Text, default="[]")            # the fault catalog
    edited_in_app = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def _get(self, col, default):
        return _loads(getattr(self, col), default)

    @property
    def tags(self) -> list:
        return self._get("tags_json", [])

    @property
    def protocols(self) -> list:
        return self._get("protocols_json", [])

    @property
    def health(self) -> list:
        return self._get("health_json", [])

    @property
    def faults(self) -> list:
        return self._get("faults_json", [])


class ScenarioAttempt(Base):
    __tablename__ = "scenario_attempts"

    id = Column(Integer, primary_key=True, index=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_version = Column(String, default="")
    username = Column(String, default="", index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="SET NULL"), nullable=True, index=True)
    difficulty = Column(String, default="standard")     # guided | standard | expert
    fault_count = Column(Integer, default=1)
    # idle → preparing → active → solved | revealed ; error
    state = Column(String, default="idle")
    progress = Column(String, default="")               # what the Chaos job is doing
    error = Column(Text, default="")
    round_json = Column(Text, default="{}")             # the current round (faults hidden from the learner)
    history_json = Column(Text, default="[]")           # finished rounds: stats only
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def round(self) -> dict:
        return _loads(self.round_json, {})

    @round.setter
    def round(self, v):
        self.round_json = json.dumps(v or {})

    @property
    def history(self) -> list:
        return _loads(self.history_json, [])

    @history.setter
    def history(self, v):
        self.history_json = json.dumps(list(v or []))

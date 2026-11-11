"""SiteTemplate — a reusable small site (1-4 devices, the links between them
and its patch panels) that can be dropped into any topology.

Global (not per-topology), like device templates. The whole site lives in
definition_json; see services/site_templates.py for its shape. String fields
may contain {{variable}} placeholders filled in when the site is added.
"""
import json
from sqlalchemy import Column, Integer, String, Text, DateTime, func
from app.models.database import Base


class SiteTemplate(Base):
    __tablename__ = "site_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, default="")
    definition_json = Column(Text, default="{}")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def definition(self) -> dict:
        try:
            v = json.loads(self.definition_json) if self.definition_json else {}
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}

    @definition.setter
    def definition(self, value: dict):
        self.definition_json = json.dumps(value or {})

"""Template groups — ordered collections of small Jinja templates that render
into a single device config.

Pattern matches how Claude Code treats a project: each individual template is
a "file" with focused content (interfaces, OSPF, BGP, banner, etc.); the
group is the "project" that defines which files belong together and in what
order they concatenate to form the final config.

A device can be assigned a group (preferred), a single template (legacy
path), or fall back to whatever has a matching platform_default. See
config_renderer.resolve_template_for_node for the priority logic.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.database import Base


class ConfigTemplateGroup(Base):
    __tablename__ = "config_template_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, default="")
    # Default-rule VALUE (see config_template.py for the full semantics):
    # when non-empty, this group is the default for every device whose
    # `match_attr` attribute equals this value. A matching group takes
    # precedence over any matching single template.
    platform_default = Column(String, default="", index=True)
    match_attr = Column(String, default="platform", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    members = relationship(
        "ConfigTemplateGroupMember",
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="ConfigTemplateGroupMember.position",
    )


class ConfigTemplateGroupMember(Base):
    """Join row: which templates belong to a group, and in what order."""
    __tablename__ = "config_template_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "template_id", name="uq_group_template"),
    )

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("config_template_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id = Column(Integer, ForeignKey("config_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    # Render order within the group. Smaller first. Gaps are fine — the
    # renderer just sorts on this column.
    position = Column(Integer, default=0)

    group = relationship("ConfigTemplateGroup", back_populates="members")
    template = relationship("ConfigTemplate")

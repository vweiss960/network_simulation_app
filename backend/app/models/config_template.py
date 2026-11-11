from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from app.models.database import Base


class ConfigTemplate(Base):
    __tablename__ = "config_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, default="")
    # Jinja2 source. Rendered in a SandboxedEnvironment so the template body
    # cannot execute arbitrary Python.
    body = Column(Text, default="")
    # Default-rule VALUE: when non-empty, this template is the default for
    # every device whose `match_attr` attribute equals this value. match_attr
    # picks WHICH device attribute the value compares against: a builtin
    # column (platform/vendor/model/role/location), "device_template" (the
    # NodeTemplate name), or a custom DeviceAttributeDefinition key. The
    # column keeps its historical name for API compatibility — with the
    # default match_attr of "platform" it behaves exactly as before.
    # A device's explicit config_template_id override always wins; when a
    # device matches several defaults, config_renderer resolves by attribute
    # specificity (custom > location > device_template > model > vendor >
    # role > platform).
    platform_default = Column(String, default="", index=True)
    match_attr = Column(String, default="platform", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

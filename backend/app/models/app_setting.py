from sqlalchemy import Column, String, Text
from app.models.database import Base


class AppSetting(Base):
    """Small JSON key/value store for host-wide settings (e.g. lab_network)."""
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value_json = Column(Text, default="{}")

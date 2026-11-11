from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.models.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)
    display_name = Column(String, default="")
    is_admin = Column(Boolean, default=False)
    is_local = Column(Boolean, default=True)
    auth_source = Column(String, default="local")  # "local" or "ldap"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

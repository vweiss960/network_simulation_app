from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, func
from app.models.database import Base


class SyncQueueItem(Base):
    """Pending sync events for the background worker to process."""
    __tablename__ = "sync_queue"

    id = Column(Integer, primary_key=True, index=True)
    external_system_id = Column(Integer, ForeignKey("external_systems.id"), nullable=False)
    phanero_resource = Column(String, nullable=False)  # "link" or "node"
    phanero_id = Column(Integer, nullable=False)
    operation = Column(String, nullable=False)  # "create", "update", "delete"
    payload_json = Column(Text, default="{}")  # snapshot of the data at enqueue time
    status = Column(String, default="pending")  # pending, processing, success, failed
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    next_attempt_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

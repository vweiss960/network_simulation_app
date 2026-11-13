from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from app.models.database import Base


class AgentAction(Base):
    """Audit trail: every tool call the lab agent makes that changes something
    (topology edits, device configuration, starting devices)."""
    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(String, default="", index=True)
    username = Column(String, default="")
    model = Column(String, default="")
    tool = Column(String, nullable=False)
    input_json = Column(Text, default="{}")
    ok = Column(Boolean, default=True)
    summary = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())

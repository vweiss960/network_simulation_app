from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from app.models.database import Base


class AgentConversation(Base):
    """A saved lab-assistant conversation: what the user saw (ui_json) and the
    model's own message history (native_json) so it can be continued later."""
    __tablename__ = "agent_conversations"

    id = Column(String, primary_key=True)
    topology_id = Column(Integer, ForeignKey("topologies.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String, default="", index=True)
    title = Column(String, default="")
    model = Column(String, default="")
    native_json = Column(Text, default="[]")
    ui_json = Column(Text, default="[]")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

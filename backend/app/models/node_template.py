from sqlalchemy import Column, Integer, String, Text
from app.models.database import Base


class NodeTemplate(Base):
    """A palette entry: a containerlab kind + image + sizing defaults.
    Dragging a template onto the canvas creates a NodeInstance that copies
    these values (the instance can then override image / interface count)."""
    __tablename__ = "node_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    vendor = Column(String, default="")
    model = Column(String, default="")
    kind = Column(String, nullable=False, default="linux")   # containerlab kind (device_kinds.KINDS)
    image_ref = Column(String, default="")                   # docker image; blank = not yet built/loaded
    icon = Column(String, default="router")                  # router | switch | host | workstation | firewall
    interface_count = Column(Integer, default=4)
    min_memory_mb = Column(Integer, default=0)                # 0 = use the kind's default
    min_cpu_cores = Column(Integer, default=0)
    requires_hugepages = Column(Integer, default=0)
    startup_config = Column(Text, default="")                 # default day-0 config body
    env_json = Column(Text, default="{}")                     # extra clab node env vars
    description = Column(Text, default="")
    created_by = Column(String, default="system")

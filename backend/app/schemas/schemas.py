from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class Interface(BaseModel):
    name: str            # device-native (config) name, e.g. GigabitEthernet0/0/0/0
    kernel: str = ""     # containerlab endpoint name, e.g. Gi0-0-0-0


# --- Node Template -----------------------------------------------------------
class NodeTemplateBase(BaseModel):
    name: str
    vendor: str = ""
    model: str = ""
    kind: str = "linux"
    image_ref: str = ""
    icon: str = "router"
    interface_count: int = Field(4, ge=0, le=64)
    min_memory_mb: int = 0
    min_cpu_cores: int = 0
    requires_hugepages: bool = False
    startup_config: str = ""
    description: str = ""


class NodeTemplateCreate(NodeTemplateBase):
    pass


class NodeTemplateUpdate(BaseModel):
    name: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    kind: Optional[str] = None
    image_ref: Optional[str] = None
    icon: Optional[str] = None
    interface_count: Optional[int] = Field(None, ge=0, le=64)
    min_memory_mb: Optional[int] = None
    min_cpu_cores: Optional[int] = None
    requires_hugepages: Optional[bool] = None
    startup_config: Optional[str] = None
    description: Optional[str] = None


class NodeTemplateOut(NodeTemplateBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_by: str = "system"
    # Derived: image present locally in docker (filled by the route).
    image_available: Optional[bool] = None


# --- Topology ----------------------------------------------------------------
class TopologyCreate(BaseModel):
    name: str
    description: str = ""


class TopologyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class TopologyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str = ""
    node_count: int = 0
    link_count: int = 0
    lab_status: str = "idle"


# --- Node --------------------------------------------------------------------
class NodeCreate(BaseModel):
    name: Optional[str] = None           # auto-named from the template when omitted
    template_id: Optional[int] = None
    kind: Optional[str] = None           # required when template_id is omitted
    image_ref: Optional[str] = None
    icon: Optional[str] = None
    interface_count: Optional[int] = Field(None, ge=0, le=64)
    x: float = 0.0
    y: float = 0.0
    startup_config: Optional[str] = None


class NetworkSpec(BaseModel):
    mode: str = "internal"     # internal | access | trunk
    nic: str = ""
    vlan: int = 0
    mgmt: bool = False         # this lab's management network


class NodeUpdate(BaseModel):
    network: Optional[NetworkSpec] = None
    name: Optional[str] = None
    image_ref: Optional[str] = None
    icon: Optional[str] = None
    interface_count: Optional[int] = Field(None, ge=0, le=64)
    x: Optional[float] = None
    y: Optional[float] = None
    startup_config: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    min_memory_mb: Optional[int] = None
    min_cpu_cores: Optional[int] = None
    color: Optional[str] = None
    notes: Optional[str] = None


class NodeOut(BaseModel):
    id: int
    topology_id: int
    template_id: Optional[int] = None
    name: str
    kind: str
    image_ref: str = ""
    icon: str = "router"
    x: float = 0.0
    y: float = 0.0
    interfaces: list[Interface] = []
    mgmt_ipv4: str = ""
    username: str = ""
    startup_config: str = ""
    min_memory_mb: int = 0
    min_cpu_cores: int = 0
    color: str = ""
    notes: str = ""
    network: Optional[NetworkSpec] = None
    network_label: str = ""


class NodePositions(BaseModel):
    positions: dict[int, tuple[float, float]]


# --- Link --------------------------------------------------------------------
class LinkCreate(BaseModel):
    source_node_id: int
    target_node_id: int
    # Omit to auto-pick the next free interface on that node.
    source_interface: Optional[str] = None
    target_interface: Optional[str] = None
    label: str = ""


class LinkUpdate(BaseModel):
    source_interface: Optional[str] = None
    target_interface: Optional[str] = None
    label: Optional[str] = None
    color: Optional[str] = None


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    topology_id: int
    source_node_id: int
    target_node_id: int
    source_interface: str
    target_interface: str
    label: str = ""
    color: str = ""

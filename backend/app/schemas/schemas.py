from pydantic import BaseModel
from typing import Any, List, Optional


class InterfaceSpec(BaseModel):
    name: str
    speed_gbps: float = 10.0
    description: str = ""
    status: str = "free"  # "free" or "reserved"


# --- Node Template ---
class NodeTemplateCreate(BaseModel):
    name: str
    vendor: str
    model: str
    platform: str = ""
    capacity_gbps: float = 100.0
    default_mtbf_hours: float = 50000.0
    default_mttr_hours: float = 4.0
    default_interfaces: list[InterfaceSpec] = []
    role: str = "router"
    # Containerlab integration
    clab_kind: str = ""
    image_ref: str = ""
    min_memory_mb: int = 0
    min_cpu_cores: int = 0
    requires_hugepages: bool = False
    startup_config: str = ""


class NodeTemplateOut(NodeTemplateCreate):
    id: int

    class Config:
        from_attributes = True


# --- Topology ---
class TopologyCreate(BaseModel):
    name: str
    description: str = ""
    parent_topology_id: Optional[int] = None
    is_protected: Optional[bool] = None


class TopologyOut(BaseModel):
    id: int
    name: str
    description: str
    parent_topology_id: Optional[int] = None
    is_protected: bool = False
    # Local (non-customer, non-site) node count; only populated by the list
    # endpoint for the landing-page tiles.
    device_count: int = 0

    class Config:
        from_attributes = True


# --- Node Instance ---
class NodeInstanceCreate(BaseModel):
    template_id: Optional[int] = None
    name: str
    vendor: str = ""
    model: str = ""
    platform: str = ""
    x: float = 0.0
    y: float = 0.0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geo_latitude: Optional[float] = None
    geo_longitude: Optional[float] = None
    interfaces: list[InterfaceSpec] = []
    mtbf_hours: Optional[float] = None
    mttr_hours: Optional[float] = None
    # None means "not provided, fall back to template's capacity_gbps".
    # Matches the mtbf/mttr pattern above. Was previously `float = 100.0`,
    # which made it impossible to tell whether the client actually meant 100
    # vs just didn't send anything — so template capacities were ignored.
    capacity_gbps: Optional[float] = None
    location: str = ""
    prometheus_id: str = ""
    mgmt_ip: str = ""
    is_planned: bool = False
    role: str = "router"
    customer_id: Optional[int] = None
    color: str = ""
    # Config Design — route reflection + custom attributes (all optional)
    is_route_reflector: bool = False
    is_super_route_reflector: bool = False
    rr_peer_ids: list[int] = []
    custom_attrs: dict = {}
    custom_iface_refs: dict = {}


class NodeInstanceUpdate(BaseModel):
    name: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    platform: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geo_latitude: Optional[float] = None
    geo_longitude: Optional[float] = None
    interfaces: Optional[list[InterfaceSpec]] = None
    mtbf_hours: Optional[float] = None
    mttr_hours: Optional[float] = None
    capacity_gbps: Optional[float] = None
    location: Optional[str] = None
    prometheus_id: Optional[str] = None
    mgmt_ip: Optional[str] = None
    is_planned: Optional[bool] = None
    role: Optional[str] = None
    customer_id: Optional[int] = None
    color: Optional[str] = None
    is_route_reflector: Optional[bool] = None
    is_super_route_reflector: Optional[bool] = None
    rr_peer_ids: Optional[list[int]] = None
    custom_attrs: Optional[dict] = None
    custom_iface_refs: Optional[dict] = None


class NodeInstanceOut(BaseModel):
    id: int
    topology_id: int
    template_id: Optional[int]
    name: str
    vendor: str
    model: str
    platform: str
    x: float
    y: float
    latitude: Optional[float]
    longitude: Optional[float]
    geo_latitude: Optional[float] = None
    geo_longitude: Optional[float] = None
    interfaces: list[InterfaceSpec]
    mtbf_hours: Optional[float]
    mttr_hours: Optional[float]
    capacity_gbps: float
    location: str
    prometheus_id: str
    mgmt_ip: str
    is_planned: bool
    role: str
    customer_id: Optional[int]
    color: str = ""
    is_inherited: bool = False
    is_route_reflector: bool = False
    is_super_route_reflector: bool = False
    rr_peer_ids: list[int] = []
    custom_attrs: dict = {}
    custom_iface_refs: dict = {}

    class Config:
        from_attributes = True


# --- Link ---
class LinkCreate(BaseModel):
    source_node_id: int
    target_node_id: int
    source_interface: str = ""
    target_interface: str = ""
    bandwidth_gbps: float = 10.0
    cost: float = 1.0
    # Omit (or set latency_source="unknown") to have the latency estimated.
    latency_ms: Optional[float] = None
    latency_source: Optional[str] = None  # "measured" | "unknown"; inferred when omitted
    transport: str = "terrestrial"        # "terrestrial" | "satellite"
    orbit: str = ""                       # satellite only: "leo" | "meo" | "geo"
    carrier: str = ""
    mtbf_hours: Optional[float] = None
    mttr_hours: Optional[float] = None
    sr_attributes: dict = {}
    circuit_cost: float = 0.0
    circuit_id: str = ""
    link_id_label: str = ""
    source_ip: str = ""
    target_ip: str = ""
    source_ipv6: str = ""
    target_ipv6: str = ""
    is_planned: bool = False
    color: str = ""


class LinkUpdate(BaseModel):
    source_node_id: Optional[int] = None
    target_node_id: Optional[int] = None
    source_interface: Optional[str] = None
    target_interface: Optional[str] = None
    bandwidth_gbps: Optional[float] = None
    cost: Optional[float] = None
    latency_ms: Optional[float] = None
    latency_source: Optional[str] = None  # a latency_ms without a source means "measured"
    transport: Optional[str] = None
    orbit: Optional[str] = None
    carrier: Optional[str] = None
    mtbf_hours: Optional[float] = None
    mttr_hours: Optional[float] = None
    sr_attributes: Optional[dict] = None
    circuit_cost: Optional[float] = None
    circuit_id: Optional[str] = None
    link_id_label: Optional[str] = None
    source_ip: Optional[str] = None
    target_ip: Optional[str] = None
    source_ipv6: Optional[str] = None
    target_ipv6: Optional[str] = None
    is_planned: Optional[bool] = None
    color: Optional[str] = None
    # geo-view fiber route. Either a single line [[lng,lat],...] or multi-part
    # [[[lng,lat],...],...] (preserves gaps between pieces). [] clears.
    geo_path: Optional[List[Any]] = None


class LinkOut(BaseModel):
    id: int
    topology_id: int
    source_node_id: int
    target_node_id: int
    source_interface: str
    target_interface: str
    bandwidth_gbps: float
    cost: float
    latency_ms: float
    latency_source: str = "unknown"
    transport: str = "terrestrial"
    orbit: str = ""
    # Effective latency (services/latency.py): the measured value, else an
    # estimate, else None (unknown).
    latency_effective_ms: Optional[float] = None
    latency_effective_source: str = "unknown"  # measured | estimated | unknown
    latency_method: str = "unknown"
    carrier: str
    mtbf_hours: Optional[float]
    mttr_hours: Optional[float]
    sr_attributes: dict
    circuit_cost: float
    circuit_id: str
    link_id_label: str
    source_ip: str
    target_ip: str
    source_ipv6: str = ""
    target_ipv6: str = ""
    is_planned: bool
    color: str
    geo_path: Optional[List[Any]] = None  # single line or multi-part (see LinkUpdate)
    is_inherited: bool = False

    class Config:
        from_attributes = True


# --- Flow ---
class FlowCreate(BaseModel):
    name: str
    description: str = ""
    source_node_id: int
    dest_node_id: int
    source_interface: str = ""
    dest_interface: str = ""
    bandwidth_gbps: float = 1.0
    routing_behavior: str = "shortest_path"
    explicit_path: list[int] = []
    explicit_link_ids: list[int] = []
    ecmp_enabled: bool = False


class FlowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_node_id: Optional[int] = None
    dest_node_id: Optional[int] = None
    source_interface: Optional[str] = None
    dest_interface: Optional[str] = None
    bandwidth_gbps: Optional[float] = None
    routing_behavior: Optional[str] = None
    explicit_path: Optional[list[int]] = None
    explicit_link_ids: Optional[list[int]] = None
    ecmp_enabled: Optional[bool] = None


class FlowOut(BaseModel):
    id: int
    topology_id: int
    name: str
    description: str
    source_node_id: int
    dest_node_id: int
    source_interface: str
    dest_interface: str
    bandwidth_gbps: float
    routing_behavior: str
    explicit_path: list[int]
    explicit_link_ids: list[int]
    ecmp_enabled: bool
    is_inherited: bool = False
    # Cached Monte Carlo availability report (None until the user runs
    # "Analyze availability" on the flow). Shape matches run_monte_carlo's
    # return plus an analyzed_at timestamp.
    availability_report: Optional[dict] = None

    class Config:
        from_attributes = True


# --- Bulk Flow Import ---
class BulkFlowImport(BaseModel):
    flows: list[FlowCreate]
    # False (default): rows whose name already exists are skipped.
    # True: they update the existing flow instead.
    update_existing: bool = False


# --- Global Settings ---
class GlobalSettingUpdate(BaseModel):
    value: float


class GlobalSettingOut(BaseModel):
    id: int
    key: str
    value: float
    description: str

    class Config:
        from_attributes = True


# --- Simulation Results ---
class LinkUtilization(BaseModel):
    link_id: int
    source_node_id: int
    target_node_id: int
    bandwidth_gbps: float
    used_gbps: float
    utilization_pct: float
    flow_ids: list[int]
    cost_per_gb: Optional[float] = None


class FlowPath(BaseModel):
    flow_id: int
    flow_name: str
    paths: list[list[int]]
    bandwidth_per_path_gbps: list[float]


class FailureScenario(BaseModel):
    failed_element_type: str
    failed_element_id: int
    affected_flows: list[int]
    link_utilizations: list[LinkUtilization]
    spof: bool


class SimulationResult(BaseModel):
    link_utilizations: list[LinkUtilization]
    flow_paths: list[FlowPath]
    failure_scenarios: list[FailureScenario]
    single_points_of_failure: list[dict]


# --- Monte Carlo ---
class MonteCarloRequest(BaseModel):
    a_location: str  # A-end: node name, node ID, or location name
    z_location: str  # Z-end: node name, node ID, or location name
    a_node_ids: Optional[list[int]] = None  # specific node IDs to connect temp A-user to
    z_node_ids: Optional[list[int]] = None  # specific node IDs to connect temp Z-user to
    iterations: int = 10000
    connection_type: str = "both"  # "single", "dual", or "both"
    exclude_node_ids: Optional[list[int]] = None  # maintenance exclusions
    exclude_link_ids: Optional[list[int]] = None  # maintenance exclusions
    include_planned: bool = False  # include planned nodes/links


class MonteCarloPathResult(BaseModel):
    a_node: str
    z_node: str
    a_location: str
    z_location: str
    single_homed_availability: float
    single_homed_downtime_min_yr: float
    dual_homed_availability: Optional[float]
    dual_homed_downtime_min_yr: Optional[float]
    single_homed_paths: int
    dual_homed_paths: int
    failure_breakdown: dict  # counts of node vs link failures causing outage


class MonteCarloResult(BaseModel):
    iterations: int
    a_location: str
    z_location: str
    connection_type: str
    pair_results: list[MonteCarloPathResult]
    overall_single_homed_availability: float
    overall_dual_homed_availability: Optional[float]
    overall_single_downtime_min_yr: float
    overall_dual_downtime_min_yr: Optional[float]


# --- SRLG ---
class SrlgMemberSpec(BaseModel):
    member_type: str  # "node" or "link"
    member_id: int


class SrlgCreate(BaseModel):
    name: str
    description: str = ""
    srlg_type: str = "custom"
    members: list[SrlgMemberSpec] = []


class SrlgUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    srlg_type: Optional[str] = None
    members: Optional[list[SrlgMemberSpec]] = None


class SrlgOut(BaseModel):
    id: int
    topology_id: int
    name: str
    description: str
    srlg_type: str
    members: list[SrlgMemberSpec]

    class Config:
        from_attributes = True


# --- Maintenance Window ---
class MaintenanceWindowRequest(BaseModel):
    failed_node_ids: list[int] = []
    failed_link_ids: list[int] = []
    srlg_ids: list[int] = []
    # Must match the canvas simulation's setting so the baseline is the same
    # network the user is looking at (planned links otherwise absorb reroutes).
    include_planned: bool = False


# --- Network Discovery ---
class DiscoveryRequest(BaseModel):
    seed_ip: str
    os_type: str  # junos, iosxe, iosxr, nxos
    username: str
    password: str
    discovery_method: str = "igp"  # "igp" (isis/ospf from seed only) or "lldp" (recursive crawl)
    igp_protocol: str = "isis"  # "isis" or "ospf" (only used when method=igp)
    max_depth: int = 5  # only used for lldp crawl
    max_devices: int = 50
    timeout_per_device: int = 30
    clear_existing: bool = True  # clear existing nodes/links before discovery


# --- Customer ---
class CustomerCreate(BaseModel):
    name: str
    contact_email: str = ""
    contact_phone: str = ""
    notes: str = ""
    color: str = ""


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None
    color: Optional[str] = None


class CustomerOut(BaseModel):
    id: int
    topology_id: int
    name: str
    contact_email: str
    contact_phone: str
    notes: str
    color: str

    class Config:
        from_attributes = True


# --- Cost Allocation ---
class CostAllocationRequest(BaseModel):
    allocation_model: str = "proportional_used"  # "proportional_used" or "proportional_total"

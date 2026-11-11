import json
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.database import Base


class NodeInstance(Base):
    __tablename__ = "node_instances"
    __table_args__ = (UniqueConstraint("topology_id", "name", name="uq_node_topology_name"),)

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), nullable=False)
    template_id = Column(Integer, ForeignKey("node_templates.id"), nullable=True)
    name = Column(String, nullable=False)
    vendor = Column(String, default="")
    model = Column(String, default="")
    platform = Column(String, default="")
    x = Column(Float, default=0.0)
    y = Column(Float, default=0.0)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    # Geographic (real-world) coordinates for the map view. Independent of
    # x/y and the logical latitude/longitude above (which are an
    # equirectangular projection of the canvas layout). Null until the node
    # is placed on the geographic map.
    geo_latitude = Column(Float, nullable=True)
    geo_longitude = Column(Float, nullable=True)
    interfaces_json = Column(Text, default="[]")
    mtbf_hours = Column(Float, nullable=True)
    mttr_hours = Column(Float, nullable=True)
    capacity_gbps = Column(Float, default=100.0)
    location = Column(String, default="")
    prometheus_id = Column(String, default="")  # Prometheus instance label (IP/hostname)
    mgmt_ip = Column(String, default="")  # Management interface IP address
    is_planned = Column(Integer, default=0)  # 0=active, 1=planned (not yet built)
    role = Column(String, default="router")  # router, customer
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    color = Column(String, default="")  # Custom node color (hex, e.g. #ff0000)
    # Config Design (Phase 1): per-device template override and cached render.
    # If config_template_id is null, the device falls back to the platform
    # default template (matched by platform string).
    config_template_id = Column(Integer, ForeignKey("config_templates.id"), nullable=True)
    # Per-device template-group override. When set, takes precedence over
    # config_template_id and any platform_default — see config_renderer.
    config_template_group_id = Column(Integer, ForeignKey("config_template_groups.id"), nullable=True)
    generated_config = Column(Text, default="")  # Cached render using design (original) interface names
    # Same render but interfaces rewritten to the lab-translated names
    # (e.g. Gi0/0/0/1 → Gi0-0-0-0 for cisco_xrd). Used as the clab
    # startup-config so the deployed lab container boots with config applied.
    generated_lab_config = Column(Text, default="")

    # Route reflection: is_route_reflector marks BGP RR devices; rr_peer_ids
    # is a JSON list of node ids this device peers with (other RRs for an
    # RR-mesh, or the RRs a client connects to for clients).
    is_route_reflector = Column(Integer, default=0)
    # Super RR — top of a two-tier RR hierarchy. Implies is_route_reflector.
    # When at least one node has this, the topology is treated as hierarchical
    # by the renderer: super RRs mesh among themselves, non-super RRs sit
    # between them and the regular nodes.
    is_super_route_reflector = Column(Integer, default=0)
    rr_peer_ids_json = Column(Text, default="[]")
    # Custom (globally-defined) device attributes — key/value dict whose keys
    # are defined in the device_attribute_definitions table. Stored as JSON
    # so adding a new definition doesn't require a schema migration.
    custom_attrs_json = Column(Text, default="{}")
    # Custom interface references — key (defined in interface_attribute_definitions)
    # → interface name (a name from this device's interfaces list). One ref
    # per definition per device, e.g. {"oob_interface": "GigabitEthernet0/0"}.
    custom_iface_refs_json = Column(Text, default="{}")

    topology = relationship("Topology", back_populates="nodes")

    @property
    def interfaces(self):
        return json.loads(self.interfaces_json) if self.interfaces_json else []

    @interfaces.setter
    def interfaces(self, value):
        self.interfaces_json = json.dumps(value)

    @property
    def rr_peer_ids(self) -> list[int]:
        try:
            return list(json.loads(self.rr_peer_ids_json) or [])
        except Exception:
            return []

    @rr_peer_ids.setter
    def rr_peer_ids(self, value: list[int]):
        self.rr_peer_ids_json = json.dumps(list(value or []))

    @property
    def custom_attrs(self) -> dict:
        try:
            return dict(json.loads(self.custom_attrs_json) or {})
        except Exception:
            return {}

    @custom_attrs.setter
    def custom_attrs(self, value: dict):
        self.custom_attrs_json = json.dumps(dict(value or {}))

    @property
    def custom_iface_refs(self) -> dict:
        try:
            return dict(json.loads(self.custom_iface_refs_json) or {})
        except Exception:
            return {}

    @custom_iface_refs.setter
    def custom_iface_refs(self, value: dict):
        self.custom_iface_refs_json = json.dumps(dict(value or {}))

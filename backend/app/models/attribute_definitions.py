"""Global attribute definitions surfaced to every topology.

DeviceAttributeDefinition  — admin-defined extra fields on NodeInstance
                              (e.g. bgp_asn, loopback_ip, region). Values
                              are stored per-device in custom_attrs_json.
                              Supports primitive types and a select list.

InterfaceAttributeDefinition — admin-defined pointer from a device to one
                                of its interfaces (e.g. oob_interface).
                                Values stored in custom_iface_refs_json
                                as {key: interface_name}.

Both tables live globally — definitions apply across every topology so a
template can reference `device.bgp_asn` regardless of which topology the
device is in.
"""
import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from app.models.database import Base


VALID_DEVICE_ATTR_TYPES = {"boolean", "string", "integer", "select"}


class DeviceAttributeDefinition(Base):
    __tablename__ = "device_attribute_definitions"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, unique=True, index=True)  # snake_case; used in templates
    label = Column(String, nullable=False)                          # human-friendly label for the UI
    description = Column(String, default="")
    type = Column(String, nullable=False, default="string")         # boolean|string|integer|select
    # For type=select, a JSON array of allowed option strings. Ignored otherwise.
    options_json = Column(Text, default="[]")
    # Default value (string form, parsed on use). For boolean: "true"/"false";
    # for integer: "0"; for select: an option string.
    default_value = Column(String, default="")
    # If 1, the value of this attribute must be unique across every device in
    # the same topology (and its inheritance chain). Enforced by the node
    # update route. Empty/unset values are exempt — only set values collide.
    unique_per_topology = Column(Integer, default=0)
    # For type=integer + unique_per_topology: the value the "next available"
    # picker starts counting from. Ignored otherwise.
    unique_start_value = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def options(self) -> list[str]:
        try:
            return list(json.loads(self.options_json) or [])
        except Exception:
            return []

    @options.setter
    def options(self, value: list[str]):
        self.options_json = json.dumps(list(value or []))


class InterfaceAttributeDefinition(Base):
    """A named pointer from a device to one of its interfaces.

    Example: key="oob_interface", label="OOB Interface" — each device assigns
    this attribute to a single interface name from its own interface list.
    Templates reference it as `interface.oob_interface` and receive the full
    interface dict (name, ip, neighbor, etc.) — same shape as members of
    the `interfaces` list. (`device.oob_interface` still works as a legacy
    alias unless a device attribute claims the same key.)
    """
    __tablename__ = "interface_attribute_definitions"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, unique=True, index=True)
    label = Column(String, nullable=False)
    description = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

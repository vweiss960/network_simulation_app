import json
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Text
from app.models.database import Base


class ResourceMapping(Base):
    """Maps a Phanero resource type to an external table with field mappings."""
    __tablename__ = "resource_mappings"

    id = Column(Integer, primary_key=True, index=True)
    external_system_id = Column(Integer, ForeignKey("external_systems.id"), nullable=False)
    phanero_resource = Column(String, nullable=False)  # "link" or "node"
    target_table = Column(String, nullable=False)
    primary_key_column = Column(String, default="id")
    # Mapping of {external_column_name: phanero_field_template}
    # Templates are simple string format with {field_name} placeholders
    # e.g. {"bandwidth_mbps": "{bandwidth_gbps}", "circuit_name": "{circuit_id}"}
    field_map_json = Column(Text, default="{}")
    enabled = Column(Boolean, default=True)

    @property
    def field_map(self) -> dict:
        try:
            return json.loads(self.field_map_json or "{}")
        except (ValueError, TypeError):
            return {}

    @field_map.setter
    def field_map(self, value: dict):
        self.field_map_json = json.dumps(value)

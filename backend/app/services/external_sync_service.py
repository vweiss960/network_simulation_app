"""Generic external system sync service.

Designed to work with any CRUD-style REST API.
The service is configured via ExternalSystem + ResourceMapping records.
"""

import json
import re
import requests
from typing import Optional
from sqlalchemy.orm import Session
from app.models.external_system import ExternalSystem
from app.models.resource_mapping import ResourceMapping
from app.models.external_ref import ExternalRef


class ExternalSyncError(Exception):
    """Sync operation failed."""
    pass


def _build_session(system: ExternalSystem) -> requests.Session:
    """Build an authenticated HTTP session for an external system."""
    s = requests.Session()
    s.timeout = 15
    if system.auth_type == "api_key" and system.auth_value:
        s.headers[system.auth_header_name or "X-API-Key"] = system.auth_value
    elif system.auth_type == "bearer" and system.auth_value:
        s.headers["Authorization"] = f"Bearer {system.auth_value}"
    elif system.auth_type == "basic" and system.auth_value and ":" in system.auth_value:
        u, p = system.auth_value.split(":", 1)
        s.auth = (u, p)
    s.headers["Content-Type"] = "application/json"
    # Allow trusting self-signed / internal-CA certs per system
    s.verify = bool(getattr(system, "verify_ssl", True))
    if not s.verify:
        # Suppress the noisy InsecureRequestWarning since the user opted in
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
    return s


def test_connection(system: ExternalSystem) -> dict:
    """Test connectivity by listing tables."""
    s = _build_session(system)
    url = f"{system.base_url.rstrip('/')}/tables"
    try:
        r = s.get(url, timeout=15)
    except requests.ConnectionError as e:
        raise ExternalSyncError(f"Cannot connect to {system.base_url}: {e}")
    except requests.Timeout:
        raise ExternalSyncError(f"Connection to {system.base_url} timed out")
    if r.status_code == 401:
        raise ExternalSyncError("Authentication failed (HTTP 401). Check the API key.")
    if r.status_code != 200:
        raise ExternalSyncError(f"Unexpected response (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


def list_tables(system: ExternalSystem) -> list[dict]:
    """Get all tables/views available in the external system."""
    data = test_connection(system)
    return data.get("tables", [])


def get_table_schema(system: ExternalSystem, table_name: str) -> dict:
    """Get column definitions for a specific table/view."""
    s = _build_session(system)
    url = f"{system.base_url.rstrip('/')}/tables/{table_name}/schema"
    r = s.get(url, timeout=15)
    if r.status_code != 200:
        raise ExternalSyncError(f"Schema fetch failed (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


def render_template(template: str, data: dict) -> str:
    """Render a simple template with {field} placeholders.

    Examples:
        render_template("{name}", {"name": "PE1"}) -> "PE1"
        render_template("{circuit_id}-A", {"circuit_id": "C123"}) -> "C123-A"

    Numeric fields can be referenced directly. The result is a string for
    string fields, but if the template is just `{field}` and the field is
    numeric, returns the numeric value.
    """
    if template is None:
        return None
    # If template is exactly {field}, return the raw value (preserves type)
    m = re.fullmatch(r"\{(\w+)\}", template)
    if m:
        return data.get(m.group(1))
    # Otherwise, render as a string
    def replace(match):
        key = match.group(1)
        val = data.get(key, "")
        return str(val) if val is not None else ""
    return re.sub(r"\{(\w+)\}", replace, template)


def render_payload(field_map: dict, source_data: dict) -> dict:
    """Render the full payload for an external API call from a field map."""
    payload = {}
    for external_col, template in field_map.items():
        rendered = render_template(template, source_data)
        if rendered is not None and rendered != "":
            payload[external_col] = rendered
    return payload


def upsert_record(
    system: ExternalSystem,
    mapping: ResourceMapping,
    source_data: dict,
    existing_pk_value: Optional[str] = None,
) -> str:
    """Create or update a record. Returns the primary key value of the record."""
    s = _build_session(system)
    payload = render_payload(mapping.field_map, source_data)
    url = f"{system.base_url.rstrip('/')}/tables/{mapping.target_table}"

    if existing_pk_value:
        # Update via PUT
        update_payload = {
            "_primaryKey": mapping.primary_key_column,
            "_primaryKeyValue": existing_pk_value,
            **payload,
        }
        r = s.put(url, json=update_payload, timeout=15)
        if r.status_code == 404:
            # Record was deleted on the external side — fall back to create
            return upsert_record(system, mapping, source_data, existing_pk_value=None)
        if r.status_code not in (200, 201):
            raise ExternalSyncError(f"Update failed (HTTP {r.status_code}): {r.text[:300]}")
        result = r.json()
        record = result.get("record", {})
        return str(record.get(mapping.primary_key_column, existing_pk_value))
    else:
        # Create via POST
        r = s.post(url, json=payload, timeout=15)
        if r.status_code not in (200, 201):
            raise ExternalSyncError(f"Create failed (HTTP {r.status_code}): {r.text[:300]}")
        result = r.json()
        record = result.get("record", {})
        pk = record.get(mapping.primary_key_column)
        if pk is None:
            raise ExternalSyncError(f"Create succeeded but no primary key returned. Response: {result}")
        return str(pk)


def delete_record(system: ExternalSystem, mapping: ResourceMapping, pk_value: str) -> bool:
    """Delete a record by primary key."""
    s = _build_session(system)
    url = f"{system.base_url.rstrip('/')}/tables/{mapping.target_table}"
    params = {"primaryKey": mapping.primary_key_column, "value": pk_value}
    r = s.delete(url, params=params, timeout=15)
    if r.status_code == 404:
        return True  # Already gone, consider it success
    if r.status_code not in (200, 204):
        raise ExternalSyncError(f"Delete failed (HTTP {r.status_code}): {r.text[:300]}")
    return True


def process_sync_event(
    db: Session,
    system: ExternalSystem,
    phanero_resource: str,
    phanero_id: int,
    operation: str,
    source_data: dict,
) -> str:
    """Process a single sync event. Returns the external PK value or empty string for deletes."""
    # Find the mapping for this resource type
    mapping = db.query(ResourceMapping).filter(
        ResourceMapping.external_system_id == system.id,
        ResourceMapping.phanero_resource == phanero_resource,
        ResourceMapping.enabled == True,
    ).first()
    if not mapping:
        return ""  # No mapping configured for this resource — silent skip

    # Find existing external ref
    existing_ref = db.query(ExternalRef).filter(
        ExternalRef.external_system_id == system.id,
        ExternalRef.phanero_resource == phanero_resource,
        ExternalRef.phanero_id == phanero_id,
    ).first()

    if operation == "delete":
        if existing_ref:
            delete_record(system, mapping, existing_ref.external_pk_value)
            db.delete(existing_ref)
            db.commit()
        return ""

    # Create or update
    existing_pk = existing_ref.external_pk_value if existing_ref else None
    new_pk = upsert_record(system, mapping, source_data, existing_pk)

    if existing_ref:
        existing_ref.external_pk_value = new_pk
    else:
        db.add(ExternalRef(
            external_system_id=system.id,
            phanero_resource=phanero_resource,
            phanero_id=phanero_id,
            external_pk_value=new_pk,
        ))
    db.commit()
    return new_pk

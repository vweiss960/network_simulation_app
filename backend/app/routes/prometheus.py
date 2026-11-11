"""Prometheus integration API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.prometheus_config import PrometheusConfig
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.services.prometheus_service import PrometheusClient, PrometheusError

router = APIRouter(prefix="/api/topologies/{topology_id}/prometheus", tags=["prometheus"])


# --- Schemas ---

class PrometheusConfigCreate(BaseModel):
    url: str = "http://prometheus:9090"
    auth_type: str = "none"
    username: str = ""
    password: str = ""
    bearer_token: str = ""
    in_octets_metric: str = "ifHCInOctets"
    out_octets_metric: str = "ifHCOutOctets"
    speed_metric: str = "ifHighSpeed"
    interface_label: str = "ifName"
    instance_label: str = "instance"
    rate_interval: str = "5m"
    mode: str = "external"  # "external" or "builtin"


class PrometheusConfigUpdate(BaseModel):
    url: Optional[str] = None
    auth_type: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    bearer_token: Optional[str] = None
    enabled: Optional[bool] = None
    in_octets_metric: Optional[str] = None
    out_octets_metric: Optional[str] = None
    mode: Optional[str] = None


class SnmpCredentialCreate(BaseModel):
    version: int = 3
    community: str = "public"
    security_level: str = "authPriv"
    username: str = ""
    auth_password: str = ""
    auth_protocol: str = "SHA"
    priv_password: str = ""
    priv_protocol: str = "AES"
    speed_metric: Optional[str] = None
    interface_label: Optional[str] = None
    instance_label: Optional[str] = None
    rate_interval: Optional[str] = None


def _get_config(topology_id: int, db: Session) -> PrometheusConfig:
    cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    if not cfg:
        raise HTTPException(404, detail={
            "error": "No Prometheus configuration found for this topology.",
            "help": "Go to the Prometheus panel and configure a Prometheus server URL first.",
        })
    return cfg


def _make_client(cfg: PrometheusConfig) -> PrometheusClient:
    return PrometheusClient(
        url=cfg.url,
        auth_type=cfg.auth_type,
        username=cfg.username,
        password=cfg.password,
        bearer_token=cfg.bearer_token,
    )


def _config_dict(cfg: PrometheusConfig) -> dict:
    return {
        "in_octets_metric": cfg.in_octets_metric,
        "out_octets_metric": cfg.out_octets_metric,
        "speed_metric": cfg.speed_metric,
        "interface_label": cfg.interface_label,
        "instance_label": cfg.instance_label,
        "rate_interval": cfg.rate_interval,
    }


def _serialize_config(cfg: PrometheusConfig) -> dict:
    return {
        "id": cfg.id,
        "topology_id": cfg.topology_id,
        "url": cfg.url,
        "auth_type": cfg.auth_type,
        "username": cfg.username,
        "password": "***" if cfg.password else "",
        "bearer_token": "***" if cfg.bearer_token else "",
        "enabled": cfg.enabled,
        "in_octets_metric": cfg.in_octets_metric,
        "out_octets_metric": cfg.out_octets_metric,
        "speed_metric": cfg.speed_metric,
        "interface_label": cfg.interface_label,
        "instance_label": cfg.instance_label,
        "rate_interval": cfg.rate_interval,
        "mode": cfg.mode or "external",
    }


# --- CRUD ---

@router.get("/config")
def get_prometheus_config(topology_id: int, db: Session = Depends(get_db)):
    """Get Prometheus config for this topology, or 404 if none configured."""
    cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    if not cfg:
        return None
    return _serialize_config(cfg)


@router.post("/config")
def create_prometheus_config(topology_id: int, data: PrometheusConfigCreate, db: Session = Depends(get_db)):
    """Create or replace Prometheus config for a topology."""
    # Force built-in URL
    if data.mode == "builtin":
        data.url = "http://prometheus:9090"
        data.auth_type = "none"

    existing = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    if existing:
        for key, val in data.dict().items():
            setattr(existing, key, val)
        db.commit()
        db.refresh(existing)
    else:
        existing = PrometheusConfig(topology_id=topology_id, **data.dict())
        db.add(existing)
        db.commit()
        db.refresh(existing)

    # Regenerate managed configs if built-in
    if existing.mode == "builtin":
        _trigger_regeneration(db)

    return _serialize_config(existing)


@router.patch("/config")
def update_prometheus_config(topology_id: int, data: PrometheusConfigUpdate, db: Session = Depends(get_db)):
    """Update specific fields of Prometheus config."""
    cfg = _get_config(topology_id, db)
    if data.mode == "builtin":
        data.url = "http://prometheus:9090"
        data.auth_type = "none"
    for key, val in data.dict(exclude_unset=True).items():
        setattr(cfg, key, val)
    db.commit()
    db.refresh(cfg)

    if cfg.mode == "builtin":
        _trigger_regeneration(db)

    return _serialize_config(cfg)


@router.delete("/config")
def delete_prometheus_config(topology_id: int, db: Session = Depends(get_db)):
    """Delete Prometheus config for a topology."""
    cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    was_builtin = cfg and cfg.mode == "builtin"
    if cfg:
        db.delete(cfg)
        db.commit()
    if was_builtin:
        _trigger_regeneration(db)
    return {"ok": True}


def _trigger_regeneration(db: Session):
    """Regenerate managed Prometheus configs from DB state."""
    from app.services.prometheus_config_generator import regenerate_prometheus_configs
    try:
        return regenerate_prometheus_configs(db)
    except Exception:
        return None


# --- SNMP Credentials ---

@router.get("/snmp-credentials")
def get_snmp_credentials(topology_id: int, db: Session = Depends(get_db)):
    from app.models.snmp_credential import SnmpCredential
    cred = db.query(SnmpCredential).filter(SnmpCredential.topology_id == topology_id).first()
    if not cred:
        return None
    return {
        "id": cred.id,
        "topology_id": cred.topology_id,
        "version": cred.version,
        "community": cred.community,
        "security_level": cred.security_level,
        "username": cred.username,
        "auth_password": "***" if cred.auth_password else "",
        "auth_protocol": cred.auth_protocol,
        "priv_password": "***" if cred.priv_password else "",
        "priv_protocol": cred.priv_protocol,
    }


@router.post("/snmp-credentials")
def save_snmp_credentials(topology_id: int, data: SnmpCredentialCreate, db: Session = Depends(get_db)):
    from app.models.snmp_credential import SnmpCredential
    existing = db.query(SnmpCredential).filter(SnmpCredential.topology_id == topology_id).first()
    if existing:
        for key, val in data.dict().items():
            setattr(existing, key, val)
        db.commit()
        db.refresh(existing)
    else:
        existing = SnmpCredential(topology_id=topology_id, name=f"topo_{topology_id}", **data.dict())
        db.add(existing)
        db.commit()
        db.refresh(existing)

    # Regenerate if this topology uses built-in mode
    cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    if cfg and cfg.mode == "builtin":
        _trigger_regeneration(db)

    return {"ok": True, "id": existing.id}


@router.delete("/snmp-credentials")
def delete_snmp_credentials(topology_id: int, db: Session = Depends(get_db)):
    from app.models.snmp_credential import SnmpCredential
    cred = db.query(SnmpCredential).filter(SnmpCredential.topology_id == topology_id).first()
    if cred:
        db.delete(cred)
        db.commit()
    cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    if cfg and cfg.mode == "builtin":
        _trigger_regeneration(db)
    return {"ok": True}


@router.post("/regenerate")
def regenerate_configs(topology_id: int, db: Session = Depends(get_db)):
    """Force regenerate managed Prometheus configs."""
    result = _trigger_regeneration(db)
    if result and result.get("success"):
        return result
    return {"success": False, "error": result.get("error", "Regeneration failed") if result else "Unknown error"}


# --- Test Connection ---

@router.post("/test")
def test_prometheus_connection(topology_id: int, db: Session = Depends(get_db)):
    """Test connectivity to the configured Prometheus server."""
    cfg = _get_config(topology_id, db)
    client = _make_client(cfg)
    try:
        build_info = client.test_connection()
        # Also check if SNMP metrics exist
        targets = client.get_targets()
        snmp_targets = [t for t in targets if t.get("labels", {}).get("job") == "snmp"]
        up_count = sum(1 for t in snmp_targets if t.get("health") == "up")

        return {
            "status": "connected",
            "prometheus_version": build_info.get("version", "unknown"),
            "total_targets": len(targets),
            "snmp_targets": len(snmp_targets),
            "snmp_targets_up": up_count,
            "message": (
                f"Connected to Prometheus {build_info.get('version', '')}. "
                f"Found {len(snmp_targets)} SNMP target(s), {up_count} up."
            ),
        }
    except PrometheusError as e:
        raise HTTPException(400, detail={"error": str(e), "help": _connection_help(cfg)})


def _connection_help(cfg: PrometheusConfig) -> str:
    """Generate contextual help for connection failures."""
    hints = []
    if "localhost" in cfg.url or "127.0.0.1" in cfg.url:
        hints.append(
            "You're using localhost/127.0.0.1, but this app runs in Docker. "
            "Try using 'http://prometheus:9090' (the Docker service name) "
            "or 'http://host.docker.internal:9090' to reach the host machine."
        )
    if cfg.auth_type != "none" and not cfg.username and not cfg.bearer_token:
        hints.append(
            f"Auth type is '{cfg.auth_type}' but no credentials are provided. "
            "Either set credentials or change auth type to 'none'."
        )
    if not cfg.url.startswith("http"):
        hints.append("URL must start with http:// or https://")
    return " | ".join(hints) if hints else "Check the Prometheus URL and ensure the server is reachable."


# --- Discovery ---

@router.post("/discover")
def discover_devices(topology_id: int, db: Session = Depends(get_db)):
    """Discover network devices from Prometheus SNMP targets."""
    cfg = _get_config(topology_id, db)
    client = _make_client(cfg)
    try:
        devices = client.discover_network_devices()
        return {
            "devices": devices,
            "count": len(devices),
            "healthy": sum(1 for d in devices if d["health"] == "up"),
        }
    except PrometheusError as e:
        raise HTTPException(400, detail={"error": str(e)})


class ImportDevicesRequest(BaseModel):
    devices: list[dict]  # Each: {instance, hostname, interfaces: [{name, speed_gbps, description}]}
    clear_existing: bool = False


@router.post("/import-devices")
def import_discovered_devices(topology_id: int, data: ImportDevicesRequest, db: Session = Depends(get_db)):
    """Import discovered devices as nodes in the topology."""
    import json

    if data.clear_existing:
        db.query(Link).filter(Link.topology_id == topology_id).delete()
        db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).delete()
        db.commit()

    created_nodes = []
    x_offset = 0
    for device in data.devices:
        # Build interfaces list
        interfaces = []
        for iface in device.get("interfaces", []):
            interfaces.append({
                "name": iface.get("name", ""),
                "speed_gbps": iface.get("speed_gbps", 1),
                "description": iface.get("description", ""),
                "status": "free",
            })

        node = NodeInstance(
            topology_id=topology_id,
            name=device.get("hostname", device.get("instance", "unknown")),
            vendor="",
            model="",
            platform="",
            x=200 + x_offset * 250,
            y=300,
            interfaces_json=json.dumps(interfaces),
            capacity_gbps=sum(i.get("speed_gbps", 0) for i in interfaces) or 100,
            prometheus_id=device.get("instance", ""),
        )
        db.add(node)
        db.flush()

        created_nodes.append({
            "id": node.id,
            "name": node.name,
            "instance": device.get("instance", ""),
            "interface_count": len(interfaces),
        })
        x_offset += 1

    db.commit()
    return {
        "created": len(created_nodes),
        "nodes": created_nodes,
    }


# --- Enrich existing topology ---

@router.post("/enrich")
def enrich_topology(topology_id: int, db: Session = Depends(get_db)):
    """Enrich existing nodes and links with SNMP data from Prometheus.
    Sets prometheus_id on nodes, updates interface names/speeds,
    fills missing link interfaces via LLDP and IP subnet correlation,
    and populates link IPs. Does NOT create or delete any nodes/links."""
    from app.routes.discovery import _enrich_from_prometheus
    cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
    if not cfg:
        raise HTTPException(400, detail={
            "error": "No Prometheus configuration found for this topology.",
            "help": "Configure Prometheus in the Prometheus panel first.",
        })
    try:
        result = _enrich_from_prometheus(topology_id, db)
        return result
    except Exception as e:
        raise HTTPException(400, detail={"error": f"Enrichment failed: {e}"})


# --- Live Metrics ---

@router.get("/live")
def get_live_metrics(topology_id: int, db: Session = Depends(get_db)):
    """Get current live metrics from Prometheus for all links in the topology."""
    cfg = _get_config(topology_id, db)
    if not cfg.enabled:
        raise HTTPException(400, detail={
            "error": "Live metrics are not enabled for this topology.",
            "help": "Toggle the 'Live' switch in the Prometheus panel to enable live metrics.",
        })

    client = _make_client(cfg)
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).all()

    node_dicts = []
    for n in nodes:
        node_dicts.append({
            "id": n.id,
            "name": n.name,
            "prometheus_id": n.prometheus_id if hasattr(n, 'prometheus_id') and n.prometheus_id else "",
            "interfaces": n.interfaces,
        })

    link_dicts = []
    for l in links:
        link_dicts.append({
            "id": l.id,
            "source_node_id": l.source_node_id,
            "target_node_id": l.target_node_id,
            "source_interface": l.source_interface or "",
            "target_interface": l.target_interface or "",
            "bandwidth_gbps": l.bandwidth_gbps,
        })

    # Check if any nodes have prometheus_id set
    mapped_nodes = [n for n in node_dicts if n["prometheus_id"]]
    if not mapped_nodes:
        raise HTTPException(400, detail={
            "error": "No nodes have a Prometheus ID configured.",
            "help": (
                "Edit each node and set the 'Prometheus ID' field to the device's "
                "IP address or hostname as it appears in Prometheus (the 'instance' label). "
                "You can find these values on the Prometheus targets page."
            ),
        })

    try:
        result = client.get_live_metrics(node_dicts, link_dicts, _config_dict(cfg))
        result["mapped_nodes"] = len(mapped_nodes)
        result["total_nodes"] = len(node_dicts)
        return result
    except PrometheusError as e:
        raise HTTPException(400, detail={"error": str(e)})


@router.get("/historical")
def get_historical_metrics(topology_id: int, days: int = 10, mode: str = "avg",
                            db: Session = Depends(get_db)):
    """Get historical avg or max utilization over the last N days."""
    cfg = _get_config(topology_id, db)
    if not cfg.enabled:
        raise HTTPException(400, detail={
            "error": "Live metrics are not enabled.",
            "help": "Enable the Prometheus live toggle first.",
        })

    client = _make_client(cfg)
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).all()

    node_dicts = [{
        "id": n.id, "name": n.name,
        "prometheus_id": n.prometheus_id if hasattr(n, 'prometheus_id') and n.prometheus_id else "",
    } for n in nodes]

    link_dicts = [{
        "id": l.id, "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
        "source_interface": l.source_interface or "", "target_interface": l.target_interface or "",
        "bandwidth_gbps": l.bandwidth_gbps,
    } for l in links]

    try:
        return client.get_historical_metrics(node_dicts, link_dicts, _config_dict(cfg), days, mode)
    except PrometheusError as e:
        raise HTTPException(400, detail={"error": str(e)})

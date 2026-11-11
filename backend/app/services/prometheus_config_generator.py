"""Generates prometheus.yml and snmp.yml from database state for built-in mode."""

import os
import yaml
import requests
from sqlalchemy.orm import Session
from app.models.prometheus_config import PrometheusConfig
from app.models.snmp_credential import SnmpCredential
from app.models.node_instance import NodeInstance
from app.models.topology import Topology

CONFIG_DIR = os.environ.get("PROMETHEUS_CONFIG_DIR", "/etc/prometheus_managed")
PROMETHEUS_URL = "http://prometheus:9090"


def _lab_telemetry_enabled() -> bool:
    def _on(n: str) -> bool:
        return os.environ.get(n, "").lower() in ("1", "true", "yes", "on")
    return _on("PHANERO_LAB_ENABLED") and _on("PHANERO_LAB_TELEMETRY_ENABLED")


def _gnmic_lab_scrape_jobs() -> list:
    """Scrape job for the lab gNMI telemetry collector (add-on), or [] when off.
    The gnmic sidecar publishes its Prometheus output on the host, so Prometheus
    (on the compose bridge) reaches it via host.docker.internal."""
    if not _lab_telemetry_enabled():
        return []
    port = os.environ.get("PHANERO_GNMIC_PROM_PORT", "9273")
    return [{
        "job_name": "gnmic_lab",
        "scrape_interval": "30s",
        "static_configs": [{"targets": [f"host.docker.internal:{port}"]}],
    }]

# Standard IF-MIB + LLDP + IP module definition (shared across all deployments)
SNMP_MODULE_IF_MIB = {
    "walk": [
        "1.3.6.1.2.1.1.5",       # sysName
        "1.3.6.1.2.1.2.2",       # ifTable
        "1.3.6.1.2.1.31.1.1",    # ifXTable (64-bit counters)
        "1.0.8802.1.1.2.1.4",    # lldpRemTable
        "1.3.6.1.2.1.4.20",      # ipAddrTable
    ],
    "get": [
        "1.3.6.1.2.1.2.1.0",     # ifNumber
    ],
    "metrics": [
        {"name": "sysName", "oid": "1.3.6.1.2.1.1.5", "type": "DisplayString", "help": "System name",
         "indexes": []},
        {"name": "ifNumber", "oid": "1.3.6.1.2.1.2.1", "type": "gauge", "help": "Number of interfaces",
         "indexes": []},
        {"name": "ifDescr", "oid": "1.3.6.1.2.1.2.2.1.2", "type": "DisplayString",
         "help": "Interface description",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifType", "oid": "1.3.6.1.2.1.2.2.1.3", "type": "gauge",
         "help": "Interface type",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifAdminStatus", "oid": "1.3.6.1.2.1.2.2.1.7", "type": "gauge",
         "help": "Admin status (1=up, 2=down, 3=testing)",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifOperStatus", "oid": "1.3.6.1.2.1.2.2.1.8", "type": "gauge",
         "help": "Operational status (1=up, 2=down, 3=testing)",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifSpeed", "oid": "1.3.6.1.2.1.2.2.1.5", "type": "gauge",
         "help": "Interface speed in bits per second",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifInErrors", "oid": "1.3.6.1.2.1.2.2.1.14", "type": "counter",
         "help": "Input errors",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifInDiscards", "oid": "1.3.6.1.2.1.2.2.1.13", "type": "counter",
         "help": "Input discards",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifOutErrors", "oid": "1.3.6.1.2.1.2.2.1.20", "type": "counter",
         "help": "Output errors",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifOutDiscards", "oid": "1.3.6.1.2.1.2.2.1.19", "type": "counter",
         "help": "Output discards",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifName", "oid": "1.3.6.1.2.1.31.1.1.1.1", "type": "DisplayString",
         "help": "Interface name (short form)",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifHCInOctets", "oid": "1.3.6.1.2.1.31.1.1.1.6", "type": "counter",
         "help": "Total octets received (64-bit)",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifHCOutOctets", "oid": "1.3.6.1.2.1.31.1.1.1.10", "type": "counter",
         "help": "Total octets transmitted (64-bit)",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifHighSpeed", "oid": "1.3.6.1.2.1.31.1.1.1.15", "type": "gauge",
         "help": "Interface speed in Mbps",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        {"name": "ifAlias", "oid": "1.3.6.1.2.1.31.1.1.1.18", "type": "DisplayString",
         "help": "Interface alias (description)",
         "indexes": [{"labelname": "ifIndex", "type": "gauge"}]},
        # LLDP
        {"name": "lldpRemSysName", "oid": "1.0.8802.1.1.2.1.4.1.1.9", "type": "DisplayString",
         "help": "LLDP remote system name",
         "indexes": [
             {"labelname": "lldpRemTimeMark", "type": "gauge"},
             {"labelname": "lldpRemLocalPortNum", "type": "gauge"},
             {"labelname": "lldpRemIndex", "type": "gauge"},
         ]},
        {"name": "lldpRemPortId", "oid": "1.0.8802.1.1.2.1.4.1.1.7", "type": "DisplayString",
         "help": "LLDP remote port ID",
         "indexes": [
             {"labelname": "lldpRemTimeMark", "type": "gauge"},
             {"labelname": "lldpRemLocalPortNum", "type": "gauge"},
             {"labelname": "lldpRemIndex", "type": "gauge"},
         ]},
        {"name": "lldpRemPortDesc", "oid": "1.0.8802.1.1.2.1.4.1.1.8", "type": "DisplayString",
         "help": "LLDP remote port description",
         "indexes": [
             {"labelname": "lldpRemTimeMark", "type": "gauge"},
             {"labelname": "lldpRemLocalPortNum", "type": "gauge"},
             {"labelname": "lldpRemIndex", "type": "gauge"},
         ]},
        # IP address table
        {"name": "ipAdEntIfIndex", "oid": "1.3.6.1.2.1.4.20.1.2", "type": "gauge",
         "help": "Interface index for this IP",
         "indexes": [{"labelname": "ipAdEntAddr", "type": "InetAddress"}]},
        {"name": "ipAdEntNetMask", "oid": "1.3.6.1.2.1.4.20.1.3", "type": "DisplayString",
         "help": "Subnet mask",
         "indexes": [{"labelname": "ipAdEntAddr", "type": "InetAddress"}]},
    ],
}


def regenerate_prometheus_configs(db: Session) -> dict:
    """Regenerate prometheus.yml and snmp.yml from all built-in topologies."""
    if not os.path.isdir(CONFIG_DIR):
        return {"success": False, "error": f"Config directory {CONFIG_DIR} does not exist. Is the Docker volume mounted?"}

    # Get all built-in topology configs
    builtin_configs = db.query(PrometheusConfig).filter(PrometheusConfig.mode == "builtin").all()
    if not builtin_configs:
        # Write minimal default configs
        _write_default_configs()
        _reload_prometheus()
        return {"success": True, "targets": 0, "message": "No built-in topologies. Wrote default configs."}

    topology_ids = [c.topology_id for c in builtin_configs]

    # Get all SNMP credentials for built-in topologies
    credentials = db.query(SnmpCredential).filter(SnmpCredential.topology_id.in_(topology_ids)).all()
    cred_by_topo = {c.topology_id: c for c in credentials}

    # Get all nodes with mgmt_ip for built-in topologies
    nodes = db.query(NodeInstance).filter(
        NodeInstance.topology_id.in_(topology_ids),
    ).all()

    # Get topology names for labels
    topos = db.query(Topology).filter(Topology.id.in_(topology_ids)).all()
    topo_names = {t.id: t.name for t in topos}

    # Group nodes by their topology's auth block
    # Each unique SNMP credential becomes a separate scrape job
    jobs_by_auth = {}  # auth_block_name -> {targets: [...], credential: SnmpCredential}
    no_cred_nodes = []

    for node in nodes:
        ip = node.mgmt_ip or node.prometheus_id
        if not ip:
            continue

        cred = cred_by_topo.get(node.topology_id)
        if not cred:
            no_cred_nodes.append(node)
            continue

        auth_name = f"topo_{node.topology_id}_{cred.name}"
        if auth_name not in jobs_by_auth:
            jobs_by_auth[auth_name] = {"targets": [], "credential": cred}

        jobs_by_auth[auth_name]["targets"].append({
            "ip": ip,
            "device_name": node.name,
            "topology_id": node.topology_id,
            "topology_name": topo_names.get(node.topology_id, ""),
        })

    # Generate prometheus.yml
    prom_yml = _build_prometheus_yml(jobs_by_auth)
    prom_path = os.path.join(CONFIG_DIR, "prometheus.yml")
    with open(prom_path, "w") as f:
        f.write(prom_yml)

    # Generate snmp.yml
    unique_creds = {f"topo_{c.topology_id}_{c.name}": c for c in credentials}
    snmp_yml = _build_snmp_yml(unique_creds)
    snmp_path = os.path.join(CONFIG_DIR, "snmp.yml")
    with open(snmp_path, "w") as f:
        f.write(snmp_yml)

    # Reload Prometheus
    reload_ok = _reload_prometheus()

    total_targets = sum(len(j["targets"]) for j in jobs_by_auth.values())

    return {
        "success": True,
        "targets": total_targets,
        "auth_blocks": len(unique_creds),
        "skipped_no_cred": len(no_cred_nodes),
        "skipped_no_ip": len([n for n in nodes if not (n.mgmt_ip or n.prometheus_id)]),
        "prometheus_reloaded": reload_ok,
        "message": f"Generated configs with {total_targets} target(s) across {len(jobs_by_auth)} scrape job(s).",
    }


def _build_prometheus_yml(jobs_by_auth: dict) -> str:
    """Build prometheus.yml content."""
    config = {
        "global": {
            "scrape_interval": "60s",
            "evaluation_interval": "60s",
        },
        "scrape_configs": [],
    }

    for auth_name, job_data in jobs_by_auth.items():
        targets = job_data["targets"]
        static_configs = []
        for t in targets:
            static_configs.append({
                "targets": [t["ip"]],
                "labels": {
                    "device_name": t["device_name"],
                    "topology_id": str(t["topology_id"]),
                    "topology_name": t["topology_name"],
                },
            })

        scrape_job = {
            "job_name": f"snmp_{auth_name}",
            "scrape_interval": "60s",
            "scrape_timeout": "30s",
            "static_configs": static_configs,
            "metrics_path": "/snmp",
            "params": {
                "auth": [auth_name],
                "module": ["if_mib"],
            },
            "relabel_configs": [
                {"source_labels": ["__address__"], "target_label": "__param_target"},
                {"source_labels": ["__param_target"], "target_label": "instance"},
                {"target_label": "__address__", "replacement": "snmp-exporter:9116"},
            ],
        }
        config["scrape_configs"].append(scrape_job)

    # SNMP exporter self-scrape
    config["scrape_configs"].append({
        "job_name": "snmp-exporter",
        "static_configs": [{"targets": ["snmp-exporter:9116"]}],
    })

    # Lab gNMI telemetry collector (add-on), when enabled.
    config["scrape_configs"].extend(_gnmic_lab_scrape_jobs())

    return yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _build_snmp_yml(credentials: dict) -> str:
    """Build snmp.yml content with auth blocks and IF-MIB module."""
    auths = {}
    for auth_name, cred in credentials.items():
        if cred.version == 2:
            auths[auth_name] = {
                "community": cred.community or "public",
                "version": 2,
            }
        else:
            auth_block = {
                "community": "",
                "security_level": cred.security_level or "authPriv",
                "username": cred.username or "",
                "password": cred.auth_password or "",
                "auth_protocol": cred.auth_protocol or "SHA",
                "priv_protocol": cred.priv_protocol or "AES",
                "priv_password": cred.priv_password or "",
                "version": 3,
            }
            auths[auth_name] = auth_block

    # Build the module with the standard OIDs
    module = {
        "walk": SNMP_MODULE_IF_MIB["walk"],
        "get": SNMP_MODULE_IF_MIB["get"],
        "metrics": SNMP_MODULE_IF_MIB["metrics"],
    }

    config = {
        "auths": auths,
        "modules": {"if_mib": module},
    }

    return yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _write_default_configs():
    """Write minimal default configs when no built-in topologies exist."""
    prom_path = os.path.join(CONFIG_DIR, "prometheus.yml")
    snmp_path = os.path.join(CONFIG_DIR, "snmp.yml")

    default_prom = {
        "global": {"scrape_interval": "60s", "evaluation_interval": "60s"},
        "scrape_configs": [
            {"job_name": "snmp-exporter", "static_configs": [{"targets": ["snmp-exporter:9116"]}]},
            *_gnmic_lab_scrape_jobs(),
        ],
    }

    default_snmp = {
        "auths": {
            "default_v2": {"community": "public", "version": 2},
        },
        "modules": {"if_mib": {
            "walk": SNMP_MODULE_IF_MIB["walk"],
            "get": SNMP_MODULE_IF_MIB["get"],
            "metrics": SNMP_MODULE_IF_MIB["metrics"],
        }},
    }

    with open(prom_path, "w") as f:
        yaml.dump(default_prom, f, default_flow_style=False, sort_keys=False)

    with open(snmp_path, "w") as f:
        yaml.dump(default_snmp, f, default_flow_style=False, sort_keys=False)


def _reload_prometheus() -> bool:
    """Reload Prometheus config via the lifecycle API.

    The backend runs with network_mode: host (for containerlab), where the
    `prometheus` compose DNS name doesn't resolve — only the host-published
    :9090 does. So try the service name first, then fall back to localhost.
    """
    for url in (PROMETHEUS_URL, "http://localhost:9090"):
        try:
            r = requests.post(f"{url}/-/reload", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            continue
    return False


def ensure_default_configs():
    """Called at startup — ensure config files exist."""
    if not os.path.isdir(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, exist_ok=True)

    prom_path = os.path.join(CONFIG_DIR, "prometheus.yml")
    snmp_path = os.path.join(CONFIG_DIR, "snmp.yml")

    if not os.path.exists(prom_path) or not os.path.exists(snmp_path):
        _write_default_configs()

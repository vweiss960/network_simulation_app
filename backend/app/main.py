import json
import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.models.database import init_db, SessionLocal
from app.models.global_settings import GlobalSettings, DEFAULT_SETTINGS
from app.models.node_template import NodeTemplate
from app.models.user import User
# Imported for the side effect of registering the table with SQLAlchemy's
# Base.metadata so init_db() creates it. The model itself is used via
# the qcow_images service.
from app.models.device_image import DeviceImage  # noqa: F401
# Registers the topology_views table with Base.metadata so init_db() creates it.
from app.models.topology_view import TopologyView  # noqa: F401
# Registers the patch_panels table (patch panel documentation module).
from app.models.patch_panel import PatchPanel  # noqa: F401
from app.models.site_template import SiteTemplate  # noqa: F401
# Registers the lab_gnmi_subscriptions table (lab telemetry add-on).
from app.models.lab_gnmi_subscription import LabGnmiSubscription  # noqa: F401
# Registers the service module tables (services / templates / variables).
from app.models.service import Service  # noqa: F401
from app.models.service_template import ServiceTemplate  # noqa: F401
from app.models.service_variable import ServiceVariable  # noqa: F401
from app.models.interface_metric import InterfaceMetric  # noqa: F401
from app.routes import topologies, node_templates, nodes, links, flows, simulation, settings, chat, discovery, srlgs, prometheus, customers, cost_allocation, maintenance_presets, integrations, topology_versions, lab, lab_console, config_templates, config_template_groups, config_ai, attribute_definitions, topology_views, map_data, edit_session, services, service_ai, patch_panels, actual_utilization, site_templates
from app.routes.edit_session import require_edit_session
from app.routes import auth as auth_routes
from app.auth.dependencies import get_current_user
from app.auth.service import hash_password
from app.auth.config import ADMIN_USERNAME, ADMIN_PASSWORD

app = FastAPI(
    title="Phanero API",
    version="1.0.0",
    description="Network modeling, simulation, and cost allocation API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip-compress responses larger than 1 KB. The big list endpoints
# (/nodes, /links, /flows) ship JSON that compresses 5-10x, turning
# multi-second downloads on slow links into sub-second ones. Browsers
# decompress transparently via the Accept-Encoding negotiation, so this
# requires no frontend change. The minimum_size threshold avoids the
# CPU overhead of compressing tiny payloads (login ACKs, settings rows).
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Auth routes (unprotected — login, etc.)
app.include_router(auth_routes.router)

# All other routes require authentication
auth_dep = [Depends(get_current_user)]
# Optional feature modules are each gated behind an env var (default OFF). When
# a module is disabled its router is not registered (its endpoints 404) and the
# frontend hides the menu via /api/features. Mirrors the original Lab pattern.
def _module_enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")

LAB_ENABLED = _module_enabled("PHANERO_LAB_ENABLED")
# Lab telemetry add-on — only effective when LAB is also on, so it's
# absent from the standard suite (and from a plain lab) unless explicitly enabled.
LAB_TELEMETRY_ENABLED = LAB_ENABLED and _module_enabled("PHANERO_LAB_TELEMETRY_ENABLED")
# Service module (config deployment) — pushes rendered config to lab nodes, so
# it requires LAB. Same dependency pattern as the telemetry add-on above.
SERVICE_ENABLED = LAB_ENABLED and _module_enabled("PHANERO_SERVICE_ENABLED")
CONFIG_DESIGN_ENABLED = _module_enabled("PHANERO_CONFIG_DESIGN_ENABLED")
GEO_ENABLED = _module_enabled("PHANERO_GEO_ENABLED")
AI_ENABLED = _module_enabled("PHANERO_AI_ENABLED")
DISCOVERY_ENABLED = _module_enabled("PHANERO_DISCOVERY_ENABLED")
PROMETHEUS_ENABLED = _module_enabled("PHANERO_PROMETHEUS_ENABLED")
COST_ENABLED = _module_enabled("PHANERO_COST_ENABLED")
INTEGRATIONS_ENABLED = _module_enabled("PHANERO_INTEGRATIONS_ENABLED")
# Patch panels — documentation of how links reach devices via panels.
PATCH_PANELS_ENABLED = _module_enabled("PHANERO_PATCH_PANELS_ENABLED")

# Always-on core routers.
# Content routers carry the protected-topology guard: a mutating request to a
# protected topology is rejected unless the caller holds an active edit session.
edit_dep = auth_dep + [Depends(require_edit_session)]
app.include_router(topologies.router, dependencies=auth_dep)
app.include_router(node_templates.router, dependencies=auth_dep)  # always on; the Templates menu is UI-gated behind Lab
app.include_router(site_templates.router, dependencies=auth_dep)
app.include_router(site_templates.instantiate_router, dependencies=edit_dep)
app.include_router(nodes.router, dependencies=edit_dep)
app.include_router(links.router, dependencies=edit_dep)
app.include_router(flows.router, dependencies=edit_dep)
app.include_router(simulation.router, dependencies=auth_dep)
app.include_router(settings.router, dependencies=auth_dep)
app.include_router(srlgs.router, dependencies=edit_dep)
app.include_router(customers.router, dependencies=auth_dep)
app.include_router(maintenance_presets.router, dependencies=auth_dep)
app.include_router(topology_versions.router, dependencies=auth_dep)
app.include_router(topology_views.router, dependencies=edit_dep)
app.include_router(edit_session.router, dependencies=auth_dep)
# Measured link utilization (CSV import now; a live source later). Measurement
# data, not design, so no edit-session guard.
app.include_router(actual_utilization.router, dependencies=auth_dep)

# Optional modules.
if GEO_ENABLED:
    app.include_router(map_data.router, dependencies=auth_dep)
if AI_ENABLED:
    app.include_router(chat.router, dependencies=auth_dep)
if DISCOVERY_ENABLED:
    app.include_router(discovery.router, dependencies=auth_dep)
if PROMETHEUS_ENABLED:
    app.include_router(prometheus.router, dependencies=auth_dep)
if COST_ENABLED:
    app.include_router(cost_allocation.router, dependencies=auth_dep)
if INTEGRATIONS_ENABLED:
    app.include_router(integrations.router, dependencies=auth_dep)
if PATCH_PANELS_ENABLED:
    # edit_dep: panel edits respect protected-topology edit sessions.
    app.include_router(patch_panels.router, dependencies=edit_dep)

# Lab Mode (containerlab integration). When disabled, routers aren't registered
# and the frontend hides the toggle (and the Device Templates menu) to match.
if LAB_ENABLED:
    app.include_router(lab.router, dependencies=auth_dep)
    app.include_router(lab.images_router, dependencies=auth_dep)
    # Lab console WebSockets — auth via ?token=<jwt> in the URL (no Depends).
    app.include_router(lab_console.router)

# Config Design (Phase 1: templates + per-platform defaults + render).
if CONFIG_DESIGN_ENABLED:
    app.include_router(config_templates.router, dependencies=auth_dep)
    app.include_router(config_template_groups.router, dependencies=auth_dep)
    app.include_router(config_ai.router, dependencies=auth_dep)
    app.include_router(attribute_definitions.router, dependencies=auth_dep)

# Service module (config deployment). Requires LAB (targets are lab nodes /
# topology mgmt IPs); registered only when both flags are set.
if SERVICE_ENABLED:
    app.include_router(services.router, dependencies=auth_dep)
    app.include_router(service_ai.router, dependencies=auth_dep)


def _migrate_db():
    """Add columns that may be missing from older databases."""
    from sqlalchemy import text, inspect
    db = SessionLocal()
    try:
        inspector = inspect(db.bind)
        # Add missing columns to flows table
        if 'flows' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('flows')]
            for col_name in ['description', 'source_interface', 'dest_interface']:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE flows ADD COLUMN {col_name} VARCHAR DEFAULT ''"))
                    db.commit()
            if 'availability_report_json' not in columns:
                db.execute(text("ALTER TABLE flows ADD COLUMN availability_report_json TEXT DEFAULT ''"))
                db.commit()
        # Add source_ip/target_ip/circuit_cost to links
        if 'links' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('links')]
            for col_name in ['source_ip', 'target_ip', 'source_ipv6', 'target_ipv6']:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE links ADD COLUMN {col_name} VARCHAR DEFAULT ''"))
                    db.commit()
            for col_name, col_type, col_default in [
                ('circuit_cost', 'FLOAT', '0.0'),
                ('circuit_id', 'VARCHAR', "''"),
                ('link_id_label', 'VARCHAR', "''"),
            ]:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE links ADD COLUMN {col_name} {col_type} DEFAULT {col_default}"))
                    db.commit()
            if 'is_planned' not in [c['name'] for c in inspector.get_columns('links')]:
                db.execute(text("ALTER TABLE links ADD COLUMN is_planned INTEGER DEFAULT 0"))
                db.commit()
            if 'geo_path_json' not in [c['name'] for c in inspector.get_columns('links')]:
                db.execute(text("ALTER TABLE links ADD COLUMN geo_path_json TEXT DEFAULT ''"))
                db.commit()
            if 'latency_source' not in [c['name'] for c in inspector.get_columns('links')]:
                db.execute(text("ALTER TABLE links ADD COLUMN latency_source TEXT DEFAULT 'unknown'"))
                # Existing links: 1.0 was the new-link default, so treat it as
                # "never set"; any other value was entered/imported → measured.
                db.execute(text("UPDATE links SET latency_source = CASE WHEN latency_ms = 1.0 OR latency_ms IS NULL OR latency_ms <= 0 THEN 'unknown' ELSE 'measured' END"))
                db.commit()
            if 'transport' not in [c['name'] for c in inspector.get_columns('links')]:
                db.execute(text("ALTER TABLE links ADD COLUMN transport TEXT DEFAULT 'terrestrial'"))
                db.commit()
            if 'orbit' not in [c['name'] for c in inspector.get_columns('links')]:
                db.execute(text("ALTER TABLE links ADD COLUMN orbit TEXT DEFAULT ''"))
                db.commit()
        # Default-view exclusion set on topologies (Default view = all nodes minus these).
        if 'topologies' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('topologies')]
            if 'default_view_excluded_node_ids_json' not in columns:
                db.execute(text("ALTER TABLE topologies ADD COLUMN default_view_excluded_node_ids_json TEXT DEFAULT '[]'"))
                db.commit()
        # Per-view layer color.
        if 'topology_views' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('topology_views')]
            if 'color' not in columns:
                db.execute(text("ALTER TABLE topology_views ADD COLUMN color VARCHAR DEFAULT ''"))
                db.commit()
        # Protected topologies + edit-session bookkeeping.
        if 'topologies' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('topologies')]
            for col_name, ddl in [
                ('is_protected', "INTEGER DEFAULT 0"),
                ('edit_session_user_id', "INTEGER"),
                ('edit_session_username', "VARCHAR DEFAULT ''"),
                ('edit_session_expires_at', "DATETIME"),
                ('edit_session_baseline_version_id', "INTEGER"),
            ]:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE topologies ADD COLUMN {col_name} {ddl}"))
                    db.commit()
        # Version authorship + baseline flag (protected-topology commits/baselines).
        if 'topology_versions' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('topology_versions')]
            for col_name, ddl in [('created_by', "VARCHAR DEFAULT ''"), ('is_baseline', "INTEGER DEFAULT 0")]:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE topology_versions ADD COLUMN {col_name} {ddl}"))
                    db.commit()
        # Add customer_id to flows
        if 'flows' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('flows')]
            if 'customer_id' not in columns:
                db.execute(text("ALTER TABLE flows ADD COLUMN customer_id INTEGER"))
                db.commit()
        # Add prometheus_id to node_instances
        if 'node_instances' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('node_instances')]
            for col_name in ['prometheus_id', 'mgmt_ip']:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE node_instances ADD COLUMN {col_name} VARCHAR DEFAULT ''"))
                    db.commit()
            if 'is_planned' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN is_planned INTEGER DEFAULT 0"))
                db.commit()
        # Add role to node_instances
        if 'node_instances' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('node_instances')]
            if 'role' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN role VARCHAR DEFAULT 'router'"))
                db.commit()
        # Add role to node_templates
        if 'node_templates' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('node_templates')]
            if 'role' not in columns:
                db.execute(text("ALTER TABLE node_templates ADD COLUMN role VARCHAR DEFAULT 'router'"))
                db.commit()
        # Add customer_id to node_instances
        if 'node_instances' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('node_instances')]
            if 'customer_id' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN customer_id INTEGER"))
                db.commit()
        # Add color to links
        if 'links' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('links')]
            if 'color' not in columns:
                db.execute(text("ALTER TABLE links ADD COLUMN color VARCHAR DEFAULT ''"))
                db.commit()
        # Add color to customers
        if 'customers' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('customers')]
            if 'color' not in columns:
                db.execute(text("ALTER TABLE customers ADD COLUMN color VARCHAR DEFAULT ''"))
                db.commit()
        # Migrate role 'site' -> 'customer'
        if 'node_instances' in inspector.get_table_names():
            db.execute(text("UPDATE node_instances SET role = 'customer' WHERE role = 'site'"))
            db.commit()
        if 'node_templates' in inspector.get_table_names():
            db.execute(text("UPDATE node_templates SET role = 'customer' WHERE role = 'site'"))
            db.commit()
        # Deduplicate node names and add unique constraint
        if 'node_instances' in inspector.get_table_names():
            indexes = inspector.get_indexes('node_instances')
            has_uq = any('uq_node_topology_name' in (idx.get('name') or '') for idx in indexes)
            if not has_uq:
                # Rename any duplicates before adding constraint
                dupes = db.execute(text(
                    "SELECT topology_id, name, COUNT(*) FROM node_instances "
                    "GROUP BY topology_id, name HAVING COUNT(*) > 1"
                )).fetchall()
                all_names_by_topo: dict[int, set] = {}
                for topo_id, name, cnt in dupes:
                    if topo_id not in all_names_by_topo:
                        existing = db.execute(text(
                            "SELECT name FROM node_instances WHERE topology_id = :tid"
                        ), {"tid": topo_id}).fetchall()
                        all_names_by_topo[topo_id] = {r[0] for r in existing}
                    used = all_names_by_topo[topo_id]
                    rows = db.execute(text(
                        "SELECT id FROM node_instances WHERE topology_id = :tid AND name = :n ORDER BY id"
                    ), {"tid": topo_id, "n": name}).fetchall()
                    for row in rows[1:]:
                        suffix = 2
                        while f"{name}-{suffix}" in used:
                            suffix += 1
                        new_name = f"{name}-{suffix}"
                        used.add(new_name)
                        db.execute(text(
                            "UPDATE node_instances SET name = :new_name WHERE id = :nid"
                        ), {"new_name": new_name, "nid": row[0]})
                db.commit()
                try:
                    db.execute(text(
                        "CREATE UNIQUE INDEX uq_node_topology_name ON node_instances (topology_id, name)"
                    ))
                    db.commit()
                except Exception:
                    db.rollback()  # index may already exist from create_all
        # Add mode to prometheus_configs
        if 'prometheus_configs' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('prometheus_configs')]
            if 'mode' not in columns:
                db.execute(text("ALTER TABLE prometheus_configs ADD COLUMN mode VARCHAR DEFAULT 'external'"))
                db.commit()
        # Add verify_ssl to external_systems
        if 'external_systems' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('external_systems')]
            if 'verify_ssl' not in columns:
                db.execute(text("ALTER TABLE external_systems ADD COLUMN verify_ssl BOOLEAN DEFAULT 1"))
                db.commit()
        # Add parent_topology_id to topologies (inheritance for planning layers)
        if 'topologies' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('topologies')]
            if 'parent_topology_id' not in columns:
                db.execute(text("ALTER TABLE topologies ADD COLUMN parent_topology_id INTEGER REFERENCES topologies(id)"))
                db.commit()
        # Add color to node_instances
        if 'node_instances' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('node_instances')]
            if 'color' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN color VARCHAR DEFAULT ''"))
                db.commit()
            # Geographic map coordinates (separate from logical lat/lng + x/y)
            for col_name in ['geo_latitude', 'geo_longitude']:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE node_instances ADD COLUMN {col_name} FLOAT"))
                    db.commit()
        # Geographic override columns for inherited nodes
        if 'node_position_overrides' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('node_position_overrides')]
            for col_name in ['geo_latitude', 'geo_longitude']:
                if col_name not in cols:
                    db.execute(text(f"ALTER TABLE node_position_overrides ADD COLUMN {col_name} FLOAT"))
                    db.commit()
        # Add Config Design columns to node_instances (Phase 1)
        if 'node_instances' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('node_instances')]
            if 'config_template_id' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN config_template_id INTEGER REFERENCES config_templates(id)"))
                db.commit()
            if 'generated_config' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN generated_config TEXT DEFAULT ''"))
                db.commit()
            if 'generated_lab_config' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN generated_lab_config TEXT DEFAULT ''"))
                db.commit()
            if 'config_template_group_id' not in columns:
                db.execute(text("ALTER TABLE node_instances ADD COLUMN config_template_group_id INTEGER REFERENCES config_template_groups(id)"))
                db.commit()
            # Route reflection + custom-attribute columns.
            for col_name, col_type, col_default in [
                ('is_route_reflector', 'INTEGER', '0'),
                ('is_super_route_reflector', 'INTEGER', '0'),
                ('rr_peer_ids_json', 'TEXT', "'[]'"),
                ('custom_attrs_json', 'TEXT', "'{}'"),
                ('custom_iface_refs_json', 'TEXT', "'{}'"),
            ]:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE node_instances ADD COLUMN {col_name} {col_type} DEFAULT {col_default}"))
                    db.commit()
        # Patch panel chaining: a panel may sit in front of another panel.
        if 'patch_panels' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('patch_panels')]
            if 'in_front_of_id' not in cols:
                db.execute(text("ALTER TABLE patch_panels ADD COLUMN in_front_of_id INTEGER"))
                db.commit()
        # Add match_attr (attribute-based default rules) to config templates/groups.
        # Default 'platform' keeps every existing platform_default rule intact.
        for tbl in ('config_templates', 'config_template_groups'):
            if tbl in inspector.get_table_names():
                cols = [c['name'] for c in inspector.get_columns(tbl)]
                if 'match_attr' not in cols:
                    db.execute(text(f"ALTER TABLE {tbl} ADD COLUMN match_attr VARCHAR DEFAULT 'platform'"))
                    db.commit()
        # Add deployment_subset_json to lab_deployments (subset deploy)
        if 'lab_deployments' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('lab_deployments')]
            if 'deployment_subset_json' not in cols:
                db.execute(text("ALTER TABLE lab_deployments ADD COLUMN deployment_subset_json TEXT DEFAULT ''"))
                db.commit()
        # Measured link delay on imported interface metrics
        if 'interface_metrics' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('interface_metrics')]
            if 'link_delay_us' not in cols:
                db.execute(text("ALTER TABLE interface_metrics ADD COLUMN link_delay_us FLOAT DEFAULT NULL"))
                db.commit()
        # Add unique_per_topology + unique_start_value to device_attribute_definitions
        if 'device_attribute_definitions' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('device_attribute_definitions')]
            if 'unique_per_topology' not in cols:
                db.execute(text("ALTER TABLE device_attribute_definitions ADD COLUMN unique_per_topology INTEGER DEFAULT 0"))
                db.commit()
            if 'unique_start_value' not in cols:
                db.execute(text("ALTER TABLE device_attribute_definitions ADD COLUMN unique_start_value INTEGER DEFAULT 0"))
                db.commit()
        # Add containerlab integration columns to node_templates
        if 'node_templates' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('node_templates')]
            for col_name, col_type, col_default in [
                ('clab_kind', 'VARCHAR', "''"),
                ('image_ref', 'VARCHAR', "''"),
                ('min_memory_mb', 'INTEGER', '0'),
                ('min_cpu_cores', 'INTEGER', '0'),
                ('requires_hugepages', 'INTEGER', '0'),
                ('startup_config', 'TEXT', "''"),
            ]:
                if col_name not in columns:
                    db.execute(text(f"ALTER TABLE node_templates ADD COLUMN {col_name} {col_type} DEFAULT {col_default}"))
                    db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@app.on_event("startup")
def startup():
    init_db()
    _migrate_db()
    # Ensure Prometheus managed config directory has default files
    from app.services.prometheus_config_generator import ensure_default_configs
    try:
        ensure_default_configs()
    except Exception:
        pass  # Non-critical if volume not mounted
    # Start the external sync background worker
    from app.services.sync_worker import start_worker
    try:
        start_worker()
    except Exception:
        pass
    # Lab Mode side effects: ensure the vrnetlab repo is cloned for the
    # QCOW VM image build flow. Best-effort; if cloning fails the build
    # endpoint will surface the same error.
    if LAB_ENABLED:
        try:
            from app.services import qcow_images
            qcow_images.ensure_dirs()
            err = qcow_images.ensure_vrnetlab_repo()
            if err:
                print(f"[startup] qcow_images: vrnetlab clone NOT available: {err}", flush=True)
            # Resolve any 'building' rows orphaned by a prior backend restart
            # (uvicorn --reload kills daemon threads, leaving the DB stale).
            qcow_images.reconcile_orphaned_builds()
        except Exception as e:
            print(f"[startup] qcow_images init skipped: {e}", flush=True)
    db = SessionLocal()
    try:
        # Seed global settings
        for key, (value, desc) in DEFAULT_SETTINGS.items():
            if not db.query(GlobalSettings).filter(GlobalSettings.key == key).first():
                db.add(GlobalSettings(key=key, value=value, description=desc))

        # Seed default node templates
        if db.query(NodeTemplate).count() == 0:
            templates = [
                {
                    "name": "Cisco ASR 9001",
                    "vendor": "Cisco",
                    "model": "ASR 9001",
                    "platform": "iosxr",
                    "capacity_gbps": 400,
                    "default_mtbf_hours": 60000,
                    "default_mttr_hours": 4,
                    "interfaces": [
                        {"name": "TenGigE0/0/0/0", "speed_gbps": 10, "description": ""},
                        {"name": "TenGigE0/0/0/1", "speed_gbps": 10, "description": ""},
                        {"name": "TenGigE0/0/0/2", "speed_gbps": 10, "description": ""},
                        {"name": "TenGigE0/0/0/3", "speed_gbps": 10, "description": ""},
                        {"name": "HundredGigE0/0/0/0", "speed_gbps": 100, "description": ""},
                        {"name": "HundredGigE0/0/0/1", "speed_gbps": 100, "description": ""},
                    ],
                },
                {
                    "name": "Cisco ASR 9006",
                    "vendor": "Cisco",
                    "model": "ASR 9006",
                    "platform": "iosxr",
                    "capacity_gbps": 2400,
                    "default_mtbf_hours": 55000,
                    "default_mttr_hours": 6,
                    "interfaces": [
                        {"name": "TenGigE0/0/0/0", "speed_gbps": 10, "description": ""},
                        {"name": "TenGigE0/0/0/1", "speed_gbps": 10, "description": ""},
                        {"name": "TenGigE0/0/0/2", "speed_gbps": 10, "description": ""},
                        {"name": "TenGigE0/0/0/3", "speed_gbps": 10, "description": ""},
                        {"name": "HundredGigE0/0/0/0", "speed_gbps": 100, "description": ""},
                        {"name": "HundredGigE0/0/0/1", "speed_gbps": 100, "description": ""},
                        {"name": "HundredGigE0/0/0/2", "speed_gbps": 100, "description": ""},
                        {"name": "HundredGigE0/0/0/3", "speed_gbps": 100, "description": ""},
                    ],
                },
                {
                    "name": "Cisco CSR 1000v",
                    "vendor": "Cisco",
                    "model": "CSR 1000v",
                    "platform": "iosxe",
                    "capacity_gbps": 10,
                    "default_mtbf_hours": 40000,
                    "default_mttr_hours": 2,
                    "interfaces": [
                        {"name": "GigabitEthernet1", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet2", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet3", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet4", "speed_gbps": 1, "description": ""},
                    ],
                },
                {
                    "name": "Cisco NCS 5501",
                    "vendor": "Cisco",
                    "model": "NCS 5501",
                    "platform": "iosxr",
                    "capacity_gbps": 800,
                    "default_mtbf_hours": 65000,
                    "default_mttr_hours": 4,
                    "interfaces": [
                        {"name": "TenGigE0/0/0/0", "speed_gbps": 10, "description": ""},
                        {"name": "TenGigE0/0/0/1", "speed_gbps": 10, "description": ""},
                        {"name": "HundredGigE0/0/0/0", "speed_gbps": 100, "description": ""},
                        {"name": "HundredGigE0/0/0/1", "speed_gbps": 100, "description": ""},
                        {"name": "HundredGigE0/0/0/2", "speed_gbps": 100, "description": ""},
                        {"name": "HundredGigE0/0/0/3", "speed_gbps": 100, "description": ""},
                    ],
                },
                {
                    "name": "Juniper MX204",
                    "vendor": "Juniper",
                    "model": "MX204",
                    "platform": "junos",
                    "capacity_gbps": 800,
                    "default_mtbf_hours": 60000,
                    "default_mttr_hours": 4,
                    "interfaces": [
                        {"name": "et-0/0/0", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/1", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/2", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/3", "speed_gbps": 100, "description": ""},
                        {"name": "xe-0/1/0", "speed_gbps": 10, "description": ""},
                        {"name": "xe-0/1/1", "speed_gbps": 10, "description": ""},
                        {"name": "xe-0/1/2", "speed_gbps": 10, "description": ""},
                        {"name": "xe-0/1/3", "speed_gbps": 10, "description": ""},
                    ],
                },
                {
                    "name": "Juniper MX480",
                    "vendor": "Juniper",
                    "model": "MX480",
                    "platform": "junos",
                    "capacity_gbps": 4000,
                    "default_mtbf_hours": 50000,
                    "default_mttr_hours": 6,
                    "interfaces": [
                        {"name": "et-0/0/0", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/1", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/2", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/3", "speed_gbps": 100, "description": ""},
                        {"name": "et-1/0/0", "speed_gbps": 100, "description": ""},
                        {"name": "et-1/0/1", "speed_gbps": 100, "description": ""},
                        {"name": "xe-2/0/0", "speed_gbps": 10, "description": ""},
                        {"name": "xe-2/0/1", "speed_gbps": 10, "description": ""},
                        {"name": "xe-2/0/2", "speed_gbps": 10, "description": ""},
                        {"name": "xe-2/0/3", "speed_gbps": 10, "description": ""},
                    ],
                },
                {
                    "name": "Juniper PTX10001",
                    "vendor": "Juniper",
                    "model": "PTX10001",
                    "platform": "junos",
                    "capacity_gbps": 3600,
                    "default_mtbf_hours": 70000,
                    "default_mttr_hours": 4,
                    "interfaces": [
                        {"name": "et-0/0/0", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/1", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/2", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/3", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/4", "speed_gbps": 100, "description": ""},
                        {"name": "et-0/0/5", "speed_gbps": 100, "description": ""},
                    ],
                },
                {
                    # Cisco vIOS L2 switch. Container-wrapped VM (vrnetlab),
                    # so clab_kind is pre-set but image_ref is left blank —
                    # the user fills it via Templates → Edit Lab config →
                    # VM Image ▾ after building their qcow.
                    "name": "Cisco vIOS L2 Switch (lab)",
                    "vendor": "Cisco",
                    "model": "vIOS-L2",
                    "platform": "ios",
                    "capacity_gbps": 8,
                    "default_mtbf_hours": 45000,
                    "default_mttr_hours": 2,
                    "clab_kind": "cisco_vios",
                    "min_memory_mb": 768,
                    "min_cpu_cores": 1,
                    "interfaces": [
                        {"name": "GigabitEthernet0/0", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet0/1", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet0/2", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet0/3", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet1/0", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet1/1", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet1/2", "speed_gbps": 1, "description": ""},
                        {"name": "GigabitEthernet1/3", "speed_gbps": 1, "description": ""},
                    ],
                },
                {
                    "name": "Customer",
                    "vendor": "Customer",
                    "model": "Endpoint",
                    "platform": "",
                    "capacity_gbps": 100,
                    "default_mtbf_hours": 0,
                    "default_mttr_hours": 0,
                    "role": "customer",
                    "interfaces": [
                        {"name": "eth0", "speed_gbps": 1, "description": "Last-mile handoff"},
                        {"name": "eth1", "speed_gbps": 10, "description": "Last-mile handoff"},
                    ],
                },
            ]
            for t in templates:
                db.add(NodeTemplate(
                    name=t["name"], vendor=t["vendor"], model=t["model"],
                    platform=t["platform"], capacity_gbps=t["capacity_gbps"],
                    default_mtbf_hours=t["default_mtbf_hours"], default_mttr_hours=t["default_mttr_hours"],
                    default_interfaces_json=json.dumps(t["interfaces"]),
                    role=t.get("role", "router"),
                    # Optional clab integration fields — entries that don't
                    # set them keep the column defaults (empty / 0).
                    clab_kind=t.get("clab_kind", ""),
                    image_ref=t.get("image_ref", ""),
                    min_memory_mb=t.get("min_memory_mb", 0),
                    min_cpu_cores=t.get("min_cpu_cores", 0),
                    requires_hugepages=t.get("requires_hugepages", 0),
                ))

        # Migrate the misnomer: containerlab's cisco_xrd kind only supports the
        # XRd Control Plane variant — the vRouter requires SR-IOV and can't be
        # deployed by clab. Rename any existing "vRouter" template to Control
        # Plane and point it at the Control Plane image (Cisco ships the tag
        # ios-xr/xrd-control-plane:<ver>). Idempotent: only touches rows that
        # still match the old seed name.
        old_xrd = db.query(NodeTemplate).filter(NodeTemplate.name == "Cisco XRd vRouter (lab)").first()
        if old_xrd:
            old_xrd.name = "Cisco XRd Control Plane (lab)"
            old_xrd.model = "XRd Control Plane"
            old_xrd.requires_hugepages = 0  # Control Plane doesn't need hugepages
            if not old_xrd.image_ref:
                old_xrd.image_ref = "ios-xr/xrd-control-plane:25.4.2"
            db.commit()
        # Catch-all: any template still pointing at xrd-vrouter is broken under
        # clab (the vRouter fails to boot without SR-IOV/DPDK), so swap to the
        # Control Plane variant preserving the version tag. Covers templates
        # whose name was already renamed but whose image_ref the prior
        # migration left alone because it wasn't empty.
        for tmpl in db.query(NodeTemplate).filter(
            NodeTemplate.image_ref.like("%xrd-vrouter%")
        ).all():
            tmpl.image_ref = tmpl.image_ref.replace("xrd-vrouter", "xrd-control-plane")
            tmpl.requires_hugepages = 0
        db.commit()

        # Seed FRR + XRd lab templates if they don't exist (for containerlab deploys).
        for lab_t in [
            {
                "name": "FRR Router (lab)", "vendor": "FRR", "model": "frr", "platform": "linux",
                "capacity_gbps": 10, "default_mtbf_hours": 50000, "default_mttr_hours": 4,
                "interfaces": [{"name": f"eth{i}", "speed_gbps": 10, "description": ""} for i in range(1, 9)],
                "role": "router", "clab_kind": "linux", "image_ref": "frrouting/frr:latest",
                "min_memory_mb": 512, "min_cpu_cores": 1, "requires_hugepages": 0,
                "startup_config": "",
            },
            {
                # containerlab's cisco_xrd kind is for the Control Plane variant —
                # the vRouter requires SR-IOV and isn't deployable via clab.
                "name": "Cisco XRd Control Plane (lab)", "vendor": "Cisco", "model": "XRd Control Plane", "platform": "iosxr",
                "capacity_gbps": 400, "default_mtbf_hours": 50000, "default_mttr_hours": 4,
                # XRd Control Plane's data-plane ports are 1 Gbps in the
                # container — the underlying hardware platforms it emulates
                # can do more, but the lab veth handoff is GigE.
                "interfaces": [{"name": f"Gi0-0-0-{i}", "speed_gbps": 1, "description": ""} for i in range(0, 4)],
                "role": "router", "clab_kind": "cisco_xrd",
                # Default image tag matches Cisco's docker save output for
                # xrd-control-plane-container-x86.<ver>.tgz. Operator can
                # override on the template after loading a different version.
                "image_ref": "ios-xr/xrd-control-plane:25.4.2",
                "min_memory_mb": 2048, "min_cpu_cores": 2, "requires_hugepages": 0,
                "startup_config": "",
            },
        ]:
            if not db.query(NodeTemplate).filter(NodeTemplate.name == lab_t["name"]).first():
                db.add(NodeTemplate(
                    name=lab_t["name"], vendor=lab_t["vendor"], model=lab_t["model"],
                    platform=lab_t["platform"], capacity_gbps=lab_t["capacity_gbps"],
                    default_mtbf_hours=lab_t["default_mtbf_hours"],
                    default_mttr_hours=lab_t["default_mttr_hours"],
                    default_interfaces_json=json.dumps(lab_t["interfaces"]),
                    role=lab_t["role"],
                    clab_kind=lab_t["clab_kind"], image_ref=lab_t["image_ref"],
                    min_memory_mb=lab_t["min_memory_mb"], min_cpu_cores=lab_t["min_cpu_cores"],
                    requires_hugepages=lab_t["requires_hugepages"],
                    startup_config=lab_t["startup_config"],
                ))
        db.commit()

        # Rename/update old Customer template if it exists
        old_tmpl = db.query(NodeTemplate).filter(NodeTemplate.role == "customer").first()
        if old_tmpl:
            old_tmpl.name = "Customer"
            old_tmpl.vendor = "Customer"
            old_tmpl.model = "Endpoint"
            db.commit()
        # Ensure Customer template exists (for existing databases)
        if not db.query(NodeTemplate).filter(NodeTemplate.name == "Customer").first():
            db.add(NodeTemplate(
                name="Customer", vendor="Customer", model="Endpoint", platform="",
                capacity_gbps=100, default_mtbf_hours=0, default_mttr_hours=0,
                default_interfaces_json=json.dumps([
                    {"name": "eth0", "speed_gbps": 1, "description": "Last-mile handoff"},
                    {"name": "eth1", "speed_gbps": 10, "description": "Last-mile handoff"},
                ]),
                role="customer",
            ))
        # Satellite node templates (role-based; satellite links themselves need
        # no interfaces — these ports are for the site / network side).
        if not db.query(NodeTemplate).filter(NodeTemplate.role == "sat_dish").first():
            db.add(NodeTemplate(
                name="Satellite Dish", vendor="Satellite", model="Dish", platform="",
                capacity_gbps=10, default_mtbf_hours=50000, default_mttr_hours=8,
                default_interfaces_json=json.dumps([
                    {"name": "eth0", "speed_gbps": 1, "description": "Site LAN (to the site router)"},
                ]),
                role="sat_dish",
            ))
        if not db.query(NodeTemplate).filter(NodeTemplate.role == "sat_pop").first():
            db.add(NodeTemplate(
                name="Satellite PoP", vendor="Satellite", model="PoP", platform="",
                capacity_gbps=400, default_mtbf_hours=100000, default_mttr_hours=4,
                default_interfaces_json=json.dumps([
                    {"name": f"et{i}", "speed_gbps": 10, "description": "Network uplink"} for i in range(4)
                ]),
                role="sat_pop",
            ))

        # Seed admin user from environment
        if ADMIN_PASSWORD:
            admin = db.query(User).filter(User.username == ADMIN_USERNAME).first()
            if not admin:
                db.add(User(
                    username=ADMIN_USERNAME,
                    hashed_password=hash_password(ADMIN_PASSWORD),
                    display_name="Administrator",
                    is_admin=True,
                    is_local=True,
                    auth_source="local",
                ))
            elif admin.is_admin and admin.is_local:
                # Update password if changed in env (allows password reset via env)
                admin.hashed_password = hash_password(ADMIN_PASSWORD)

        db.commit()
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/features")
def features():
    """Feature flags surfaced to the frontend so UI elements for gated
    features can be conditionally rendered."""
    # Geographic map config — read at runtime from env so switching between
    # the free Protomaps demo basemap, a keyed cloud account, or a
    # self-hosted PMTiles instance is a .env change with no rebuild.
    #   PROTOMAPS_STYLE_URL — a full MapLibre style JSON URL (overrides theme)
    #   PROTOMAPS_TILES_URL — a PMTiles/tiles URL for self-hosted basemaps
    #   PROTOMAPS_THEME     — protomaps-themes-base flavor (default: dark)
    #   PROTOMAPS_API_KEY   — cloud API key, if using api.protomaps.com
    return {
        "lab_enabled": LAB_ENABLED,
        "lab_telemetry_enabled": LAB_TELEMETRY_ENABLED,
        "service_enabled": SERVICE_ENABLED,
        "config_design_enabled": CONFIG_DESIGN_ENABLED,
        "geo_enabled": GEO_ENABLED,
        "ai_enabled": AI_ENABLED,
        "discovery_enabled": DISCOVERY_ENABLED,
        "prometheus_enabled": PROMETHEUS_ENABLED,
        "cost_enabled": COST_ENABLED,
        "integrations_enabled": INTEGRATIONS_ENABLED,
        "patch_panels_enabled": PATCH_PANELS_ENABLED,
        "map": {
            "style_url": os.environ.get("PROTOMAPS_STYLE_URL", ""),
            "tiles_url": os.environ.get("PROTOMAPS_TILES_URL", ""),
            "theme": os.environ.get("PROTOMAPS_THEME", "dark"),
            "api_key": os.environ.get("PROTOMAPS_API_KEY", ""),
        },
    }

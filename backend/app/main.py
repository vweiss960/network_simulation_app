import logging
import re

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app import config
from app.auth.config import ADMIN_PASSWORD, ADMIN_USERNAME
from app.auth.dependencies import get_current_user
from app.auth.service import hash_password
from app.models.database import SessionLocal, init_db
from app.models.node_template import NodeTemplate
from app.models.user import User
from app.routes import auth as auth_routes
from app.routes import agent, capture, device, lessons, modules, uploads, lab, lab_console, lab_network, links, node_templates, nodes, topologies

class _RedactTokens(logging.Filter):
    """Console WebSockets carry the JWT as ?token=…; keep it out of access logs."""
    _re = re.compile(r"(token=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(self._re.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactTokens())

app = FastAPI(
    title="Phanero Labs API",
    version="0.1.0",
    description="Network simulation labs: containerlab devices on a canvas, reachable on their own IPs.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)

auth_dep = [Depends(get_current_user)]

app.include_router(auth_routes.router)
app.include_router(topologies.router, dependencies=auth_dep)
app.include_router(node_templates.router, dependencies=auth_dep)
app.include_router(nodes.router, dependencies=auth_dep)
app.include_router(links.router, dependencies=auth_dep)
app.include_router(lab.router, dependencies=auth_dep)
app.include_router(lab.images_router, dependencies=auth_dep)
app.include_router(lab_network.router, dependencies=auth_dep)
app.include_router(device.router, dependencies=auth_dep)
app.include_router(uploads.router, dependencies=auth_dep)
app.include_router(agent.router, dependencies=auth_dep)
app.include_router(capture.router, dependencies=auth_dep)
app.include_router(lessons.router, dependencies=auth_dep)
app.include_router(modules.router, dependencies=auth_dep)
app.include_router(capture.ws_router)   # ?token= auth, like the consoles
# Console / log WebSockets authenticate via ?token=<jwt> (browsers can't set
# headers on a WebSocket upgrade).
app.include_router(lab_console.router)


# Seeded palette. Images left blank are built from a qcow2 under Images and
# then filled in on the template.
SEED_TEMPLATES = [
    dict(name="Cisco XRd Control Plane", vendor="Cisco", model="XRd CP", kind="cisco_xrd",
         image_ref="ios-xr/xrd-control-plane:25.4.2", icon="router", interface_count=4,
         description="IOS-XR control plane in a container. Great for routing-protocol and RR labs."),
    dict(name="Cisco XRd vRouter", vendor="Cisco", model="XRd vRouter", kind="cisco_xrd",
         image_ref="ios-xr/xrd-vrouter:25.4.2", icon="router", interface_count=4, requires_hugepages=1,
         min_memory_mb=5120, min_cpu_cores=2,
         description="IOS-XR with a forwarding plane (MPLS / SR data path). Needs hugepages."),
    dict(name="Cisco XRv 9000", vendor="Cisco", model="XRv9k", kind="cisco_xrv9k", image_ref="",
         icon="router", interface_count=4,
         description="IOS-XR VM with full data plane. Build the image from a qcow2 under Images."),
    dict(name="Cisco Catalyst 8000v", vendor="Cisco", model="C8000v", kind="cisco_c8000v", image_ref="",
         icon="router", interface_count=4,
         description="IOS-XE VM: NETCONF / RESTCONF / gNMI, MPLS, L3VPN CE/PE. Build from a qcow2."),
    dict(name="Cisco Catalyst 9000v", vendor="Cisco", model="Cat9kv", kind="cisco_cat9kv", image_ref="",
         icon="switch", interface_count=8,
         description="IOS-XE Catalyst 9000 switch VM (~18 GB RAM). Build the image from a qcow2 under Images."),
    dict(name="Cisco CSR 1000v", vendor="Cisco", model="CSR1000v", kind="cisco_csr1000v", image_ref="",
         icon="router", interface_count=4, description="IOS-XE VM. Build from a qcow2."),
    dict(name="Cisco vIOS L2", vendor="Cisco", model="vIOS-L2", kind="cisco_vios", image_ref="",
         icon="switch", interface_count=7, description="IOS L2 switch VM. Build from a qcow2."),
    dict(name="FRR Router", vendor="FRR", model="frr", kind="linux", image_ref="plabs/frr:latest",
         icon="router", interface_count=4,
         description="FRRouting with all daemons enabled (scripts/build-images.sh)."),
    dict(name="Linux Host", vendor="Linux", model="alpine", kind="linux", image_ref="plabs/host:latest",
         icon="host", interface_count=1, description="End host with ping / traceroute / iperf3 / tcpdump."),
    dict(name="Network (NIC / VLAN)", vendor="Network", model="uplink", kind="bridge", image_ref="",
         icon="cloud", interface_count=0,
         description="Connects devices to a physical network: a host NIC (untagged) or one VLAN on a trunk NIC."),
    dict(name="Lab Switch", vendor="Network", model="switch", kind="bridge", image_ref="",
         icon="switch", interface_count=0,
         description="Internal L2 segment between lab devices (no physical uplink)."),
    dict(name="Automation Workstation", vendor="Linux", model="workstation", kind="linux",
         image_ref="plabs/workstation:latest", icon="workstation", interface_count=1,
         description="Python, ncclient, netmiko, pyATS, Ansible, gnmic — for automation labs."),
]


@app.on_event("startup")
def startup():
    config.LAB_STATE_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    from app.services import packet_capture
    packet_capture.sweep_orphans()
    # Bundled lessons (curriculum/lessons/*/): new ones and newer versions.
    try:
        from app.services import lessons as lesson_svc
        _db = SessionLocal()
        try:
            seeded = lesson_svc.seed_bundled(_db, config.CURRICULUM_DIR)
            if seeded:
                print(f"[startup] lessons imported: {', '.join(seeded)}", flush=True)
            # Then modules (curriculum/modules/*/), which reference lessons.
            from app.services import modules as module_svc
            seeded = module_svc.seed_bundled(_db, config.CURRICULUM_DIR)
            if seeded:
                print(f"[startup] modules imported: {', '.join(seeded)}", flush=True)
        finally:
            _db.close()
    except Exception as e:
        print(f"[startup] lesson seeding skipped: {e}", flush=True)
    try:
        from app.services import qcow_images
        qcow_images.ensure_dirs()
        err = qcow_images.ensure_vrnetlab_repo()
        if err:
            print(f"[startup] vrnetlab clone not available: {err}", flush=True)
        qcow_images.reconcile_orphaned_builds()
    except Exception as e:
        print(f"[startup] qcow_images init skipped: {e}", flush=True)
    db = SessionLocal()
    try:
        existing = {t.name for t in db.query(NodeTemplate.name)}
        for t in SEED_TEMPLATES:
            if t["name"] not in existing:
                db.add(NodeTemplate(**t))
        if ADMIN_PASSWORD:
            admin = db.query(User).filter(User.username == ADMIN_USERNAME).first()
            if not admin:
                db.add(User(username=ADMIN_USERNAME, hashed_password=hash_password(ADMIN_PASSWORD),
                            display_name="Administrator", is_admin=True, is_local=True, auth_source="local"))
            elif admin.is_admin and admin.is_local:
                admin.hashed_password = hash_password(ADMIN_PASSWORD)  # reset via env
        db.commit()
        from app.services.lab_service import restore_host_network
        restore_host_network(db)
        from app.services.lab_network import start_renewer
        start_renewer()
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/features")
def features():
    return {
        "ai_enabled": config.AI_ENABLED,
        "lab_telemetry_enabled": config.LAB_TELEMETRY_ENABLED,
    }

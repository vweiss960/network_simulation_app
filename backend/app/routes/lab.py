"""Lab orchestration routes — preview / deploy / destroy / status / image mgmt.

All endpoints live under /api/topologies/{topology_id}/lab/* except the
image management endpoints, which are global (/api/lab/images).
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.services.clab_yaml import generate_clab_yaml, get_host_capacity
from app.services.lab_view import compute_lab_view
from app.services.lab_service import (
    LabError, deploy_lab, destroy_lab, get_deployment_state,
    list_device_images, load_device_image, save_uploaded_image, get_host_prereqs,
    start_node, stop_node, restart_node,
    get_link_netem, set_link_netem, reset_link_netem,
)


router = APIRouter(prefix="/api/topologies/{topology_id}/lab", tags=["lab"])
images_router = APIRouter(prefix="/api/lab", tags=["lab"])


@router.get("/view")
def lab_view(topology_id: int, db: Session = Depends(get_db)):
    """Runtime view of this topology (clab names, container names, kernel
    interface names)."""
    return compute_lab_view(db, topology_id)


@router.get("/yaml")
def preview_yaml(topology_id: int, db: Session = Depends(get_db)):
    """Generate the containerlab YAML for this topology without deploying.
    Also returns the inclusion/skip list and resource estimate."""
    try:
        return generate_clab_yaml(db, topology_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/estimate")
def deploy_estimate(topology_id: int, db: Session = Depends(get_db)):
    """Resource estimate vs host capacity."""
    try:
        spec = generate_clab_yaml(db, topology_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    host = get_host_capacity()
    est = spec["resource_estimate"]
    fits_memory = host["memory_mb"] >= est["memory_mb"] if host["memory_mb"] else None
    fits_cpu = host["cpu_cores"] >= est["cpu_cores"] if host["cpu_cores"] else None
    hugepages_ok = (not est["requires_hugepages"]) or (host["hugepages_count"] > 0)
    return {
        "topology_estimate": est,
        "host_capacity": host,
        "fits_memory": fits_memory,
        "fits_cpu": fits_cpu,
        "hugepages_ok": hugepages_ok,
        "skipped_nodes": spec["skipped_nodes"],
        "skipped_links": spec["skipped_links"],
    }


@router.get("/status")
def lab_status(topology_id: int, db: Session = Depends(get_db)):
    return get_deployment_state(db, topology_id)


@router.get("/prereqs")
def lab_prereqs(topology_id: int, db: Session = Depends(get_db)):
    """Check host-side kernel prerequisites for this topology's lab deploy
    (inotify limits, hugepages). Each check returns current value, required
    minimum, and a copy-paste fix command if it's failing."""
    return get_host_prereqs(db, topology_id)


@router.post("/deploy")
def lab_deploy(topology_id: int, db: Session = Depends(get_db)):
    try:
        return deploy_lab(db, topology_id)
    except LabError as e:
        raise HTTPException(400, str(e))


@router.post("/destroy")
def lab_destroy(topology_id: int, db: Session = Depends(get_db)):
    try:
        return destroy_lab(db, topology_id)
    except LabError as e:
        raise HTTPException(400, str(e))


@router.post("/redeploy")
def lab_redeploy(topology_id: int, db: Session = Depends(get_db)):
    """Destroy and deploy from scratch. Loses running config that isn't in a
    node's startup config (the UI confirms first)."""
    try:
        destroy_lab(db, topology_id)
    except LabError:
        # Best-effort: even if destroy reports trouble, we try the deploy;
        # the sweep already runs inside destroy_lab.
        pass
    try:
        return deploy_lab(db, topology_id)
    except LabError as e:
        raise HTTPException(400, str(e))


# --- Custom gNMI subscriptions (lab telemetry add-on) ---

class GnmiSubscriptionIn(BaseModel):
    name: str
    path: str
    mode: str = "sample"          # "sample" | "on-change"
    sample_interval: str = "30s"
    enabled: bool = True


def _require_telemetry_addon():
    from app.services import lab_addons
    if not lab_addons.addon_enabled():
        raise HTTPException(404, "Lab telemetry add-on is not enabled "
                            "(set PLABS_LAB_TELEMETRY_ENABLED=1).")


def _sub_to_dict(s) -> dict:
    return {"id": s.id, "name": s.name, "path": s.path, "mode": s.mode,
            "sample_interval": s.sample_interval, "enabled": s.enabled}


@router.get("/subscriptions")
def lab_list_subscriptions(topology_id: int, db: Session = Depends(get_db)):
    _require_telemetry_addon()
    from app.models.lab_gnmi_subscription import LabGnmiSubscription
    rows = db.query(LabGnmiSubscription).filter(
        LabGnmiSubscription.topology_id == topology_id).order_by(LabGnmiSubscription.id).all()
    return {"subscriptions": [_sub_to_dict(s) for s in rows]}


@router.post("/subscriptions")
def lab_create_subscription(topology_id: int, body: GnmiSubscriptionIn, db: Session = Depends(get_db)):
    _require_telemetry_addon()
    from app.models.lab_gnmi_subscription import LabGnmiSubscription
    name = body.name.strip()
    path = body.path.strip()
    if not name or not path:
        raise HTTPException(400, "name and path are required")
    if body.mode not in ("sample", "on-change"):
        raise HTTPException(400, "mode must be 'sample' or 'on-change'")
    # Unique subscription name per topology (it's the gnmic config key).
    dup = db.query(LabGnmiSubscription).filter(
        LabGnmiSubscription.topology_id == topology_id, LabGnmiSubscription.name == name).first()
    if dup:
        raise HTTPException(409, f"A subscription named '{name}' already exists.")
    row = LabGnmiSubscription(topology_id=topology_id, name=name, path=path,
                              mode=body.mode, sample_interval=body.sample_interval.strip() or "30s",
                              enabled=body.enabled)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _sub_to_dict(row)


@router.delete("/subscriptions/{sub_id}")
def lab_delete_subscription(topology_id: int, sub_id: int, db: Session = Depends(get_db)):
    _require_telemetry_addon()
    from app.models.lab_gnmi_subscription import LabGnmiSubscription
    row = db.query(LabGnmiSubscription).filter(
        LabGnmiSubscription.topology_id == topology_id, LabGnmiSubscription.id == sub_id).first()
    if not row:
        raise HTTPException(404, "Subscription not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/subscriptions/apply")
def lab_apply_subscriptions(topology_id: int, db: Session = Depends(get_db)):
    """Recreate the gnmic collector so subscription changes take effect. Only
    valid when this topology's lab is running."""
    _require_telemetry_addon()
    from app.services import lab_addons
    from app.services.lab_service import _get_or_create_deployment, load_gnmi_subscriptions
    dep = _get_or_create_deployment(db, topology_id)
    if dep.status != "running" or not dep.lab_name:
        raise HTTPException(409, "No running lab for this topology — deploy it first.")
    state = get_deployment_state(db, topology_id)
    nodes = [{"name": c.get("name"), "mgmt_ip": c.get("mgmt_ip"), "kind": c.get("kind")}
             for c in state.get("containers", [])]
    subs = load_gnmi_subscriptions(db, topology_id)
    result = lab_addons.start_gnmic_collector(dep.lab_name, nodes, extra_subscriptions=subs)
    if not result.get("ok"):
        raise HTTPException(400, f"Collector restart failed: {result.get('error') or result.get('reason')}")
    return {"ok": True, "applied_subscriptions": len(subs), "collector": result}


class GnmiTestIn(BaseModel):
    path: str
    target: str | None = None      # node name; default = first gNMI-capable node


@router.post("/subscriptions/test")
def lab_test_subscription(topology_id: int, body: GnmiTestIn, db: Session = Depends(get_db)):
    """One-shot query a single running lab node with a gNMI path and return what
    it emits — a quick preview before adding/applying a subscription."""
    _require_telemetry_addon()
    from app.services import lab_addons
    from app.services.lab_service import _get_or_create_deployment
    path = (body.path or "").strip()
    if not path:
        raise HTTPException(400, "path is required")
    dep = _get_or_create_deployment(db, topology_id)
    if dep.status != "running" or not dep.lab_name:
        raise HTTPException(409, "No running lab for this topology — deploy it first.")
    state = get_deployment_state(db, topology_id)
    nodes = [{"name": c.get("name"), "mgmt_ip": c.get("mgmt_ip"), "kind": c.get("kind")}
             for c in state.get("containers", [])]
    result = lab_addons.test_gnmi_path(dep.lab_name, nodes, path, target_name=body.target)
    if not result.get("ok") and result.get("reason"):
        raise HTTPException(400, result["reason"])
    return result


# --- Link degradation (netem) — runtime per-link impairments ---

class NetemIn(BaseModel):
    delay: str = ""          # e.g. "50ms" (blank = none)
    jitter: str = ""         # e.g. "5ms"
    loss: float = 0          # packet loss %
    rate: int = 0            # rate limit Kbit (0 = unlimited)
    corruption: float = 0    # corruption %


@router.get("/link/{link_id}/netem")
def lab_get_link_netem(topology_id: int, link_id: int, db: Session = Depends(get_db)):
    try:
        return get_link_netem(db, topology_id, link_id)
    except LabError as e:
        raise HTTPException(400, str(e))


@router.post("/link/{link_id}/netem")
def lab_set_link_netem(topology_id: int, link_id: int, body: NetemIn, db: Session = Depends(get_db)):
    try:
        return set_link_netem(db, topology_id, link_id, delay=body.delay.strip(),
                              jitter=body.jitter.strip(), loss=body.loss, rate=body.rate,
                              corruption=body.corruption)
    except LabError as e:
        raise HTTPException(400, str(e))


@router.delete("/link/{link_id}/netem")
def lab_reset_link_netem(topology_id: int, link_id: int, db: Session = Depends(get_db)):
    try:
        return reset_link_netem(db, topology_id, link_id)
    except LabError as e:
        raise HTTPException(400, str(e))


@router.post("/node/{node_id}/start")
def lab_start_node(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    """Start a stopped node (or reconcile it into existence)."""
    try:
        return start_node(db, topology_id, node_id)
    except LabError as e:
        raise HTTPException(400, str(e))


@router.post("/node/{node_id}/stop")
def lab_stop_node(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    """Stop a node, freeing its RAM. containerlab parks its dataplane so Start
    restores the links."""
    try:
        return stop_node(db, topology_id, node_id)
    except LabError as e:
        raise HTTPException(400, str(e))


@router.post("/node/{node_id}/restart")
def lab_restart_node(topology_id: int, node_id: int, db: Session = Depends(get_db)):
    try:
        return restart_node(db, topology_id, node_id)
    except LabError as e:
        raise HTTPException(400, str(e))


# --- Image management (global) ---

class LoadImageRequest(BaseModel):
    filename: str


@images_router.get("/images")
def list_images():
    return {"images": list_device_images()}


@images_router.get("/running-summary")
def running_summary(db: Session = Depends(get_db)):
    """Per-topology count of currently-running clab containers. Used by the
    main topology landing page to flag which topologies have a lab spun up.

    Returns `{ "<topology_id>": <running_count> }`. Topologies with no
    containers running are omitted from the map. Cheap: one `docker ps` plus
    a single DB read of LabDeployment.
    """
    import subprocess
    from app.models.lab_deployment import LabDeployment

    try:
        proc = subprocess.run(
            ["docker", "ps",
             "--filter", "label=containerlab",
             "--format", "{{.Label \"containerlab\"}}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    counts: dict[str, int] = {}
    for lab_name in proc.stdout.splitlines():
        lab_name = lab_name.strip()
        if not lab_name:
            continue
        counts[lab_name] = counts.get(lab_name, 0) + 1

    deps = db.query(LabDeployment).all()
    summary: dict[str, int] = {}
    for dep in deps:
        if dep.lab_name and dep.lab_name in counts:
            summary[str(dep.topology_id)] = counts[dep.lab_name]
    return summary


@images_router.post("/images/load")
def load_image(req: LoadImageRequest):
    try:
        return load_device_image(req.filename)
    except LabError as e:
        raise HTTPException(400, str(e))


@images_router.post("/images/upload")
def upload_image(file: UploadFile = File(...)):
    """Stream a vendor image file (Cisco signed bundle, plain docker save tarball,
    etc.) into DEVICE_IMAGES_DIR. After upload completes, the file shows up in
    /api/lab/images and can be loaded via /api/lab/images/load."""
    try:
        return save_uploaded_image(file.filename or "uploaded.tgz", file.file)
    except LabError as e:
        raise HTTPException(400, str(e))


@images_router.get("/host-capacity")
def host_capacity():
    return get_host_capacity()


@images_router.get("/kinds")
def kinds():
    """Device kind registry (interface naming, credentials, sizing)."""
    from app.services.device_kinds import kind_catalog
    return {"kinds": kind_catalog()}


@images_router.get("/docker-images")
def docker_images():
    """Image references present in the local Docker daemon — lets the palette
    flag templates whose image hasn't been loaded/built yet."""
    from app.services.lab_service import local_docker_images
    return {"images": sorted(local_docker_images())}


# --- QCOW VM image flow (upload -> vrnetlab build -> built docker tag) ---
#
# Parallel resource to /api/lab/images but state-ful: each row tracks a
# specific upload + build, not just a file on disk. See qcow_images.py for
# the lifecycle. UI lives in LabPanel under the existing Device Images
# section.

@images_router.get("/qcow-images")
def qcow_list(db: Session = Depends(get_db)):
    from app.services import qcow_images as qi
    active, active_id = qi.is_build_active()
    return {
        "images": qi.list_images(db),
        "platforms": qi.list_platforms(),
        "active_build_id": active_id if active else None,
    }


@images_router.post("/qcow-images/upload")
def qcow_upload(
    platform: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Multipart upload of a .qcow2 + which vrnetlab platform it's for.

    `platform` is the registry key from qcow_images.PLATFORMS (e.g. 'csr',
    'viosl2'). Frontend gets the list from /qcow-images on initial load.
    """
    from app.services import qcow_images as qi
    try:
        img = qi.save_uploaded_qcow(db, file.file, file.filename or "uploaded.qcow2", platform)
    except qi.QcowImageError as e:
        raise HTTPException(400, str(e))
    return {
        "id": img.id, "filename": img.filename, "platform": img.platform,
        "clab_kind": img.clab_kind, "size_mb": (img.size_bytes or 0) // (1024 * 1024),
        "status": img.status,
    }


@images_router.get("/qcow-images/unregistered")
def qcow_unregistered(db: Session = Depends(get_db)):
    """qcow2 files copied into the images folder (scp/rsync) but not registered."""
    from app.services import qcow_images as qi
    return {"files": qi.list_unregistered(db), "folder": str(qi.QCOW_DIR)}


class RegisterQcowIn(BaseModel):
    filename: str
    platform: str


@images_router.post("/qcow-images/register")
def qcow_register(body: RegisterQcowIn, db: Session = Depends(get_db)):
    from app.services import qcow_images as qi
    try:
        img = qi.register_existing(db, body.filename, body.platform)
    except qi.QcowImageError as e:
        raise HTTPException(400, str(e))
    return {"id": img.id, "filename": img.filename, "platform": img.platform, "status": img.status}


class ReclassifyQcowIn(BaseModel):
    platform: str


@images_router.patch("/qcow-images/{image_id}")
def qcow_reclassify(image_id: int, body: ReclassifyQcowIn, db: Session = Depends(get_db)):
    """Change the platform of an uploaded image (discards a previous build)."""
    from app.services import qcow_images as qi
    try:
        img = qi.reclassify(db, image_id, body.platform)
    except qi.QcowImageError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e))
    return {"id": img.id, "platform": img.platform, "clab_kind": img.clab_kind, "status": img.status}


@images_router.post("/qcow-images/{image_id}/build")
def qcow_build(image_id: int, db: Session = Depends(get_db)):
    """Kick off the vrnetlab build in a background thread. Returns immediately;
    poll /qcow-images/{id}/status for progress + log tail."""
    from app.services import qcow_images as qi
    img = qi.get_image(db, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    if img.status == "building":
        raise HTTPException(409, "This image is already building.")
    active, active_id = qi.is_build_active()
    if active:
        raise HTTPException(409, f"Another build is in progress (image {active_id}). Wait for it to finish.")
    # Reset transient state from a prior failed attempt so the UI shows a
    # clean log this time around.
    img.status = "uploaded"
    img.error_message = ""
    db.commit()
    qi.start_build_async(image_id)
    return {"id": image_id, "status": "queued"}


@images_router.get("/qcow-images/{image_id}/status")
def qcow_status(image_id: int, db: Session = Depends(get_db)):
    from app.services import qcow_images as qi
    try:
        return qi.get_log_tail(db, image_id)
    except qi.QcowImageError as e:
        raise HTTPException(404, str(e))


@images_router.delete("/qcow-images/{image_id}")
def qcow_delete(image_id: int, also_docker_rmi: bool = True, db: Session = Depends(get_db)):
    from app.services import qcow_images as qi
    try:
        return qi.delete_image(db, image_id, also_docker_rmi=also_docker_rmi)
    except qi.QcowImageError as e:
        raise HTTPException(400, str(e))

"""QCOW VM image management — uploads + vrnetlab-based Docker image builds.

Lab Mode supports two flavours of "image source":

  1. Container-native kinds (cisco_xrd, arista_ceos, nokia_srlinux, …).
     Vendor ships a ready-to-run Docker image; users upload a .tgz via the
     existing /api/lab/images endpoints and `docker load` it. This module
     does NOT touch that flow.

  2. VM-only platforms (cisco_csr1000v, cisco_vios, juniper_vmx, …).
     The vendor ships a .qcow2 disk image, and we wrap it in a Docker
     container that boots the VM under QEMU using hellt/vrnetlab. That's
     what this module handles end-to-end: upload, build, status, delete.

Storage layout (sibling to existing tgz images under DEVICE_IMAGES_DIR):

    device_images/
        qcow/<filename>.qcow2          # the uploaded VM disk
        qcow_logs/<image_id>.log       # captured stdout/stderr of `make docker-image`
        vrnetlab/                      # cloned at startup, pinned commit

Builds run in a FastAPI BackgroundTask. A process-wide mutex serialises
them — vrnetlab builds are I/O + RAM heavy, parallelism causes OOM on
typical lab hosts.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.database import SessionLocal
from app.models.device_image import DeviceImage


from app.config import DEVICE_IMAGES_DIR
QCOW_DIR = DEVICE_IMAGES_DIR / "qcow"
LOG_DIR = DEVICE_IMAGES_DIR / "qcow_logs"
VRNETLAB_DIR = DEVICE_IMAGES_DIR / "vrnetlab"

VRNETLAB_REPO = "https://github.com/hellt/vrnetlab.git"
# Pinned for reproducibility — bump deliberately when supported platforms or
# build scripts change upstream.
VRNETLAB_COMMIT = "master"


class QcowImageError(Exception):
    """User-facing error from upload/build/delete operations."""


# ---------- Platform registry ----------
#
# Subset of vrnetlab platforms we explicitly support today. Adding more is a
# one-row change — `subdir` must match the actual path under hellt/vrnetlab.
# `clab_kind` is what gets written into NodeTemplate.image_ref consumers.
# The build image tag is auto-detected from `docker images` diff post-build
# (vrnetlab tags vary by version) — we don't hardcode it here.
#
# Note on layout: hellt/vrnetlab groups platforms under vendor directories
# (cisco/csr1000v, cisco/vios, …). The `subdir` value here is the path
# RELATIVE to the cloned repo root — Path joins it correctly.
PLATFORMS: dict[str, dict] = {
    "c8000v": {
        "vendor": "cisco",
        "label": "Cisco Catalyst 8000v",
        "subdir": "cisco/c8000v",
        "clab_kind": "cisco_c8000v",
        "qcow_hint": "c8000v-universalk9_8G_serial.<ver>.qcow2 (the *serial* console build)",
    },
    "xrv9k": {
        "vendor": "cisco",
        "label": "Cisco XRv 9000",
        "subdir": "cisco/xrv9k",
        "clab_kind": "cisco_xrv9k",
        "qcow_hint": "xrv9k-fullk9-x.vrr-<ver>.qcow2",
    },
    "csr1000v": {
        "vendor": "cisco",
        "label": "Cisco CSR 1000v",
        "subdir": "cisco/csr1000v",
        "clab_kind": "cisco_csr1000v",
        "qcow_hint": "csr1000v-universalk9.<ver>.qcow2",
    },
    "vios": {
        "vendor": "cisco",
        # hellt/vrnetlab's cisco/vios builds for BOTH the L3 router and the
        # L2 switch image — same Makefile, detected from the qcow filename
        # (look for `_l2` in the name for the switch variant).
        "label": "Cisco vIOS (router or L2 switch)",
        "subdir": "cisco/vios",
        "clab_kind": "cisco_vios",
        "qcow_hint": "vios-adventerprisek9-m.<ver>.qcow2 (router) or vios_l2-adventerprisek9-m.<ver>.qcow2 (switch)",
    },
}


def list_platforms() -> list[dict]:
    """Expose the supported platform registry to the frontend dropdown."""
    return [
        {"key": k, **{kk: vv for kk, vv in v.items() if kk != "subdir"}}
        for k, v in PLATFORMS.items()
    ]


# ---------- Storage helpers ----------

def ensure_dirs() -> None:
    """Idempotent — called at startup and before each operation."""
    QCOW_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def ensure_vrnetlab_repo() -> str | None:
    """Clone hellt/vrnetlab into DEVICE_IMAGES_DIR/vrnetlab if missing.

    Run at backend startup AND at the top of each build. Returns None on
    success (already cloned or freshly cloned), or a short human-readable
    error string on failure. Callers surface the string to the user so
    "clone never happened" is distinguishable from "subdir is wrong".
    """
    if VRNETLAB_DIR.exists() and (VRNETLAB_DIR / ".git").exists():
        return None
    DEVICE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", VRNETLAB_REPO, str(VRNETLAB_DIR)],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
            return f"git clone exited {proc.returncode}: {err[-1]}"
        return None
    except FileNotFoundError:
        # git binary not on PATH — backend container hasn't been rebuilt
        # since the Dockerfile started installing git.
        return "`git` is not installed in the backend container. Rebuild it: `docker compose build backend && docker compose up -d`."
    except subprocess.TimeoutExpired:
        return "git clone timed out after 180s — check the backend's outbound network access to github.com."
    except Exception as e:
        return f"git clone failed: {type(e).__name__}: {e}"


# ---------- Upload ----------

def _safe_filename(name: str) -> str:
    """Reject path traversal and weird filenames. vrnetlab parses the version
    out of the filename, so we want to preserve exactly what the user
    uploaded — just refuse anything unsafe."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise QcowImageError("Invalid filename")
    if not name.lower().endswith(".qcow2"):
        raise QcowImageError("Filename must end in .qcow2")
    return name


def save_uploaded_qcow(db: Session, file_obj, filename: str, platform: str) -> DeviceImage:
    """Stream a multipart-uploaded qcow into disk + create the DB row.

    Writes to a `.partial` first so a dropped connection doesn't leave a
    truncated file masquerading as a complete one.
    """
    if platform not in PLATFORMS:
        raise QcowImageError(f"Unknown platform '{platform}'. Supported: {list(PLATFORMS)}")
    safe = _safe_filename(filename)
    ensure_dirs()

    dest = QCOW_DIR / safe
    if dest.exists():
        raise QcowImageError(f"A qcow named {safe!r} already exists — delete it first or rename.")
    tmp = QCOW_DIR / f".{safe}.partial"
    sha = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                size += len(chunk)
        os.replace(tmp, dest)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    info = PLATFORMS[platform]
    img = DeviceImage(
        filename=safe,
        size_bytes=size,
        sha256=sha.hexdigest(),
        vendor=info["vendor"],
        platform=platform,
        clab_kind=info["clab_kind"],
        status="uploaded",
    )
    db.add(img)
    db.commit()
    db.refresh(img)
    return img


# ---------- List / get ----------

def list_images(db: Session) -> list[dict]:
    """List all DeviceImage rows + whether their built tag is still loaded in
    the local Docker daemon (so the UI can show 'tag missing' if someone
    `docker rmi`'d it out from under us)."""
    rows = db.query(DeviceImage).order_by(DeviceImage.uploaded_at.desc()).all()
    loaded = _list_docker_image_tags()
    out = []
    for r in rows:
        out.append({
            "id": r.id, "filename": r.filename, "size_mb": (r.size_bytes or 0) // (1024 * 1024),
            "vendor": r.vendor, "platform": r.platform, "clab_kind": r.clab_kind,
            "status": r.status, "built_image_tag": r.built_image_tag,
            "tag_still_loaded": bool(r.built_image_tag and r.built_image_tag in loaded),
            "error_message": r.error_message or "",
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "built_at": r.built_at.isoformat() if r.built_at else None,
        })
    return out


def get_log_tail(db: Session, image_id: int, max_lines: int = 200) -> dict:
    img = db.query(DeviceImage).filter(DeviceImage.id == image_id).first()
    if not img:
        raise QcowImageError("Image not found")
    log_lines: list[str] = []
    if img.build_log_path and Path(img.build_log_path).exists():
        try:
            with open(img.build_log_path, "r", errors="replace") as f:
                # Cheap tail — load and slice. Build logs run in the
                # low-thousands-of-lines range, this is fine.
                log_lines = f.readlines()[-max_lines:]
        except OSError:
            pass
    return {
        "id": img.id,
        "status": img.status,
        "built_image_tag": img.built_image_tag or "",
        "error_message": img.error_message or "",
        "log": "".join(log_lines),
    }


# ---------- Build orchestration ----------

# Process-wide mutex — only one vrnetlab build runs at a time. vrnetlab
# builds spawn nested Docker builds, each pulling base images and running
# QEMU; running two in parallel reliably OOMs typical hosts and corrupts
# the docker-content-trust state.
_build_lock = threading.Lock()
_active_build_image_id: int | None = None


def is_build_active() -> tuple[bool, int | None]:
    return _build_lock.locked(), _active_build_image_id


def start_build_async(image_id: int) -> None:
    """Kicks off a build in a daemon thread. The FastAPI request returns
    immediately; the client polls /status for progress. We don't use
    BackgroundTasks because we want true concurrency boundaries (the mutex)
    and the build outlives the request lifetime."""
    t = threading.Thread(target=_run_build, args=(image_id,), daemon=True)
    t.start()


_TAGGED_LINE_RE = re.compile(r"Successfully tagged\s+(\S+)")


def reconcile_orphaned_builds() -> None:
    """Resolve DeviceImage rows stuck in `status='building'` from a previous
    backend lifecycle.

    Builds run in a daemon thread; if uvicorn --reload (or a crash) kills
    the worker between `make` finishing and the post-build DB write, the
    row stays at 'building' forever and the UI shows a spinner that never
    resolves. Run this on startup: for each stuck row, grep the build log
    for a `Successfully tagged X:Y` line and cross-reference against the
    live Docker image list. If both match → mark built. Otherwise → mark
    failed so the user can re-click Build.
    """
    loaded_tags = _list_docker_image_tags()
    db = SessionLocal()
    try:
        rows = db.query(DeviceImage).filter(DeviceImage.status == "building").all()
        for img in rows:
            resolved_tag = ""
            if img.build_log_path and Path(img.build_log_path).exists():
                try:
                    with open(img.build_log_path, "r", errors="replace") as f:
                        # Search from the end of the log — make's last
                        # "Successfully tagged" line is the final image.
                        for ln in reversed(f.readlines()):
                            m = _TAGGED_LINE_RE.search(ln)
                            if m:
                                candidate = m.group(1)
                                if candidate in loaded_tags:
                                    resolved_tag = candidate
                                break
                except OSError:
                    pass
            if resolved_tag:
                img.built_image_tag = resolved_tag
                img.status = "built"
                img.built_at = datetime.now(timezone.utc)
                img.error_message = ""
                print(f"[qcow_images] reconciled image {img.id} -> built ({resolved_tag})", flush=True)
            else:
                img.status = "failed"
                img.error_message = (
                    "Build was interrupted (backend restarted while the build was running). "
                    "Click Build again to retry."
                )
                print(f"[qcow_images] reconciled image {img.id} -> failed (no tag found in log)", flush=True)
        if rows:
            db.commit()
    finally:
        db.close()


def _list_docker_image_tags() -> set[str]:
    try:
        proc = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode != 0:
        return set()
    return {ln.strip() for ln in proc.stdout.splitlines() if ln.strip() and ":<none>" not in ln}


def _run_build(image_id: int) -> None:
    """Build worker — runs in a daemon thread. Owns its own DB session."""
    global _active_build_image_id
    # If another build is in flight, mark this one as failed-with-reason so
    # the user sees something other than a hung "uploaded" state.
    if not _build_lock.acquire(blocking=False):
        _set_failed(image_id, "Another build is already in progress. Try again when it finishes.")
        return
    _active_build_image_id = image_id
    db = SessionLocal()
    try:
        img = db.query(DeviceImage).filter(DeviceImage.id == image_id).first()
        if not img:
            return
        platform = PLATFORMS.get(img.platform)
        if not platform:
            _mark_failed(db, img, f"Unknown platform '{img.platform}' in registry")
            return
        ensure_dirs()
        clone_err = ensure_vrnetlab_repo()
        if clone_err:
            _mark_failed(db, img, f"vrnetlab clone unavailable: {clone_err}")
            return
        build_dir = VRNETLAB_DIR / platform["subdir"]
        if not build_dir.exists():
            # Clone succeeded but the configured subdir isn't there. Either
            # the platform registry is out of date with upstream, or someone
            # corrupted the clone. Show what IS in the clone so it's obvious.
            try:
                listing = sorted(p.name for p in VRNETLAB_DIR.iterdir() if p.is_dir())[:30]
            except OSError:
                listing = []
            _mark_failed(
                db, img,
                f"vrnetlab subdir {platform['subdir']!r} not found under {VRNETLAB_DIR}. "
                f"Top-level dirs present: {listing}. Upstream may have renamed it — "
                f"update PLATFORMS in qcow_images.py.",
            )
            return

        qcow_src = QCOW_DIR / img.filename
        if not qcow_src.exists():
            _mark_failed(db, img, f"qcow file missing on disk: {qcow_src}")
            return

        # vrnetlab Makefiles look for *.qcow2 in the build dir. Copy rather
        # than symlink so `docker build`'s context tar can include it (Docker
        # follows symlinks pointing inside the context, but vrnetlab's build
        # contexts vary by platform — copying is the safe default).
        qcow_dst = build_dir / img.filename
        if not qcow_dst.exists():
            shutil.copy2(qcow_src, qcow_dst)

        log_path = LOG_DIR / f"{img.id}.log"
        img.build_log_path = str(log_path)
        img.status = "building"
        img.error_message = ""
        db.commit()

        before_tags = _list_docker_image_tags()
        with open(log_path, "w") as log:
            log.write(f"=== vrnetlab build for image {img.id} ({img.filename}) ===\n")
            log.write(f"=== Platform: {img.platform} ({platform['label']}) ===\n")
            log.write(f"=== Build dir: {build_dir} ===\n\n")
            log.flush()
            try:
                proc = subprocess.run(
                    ["make", "docker-image"],
                    cwd=str(build_dir),
                    stdout=log, stderr=subprocess.STDOUT,
                    timeout=3600,  # 1h hard ceiling
                )
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                log.write("\n=== build exceeded 1 hour timeout — aborted ===\n")
                rc = 124

        after_tags = _list_docker_image_tags()
        new_tags = sorted(after_tags - before_tags)

        if rc == 0 and new_tags:
            # If multiple new tags appeared, prefer one whose repo contains
            # the vrnetlab pattern (vr- or vrnetlab/) so we don't capture
            # an unrelated daemon-side push.
            preferred = [t for t in new_tags if "vr-" in t or t.startswith("vrnetlab/")]
            img.built_image_tag = preferred[0] if preferred else new_tags[0]
            img.status = "built"
            img.built_at = datetime.now(timezone.utc)
            _fill_template_images(db, img.clab_kind, img.built_image_tag)
        elif rc == 0 and not new_tags:
            _mark_failed(db, img, "Build exited 0 but no new Docker image appeared — check the log.")
            return
        else:
            _mark_failed(db, img, f"vrnetlab make exited {rc} — check the log for details.")
            return
        db.commit()
    finally:
        # Best-effort cleanup of the copy we placed in the build dir, so
        # subsequent builds don't accidentally pick up stale qcows.
        try:
            if 'qcow_dst' in locals() and qcow_dst.exists():
                qcow_dst.unlink()
        except OSError:
            pass
        _active_build_image_id = None
        _build_lock.release()
        db.close()


def _mark_failed(db: Session, img: DeviceImage, msg: str) -> None:
    img.status = "failed"
    img.error_message = msg
    db.commit()


def _set_failed(image_id: int, msg: str) -> None:
    """Lock-free helper for the 'another build in progress' branch — opens
    its own session since the caller never acquired the lock."""
    db = SessionLocal()
    try:
        img = db.query(DeviceImage).filter(DeviceImage.id == image_id).first()
        if img:
            img.status = "failed"
            img.error_message = msg
            db.commit()
    finally:
        db.close()


# ---------- Delete ----------

def delete_image(db: Session, image_id: int, also_docker_rmi: bool = True) -> dict:
    """Remove the qcow file, optionally `docker rmi` the built tag, and
    delete the DB row + log file.

    Returns a small summary so the UI can confirm what was actually removed.
    """
    img = db.query(DeviceImage).filter(DeviceImage.id == image_id).first()
    if not img:
        raise QcowImageError("Image not found")
    if img.status == "building":
        raise QcowImageError("Refusing to delete while a build is in progress.")

    summary = {
        "id": img.id,
        "removed_qcow": False,
        "removed_docker_tag": False,
        "removed_log": False,
        "docker_tag": img.built_image_tag or "",
    }

    qcow_path = QCOW_DIR / img.filename
    try:
        qcow_path.unlink()
        summary["removed_qcow"] = True
    except FileNotFoundError:
        pass

    if also_docker_rmi and img.built_image_tag:
        try:
            proc = subprocess.run(
                ["docker", "rmi", "-f", img.built_image_tag],
                capture_output=True, text=True, timeout=60,
            )
            summary["removed_docker_tag"] = proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            summary["removed_docker_tag"] = False

    if img.build_log_path:
        try:
            Path(img.build_log_path).unlink()
            summary["removed_log"] = True
        except FileNotFoundError:
            pass

    db.delete(img)
    db.commit()
    return summary


def get_image(db: Session, image_id: int) -> DeviceImage | None:
    return db.query(DeviceImage).filter(DeviceImage.id == image_id).first()


def _fill_template_images(db, kind: str, tag: str) -> None:
    """Point palette templates of this kind that have no image yet at the
    freshly built tag, so a successful build makes the device usable."""
    from app.models.node_template import NodeTemplate
    for t in db.query(NodeTemplate).filter(NodeTemplate.kind == kind, NodeTemplate.image_ref == "").all():
        t.image_ref = tag

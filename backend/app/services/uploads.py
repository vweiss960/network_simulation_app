"""Resumable uploads for large device images.

The browser sends a file as fixed-size chunks, each tagged with its byte
offset. The partial file on disk is the source of truth for progress, so an
upload survives dropped connections, a sleeping laptop, a page reload or a
backend restart: the client asks how much arrived and continues from there.

An upload's id is derived from (user, file name, size, mtime, purpose) so
picking the same file again finds the same session.

Layout: $DEVICE_IMAGES_DIR/.uploads/<id>.part + <id>.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from app.config import DEVICE_IMAGES_DIR

UPLOAD_DIR = DEVICE_IMAGES_DIR / ".uploads"
MAX_CHUNK = 64 * 1024 * 1024
STALE_SECONDS = 7 * 24 * 3600
_ID = re.compile(r"^[0-9a-f]{24}$")


class UploadError(ValueError):
    pass


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "").strip()
    name = re.sub(r"[^A-Za-z0-9._+-]", "_", name)
    if not name or name.startswith("."):
        raise UploadError("Invalid file name")
    return name


def _paths(upload_id: str) -> tuple[Path, Path]:
    if not _ID.match(upload_id or ""):
        raise UploadError("Invalid upload id")
    return UPLOAD_DIR / f"{upload_id}.part", UPLOAD_DIR / f"{upload_id}.json"


def _meta(upload_id: str) -> dict:
    _, mp = _paths(upload_id)
    try:
        return json.loads(mp.read_text())
    except (OSError, json.JSONDecodeError):
        raise UploadError("Upload not found (it may have finished or been cancelled)")


def _status(meta: dict) -> dict:
    part, _ = _paths(meta["id"])
    received = part.stat().st_size if part.exists() else 0
    return {**meta, "received": received, "complete": received >= meta["size"]}


def start(user: str, filename: str, size: int, last_modified: int, purpose: str, platform: str = "") -> dict:
    if purpose not in ("qcow", "tarball"):
        raise UploadError("purpose must be 'qcow' or 'tarball'")
    if size <= 0:
        raise UploadError("Empty file")
    name = _safe_name(filename)
    free = shutil.disk_usage(DEVICE_IMAGES_DIR).free if DEVICE_IMAGES_DIR.exists() else 0
    upload_id = hashlib.sha256(f"{user}|{name}|{size}|{last_modified}|{purpose}".encode()).hexdigest()[:24]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    part, mp = _paths(upload_id)
    if mp.exists():
        meta = _meta(upload_id)
        if platform and platform != meta.get("platform"):
            meta["platform"] = platform
            mp.write_text(json.dumps(meta))
        return _status(meta)
    if free and size > free:
        raise UploadError(f"Not enough disk space: the file is {size >> 20} MB, {free >> 20} MB free")
    meta = {"id": upload_id, "user": user, "filename": name, "size": size, "purpose": purpose,
            "platform": platform, "started_at": time.time()}
    part.touch()
    mp.write_text(json.dumps(meta))
    return _status(meta)


def status(upload_id: str) -> dict:
    return _status(_meta(upload_id))


async def write_chunk(upload_id: str, offset: int, stream) -> dict:
    """Append the request body at `offset`. A retried chunk (offset below what
    we have) truncates back to the offset first, so it can't be duplicated."""
    meta = _meta(upload_id)
    part, mp = _paths(upload_id)
    have = part.stat().st_size if part.exists() else 0
    if offset > have:
        raise UploadError(f"offset {offset} is past the {have} bytes received — resume from {have}")
    written = 0
    with open(part, "r+b") as f:
        f.truncate(offset)
        f.seek(offset)
        async for chunk in stream:
            written += len(chunk)
            if written > MAX_CHUNK or offset + written > meta["size"]:
                f.truncate(offset)
                raise UploadError("Chunk too large")
            f.write(chunk)
    os.utime(mp)
    return _status(meta)


def finish(upload_id: str) -> tuple[dict, Path]:
    """Move a complete upload into place; returns (meta, final path)."""
    meta = _meta(upload_id)
    st = _status(meta)
    if not st["complete"]:
        raise UploadError(f"Upload incomplete: {st['received']} of {meta['size']} bytes")
    part, mp = _paths(upload_id)
    dest_dir = DEVICE_IMAGES_DIR / "qcow" if meta["purpose"] == "qcow" else DEVICE_IMAGES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / meta["filename"]
    if dest.exists():
        raise UploadError(f"{meta['filename']} already exists on the server — delete it first")
    os.replace(part, dest)
    mp.unlink(missing_ok=True)
    return meta, dest


def cancel(upload_id: str) -> None:
    part, mp = _paths(upload_id)
    part.unlink(missing_ok=True)
    mp.unlink(missing_ok=True)


def list_unfinished(user: str | None = None) -> list[dict]:
    """Unfinished uploads (stale ones older than a week are removed)."""
    if not UPLOAD_DIR.exists():
        return []
    out = []
    for mp in UPLOAD_DIR.glob("*.json"):
        try:
            meta = json.loads(mp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if time.time() - mp.stat().st_mtime > STALE_SECONDS:
            cancel(meta["id"])
            continue
        if user is None or meta.get("user") == user:
            out.append(_status(meta))
    return sorted(out, key=lambda m: m.get("started_at", 0), reverse=True)

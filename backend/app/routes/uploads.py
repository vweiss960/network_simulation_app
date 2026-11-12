"""Resumable chunked uploads (see services/uploads.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.models.database import get_db
from app.services import uploads
from app.services.uploads import UploadError

router = APIRouter(prefix="/api/lab/uploads", tags=["uploads"])


class StartIn(BaseModel):
    filename: str
    size: int
    last_modified: int = 0
    purpose: str            # qcow | tarball
    platform: str = ""      # qcow only


def _owned(upload_id: str, user) -> dict:
    try:
        st = uploads.status(upload_id)
    except UploadError as e:
        raise HTTPException(404, str(e))
    if st["user"] != user.username and not getattr(user, "is_admin", False):
        raise HTTPException(404, "Upload not found")
    return st


@router.get("/")
def list_uploads(user=Depends(get_current_user)):
    return {"uploads": uploads.list_unfinished(None if user.is_admin else user.username)}


@router.post("/")
def start_upload(body: StartIn, user=Depends(get_current_user)):
    if body.purpose == "qcow":
        from app.services.qcow_images import PLATFORMS, QcowImageError, check_platform
        if body.platform not in PLATFORMS:
            raise HTTPException(400, f"Pick a platform ({', '.join(PLATFORMS)})")
        try:
            check_platform(body.filename, body.platform)
        except QcowImageError as e:
            raise HTTPException(400, str(e))
    try:
        return uploads.start(user.username, body.filename, body.size, body.last_modified, body.purpose, body.platform)
    except UploadError as e:
        raise HTTPException(400, str(e))


@router.get("/{upload_id}")
def upload_status(upload_id: str, user=Depends(get_current_user)):
    return _owned(upload_id, user)


@router.put("/{upload_id}")
async def put_chunk(upload_id: str, offset: int, request: Request, user=Depends(get_current_user)):
    _owned(upload_id, user)
    try:
        return await uploads.write_chunk(upload_id, offset, request.stream())
    except UploadError as e:
        raise HTTPException(409, str(e))


@router.post("/{upload_id}/complete")
def complete_upload(upload_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _owned(upload_id, user)
    try:
        meta, dest = uploads.finish(upload_id)
    except UploadError as e:
        raise HTTPException(409, str(e))
    if meta["purpose"] == "qcow":
        from app.services import qcow_images as qi
        try:
            img = qi.register_existing(db, dest.name, meta["platform"])
        except qi.QcowImageError as e:
            raise HTTPException(400, f"Uploaded, but registering failed: {e}")
        return {"ok": True, "filename": img.filename, "image_id": img.id, "platform": img.platform}
    return {"ok": True, "filename": dest.name}


@router.delete("/{upload_id}")
def cancel_upload(upload_id: str, user=Depends(get_current_user)):
    _owned(upload_id, user)
    uploads.cancel(upload_id)
    return {"ok": True}

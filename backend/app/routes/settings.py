from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.global_settings import GlobalSettings, DEFAULT_SETTINGS
from app.schemas.schemas import GlobalSettingUpdate, GlobalSettingOut

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/", response_model=list[GlobalSettingOut])
def list_settings(db: Session = Depends(get_db)):
    return db.query(GlobalSettings).all()


@router.put("/{key}", response_model=GlobalSettingOut)
def update_setting(key: str, data: GlobalSettingUpdate, db: Session = Depends(get_db)):
    setting = db.query(GlobalSettings).filter(GlobalSettings.key == key).first()
    if not setting:
        raise HTTPException(404, "Setting not found")
    setting.value = data.value
    db.commit()
    db.refresh(setting)
    return setting

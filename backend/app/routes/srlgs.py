from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.srlg import SrlgGroup, SrlgMember
from app.schemas.schemas import SrlgCreate, SrlgUpdate, SrlgOut, SrlgMemberSpec

router = APIRouter(prefix="/api/topologies/{topology_id}/srlgs", tags=["srlgs"])


def _srlg_to_out(srlg: SrlgGroup) -> dict:
    return {
        "id": srlg.id,
        "topology_id": srlg.topology_id,
        "name": srlg.name,
        "description": srlg.description or "",
        "srlg_type": srlg.srlg_type or "custom",
        "members": [
            {"member_type": m.member_type, "member_id": m.member_id}
            for m in srlg.members
        ],
    }


@router.get("/")
def list_srlgs(topology_id: int, db: Session = Depends(get_db)):
    srlgs = db.query(SrlgGroup).filter(SrlgGroup.topology_id == topology_id).all()
    return [_srlg_to_out(s) for s in srlgs]


@router.post("/")
def create_srlg(topology_id: int, body: SrlgCreate, db: Session = Depends(get_db)):
    srlg = SrlgGroup(
        topology_id=topology_id,
        name=body.name,
        description=body.description,
        srlg_type=body.srlg_type,
    )
    db.add(srlg)
    db.flush()
    for m in body.members:
        db.add(SrlgMember(srlg_id=srlg.id, member_type=m.member_type, member_id=m.member_id))
    db.commit()
    db.refresh(srlg)
    return _srlg_to_out(srlg)


@router.get("/{srlg_id}")
def get_srlg(topology_id: int, srlg_id: int, db: Session = Depends(get_db)):
    srlg = db.query(SrlgGroup).filter(
        SrlgGroup.id == srlg_id, SrlgGroup.topology_id == topology_id
    ).first()
    if not srlg:
        raise HTTPException(status_code=404, detail="SRLG not found")
    return _srlg_to_out(srlg)


@router.put("/{srlg_id}")
def update_srlg(topology_id: int, srlg_id: int, body: SrlgUpdate, db: Session = Depends(get_db)):
    srlg = db.query(SrlgGroup).filter(
        SrlgGroup.id == srlg_id, SrlgGroup.topology_id == topology_id
    ).first()
    if not srlg:
        raise HTTPException(status_code=404, detail="SRLG not found")
    if body.name is not None:
        srlg.name = body.name
    if body.description is not None:
        srlg.description = body.description
    if body.srlg_type is not None:
        srlg.srlg_type = body.srlg_type
    if body.members is not None:
        # Replace all members
        db.query(SrlgMember).filter(SrlgMember.srlg_id == srlg.id).delete()
        for m in body.members:
            db.add(SrlgMember(srlg_id=srlg.id, member_type=m.member_type, member_id=m.member_id))
    db.commit()
    db.refresh(srlg)
    return _srlg_to_out(srlg)


@router.delete("/{srlg_id}")
def delete_srlg(topology_id: int, srlg_id: int, db: Session = Depends(get_db)):
    srlg = db.query(SrlgGroup).filter(
        SrlgGroup.id == srlg_id, SrlgGroup.topology_id == topology_id
    ).first()
    if not srlg:
        raise HTTPException(status_code=404, detail="SRLG not found")
    db.delete(srlg)
    db.commit()
    return {"ok": True}

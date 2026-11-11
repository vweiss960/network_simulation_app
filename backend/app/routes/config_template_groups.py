"""Template group CRUD + member management.

Mounted alongside config_templates router behind PHANERO_CONFIG_DESIGN_ENABLED.
Groups stitch ordered ConfigTemplate rows into a composite config for a
single device — see app.services.config_renderer.render_group_for_node.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.models.database import get_db
from app.models.config_template import ConfigTemplate
from app.models.config_template_group import ConfigTemplateGroup, ConfigTemplateGroupMember
from app.services.config_renderer import demote_default_owners
from app.models.node_instance import NodeInstance


router = APIRouter(prefix="/api/config-template-groups", tags=["config_design"])


# ---------------------- Schemas ----------------------

class GroupMemberOut(BaseModel):
    id: int
    template_id: int
    template_name: str
    position: int
    body_length: int

    class Config:
        from_attributes = True


class GroupCreate(BaseModel):
    name: str
    description: str = ""
    # Default rule: platform_default = VALUE (name kept for API compat);
    # match_attr = which device attribute it compares against.
    platform_default: str = ""
    match_attr: str = "platform"


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    platform_default: Optional[str] = None
    match_attr: Optional[str] = None


class GroupOut(BaseModel):
    id: int
    name: str
    description: str
    platform_default: str
    match_attr: str = "platform"
    members: list[GroupMemberOut]


class AddMemberIn(BaseModel):
    template_id: int
    position: Optional[int] = None  # None → append to the end


class ReorderIn(BaseModel):
    # Ordered list of member ids — the new position for each is its index here.
    member_ids: list[int]


class AssignGroupIn(BaseModel):
    config_template_group_id: Optional[int] = None


# ---------------------- Serializer ----------------------

def _group_to_out(g: ConfigTemplateGroup) -> GroupOut:
    members = sorted(g.members, key=lambda m: m.position)
    return GroupOut(
        id=g.id, name=g.name, description=g.description or "",
        platform_default=g.platform_default or "",
        match_attr=g.match_attr or "platform",
        members=[
            GroupMemberOut(
                id=m.id, template_id=m.template_id,
                template_name=(m.template.name if m.template else f"#{m.template_id}"),
                position=m.position,
                body_length=len(m.template.body or "") if m.template else 0,
            )
            for m in members
        ],
    )


# ---------------------- CRUD ----------------------

@router.get("/", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db)):
    rows = (
        db.query(ConfigTemplateGroup)
        .options(selectinload(ConfigTemplateGroup.members).selectinload(ConfigTemplateGroupMember.template))
        .order_by(ConfigTemplateGroup.name)
        .all()
    )
    return [_group_to_out(g) for g in rows]


@router.post("/", response_model=GroupOut)
def create_group(data: GroupCreate, db: Session = Depends(get_db)):
    if db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.name == data.name).first():
        raise HTTPException(status_code=409, detail=f"Group '{data.name}' already exists")
    # Only one group can own a given (match_attr, value) pair at a time.
    demote_default_owners(db, ConfigTemplateGroup, data.match_attr or "platform", data.platform_default)
    g = ConfigTemplateGroup(name=data.name, description=data.description,
                            platform_default=data.platform_default,
                            match_attr=data.match_attr or "platform")
    db.add(g)
    db.commit()
    db.refresh(g)
    return _group_to_out(g)


@router.get("/{group_id}", response_model=GroupOut)
def get_group(group_id: int, db: Session = Depends(get_db)):
    g = (
        db.query(ConfigTemplateGroup)
        .options(selectinload(ConfigTemplateGroup.members).selectinload(ConfigTemplateGroupMember.template))
        .filter(ConfigTemplateGroup.id == group_id).first()
    )
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    return _group_to_out(g)


@router.put("/{group_id}", response_model=GroupOut)
def update_group(group_id: int, data: GroupUpdate, db: Session = Depends(get_db)):
    g = db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    if data.name is not None and data.name != g.name:
        if db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.name == data.name).first():
            raise HTTPException(status_code=409, detail=f"Group '{data.name}' already exists")
        g.name = data.name
    if data.description is not None:
        g.description = data.description
    # Default rule change — effective (attr, value) pair, demote other owners.
    new_attr = data.match_attr if data.match_attr is not None else (g.match_attr or "platform")
    new_value = data.platform_default if data.platform_default is not None else g.platform_default
    if new_attr != (g.match_attr or "platform") or new_value != g.platform_default:
        demote_default_owners(db, ConfigTemplateGroup, new_attr, new_value, exclude_id=g.id)
        g.match_attr = new_attr
        g.platform_default = new_value
    db.commit()
    db.refresh(g)
    return _group_to_out(g)


@router.delete("/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)):
    g = db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    # Clear per-device overrides pointing here so we don't dangle FKs.
    db.query(NodeInstance).filter(NodeInstance.config_template_group_id == g.id).update(
        {NodeInstance.config_template_group_id: None}
    )
    db.delete(g)
    db.commit()
    return {"ok": True}


# ---------------------- Member management ----------------------

@router.post("/{group_id}/members", response_model=GroupOut)
def add_member(group_id: int, data: AddMemberIn, db: Session = Depends(get_db)):
    g = db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    t = db.query(ConfigTemplate).filter(ConfigTemplate.id == data.template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if db.query(ConfigTemplateGroupMember).filter(
        ConfigTemplateGroupMember.group_id == group_id,
        ConfigTemplateGroupMember.template_id == data.template_id,
    ).first():
        raise HTTPException(status_code=409, detail="Template already in this group")
    # Default position = end of current member list.
    if data.position is not None:
        pos = data.position
    else:
        max_pos = db.query(ConfigTemplateGroupMember).filter(
            ConfigTemplateGroupMember.group_id == group_id
        ).count()
        pos = max_pos
    m = ConfigTemplateGroupMember(group_id=group_id, template_id=data.template_id, position=pos)
    db.add(m)
    db.commit()
    db.refresh(g)
    return _group_to_out(g)


@router.delete("/{group_id}/members/{member_id}")
def remove_member(group_id: int, member_id: int, db: Session = Depends(get_db)):
    m = db.query(ConfigTemplateGroupMember).filter(
        ConfigTemplateGroupMember.id == member_id,
        ConfigTemplateGroupMember.group_id == group_id,
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.put("/{group_id}/reorder", response_model=GroupOut)
def reorder_members(group_id: int, data: ReorderIn, db: Session = Depends(get_db)):
    """Reorder members in one shot. `member_ids` is the new order; each
    member's position becomes its index in that list. Members not listed
    are pushed to the end in their existing relative order — so the
    frontend can send just the affected slice if it wants."""
    g = db.query(ConfigTemplateGroup).filter(ConfigTemplateGroup.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    members = db.query(ConfigTemplateGroupMember).filter(
        ConfigTemplateGroupMember.group_id == group_id
    ).all()
    by_id = {m.id: m for m in members}
    pos = 0
    for mid in data.member_ids:
        m = by_id.pop(mid, None)
        if m is None:
            continue
        m.position = pos
        pos += 1
    # Trailing members keep their order amongst each other.
    for m in sorted(by_id.values(), key=lambda x: x.position):
        m.position = pos
        pos += 1
    db.commit()
    db.refresh(g)
    return _group_to_out(g)


# ---------------------- Assignment to nodes ----------------------

@router.put("/assign/{node_id}")
def assign_group(node_id: int, data: AssignGroupIn, db: Session = Depends(get_db)):
    """Set or clear the per-device template-group override. Passing null
    reverts the device to whatever resolves via single-template or
    platform_default."""
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if data.config_template_group_id is not None:
        g = db.query(ConfigTemplateGroup).filter(
            ConfigTemplateGroup.id == data.config_template_group_id
        ).first()
        if not g:
            raise HTTPException(status_code=404, detail="Group not found")
    node.config_template_group_id = data.config_template_group_id
    db.commit()
    return {"ok": True, "node_id": node.id, "config_template_group_id": node.config_template_group_id}

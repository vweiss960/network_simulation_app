"""Config Design routes — CRUD on config templates plus render endpoints.

Gated behind PHANERO_CONFIG_DESIGN_ENABLED at registration time in main.py.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.config_template import ConfigTemplate
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.services.config_renderer import (
    validate_template, render_for_node, resolve_template_for_node, build_context,
    resolve_for_node, render_group_for_node, demote_default_owners,
)


router = APIRouter(prefix="/api/config-templates", tags=["config_design"])


# ---------------------- Schemas ----------------------

class ConfigTemplateCreate(BaseModel):
    name: str
    description: str = ""
    body: str = ""
    # Default rule: platform_default is the VALUE (name kept for API
    # compatibility); match_attr picks the device attribute it compares
    # against (platform/vendor/model/role/location/device_template or a
    # custom device-attribute key).
    platform_default: str = ""
    match_attr: str = "platform"


class ConfigTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    body: Optional[str] = None
    platform_default: Optional[str] = None
    match_attr: Optional[str] = None


class ConfigTemplateOut(BaseModel):
    id: int
    name: str
    description: str
    body: str
    platform_default: str
    match_attr: str = "platform"

    class Config:
        from_attributes = True


class ValidateRequest(BaseModel):
    body: str


class RenderRequest(BaseModel):
    body: str
    node_id: int


class AssignRequest(BaseModel):
    config_template_id: Optional[int] = None


# ---------------------- CRUD ----------------------

@router.get("/", response_model=list[ConfigTemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return db.query(ConfigTemplate).order_by(ConfigTemplate.name).all()


@router.post("/", response_model=ConfigTemplateOut)
def create_template(data: ConfigTemplateCreate, db: Session = Depends(get_db)):
    if db.query(ConfigTemplate).filter(ConfigTemplate.name == data.name).first():
        raise HTTPException(status_code=409, detail=f"Template '{data.name}' already exists")
    # If a default rule is set, demote any existing template that owns the
    # same (match_attr, value) pair — one owner per pair. Rules on OTHER
    # attributes are untouched.
    demote_default_owners(db, ConfigTemplate, data.match_attr or "platform", data.platform_default)
    t = ConfigTemplate(
        name=data.name,
        description=data.description,
        body=data.body,
        platform_default=data.platform_default,
        match_attr=data.match_attr or "platform",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("/{template_id}", response_model=ConfigTemplateOut)
def get_template(template_id: int, db: Session = Depends(get_db)):
    t = db.query(ConfigTemplate).filter(ConfigTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@router.put("/{template_id}", response_model=ConfigTemplateOut)
def update_template(template_id: int, data: ConfigTemplateUpdate, db: Session = Depends(get_db)):
    t = db.query(ConfigTemplate).filter(ConfigTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if data.name is not None and data.name != t.name:
        if db.query(ConfigTemplate).filter(ConfigTemplate.name == data.name).first():
            raise HTTPException(status_code=409, detail=f"Template '{data.name}' already exists")
        t.name = data.name
    if data.description is not None:
        t.description = data.description
    if data.body is not None:
        t.body = data.body
    # Default rule change — evaluate the EFFECTIVE (attr, value) pair (either
    # side may be updated alone) and demote any other owner of that pair.
    new_attr = data.match_attr if data.match_attr is not None else (t.match_attr or "platform")
    new_value = data.platform_default if data.platform_default is not None else t.platform_default
    if new_attr != (t.match_attr or "platform") or new_value != t.platform_default:
        demote_default_owners(db, ConfigTemplate, new_attr, new_value, exclude_id=t.id)
        t.match_attr = new_attr
        t.platform_default = new_value
    db.commit()
    db.refresh(t)
    return t


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    t = db.query(ConfigTemplate).filter(ConfigTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    # Clear any per-device overrides pointing at this template so the FK
    # cascade doesn't accidentally delete devices.
    db.query(NodeInstance).filter(NodeInstance.config_template_id == t.id).update(
        {NodeInstance.config_template_id: None}
    )
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---------------------- Validate / Render ----------------------

@router.post("/validate")
def validate(req: ValidateRequest):
    """Compile-check a template body without saving. Used by the editor for
    live syntax feedback as the user types."""
    return validate_template(req.body)


@router.post("/render")
def render(req: RenderRequest, db: Session = Depends(get_db)):
    """Render a template body against a specific device. Does not persist."""
    node = db.query(NodeInstance).filter(NodeInstance.id == req.node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    links = db.query(Link).filter(
        (Link.source_node_id == node.id) | (Link.target_node_id == node.id)
    ).all()
    return render_for_node(req.body, node, links, db=db)


class RenderGroupRequest(BaseModel):
    group_id: int
    node_id: int


@router.post("/render-group")
def render_group(req: RenderGroupRequest, db: Session = Depends(get_db)):
    """Render every member template of a group, in order, against one device.
    Returns the same shape as /render. Does not persist — pure preview."""
    from sqlalchemy.orm import selectinload
    from app.models.config_template_group import ConfigTemplateGroup, ConfigTemplateGroupMember

    node = db.query(NodeInstance).filter(NodeInstance.id == req.node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    group = (
        db.query(ConfigTemplateGroup)
        .options(selectinload(ConfigTemplateGroup.members).selectinload(ConfigTemplateGroupMember.template))
        .filter(ConfigTemplateGroup.id == req.group_id).first()
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    links = db.query(Link).filter(
        (Link.source_node_id == node.id) | (Link.target_node_id == node.id)
    ).all()
    from app.services.config_renderer import render_group_for_node
    out = render_group_for_node(group, node, links, db=db)
    out["group_id"] = group.id
    out["group_name"] = group.name
    out["member_count"] = len(group.members)
    return out


@router.get("/context/{node_id}")
def get_context(node_id: int, db: Session = Depends(get_db)):
    """Return the variable namespace a template would see for this device.
    Useful for the editor's variable-inspector pane."""
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    links = db.query(Link).filter(
        (Link.source_node_id == node.id) | (Link.target_node_id == node.id)
    ).all()
    return build_context(node, links, db=db)


# ---------------------- Assign + bulk render ----------------------

@router.put("/assign/{node_id}")
def assign(node_id: int, data: AssignRequest, db: Session = Depends(get_db)):
    """Set or clear a per-device template override. Passing null reverts the
    device to its platform default."""
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if data.config_template_id is not None:
        t = db.query(ConfigTemplate).filter(ConfigTemplate.id == data.config_template_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
    node.config_template_id = data.config_template_id
    db.commit()
    return {"ok": True, "node_id": node.id, "config_template_id": node.config_template_id}


@router.post("/render-all/{topology_id}")
def render_all(topology_id: int, db: Session = Depends(get_db)):
    """Resolve + render every device twice: once with design-side interface
    names → NodeInstance.generated_config, and (when the node has a lab
    translation) once with lab-translated names → generated_lab_config.
    The lab variant is what gets fed to clab as startup-config on deploy.

    Skips the lab pass for non-deployable nodes (customer endpoints,
    nodes with no clab kind), keeping their generated_lab_config empty.
    """
    # IMPORTANT ordering: compute_lab_view internally calls
    # get_merged_topology_data, which EXPUNGES every NodeInstance and Link in
    # the topology chain from the session as a defensive measure (so its
    # in-memory edits can't accidentally persist). If we fetch nodes/links
    # FIRST and call compute_lab_view AFTER, SQLAlchemy's identity map means
    # the same Python objects we're holding get expunged out from under us —
    # subsequent `node.generated_config = ...` writes don't persist on commit,
    # and `link.source_node` lazy-loads raise DetachedInstanceError.
    # So: fetch the lab translation map first, THEN query nodes + links so
    # we end up holding session-bound instances.
    lab_iface_map_by_node: dict[int, dict] = {}
    lab_view_error: str = ""
    try:
        from app.services.lab_view import compute_lab_view
        view = compute_lab_view(db, topology_id)
        for ln in view.get("nodes", []):
            # Prefer the vendor-config form of the interface name (e.g.
            # GigabitEthernet0/0/0/1 for IOS-XR) since this map is what the
            # device's own running-config will be rendered against. Fall back
            # to the kernel-name map for kinds whose config uses the same
            # identifier (linux, juniper_crpd, …).
            iface_map = ln.get("lab_iface_config_translation") or ln.get("lab_iface_translation")
            if iface_map:
                lab_iface_map_by_node[ln["id"]] = iface_map
    except Exception as e:
        # Capture but don't fail — the design pass below still runs. The
        # captured message gets attached to the response so the UI can
        # surface it instead of pretending the lab pass succeeded.
        import traceback
        lab_view_error = f"{type(e).__name__}: {e}"
        traceback.print_exc()

    # NOW fetch nodes + links — these will be attached to the session and
    # any writes inside the loop persist via db.commit() at the end.
    from sqlalchemy.orm import selectinload
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).options(
        selectinload(Link.source_node),
        selectinload(Link.target_node),
    ).all()
    # Index links by node id once so the per-device loop is cheap.
    links_by_node: dict[int, list[Link]] = {}
    for link in links:
        links_by_node.setdefault(link.source_node_id, []).append(link)
        links_by_node.setdefault(link.target_node_id, []).append(link)

    # Default-rule rows change only via the template/group editors, so fetch
    # them once here instead of re-querying (bodies + members) for every node.
    from app.models.config_template_group import ConfigTemplateGroup
    tmpl_rows = db.query(ConfigTemplate).filter(ConfigTemplate.platform_default != "").all()
    group_rows = (db.query(ConfigTemplateGroup)
                  .options(selectinload(ConfigTemplateGroup.members))
                  .filter(ConfigTemplateGroup.platform_default != "").all())

    results = []
    for node in nodes:
        resolved = resolve_for_node(db, node, template_rows=tmpl_rows, group_rows=group_rows)
        if not resolved:
            results.append({"node_id": node.id, "name": node.name, "status": "no_template"})
            continue
        kind, target = resolved
        node_links = links_by_node.get(node.id, [])

        # ---- Design pass ----
        if kind == "group":
            outcome = render_group_for_node(target, node, node_links, db=db)
            target_info = {"group_id": target.id, "group_name": target.name}
        else:
            outcome = render_for_node(target.body, node, node_links, db=db)
            target_info = {"template_id": target.id, "template_name": target.name}

        if not outcome["ok"]:
            results.append({
                "node_id": node.id, "name": node.name, "status": "error",
                "kind": kind, **target_info,
                "error": outcome["error"], "line": outcome.get("line"),
            })
            continue

        node.generated_config = outcome["rendered"]
        result = {
            "node_id": node.id, "name": node.name, "status": "rendered",
            "kind": kind, **target_info,
            "chars": len(outcome["rendered"]),
            "lab_chars": 0,
        }

        # ---- Lab pass (when applicable) ----
        iface_map = lab_iface_map_by_node.get(node.id)
        if iface_map:
            if kind == "group":
                lab_outcome = render_group_for_node(target, node, node_links, db=db, iface_name_map=iface_map)
            else:
                lab_outcome = render_for_node(target.body, node, node_links, db=db, iface_name_map=iface_map)
            if lab_outcome["ok"]:
                node.generated_lab_config = lab_outcome["rendered"]
                result["lab_chars"] = len(lab_outcome["rendered"])
            else:
                # The design render succeeded but the lab variant didn't —
                # surface that without failing the whole row. Empty
                # generated_lab_config means deploy falls back to no
                # startup-config (clean container boot).
                node.generated_lab_config = ""
                result["lab_error"] = lab_outcome["error"]
        else:
            # No lab translation available for this node — clear the cache
            # so stale data from a previous render doesn't linger.
            node.generated_lab_config = ""

        results.append(result)
    db.commit()
    out: dict = {"topology_id": topology_id, "results": results}
    if lab_view_error:
        out["lab_view_error"] = lab_view_error
    out["lab_iface_map_node_count"] = len(lab_iface_map_by_node)
    return out


@router.get("/generated/{node_id}")
def get_generated(node_id: int, db: Session = Depends(get_db)):
    """Return the last cached render for a device, plus which template would
    apply right now (so the UI can show 'stale' state)."""
    node = db.query(NodeInstance).filter(NodeInstance.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    # Group-aware resolution (a device resolving to a GROUP used to report
    # template_name: null here even though render-all rendered it).
    resolved = resolve_for_node(db, node)
    kind, obj = resolved if resolved else (None, None)
    # Legacy fields keep their template-only meaning for old clients.
    t = obj if kind == "template" else None
    return {
        "node_id": node.id,
        "generated_config": node.generated_config or "",
        "generated_lab_config": node.generated_lab_config or "",
        "template_id": t.id if t else None,
        "template_name": t.name if t else None,
        "resolved_kind": kind,
        "resolved_name": obj.name if obj else None,
        "config_template_id": node.config_template_id,
        "config_template_group_id": node.config_template_group_id,
    }

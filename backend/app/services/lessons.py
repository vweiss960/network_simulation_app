"""Lessons: packs (import / export / validation / seeding), learner attempts
and the authoring lab.

A lesson pack is a zip (or a directory, for the bundled ones) with:

    lesson.yaml     metadata (slug, title, summary, level, track, topic, tags,
                    minutes, ai_policy, hints_mode, status, version, author)
    topology.yaml   topology_io native format: the starting state
    tasks.yaml      list of tasks: {id, title, body, hints, solution, checks}
    content.md      the written lesson (each "## " section is one reading)
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.models.lesson import Lesson, LessonAttempt
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.topology import Topology
from app.services import lesson_checks, topology_io
from app.services.device_kinds import get_kind, is_bridge, is_known_kind
from app.services.topology_ops import TopologyError

LEVELS = ("beginner", "intermediate", "advanced")
TRACKS = ("enterprise", "service_provider", "data_center", "automation", "security", "general")
AI_POLICIES = ("teach", "hints", "off")
HINT_MODES = ("open", "on_request", "none")
STATUSES = ("draft", "published")
DEFAULT_HINTS = {"beginner": "open", "intermediate": "on_request", "advanced": "none"}
META_FIELDS = ("slug", "title", "summary", "level", "track", "topic", "tags", "minutes", "ai_policy",
               "hints_mode", "status", "version", "author")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
MAX_PACK_BYTES = 5 * 1024 * 1024


class LessonError(Exception):
    pass


# --- validation ---------------------------------------------------------------------

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:70]
    return s if len(s) >= 2 else f"lesson-{s or 'x'}"


def topology_devices(topology_yaml: str) -> dict[str, dict]:
    """Device name → node spec of a lesson topology (Network nodes excluded)."""
    spec = topology_io.parse(topology_yaml or "name: empty\nnodes: []\nlinks: []\n")
    return {n["name"]: n for n in spec.get("nodes") or [] if n.get("name") and n.get("kind") != "bridge"}


def normalize_meta(meta: dict) -> dict:
    m = {k: meta.get(k) for k in META_FIELDS if meta.get(k) is not None}
    m["title"] = str(m.get("title") or "").strip()
    m["slug"] = str(m.get("slug") or slugify(m["title"])).strip()
    m["level"] = str(m.get("level") or "beginner").lower()
    m["track"] = str(m.get("track") or "general").lower().replace("-", "_").replace(" ", "_")
    m["ai_policy"] = str(m.get("ai_policy") or "hints").lower()
    m["hints_mode"] = str(m.get("hints_mode") or DEFAULT_HINTS.get(m["level"], "on_request")).lower()
    m["status"] = str(m.get("status") or "draft").lower()
    m["tags"] = [str(t).strip().lower() for t in (m.get("tags") or []) if str(t).strip()][:30]
    m["minutes"] = int(m.get("minutes") or 30)
    m["version"] = str(m.get("version") or "1.0")
    for k in ("summary", "topic", "author"):
        m[k] = str(m.get(k) or "")
    return m


def validate(meta: dict, topology_yaml: str, tasks: list, content_md: str = "") -> list[str]:
    """Every problem with a lesson (empty list when it's fine)."""
    errs = []
    if not meta.get("title"):
        errs.append("lesson: a title is required")
    if not _SLUG.match(meta.get("slug") or ""):
        errs.append("lesson: slug must be lowercase letters, digits and dashes")
    for key, allowed in (("level", LEVELS), ("track", TRACKS), ("ai_policy", AI_POLICIES),
                         ("hints_mode", HINT_MODES), ("status", STATUSES)):
        if meta.get(key) not in allowed:
            errs.append(f"lesson: {key} must be one of {', '.join(allowed)}")
    try:
        spec = topology_io.parse(topology_yaml or "nodes: []\nlinks: []\n")
        names = [n.get("name") for n in spec.get("nodes") or []]
        if len(names) != len(set(names)):
            errs.append("topology: device names must be unique")
        for n in spec.get("nodes") or []:
            if not is_known_kind(n.get("kind") or "linux"):
                errs.append(f"topology: {n.get('name')}: unknown device kind {n.get('kind')!r}")
        for row in spec.get("links") or []:
            ends = [row.get("a"), row.get("b")] if isinstance(row, dict) else [row[0], row[2]] if len(row) >= 4 else []
            if len(ends) != 2 or any(e not in names for e in ends):
                errs.append(f"topology: bad link {row!r}")
    except (TopologyError, KeyError, ValueError, TypeError, IndexError) as e:
        errs.append(f"topology: {e}")
        names = []
    devices = {n for n in names if n}
    if not isinstance(tasks, list):
        return errs + ["tasks: expected a list"]
    seen = set()
    for i, t in enumerate(tasks, 1):
        if not isinstance(t, dict):
            errs.append(f"task {i}: expected a mapping")
            continue
        tid = str(t.get("id") or "")
        where = f"task {i} ({tid or 'no id'})"
        if not re.match(r"^[A-Za-z0-9_-]{1,40}$", tid):
            errs.append(f"{where}: id must be letters, digits, '-' or '_'")
        if tid in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(tid)
        if not str(t.get("title") or "").strip():
            errs.append(f"{where}: a title is required")
        if not isinstance(t.get("hints") or [], list):
            errs.append(f"{where}: hints must be a list")
        sol = t.get("solution") or {}
        if not isinstance(sol, dict):
            errs.append(f"{where}: solution must map device → config")
        else:
            for d in sol:
                if d not in devices:
                    errs.append(f"{where}: solution for unknown device {d!r}")
        checks = t.get("checks") or []
        if not isinstance(checks, list):
            errs.append(f"{where}: checks must be a list")
            continue
        for j, c in enumerate(checks, 1):
            for e in lesson_checks.validate_check(c if isinstance(c, dict) else {}, devices):
                errs.append(f"{where}, check {j}: {e}")
    return errs


def normalize_tasks(tasks: list) -> list[dict]:
    out = []
    for t in tasks or []:
        out.append({
            "id": str(t.get("id")), "title": str(t.get("title") or "").strip(), "body": str(t.get("body") or ""),
            "hints": [str(h) for h in (t.get("hints") or [])],
            "solution": {str(k): str(v) for k, v in (t.get("solution") or {}).items()},
            "checks": [dict(c) for c in (t.get("checks") or [])],
        })
    return out


# --- pack import / export ------------------------------------------------------------

def _read_pack(files: dict[str, str]) -> tuple[dict, str, list, str]:
    if "lesson.yaml" not in files:
        raise LessonError("The pack has no lesson.yaml")
    try:
        meta = yaml.safe_load(files["lesson.yaml"]) or {}
        tasks = yaml.safe_load(files.get("tasks.yaml") or "[]") or []
    except yaml.YAMLError as e:
        raise LessonError(f"Not valid YAML: {e}")
    if not isinstance(meta, dict):
        raise LessonError("lesson.yaml must be a mapping")
    if "tasks" in meta and not files.get("tasks.yaml"):
        tasks = meta.pop("tasks") or []
    topo = files.get("topology.yaml")
    if topo is None and isinstance(meta.get("topology"), dict):
        topo = yaml.safe_dump(meta.pop("topology"), sort_keys=False, allow_unicode=True)
    return meta, topo or "", tasks, files.get("content.md", "")


def read_zip(data: bytes) -> tuple[dict, str, list, str]:
    if len(data) > MAX_PACK_BYTES:
        raise LessonError("The pack is too large (5 MB max)")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise LessonError("Not a zip file")
    files = {}
    for info in z.infolist():
        if info.is_dir() or info.file_size > MAX_PACK_BYTES:
            continue
        name = info.filename.rsplit("/", 1)[-1]       # tolerate one top folder
        if name in ("lesson.yaml", "topology.yaml", "tasks.yaml", "content.md"):
            files[name] = z.read(info).decode("utf-8", errors="replace")
    return _read_pack(files)


def read_dir(path: Path) -> tuple[dict, str, list, str]:
    files = {p.name: p.read_text() for p in path.iterdir()
             if p.is_file() and p.name in ("lesson.yaml", "topology.yaml", "tasks.yaml", "content.md")}
    return _read_pack(files)


def export_zip(lesson: Lesson) -> bytes:
    buf = io.BytesIO()
    meta = lesson_meta(lesson)
    meta["requires"] = requirements(lesson)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{lesson.slug}/lesson.yaml", yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
        z.writestr(f"{lesson.slug}/topology.yaml", lesson.topology_yaml or "")
        z.writestr(f"{lesson.slug}/tasks.yaml", _dump_tasks(lesson.tasks))
        z.writestr(f"{lesson.slug}/content.md", lesson.content_md or "")
    return buf.getvalue()


class _Literal(str):
    pass


def _literal_repr(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.SafeDumper.add_representer(_Literal, _literal_repr)


def _dump_tasks(tasks: list) -> str:
    """Tasks as readable YAML: multi-line text (bodies, solutions) as blocks."""
    def lit(v):
        if isinstance(v, str) and "\n" in v:
            return _Literal(v if v.endswith("\n") else v + "\n")
        if isinstance(v, dict):
            return {k: lit(x) for k, x in v.items()}
        if isinstance(v, list):
            return [lit(x) for x in v]
        return v
    return yaml.safe_dump(lit(tasks), sort_keys=False, allow_unicode=True, width=120)


def lesson_meta(lesson: Lesson) -> dict:
    return {"slug": lesson.slug, "title": lesson.title, "summary": lesson.summary or "", "level": lesson.level,
            "track": lesson.track, "topic": lesson.topic or "", "tags": lesson.tags, "minutes": lesson.minutes,
            "ai_policy": lesson.ai_policy, "hints_mode": lesson.hints_mode, "status": lesson.status,
            "version": lesson.version, "author": lesson.author or ""}


def save_lesson(db: Session, meta: dict, topology_yaml: str, tasks: list, content_md: str,
                lesson: Lesson | None = None, edited_in_app: bool = False) -> Lesson:
    """Validate and store (create, or update `lesson`)."""
    meta = normalize_meta(meta)
    errs = validate(meta, topology_yaml, tasks, content_md)
    if errs:
        raise LessonError("; ".join(errs[:12]) + (f" (+{len(errs) - 12} more)" if len(errs) > 12 else ""))
    clash = db.query(Lesson).filter(Lesson.slug == meta["slug"]).first()
    if clash and (lesson is None or clash.id != lesson.id):
        raise LessonError(f"A lesson with slug {meta['slug']!r} already exists")
    if lesson is None:
        lesson = Lesson(slug=meta["slug"], title=meta["title"])
        db.add(lesson)
    for k in ("slug", "title", "summary", "level", "track", "topic", "minutes", "ai_policy", "hints_mode",
              "status", "version", "author"):
        setattr(lesson, k, meta[k])
    lesson.tags = meta["tags"]
    lesson.topology_yaml = topology_yaml or ""
    lesson.tasks = normalize_tasks(tasks)
    lesson.content_md = content_md or ""
    if edited_in_app:
        lesson.edited_in_app = 1
    db.flush()
    return lesson


def import_pack(db: Session, meta: dict, topology_yaml: str, tasks: list, content_md: str,
                mode: str = "new") -> Lesson:
    """mode: new (409-style error if the slug exists), replace (update it),
    copy (import under a free slug)."""
    meta = normalize_meta(meta)
    existing = db.query(Lesson).filter(Lesson.slug == meta["slug"]).first()
    if existing and mode == "replace":
        return save_lesson(db, meta, topology_yaml, tasks, content_md, lesson=existing)
    if existing and mode == "copy":
        base, i = meta["slug"], 2
        while db.query(Lesson).filter(Lesson.slug == f"{base}-{i}").first():
            i += 1
        meta["slug"] = f"{base}-{i}"
        meta["title"] = f"{meta['title']} (copy)"
    elif existing:
        raise LessonExists(meta["slug"])
    return save_lesson(db, meta, topology_yaml, tasks, content_md)


class LessonExists(LessonError):
    def __init__(self, slug: str):
        super().__init__(f"A lesson with slug {slug!r} already exists")
        self.slug = slug


def _version_key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v or "0"))


def seed_bundled(db: Session, root: Path) -> list[str]:
    """Import the packs under <root>/lessons/*/: new ones, and newer versions
    of lessons nobody edited in the app."""
    done = []
    base = root / "lessons"
    if not base.is_dir():
        return done
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            meta, topo, tasks, content = read_dir(d)
            meta = normalize_meta(meta)
            cur = db.query(Lesson).filter(Lesson.slug == meta["slug"]).first()
            if cur is None:
                save_lesson(db, meta, topo, tasks, content)
            elif not cur.edited_in_app and _version_key(meta["version"]) > _version_key(cur.version):
                save_lesson(db, meta, topo, tasks, content, lesson=cur)
            else:
                continue
            db.commit()
            done.append(meta["slug"])
        except (LessonError, OSError) as e:
            db.rollback()
            print(f"[lessons] {d.name}: not imported: {e}", flush=True)
    return done


# --- summaries ---------------------------------------------------------------------------

_ICON_NOUN = {"router": "router", "switch": "switch", "l3switch": "switch", "host": "host", "server": "server",
              "firewall": "firewall", "workstation": "workstation", "cloud": "cloud", "pc": "host"}


def topology_summary(topology_yaml: str) -> str:
    """'5 routers · 2 hosts'"""
    try:
        devs = topology_devices(topology_yaml)
    except TopologyError:
        return ""
    counts: dict[str, int] = {}
    for n in devs.values():
        noun = _ICON_NOUN.get(str(n.get("icon") or "router"), "device")
        counts[noun] = counts.get(noun, 0) + 1
    order = ["router", "switch", "firewall", "server", "workstation", "host", "cloud", "device"]
    return " · ".join(f"{counts[k]} {k if counts[k] == 1 else (k + 'es' if k.endswith('ch') else k + 's')}"
                      for k in order if k in counts)


def requirements(lesson: Lesson) -> list[dict]:
    """Images the lesson's devices need: [{kind, image, devices}]."""
    try:
        devs = topology_devices(lesson.topology_yaml)
    except TopologyError:
        return []
    req: dict[tuple, list] = {}
    for name, n in devs.items():
        req.setdefault((n.get("kind") or "linux", n.get("image") or ""), []).append(name)
    return [{"kind": k, "label": get_kind(k).label, "image": img, "devices": sorted(v)} for (k, img), v in req.items()]


def content_sections(md: str) -> list[dict]:
    """The written lesson split at '## ' headings: [{title, body, minutes}]."""
    sections, cur = [], None
    for line in (md or "").splitlines():
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "lines": []}
            sections.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
        elif line.strip():
            cur = {"title": "Introduction", "lines": [line]}
            sections.append(cur)
    out = []
    for s in sections:
        body = "\n".join(s["lines"]).strip()
        out.append({"title": s["title"], "body": body, "minutes": max(1, round(len(body.split()) / 200))})
    return out


# --- attempts ----------------------------------------------------------------------------

def _build_lab(db: Session, lesson: Lesson, name: str, role: str) -> Topology:
    spec = topology_io.parse(lesson.topology_yaml or "nodes: []\nlinks: []\n")
    t = Topology(name=name, description=lesson.summary or "", lesson_id=lesson.id, lesson_role=role)
    db.add(t)
    db.flush()
    topology_io.build_into(db, t, spec)
    return t


def current_attempt(db: Session, lesson: Lesson, username: str) -> LessonAttempt | None:
    """This user's latest attempt whose lab still exists."""
    q = (db.query(LessonAttempt).filter(LessonAttempt.lesson_id == lesson.id, LessonAttempt.username == username)
         .order_by(LessonAttempt.id.desc()))
    for a in q:
        if a.topology_id and db.get(Topology, a.topology_id):
            return a
    return None


def start_attempt(db: Session, lesson: Lesson, username: str) -> tuple[LessonAttempt, bool]:
    """Resume this user's attempt, or build a fresh lab for a new one.
    Returns (attempt, created)."""
    a = current_attempt(db, lesson, username)
    if a:
        return a, False
    t = _build_lab(db, lesson, f"{lesson.title} — {username}" if username else lesson.title, "attempt")
    a = LessonAttempt(lesson_id=lesson.id, lesson_version=lesson.version, username=username, topology_id=t.id)
    db.add(a)
    db.flush()
    return a, True


def _rebuild(db: Session, lesson: Lesson, t: Topology) -> None:
    """Back to the lesson's starting topology (lab must already be wiped)."""
    db.flush()
    for l in db.query(Link).filter(Link.topology_id == t.id).all():
        db.delete(l)
    db.flush()
    for n in db.query(NodeInstance).filter(NodeInstance.topology_id == t.id).all():
        db.delete(n)
    db.flush()
    db.expire(t)
    topology_io.build_into(db, t, topology_io.parse(lesson.topology_yaml or "nodes: []\nlinks: []\n"))


def reset_attempt(db: Session, attempt: LessonAttempt) -> LessonAttempt:
    """Start over: wipe the lab and rebuild the starting topology."""
    from app.services import lab_service
    lesson = db.get(Lesson, attempt.lesson_id)
    t = db.get(Topology, attempt.topology_id) if attempt.topology_id else None
    if not lesson or not t:
        raise LessonError("This attempt's lab is gone; start the lesson again.")
    try:
        lab_service.destroy_lab(db, t.id)
    except lab_service.LabError as e:
        raise LessonError(f"Couldn't wipe the lab: {e}")
    _rebuild(db, lesson, t)
    attempt.results, attempt.hints = {}, {}
    attempt.completed_at = None
    attempt.started_at = datetime.utcnow()
    attempt.lesson_version = lesson.version
    db.flush()
    return attempt


def attempt_for_topology(db: Session, topology_id: int) -> LessonAttempt | None:
    return (db.query(LessonAttempt).filter(LessonAttempt.topology_id == topology_id)
            .order_by(LessonAttempt.id.desc()).first())


def check_attempt(db: Session, attempt: LessonAttempt, task_id: str | None = None) -> dict:
    """Run one task's checks (or, with no task, every task not passed yet)
    and record the results.

    A pass is kept: lessons build step by step, and a later task often
    changes what an earlier one checked (making an area stub removes the
    external route the previous task looked for). Re-checking a passed task
    records the fresh results (`changed_since` when they now fail) but the
    task stays passed."""
    lesson = db.get(Lesson, attempt.lesson_id)
    if not lesson or not attempt.topology_id:
        raise LessonError("This attempt has no lab")
    results = attempt.results
    if task_id:
        tasks = [t for t in lesson.tasks if t["id"] == task_id]
        if not tasks:
            raise LessonError(f"No task {task_id!r}")
    else:
        tasks = [t for t in lesson.tasks if not results.get(t["id"], {}).get("passed")]
    for t in tasks:
        new = lesson_checks.task_result(lesson_checks.run_checks(db, attempt.topology_id, t["checks"]))
        prev = results.get(t["id"]) or {}
        if prev.get("passed") and not new["passed"]:
            new = {**new, "passed": True, "passed_at": prev.get("passed_at") or prev.get("checked_at"),
                   "changed_since": True}
        elif new["passed"]:
            new["passed_at"] = prev.get("passed_at") or new["checked_at"]
        results[t["id"]] = new
    attempt.results = results
    if all(results.get(t["id"], {}).get("passed") for t in lesson.tasks) and lesson.tasks:
        attempt.completed_at = attempt.completed_at or datetime.utcnow()
    db.flush()
    return {t["id"]: results[t["id"]] for t in tasks}


def reveal_hint(db: Session, attempt: LessonAttempt, task_id: str) -> int:
    lesson = db.get(Lesson, attempt.lesson_id)
    task = next((t for t in lesson.tasks if t["id"] == task_id), None) if lesson else None
    if not task:
        raise LessonError(f"No task {task_id!r}")
    if lesson.hints_mode == "none":
        raise LessonError("This lesson has no hints")
    h = attempt.hints
    h[task_id] = min(len(task["hints"]), int(h.get(task_id, 0)) + 1)
    attempt.hints = h
    db.flush()
    return h[task_id]


# --- authoring -----------------------------------------------------------------------------

def authoring_topology(db: Session, lesson: Lesson) -> Topology | None:
    return (db.query(Topology).filter(Topology.lesson_id == lesson.id, Topology.lesson_role == "authoring")
            .order_by(Topology.id.desc()).first())


def open_authoring(db: Session, lesson: Lesson) -> Topology:
    t = authoring_topology(db, lesson)
    if t:
        return t
    return _build_lab(db, lesson, f"[Editing] {lesson.title}", "authoring")


def starting_config_from_running(kind: str, text: str) -> str:
    """A device's running config made into a lesson starting config: drop
    what the platform adds per lab (management interface / route, the clab
    user, SSH / NETCONF / gRPC), which would carry this lab's addresses."""
    fam = get_kind(kind).family
    mgmt = get_kind(kind).mgmt_iface
    user = get_kind(kind).default_user
    drop_blocks = ("username clab", "grpc", "line default", "netconf-yang agent", "ssh server", "hostname ",
                   f"interface {mgmt}", "vrf definition clab-mgmt", "ip vrf clab-mgmt") if fam != "linux" else ()
    out, skipping = [], False
    for line in text.splitlines():
        if skipping:
            if line.startswith((" ", "\t")):
                continue
            skipping = False
        platform_user = user and fam != "linux" and (line == f"username {user}"
                                                     or line.startswith(f"username {user} "))
        if platform_user or (drop_blocks and line.startswith(drop_blocks)):
            skipping = True
            continue
        if mgmt and mgmt in line and fam != "linux":
            continue                        # e.g. the mgmt default route under router static
        if "clab-mgmt" in line or line.startswith(("Building configuration", "!! Last configuration change")):
            continue
        if line.strip() == "end":
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def save_authoring(db: Session, lesson: Lesson, capture_running: bool = False) -> dict:
    """Write the authoring lab back into the lesson's topology. With
    capture_running, each running network device's live config becomes its
    starting config."""
    import asyncio
    from app.services import device_exec, lab_service
    t = authoring_topology(db, lesson)
    if not t:
        raise LessonError("This lesson has no authoring lab open")
    captured, failed = [], []
    if capture_running:
        state = lab_service.get_deployment_state(db, t.id)
        for n in db.query(NodeInstance).filter(NodeInstance.topology_id == t.id):
            if is_bridge(n.kind) or get_kind(n.kind).family == "linux" and "frr" not in (n.image_ref or "").lower():
                continue
            if (state["nodes"].get(n.id) or {}).get("state") != "running":
                continue
            try:
                cfg = asyncio.run(device_exec.running_config(device_exec.target_for(db, t.id, n.id)))
                n.startup_config = starting_config_from_running(n.kind, cfg)
                captured.append(n.name)
            except Exception as e:          # noqa: BLE001 — reported per device
                failed.append({"device": n.name, "error": str(e)})
        db.flush()
    spec = topology_io.export_topology(db, t.id)
    spec["name"] = lesson.title
    spec["description"] = lesson.summary or ""
    topo_yaml = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)
    errs = [e for e in validate(normalize_meta(lesson_meta(lesson)), topo_yaml, lesson.tasks) if e.startswith("task")]
    lesson.topology_yaml = topo_yaml
    lesson.edited_in_app = 1
    db.flush()
    return {"captured": captured, "failed": failed, "warnings": errs}

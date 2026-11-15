"""Modules: validation, packs (import / export / bundled seeding) and each
learner's progress through them.

Items are lessons (`lesson: <slug>`) or troubleshooting scenarios
(`scenario: <slug>`), in order.

A module pack is a zip (or a curriculum/modules/<slug>/ folder) with:
  module.yaml   metadata + `lessons:` — the ordered items
  overview.md   the module's introduction
  lessons/<slug>/{lesson.yaml, topology.yaml, tasks.yaml, content.md}
                (optional) the lesson packs themselves — exports include them,
                so a module moves between installations in one file.
  scenarios/<slug>/{scenario.yaml, topology.yaml, health.yaml, faults.yaml, docs.md}
                (optional) scenario packs — only in an admin's export: they
                contain the faults (the answer key).
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.models.lesson import Lesson, LessonAttempt
from app.models.module import Module
from app.models.scenario import Scenario, ScenarioAttempt
from app.services import lessons as lesson_svc
from app.services import troubleshooting as ts_svc

META_FIELDS = ("slug", "title", "summary", "level", "track", "blueprint", "tags", "status", "version", "author")
LESSON_FILES = ("lesson.yaml", "topology.yaml", "tasks.yaml", "content.md")
SCENARIO_MINUTES = 45            # a troubleshooting scenario's share of a module's time
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
MAX_PACK_BYTES = 40 * 1024 * 1024


class ModuleError(Exception):
    pass


class ModuleExists(ModuleError):
    def __init__(self, slug: str):
        super().__init__(f"A module with slug {slug!r} already exists")
        self.slug = slug


# --- validation ------------------------------------------------------------------------

def normalize_meta(meta: dict) -> dict:
    m = {k: meta.get(k) for k in META_FIELDS if meta.get(k) is not None}
    m["title"] = str(m.get("title") or "").strip()
    m["slug"] = str(m.get("slug") or lesson_svc.slugify(m["title"])).strip()
    m["level"] = str(m.get("level") or "intermediate").lower()
    m["track"] = str(m.get("track") or "general").lower().replace("-", "_").replace(" ", "_")
    m["status"] = str(m.get("status") or "draft").lower()
    m["tags"] = [str(t).strip().lower() for t in (m.get("tags") or []) if str(t).strip()][:30]
    m["version"] = str(m.get("version") or "1.0")
    for k in ("summary", "blueprint", "author"):
        m[k] = str(m.get(k) or "")
    return m


def normalize_items(items) -> list[dict]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            it = {"lesson": it}
        if not isinstance(it, dict):
            continue
        scenario = str(it.get("scenario") or "").strip()
        kind = "scenario" if scenario or it.get("kind") == "scenario" else "lesson"
        out.append({
            "kind": kind,
            "lesson": "" if kind == "scenario" else str(it.get("lesson") or "").strip(),
            "scenario": scenario if kind == "scenario" else "",
            "section": str(it.get("section") or "").strip(),
            "title": str(it.get("title") or "").strip(),
            "objectives": [str(o).strip() for o in (it.get("objectives") or []) if str(o).strip()],
        })
    return out


def validate(meta: dict, items: list) -> list[str]:
    errs = []
    if not meta.get("title"):
        errs.append("title is required")
    if not _SLUG.match(meta.get("slug") or ""):
        errs.append("slug must be lowercase letters, digits and dashes (2–80 characters)")
    if meta.get("level") not in lesson_svc.LEVELS:
        errs.append(f"level must be one of {', '.join(lesson_svc.LEVELS)}")
    if meta.get("track") not in lesson_svc.TRACKS:
        errs.append(f"track must be one of {', '.join(lesson_svc.TRACKS)}")
    if meta.get("status") not in lesson_svc.STATUSES:
        errs.append("status must be draft or published")
    seen = set()
    for i, it in enumerate(items, 1):
        kind = it.get("kind", "lesson")
        slug = it.get(kind) or ""
        if not _SLUG.match(slug):
            errs.append(f"item {i}: '{kind}' must be a {kind} slug")
        elif (kind, slug) in seen:
            errs.append(f"item {i}: {kind} {slug!r} is in the module twice")
        seen.add((kind, slug))
    return errs


# --- storage ---------------------------------------------------------------------------

def module_meta(m: Module) -> dict:
    return {"slug": m.slug, "title": m.title, "summary": m.summary or "", "level": m.level, "track": m.track,
            "blueprint": m.blueprint or "", "tags": m.tags, "status": m.status, "version": m.version,
            "author": m.author or ""}


def save_module(db: Session, meta: dict, items: list, overview_md: str = "",
                module: Module | None = None, edited_in_app: bool = False) -> Module:
    meta = normalize_meta(meta)
    items = normalize_items(items)
    errs = validate(meta, items)
    if errs:
        raise ModuleError("; ".join(errs[:12]))
    clash = db.query(Module).filter(Module.slug == meta["slug"]).first()
    if clash and (module is None or clash.id != module.id):
        raise ModuleError(f"A module with slug {meta['slug']!r} already exists")
    if module is None:
        module = Module(slug=meta["slug"], title=meta["title"])
        db.add(module)
    for k in ("slug", "title", "summary", "level", "track", "blueprint", "status", "version", "author"):
        setattr(module, k, meta[k])
    module.tags = meta["tags"]
    module.items = items
    module.overview_md = overview_md or ""
    if edited_in_app:
        module.edited_in_app = 1
    db.flush()
    return module


def items_of(m: Module) -> list[dict]:
    """A module's items, normalised (modules saved before scenarios existed
    have lesson-only items)."""
    return normalize_items(m.items)


def modules_for(db: Session, kind: str, slug: str) -> list[dict]:
    """The modules a lesson / scenario is part of ("Part of")."""
    out = []
    for m in db.query(Module).order_by(Module.title):
        for it in items_of(m):
            if it["kind"] == kind and it[kind] == slug:
                out.append({"id": m.id, "slug": m.slug, "title": m.title, "section": it.get("section", "")})
    return out


def modules_for_lesson(db: Session, slug: str) -> list[dict]:
    return modules_for(db, "lesson", slug)


# --- packs -----------------------------------------------------------------------------

def _parse(files: dict[str, str], lesson_files: dict[str, dict[str, str]],
           scenario_files: dict[str, dict[str, str]] | None = None):
    """(meta, items, overview, lesson packs, scenario packs)."""
    if "module.yaml" not in files:
        raise ModuleError("The pack has no module.yaml")
    try:
        meta = yaml.safe_load(files["module.yaml"]) or {}
    except yaml.YAMLError as e:
        raise ModuleError(f"module.yaml isn't valid YAML: {e}")
    if not isinstance(meta, dict):
        raise ModuleError("module.yaml must be a mapping")
    items = meta.pop("lessons", None) or meta.pop("items", None) or []
    packs = {}
    for slug, lf in lesson_files.items():
        try:
            packs[slug] = lesson_svc._read_pack(lf)
        except lesson_svc.LessonError as e:
            raise ModuleError(f"lessons/{slug}: {e}")
    spacks = {}
    for slug, sf in (scenario_files or {}).items():
        try:
            spacks[slug] = ts_svc.parse_files(sf)
        except ts_svc.ScenarioError as e:
            raise ModuleError(f"scenarios/{slug}: {e}")
    return meta, items, files.get("overview.md", ""), packs, spacks


def read_zip(data: bytes):
    if len(data) > MAX_PACK_BYTES:
        raise ModuleError("The pack is too large (40 MB max)")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ModuleError("Not a zip file")
    files, lesson_files, scenario_files = {}, {}, {}
    for info in z.infolist():
        if info.is_dir() or info.file_size > MAX_PACK_BYTES:
            continue
        parts = [p for p in info.filename.split("/") if p]
        name = parts[-1]
        sub = next((d for d in ("lessons", "scenarios") if d in parts[:-1]), None)
        if sub:
            i = parts.index(sub)
            allowed = LESSON_FILES if sub == "lessons" else ts_svc.PACK_FILES
            if len(parts) == i + 3 and name in allowed:                    # <sub>/<slug>/<file>
                target = lesson_files if sub == "lessons" else scenario_files
                target.setdefault(parts[i + 1], {})[name] = z.read(info).decode("utf-8", errors="replace")
            continue
        if name in ("module.yaml", "overview.md"):
            files[name] = z.read(info).decode("utf-8", errors="replace")
    return _parse(files, lesson_files, scenario_files)


def read_dir(path: Path):
    files = {p.name: p.read_text() for p in path.iterdir() if p.is_file() and p.name in ("module.yaml", "overview.md")}
    found = {}
    for sub, allowed in (("lessons", LESSON_FILES), ("scenarios", ts_svc.PACK_FILES)):
        found[sub] = {}
        if (path / sub).is_dir():
            for d in (path / sub).iterdir():
                if d.is_dir():
                    found[sub][d.name] = {p.name: p.read_text() for p in d.iterdir() if p.is_file() and p.name in allowed}
    return _parse(files, found["lessons"], found["scenarios"])


def export_zip(db: Session, m: Module, include_scenarios: bool = False) -> bytes:
    """The module plus every lesson it uses that's installed here — and, for
    admins, its scenarios (their packs are the answer key)."""
    buf = io.BytesIO()
    meta = module_meta(m)
    meta["lessons"] = [{k: v for k, v in it.items() if v not in ("", None) and k != "kind"} for it in items_of(m)]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{m.slug}/module.yaml", yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
        z.writestr(f"{m.slug}/overview.md", m.overview_md or "")
        for it in items_of(m):
            if it["kind"] == "lesson":
                l = db.query(Lesson).filter(Lesson.slug == it["lesson"]).first()
                pack, sub = (lesson_svc.export_zip(l), "lessons") if l else (None, None)
            else:
                sc = db.query(Scenario).filter(Scenario.slug == it["scenario"]).first() if include_scenarios else None
                pack, sub = (ts_svc.export_zip(sc), "scenarios") if sc else (None, None)
            if not pack:
                continue
            pz = zipfile.ZipFile(io.BytesIO(pack))
            for info in pz.infolist():
                z.writestr(f"{m.slug}/{sub}/{info.filename}", pz.read(info))
    return buf.getvalue()


def _newer(new_version: str, cur) -> bool:
    return not cur.edited_in_app and lesson_svc._version_key(new_version) > lesson_svc._version_key(cur.version)


def _install_lessons(db: Session, packs: dict, scenario_packs: dict | None = None) -> dict:
    """Bundled lesson / scenario packs: new ones are imported, newer versions
    replace ones nobody edited in the app, everything else is left alone."""
    report = {"imported": [], "updated": [], "kept": []}
    for slug, (meta, topo, tasks, content) in packs.items():
        meta = lesson_svc.normalize_meta(meta)
        cur = db.query(Lesson).filter(Lesson.slug == meta["slug"]).first()
        try:
            if cur is None:
                lesson_svc.save_lesson(db, meta, topo, tasks, content)
                report["imported"].append(meta["slug"])
            elif _newer(meta["version"], cur):
                lesson_svc.save_lesson(db, meta, topo, tasks, content, lesson=cur)
                report["updated"].append(meta["slug"])
            else:
                report["kept"].append(meta["slug"])
        except lesson_svc.LessonError as e:
            raise ModuleError(f"lessons/{slug}: {e}")
    for slug, pack in (scenario_packs or {}).items():
        meta = ts_svc.normalize_meta(pack["meta"])
        cur = db.query(Scenario).filter(Scenario.slug == meta["slug"]).first()
        try:
            if cur is None:
                ts_svc.save_scenario(db, pack)
                report["imported"].append(meta["slug"])
            elif _newer(meta["version"], cur):
                ts_svc.save_scenario(db, pack, scenario=cur)
                report["updated"].append(meta["slug"])
            else:
                report["kept"].append(meta["slug"])
        except ts_svc.ScenarioError as e:
            raise ModuleError(f"scenarios/{slug}: {e}")
    return report


def import_pack(db: Session, meta: dict, items: list, overview_md: str, packs: dict,
                scenario_packs: dict | None = None, mode: str = "new") -> tuple[Module, dict]:
    """mode: new (error if the slug exists), replace, copy."""
    meta = normalize_meta(meta)
    existing = db.query(Module).filter(Module.slug == meta["slug"]).first()
    if existing and mode not in ("replace", "copy"):
        raise ModuleExists(meta["slug"])
    report = _install_lessons(db, packs, scenario_packs)
    if existing and mode == "replace":
        return save_module(db, meta, items, overview_md, module=existing), report
    if existing:                                    # copy
        base, i = meta["slug"], 2
        while db.query(Module).filter(Module.slug == f"{base}-{i}").first():
            i += 1
        meta["slug"], meta["title"] = f"{base}-{i}", f"{meta['title']} (copy)"
    return save_module(db, meta, items, overview_md), report


def seed_bundled(db: Session, root: Path) -> list[str]:
    """Import curriculum/modules/*/: new modules, and newer versions of
    modules nobody edited in the app. Run after the bundled lessons and
    scenarios."""
    done = []
    base = root / "modules"
    if not base.is_dir():
        return done
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            meta, items, overview, packs, spacks = read_dir(d)
            meta = normalize_meta(meta)
            _install_lessons(db, packs, spacks)
            cur = db.query(Module).filter(Module.slug == meta["slug"]).first()
            if cur is None:
                save_module(db, meta, items, overview)
            elif _newer(meta["version"], cur):
                save_module(db, meta, items, overview, module=cur)
            else:
                db.commit()
                continue
            db.commit()
            done.append(meta["slug"])
        except (ModuleError, OSError) as e:
            db.rollback()
            print(f"[modules] {d.name}: not imported: {e}", flush=True)
    return done


# --- progress --------------------------------------------------------------------------

def lesson_status(db: Session, lesson: Lesson | None, username: str) -> dict:
    """not_installed | not_started | in_progress | completed, with task counts."""
    if lesson is None:
        return {"state": "not_installed"}
    a = lesson_svc.current_attempt(db, lesson, username)
    total = len(lesson.tasks)
    if a is None:
        return {"state": "not_started", "passed": 0, "total": total}
    passed = sum(1 for t in lesson.tasks if a.results.get(t["id"], {}).get("passed"))
    return {"state": "completed" if a.completed_at else "in_progress", "passed": passed, "total": total,
            "outdated": bool(a.lesson_version) and a.lesson_version != lesson.version}


def ever_completed(db: Session, lesson: Lesson, username: str) -> bool:
    return db.query(LessonAttempt).filter(LessonAttempt.lesson_id == lesson.id, LessonAttempt.username == username,
                                          LessonAttempt.completed_at.isnot(None)).first() is not None


def scenario_status(db: Session, sc: Scenario | None, username: str) -> dict:
    """A scenario is completed once the learner has solved a round (in any
    of their labs for it); in progress while they have a lab for it."""
    if sc is None:
        return {"state": "not_installed"}
    attempts = db.query(ScenarioAttempt).filter(ScenarioAttempt.scenario_id == sc.id,
                                                ScenarioAttempt.username == username).all()
    solved = sum(1 for a in attempts for h in a.history if h.get("outcome") == "solved")
    if solved:
        return {"state": "completed", "solved": solved}
    if ts_svc.current_attempt(db, sc, username):
        return {"state": "in_progress", "solved": 0}
    return {"state": "not_started", "solved": 0}


def progress(db: Session, m: Module, username: str) -> dict:
    """Per item: the lesson / scenario (if installed) and this user's state in
    it; plus the module's totals and the next item to do."""
    rows, done, minutes = [], 0, 0
    nxt = None
    for it in items_of(m):
        if it["kind"] == "scenario":
            sc = db.query(Scenario).filter(Scenario.slug == it["scenario"]).first()
            st = scenario_status(db, sc, username)
            ref = sc.id if sc else None
            if sc is not None:
                minutes += SCENARIO_MINUTES
        else:
            l = db.query(Lesson).filter(Lesson.slug == it["lesson"]).first()
            st = lesson_status(db, l, username)
            if l is not None and st["state"] != "completed" and ever_completed(db, l, username):
                st["state"] = "completed"           # done before, restarted since: still counts
            ref = l.id if l else None
            if l is not None:
                minutes += l.minutes or 0
        if st["state"] == "completed":
            done += 1
        elif nxt is None and ref is not None:
            nxt = {"kind": it["kind"], "id": ref}
        rows.append({**it, "lesson_id": ref if it["kind"] == "lesson" else None,
                     "scenario_id": ref if it["kind"] == "scenario" else None, "status": st})
    return {"items": rows, "completed": done, "total": len(rows), "minutes": minutes,
            "next_lesson_id": nxt["id"] if nxt and nxt["kind"] == "lesson" else None, "next_item": nxt}

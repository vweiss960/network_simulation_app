"""Troubleshooting scenarios: "Chaos" breaks a known-good network with faults
from a vetted catalog; the learner finds and fixes them with show commands.

A scenario pack (curriculum/troubleshooting/<slug>/ or a zip):
  scenario.yaml  metadata (slug, title, summary, level, track, tags,
                 protocols, max_faults, version, author)
  topology.yaml  the healthy baseline (topology_io format)
  health.yaml    checks (lesson check format) that all pass when healthy;
                 `scope: service` marks customer-visible ones
  faults.yaml    the catalog — see FAULT FORMAT below
  docs.md        network documentation (shown at Guided difficulty)

FAULT FORMAT
  - id, protocol (bgp | isis | ospf), category (adjacency | advertisement),
    title, explanation (markdown), find (how to spot it), leak_terms
    (words a generated ticket must never contain), variants:
      - id, scope [keys of what it touches — two faults sharing a key are
        never chosen together], reporter, ticket, hints [...],
        inject {device: config}, revert {device: config},
        symptoms [checks that fail while the fault is in place]
  Config may use placeholders {POLICY1} {ROUTEMAP1} {PREFIXLIST1}
  {KEYCHAIN1} {SECRET1} (any digit): each round fills them with neutral,
  plausible names, so nothing Chaos creates gives the fault away.

A round: wait for the lab → (revert the previous round) → health checks
must all pass → Chaos picks N faults (disjoint scopes, spread over
protocols / categories) → inject → wait for the symptoms → tickets (written
by the Chaos model when AI is on) → the learner works → Check: every
fault's symptoms clear and every health check passes → solved.
"""
from __future__ import annotations

import asyncio
import io
import json
import random
import re
import secrets
import string
import threading
import time
import zipfile
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.models.database import SessionLocal
from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.scenario import Scenario, ScenarioAttempt
from app.models.topology import Topology
from app.services import lesson_checks, topology_io
from app.services import lessons as lesson_svc

DIFFICULTIES = ("guided", "standard", "expert")
PROTOCOLS = ("bgp", "isis", "ospf")
CATEGORIES = ("adjacency", "advertisement")
MAX_FAULTS_CAP = 5
PACK_FILES = ("scenario.yaml", "topology.yaml", "health.yaml", "faults.yaml", "docs.md")
MAX_PACK_BYTES = 5 * 1024 * 1024

# Neutral, plausible names for whatever Chaos has to create. Nothing in a
# name may hint at the fault (no DENY / BLOCK / BAD / TEST / CHAOS…).
NAME_POOLS = {
    "POLICY": ["EDGE-IN", "EDGE-OUT", "PEER-IN", "PEER-OUT", "UPLINK-IN", "UPLINK-OUT", "IMPORT-STD",
               "EXPORT-STD", "IMPORT-V2", "EXPORT-V2", "TRANSIT-IN", "EBGP-IN", "CORE-POL", "POL-2207",
               "POL-0419", "STD-IN-2"],
    "ROUTEMAP": ["RM-EDGE-IN", "RM-PEER", "RM-UPLINK", "RM-0419", "RM-STD-IN", "RM-IMPORT-2", "RM-2207"],
    "PREFIXLIST": ["PL-0815", "PL-EDGE", "PL-STD", "NETS-01", "AGG-V2", "PL-2207", "PL-CORE-2"],
    "KEYCHAIN": ["KC-01", "KEYS-CORE", "KEYCHAIN-1", "KC-EDGE", "KC-2207"],
}
_PLACEHOLDER = re.compile(r"\{(POLICY|ROUTEMAP|PREFIXLIST|KEYCHAIN|SECRET)(\d)\}")
# Objects a config can define by name — Chaos may only use placeholders or
# names the baseline already has.
_DEFINES = re.compile(r"^\s*(?:route-policy|route-map|ip prefix-list|ipv6 prefix-list|prefix-set|community-set|"
                      r"as-path-set|key chain)\s+(\S+)", re.M | re.I)


class ScenarioError(Exception):
    pass


class ScenarioExists(ScenarioError):
    def __init__(self, slug: str):
        super().__init__(f"A scenario with slug {slug!r} already exists")
        self.slug = slug


# --- pack: parse / validate / store ------------------------------------------------------

def _yaml(text: str, what: str, default):
    try:
        v = yaml.safe_load(text or "")
    except yaml.YAMLError as e:
        raise ScenarioError(f"{what} isn't valid YAML: {e}")
    return default if v is None else v


def parse_files(files: dict[str, str]) -> dict:
    if "scenario.yaml" not in files:
        raise ScenarioError("The pack has no scenario.yaml")
    meta = _yaml(files["scenario.yaml"], "scenario.yaml", {})
    if not isinstance(meta, dict):
        raise ScenarioError("scenario.yaml must be a mapping")
    return {"meta": meta, "topology_yaml": files.get("topology.yaml", ""),
            "health": _yaml(files.get("health.yaml", ""), "health.yaml", []),
            "faults": _yaml(files.get("faults.yaml", ""), "faults.yaml", []),
            "docs_md": files.get("docs.md", "")}


def read_dir(path: Path) -> dict:
    return parse_files({p.name: p.read_text() for p in path.iterdir() if p.is_file() and p.name in PACK_FILES})


def read_zip(data: bytes) -> dict:
    if len(data) > MAX_PACK_BYTES:
        raise ScenarioError("The pack is too large (5 MB max)")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ScenarioError("Not a zip file")
    files = {}
    for info in z.infolist():
        name = info.filename.rsplit("/", 1)[-1]
        if not info.is_dir() and name in PACK_FILES and info.file_size <= MAX_PACK_BYTES:
            files[name] = z.read(info).decode("utf-8", errors="replace")
    return parse_files(files)


def normalize_meta(meta: dict) -> dict:
    m = dict(meta)
    m["title"] = str(m.get("title") or "").strip()
    m["slug"] = str(m.get("slug") or lesson_svc.slugify(m["title"])).strip()
    m["summary"] = str(m.get("summary") or "")
    m["level"] = str(m.get("level") or "advanced").lower()
    m["track"] = str(m.get("track") or "service_provider").lower()
    m["tags"] = [str(t).lower() for t in (m.get("tags") or [])]
    m["protocols"] = [str(p).lower() for p in (m.get("protocols") or [])]
    m["max_faults"] = max(1, min(MAX_FAULTS_CAP, int(m.get("max_faults") or 4)))
    m["status"] = str(m.get("status") or "published").lower()
    m["version"] = str(m.get("version") or "1.0")
    m["author"] = str(m.get("author") or "")
    return m


def _config_texts(v: dict):
    for part in ("inject", "revert"):
        for dev, cfg in (v.get(part) or {}).items():
            yield part, dev, str(cfg or "")


def validate(pack: dict) -> list[str]:
    errs = []
    meta = normalize_meta(pack["meta"])
    if not meta["title"]:
        errs.append("title is required")
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,79}$", meta["slug"]):
        errs.append("slug must be lowercase letters, digits and dashes")
    if meta["level"] not in lesson_svc.LEVELS:
        errs.append(f"level must be one of {', '.join(lesson_svc.LEVELS)}")
    try:
        devices = {d.lower() for d in lesson_svc.topology_devices(pack["topology_yaml"])}
    except Exception as e:  # noqa: BLE001 — reported
        return errs + [f"topology.yaml: {e}"]
    baseline = pack["topology_yaml"] or ""
    health = pack["health"] if isinstance(pack["health"], list) else []
    if not health:
        errs.append("health.yaml needs at least one check")
    for i, c in enumerate(health, 1):
        for e in lesson_checks.validate_check(c, devices):
            errs.append(f"health check {i}: {e}")
    faults = pack["faults"] if isinstance(pack["faults"], list) else []
    if not faults:
        errs.append("faults.yaml needs at least one fault")
    seen = set()
    for f in faults:
        fid = str((f or {}).get("id") or "")
        where = f"fault {fid or '?'}"
        if not fid or fid in seen:
            errs.append(f"{where}: needs a unique id")
        seen.add(fid)
        if f.get("protocol") not in PROTOCOLS:
            errs.append(f"{where}: protocol must be one of {', '.join(PROTOCOLS)}")
        if f.get("category") not in CATEGORIES:
            errs.append(f"{where}: category must be adjacency or advertisement")
        if not f.get("title") or not f.get("explanation"):
            errs.append(f"{where}: needs a title and an explanation")
        variants = f.get("variants") or []
        if not variants:
            errs.append(f"{where}: needs at least one variant")
        vseen = set()
        for v in variants:
            vid = str(v.get("id") or "")
            vw = f"{where} / {vid or '?'}"
            if not vid or vid in vseen:
                errs.append(f"{vw}: needs a unique id")
            vseen.add(vid)
            if not v.get("ticket"):
                errs.append(f"{vw}: needs a ticket")
            if not v.get("inject") or not v.get("revert"):
                errs.append(f"{vw}: needs inject and revert configs")
            if not v.get("symptoms"):
                errs.append(f"{vw}: needs symptom checks")
            for c in v.get("symptoms") or []:
                for e in lesson_checks.validate_check(c, devices):
                    errs.append(f"{vw} symptom: {e}")
            for part, dev, cfg in _config_texts(v):
                if dev.lower() not in devices:
                    errs.append(f"{vw}: {part} device {dev!r} isn't in the topology")
                if re.search(r"^\s*description\b", cfg, re.M | re.I) and part == "inject":
                    errs.append(f"{vw}: inject must not add descriptions (they give the fault away)")
                for name in _DEFINES.findall(cfg):
                    if not _PLACEHOLDER.fullmatch(name) and name not in baseline:
                        errs.append(f"{vw}: {part} defines {name!r} — use a placeholder such as {{POLICY1}} "
                                    "or a name the baseline already has")
    return errs


def save_scenario(db: Session, pack: dict, scenario: Scenario | None = None) -> Scenario:
    errs = validate(pack)
    if errs:
        raise ScenarioError("; ".join(errs[:12]) + (f" (+{len(errs) - 12} more)" if len(errs) > 12 else ""))
    meta = normalize_meta(pack["meta"])
    clash = db.query(Scenario).filter(Scenario.slug == meta["slug"]).first()
    if clash and (scenario is None or clash.id != scenario.id):
        raise ScenarioError(f"A scenario with slug {meta['slug']!r} already exists")
    if scenario is None:
        scenario = Scenario(slug=meta["slug"], title=meta["title"])
        db.add(scenario)
    for k in ("slug", "title", "summary", "level", "track", "max_faults", "status", "version", "author"):
        setattr(scenario, k, meta[k])
    scenario.tags_json = json.dumps(meta["tags"])
    scenario.protocols_json = json.dumps(meta["protocols"])
    scenario.topology_yaml = pack["topology_yaml"]
    scenario.docs_md = pack["docs_md"] or ""
    scenario.health_json = json.dumps(pack["health"])
    scenario.faults_json = json.dumps(pack["faults"])
    db.flush()
    return scenario


def import_pack(db: Session, pack: dict, mode: str = "new") -> Scenario:
    meta = normalize_meta(pack["meta"])
    cur = db.query(Scenario).filter(Scenario.slug == meta["slug"]).first()
    if cur and mode == "replace":
        return save_scenario(db, pack, scenario=cur)
    if cur and mode == "copy":
        base, i = meta["slug"], 2
        while db.query(Scenario).filter(Scenario.slug == f"{base}-{i}").first():
            i += 1
        pack = {**pack, "meta": {**pack["meta"], "slug": f"{base}-{i}", "title": f"{meta['title']} (copy)"}}
    elif cur:
        raise ScenarioExists(meta["slug"])
    return save_scenario(db, pack)


def export_zip(s: Scenario) -> bytes:
    meta = {"slug": s.slug, "title": s.title, "summary": s.summary, "level": s.level, "track": s.track,
            "tags": s.tags, "protocols": s.protocols, "max_faults": s.max_faults, "status": s.status,
            "version": s.version, "author": s.author}
    dump = lambda v: yaml.safe_dump(v, sort_keys=False, allow_unicode=True, width=120)  # noqa: E731
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{s.slug}/scenario.yaml", dump(meta))
        z.writestr(f"{s.slug}/topology.yaml", s.topology_yaml or "")
        z.writestr(f"{s.slug}/health.yaml", dump(s.health))
        z.writestr(f"{s.slug}/faults.yaml", dump(s.faults))
        z.writestr(f"{s.slug}/docs.md", s.docs_md or "")
    return buf.getvalue()


def seed_bundled(db: Session, root: Path) -> list[str]:
    done = []
    base = root / "troubleshooting"
    if not base.is_dir():
        return done
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            pack = read_dir(d)
            meta = normalize_meta(pack["meta"])
            cur = db.query(Scenario).filter(Scenario.slug == meta["slug"]).first()
            if cur is None:
                save_scenario(db, pack)
            elif not cur.edited_in_app and lesson_svc._version_key(meta["version"]) > lesson_svc._version_key(cur.version):
                save_scenario(db, pack, scenario=cur)
            else:
                continue
            db.commit()
            done.append(meta["slug"])
        except (ScenarioError, OSError) as e:
            db.rollback()
            print(f"[troubleshooting] {d.name}: not imported: {e}", flush=True)
    return done


def coverage(s: Scenario) -> dict:
    """What the catalog covers (shown before starting — never the faults)."""
    out: dict[str, dict[str, int]] = {}
    for f in s.faults:
        out.setdefault(f["protocol"], {}).setdefault(f["category"], 0)
        out[f["protocol"]][f["category"]] += len(f.get("variants") or [])
    return out


# --- Chaos: choosing and rendering faults ---------------------------------------------------

def _find(s: Scenario, fault_id: str, variant_id: str) -> tuple[dict, dict]:
    f = next(f for f in s.faults if f["id"] == fault_id)
    v = next(v for v in f["variants"] if v["id"] == variant_id)
    return f, v


def choose_faults(s: Scenario, n: int, rng: random.Random) -> list[tuple[dict, dict]]:
    """n faults, distinct, with disjoint scopes, spread over protocol ×
    category so a round isn't four BGP neighbor problems."""
    pool = [(f, v) for f in s.faults for v in (f.get("variants") or [])]
    rng.shuffle(pool)
    chosen: list[tuple[dict, dict]] = []
    used_scope: set[str] = set()
    used_fault: set[str] = set()
    used_kind: dict[tuple, int] = {}
    while len(chosen) < n:
        ok = [(f, v) for f, v in pool if f["id"] not in used_fault and not (set(v.get("scope") or []) & used_scope)]
        if not ok:
            break
        ok.sort(key=lambda fv: (used_kind.get((fv[0]["protocol"], fv[0]["category"]), 0),
                                used_kind.get((fv[0]["protocol"],), 0)))
        f, v = ok[0]
        chosen.append((f, v))
        used_fault.add(f["id"])
        used_scope |= set(v.get("scope") or [])
        used_kind[(f["protocol"], f["category"])] = used_kind.get((f["protocol"], f["category"]), 0) + 1
        used_kind[(f["protocol"],)] = used_kind.get((f["protocol"],), 0) + 1
    return chosen


def make_names(v: dict, rng: random.Random, taken: set[str]) -> dict[str, str]:
    """Fill a variant's placeholders with neutral names (unique in the round)."""
    names = {}
    text = "\n".join(cfg for _p, _d, cfg in _config_texts(v))
    for kind, idx in sorted(set(_PLACEHOLDER.findall(text))):
        key = f"{kind}{idx}"
        if kind == "SECRET":
            names[key] = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(10))
            continue
        options = [x for x in NAME_POOLS[kind] if x not in taken]
        name = rng.choice(options) if options else f"{NAME_POOLS[kind][0]}-{rng.randint(10, 99)}"
        names[key] = name
        taken.add(name)
    return names


def render(cfg: str, names: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: names.get(f"{m.group(1)}{m.group(2)}", m.group(0)), cfg or "")


# --- labs and attempts ------------------------------------------------------------------------

def _build_lab(db: Session, s: Scenario, name: str, role: str) -> Topology:
    spec = topology_io.parse(s.topology_yaml or "nodes: []\nlinks: []\n")
    spec["annotations"] = []                      # no design boxes in a troubleshooting lab
    for n in spec.get("nodes") or []:
        n.pop("notes", None)
    t = Topology(name=name, description=s.summary or "", scenario_id=s.id, lesson_role=role)
    db.add(t)
    db.flush()
    topology_io.build_into(db, t, spec)
    return t


def current_attempt(db: Session, s: Scenario, username: str) -> ScenarioAttempt | None:
    for a in (db.query(ScenarioAttempt).filter(ScenarioAttempt.scenario_id == s.id, ScenarioAttempt.username == username)
              .order_by(ScenarioAttempt.id.desc())):
        if a.topology_id and db.get(Topology, a.topology_id):
            return a
    return None


def attempt_for_topology(db: Session, topology_id: int) -> ScenarioAttempt | None:
    return db.query(ScenarioAttempt).filter(ScenarioAttempt.topology_id == topology_id).order_by(
        ScenarioAttempt.id.desc()).first()


def _settings(s: Scenario, difficulty: str | None, count: int | None, a: ScenarioAttempt | None = None):
    d = difficulty or (a.difficulty if a else "standard")
    if d not in DIFFICULTIES:
        raise ScenarioError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
    c = int(count or (a.fault_count if a else 1))
    if not 1 <= c <= s.max_faults:
        raise ScenarioError(f"Chaos can create 1 to {s.max_faults} faults in this scenario")
    return d, c


def start_attempt(db: Session, s: Scenario, username: str, difficulty: str, count: int) -> tuple[ScenarioAttempt, bool]:
    d, c = _settings(s, difficulty, count)
    a = current_attempt(db, s, username)
    if a:
        if a.state not in ("preparing",):
            a.difficulty, a.fault_count = d, c
        return a, False
    t = _build_lab(db, s, f"{s.title} — {username}" if username else s.title, "troubleshoot")
    a = ScenarioAttempt(scenario_id=s.id, scenario_version=s.version, username=username, topology_id=t.id,
                        difficulty=d, fault_count=c, state="idle")
    db.add(a)
    db.flush()
    return a, True


def rebuild_lab(db: Session, a: ScenarioAttempt) -> None:
    """Wipe the lab and rebuild the healthy baseline (last resort)."""
    from app.services import lab_service
    s = db.get(Scenario, a.scenario_id)
    t = db.get(Topology, a.topology_id) if a.topology_id else None
    if not t:
        raise ScenarioError("This attempt's lab no longer exists")
    lab_service.destroy_lab(db, t.id)
    db.flush()
    for l in db.query(Link).filter(Link.topology_id == t.id).all():
        db.delete(l)
    db.flush()
    for n in db.query(NodeInstance).filter(NodeInstance.topology_id == t.id).all():
        db.delete(n)
    db.flush()
    spec = topology_io.parse(s.topology_yaml)
    spec["annotations"] = []
    for n in spec.get("nodes") or []:
        n.pop("notes", None)
    db.expire(t)
    topology_io.build_into(db, t, spec)
    _archive_round(a, outcome="abandoned")
    a.state, a.progress, a.error = "idle", "", ""
    a.round = {}
    a.scenario_version = s.version


def _archive_round(a: ScenarioAttempt, outcome: str) -> None:
    r = a.round
    if not r.get("faults"):
        return
    h = a.history
    h.append({"difficulty": r.get("difficulty"), "faults": len(r["faults"]), "outcome": outcome,
              "started_at": r.get("started_at"), "ended_at": time.time(),
              "hints": sum(int(x) for x in (r.get("hints") or {}).values()), "checks": r.get("checks_run", 0),
              "fault_ids": [f["fault"] for f in r["faults"]]})
    a.history = h[-50:]


# --- the Chaos job ------------------------------------------------------------------------------

_RUNNING: set[int] = set()
_LOCK = threading.Lock()
READY_TIMEOUT = 15 * 60
HEALTH_TIMEOUT = 6 * 60
SYMPTOM_TIMEOUT = 150


def _set(db: Session, a: ScenarioAttempt, **kw) -> None:
    for k, v in kw.items():
        setattr(a, k, v)
    db.commit()


def _devices_up(db: Session, topology_id: int) -> tuple[bool, str]:
    from app.services import lab_service
    from app.services.device_kinds import is_bridge
    st = lab_service.get_deployment_state(db, topology_id)
    if st.get("status") not in ("running", "syncing"):
        return False, f"The lab isn't running ({st.get('status', 'idle')}) — deploy it"
    nodes = {n.id: n for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id)}
    waiting = [n.name for nid, n in nodes.items() if not is_bridge(n.kind)
               and (st.get("nodes", {}).get(nid) or {}).get("ready_state") != "up"]
    return not waiting, f"Waiting for {', '.join(sorted(waiting))} to boot" if waiting else ""


def _configure(db: Session, topology_id: int, device: str, cfg: str) -> list[str]:
    from app.services import device_exec
    n = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id, NodeInstance.name == device).first()
    if not n:
        return [f"{device}: not in the lab"]
    t = device_exec.target_for(db, topology_id, n.id)
    for attempt in (1, 2):
        try:
            r = asyncio.run(device_exec.configure(t, cfg, timeout=180))
            return r.get("errors") or []
        except device_exec.ExecError as e:
            if attempt == 2:
                return [str(e)]
            time.sleep(5)
    return []


def apply_configs(db: Session, topology_id: int, configs: dict[str, str]) -> list[str]:
    errs = []
    for dev, cfg in configs.items():
        errs += _configure(db, topology_id, dev, cfg)
    return errs


def run_health(db: Session, s: Scenario, topology_id: int) -> list[dict]:
    return lesson_checks.run_checks(db, topology_id, s.health)


def _wait_healthy(db: Session, s: Scenario, a: ScenarioAttempt, what: str) -> list[dict]:
    deadline = time.time() + HEALTH_TIMEOUT
    while True:
        res = run_health(db, s, a.topology_id)
        bad = [r for r in res if not r["ok"]]
        if not bad:
            return res
        if time.time() > deadline:
            raise ScenarioError(f"{what}: the network isn't healthy ({len(bad)} of {len(res)} health checks fail, "
                                f"e.g. “{bad[0]['describe']}”). Rebuild the lab to start from a clean network.")
        _set(db, a, progress=f"{what} ({len(res) - len(bad)}/{len(res)} health checks pass)")
        time.sleep(15)


def start_round(db: Session, a: ScenarioAttempt, difficulty: str | None = None, count: int | None = None) -> None:
    s = db.get(Scenario, a.scenario_id)
    d, c = _settings(s, difficulty, count, a)
    with _LOCK:
        if a.id in _RUNNING:
            raise ScenarioError("Chaos is already at work in this lab")
        _RUNNING.add(a.id)
    prev = a.round if a.round.get("faults") and not a.round.get("reverted") else None
    if a.state in ("active", "solved", "revealed"):
        _archive_round(a, outcome={"active": "abandoned"}.get(a.state, a.state))
    a.difficulty, a.fault_count = d, c
    a.state, a.progress, a.error = "preparing", "Getting ready", ""
    a.round = {"pending_revert": prev} if prev else {}
    db.commit()
    threading.Thread(target=_job, args=(a.id,), daemon=True, name=f"chaos-{a.id}").start()


def _job(attempt_id: int) -> None:
    db = SessionLocal()
    try:
        a = db.get(ScenarioAttempt, attempt_id)
        s = db.get(Scenario, a.scenario_id)
        try:
            _run(db, s, a)
        except Exception as e:  # noqa: BLE001 — surfaced to the learner
            db.rollback()
            a = db.get(ScenarioAttempt, attempt_id)
            _set(db, a, state="error", progress="", error=str(e) if isinstance(e, ScenarioError) else f"{type(e).__name__}: {e}")
    finally:
        db.close()
        with _LOCK:
            _RUNNING.discard(attempt_id)


def _run(db: Session, s: Scenario, a: ScenarioAttempt) -> None:
    deadline = time.time() + READY_TIMEOUT
    while True:
        db.expire_all()
        up, why = _devices_up(db, a.topology_id)
        if up:
            break
        if time.time() > deadline:
            raise ScenarioError(why)
        _set(db, a, progress=why)
        time.sleep(10)

    prev = a.round.get("pending_revert")
    if prev:
        _set(db, a, progress="Putting the network back together")
        for f in prev["faults"]:
            _fault, v = _find(s, f["fault"], f["variant"])
            apply_configs(db, a.topology_id, {d: render(c, f["names"]) for d, c in v["revert"].items()})

    _set(db, a, progress="Checking the network is healthy")
    _wait_healthy(db, s, a, "Checking the network is healthy")

    seed = secrets.randbelow(2**31)
    rng = random.Random(seed)
    picked = choose_faults(s, a.fault_count, rng)
    if not picked:
        raise ScenarioError("The fault catalog is empty")
    _set(db, a, progress="Chaos is picking its targets")
    taken: set[str] = set()
    faults = []
    for f, v in picked:
        names = make_names(v, rng, taken)
        faults.append({"fault": f["id"], "variant": v["id"], "names": names, "reporter": v.get("reporter", "NOC"),
                       "ticket": v["ticket"].strip(), "resolved_at": None, "silent": False})
    # Record the faults before touching the devices: a failure half-way can
    # still be reverted.
    a.round = {"seed": seed, "difficulty": a.difficulty, "faults": faults, "hints": {}, "checks_run": 0,
               "prepared_at": time.time()}
    _set(db, a, progress="Chaos is at work")
    for fr in faults:
        _f, v = _find(s, fr["fault"], fr["variant"])
        errs = apply_configs(db, a.topology_id, {d: render(c, fr["names"]) for d, c in v["inject"].items()})
        if errs:
            raise ScenarioError(f"Chaos couldn't apply a fault ({'; '.join(errs)[:300]}). Rebuild the lab and try again.")

    _set(db, a, progress="Waiting for the damage to show")
    deadline = time.time() + SYMPTOM_TIMEOUT
    pending = list(range(len(faults)))
    while pending and time.time() < deadline:
        time.sleep(10)
        for i in list(pending):
            _f, v = _find(s, faults[i]["fault"], faults[i]["variant"])
            if not all(r["ok"] for r in lesson_checks.run_checks(db, a.topology_id, v["symptoms"])):
                pending.remove(i)
    for i in pending:                                   # never showed: still counts, flagged for the debrief
        faults[i]["silent"] = True

    _set(db, a, progress="Writing up the tickets")
    from app.services import chaos_ai
    for fr in faults:
        f, v = _find(s, fr["fault"], fr["variant"])
        fr["ticket"] = chaos_ai.write_ticket(f, v, a.difficulty) or fr["ticket"]
    r = a.round
    r["faults"] = faults
    r["started_at"] = time.time()
    a.round = r
    _set(db, a, state="active", progress="")


# --- the learner's side ---------------------------------------------------------------------------

def check(db: Session, a: ScenarioAttempt) -> dict:
    """Run every symptom and health check; solved when all pass."""
    s = db.get(Scenario, a.scenario_id)
    if a.state != "active":
        raise ScenarioError("There's no round in progress")
    r = a.round
    health = run_health(db, s, a.topology_id)
    now = time.time()
    per = []
    for fr in r["faults"]:
        _f, v = _find(s, fr["fault"], fr["variant"])
        res = lesson_checks.run_checks(db, a.topology_id, v["symptoms"])
        ok = all(x["ok"] for x in res)
        if ok and not fr.get("resolved_at"):
            fr["resolved_at"] = now
        elif not ok:
            fr["resolved_at"] = None
        per.append(ok)
    r["checks_run"] = int(r.get("checks_run", 0)) + 1
    r["last_check"] = {"at": now, "tickets": per,
                       "health": [{"describe": h["describe"], "ok": h["ok"], "scope": c.get("scope", "internal")}
                                  for h, c in zip(health, s.health)]}
    solved = all(per) and all(h["ok"] for h in health)
    if solved:
        r["solved_at"] = now
        a.state = "solved"
    a.round = r
    if solved:
        _archive_round(a, outcome="solved")
    db.flush()
    return view(db, a)


def reveal_hint(db: Session, a: ScenarioAttempt, idx: int) -> dict:
    s = db.get(Scenario, a.scenario_id)
    if a.difficulty == "expert":
        raise ScenarioError("No hints at Expert difficulty")
    if a.state != "active":
        raise ScenarioError("There's no round in progress")
    r = a.round
    if not 0 <= idx < len(r["faults"]):
        raise ScenarioError("No such ticket")
    _f, v = _find(s, r["faults"][idx]["fault"], r["faults"][idx]["variant"])
    hints = r.get("hints") or {}
    n = int(hints.get(str(idx), 0))
    if n < len(v.get("hints") or []):
        hints[str(idx)] = n + 1
    r["hints"] = hints
    a.round = r
    db.flush()
    return view(db, a)


def give_up(db: Session, a: ScenarioAttempt) -> dict:
    if a.state != "active":
        raise ScenarioError("There's no round in progress")
    r = a.round
    r["revealed_at"] = time.time()
    a.round = r
    a.state = "revealed"
    _archive_round(a, outcome="revealed")
    db.flush()
    return view(db, a)


def _elapsed(r: dict) -> int | None:
    if not r.get("started_at"):
        return None
    end = r.get("solved_at") or r.get("revealed_at") or time.time()
    return int(end - r["started_at"])


def view(db: Session, a: ScenarioAttempt) -> dict:
    """The attempt as the learner may see it: tickets, never the faults —
    until the round is solved or given up (then the debrief)."""
    s = db.get(Scenario, a.scenario_id)
    r = a.round
    finished = a.state in ("solved", "revealed")
    tickets = []
    last = r.get("last_check") or {}
    for i, fr in enumerate(r.get("faults") or []):
        f, v = _find(s, fr["fault"], fr["variant"])
        shown = int((r.get("hints") or {}).get(str(i), 0))
        t = {"reporter": fr.get("reporter", "NOC"), "ticket": fr["ticket"], "resolved": bool(fr.get("resolved_at")),
             "hints": (v.get("hints") or [])[:shown] if a.difficulty != "expert" else [],
             "hints_left": max(0, len(v.get("hints") or []) - shown) if a.difficulty != "expert" else 0}
        if finished:
            t["debrief"] = {
                "title": f["title"], "protocol": f["protocol"], "category": f["category"],
                "explanation": f["explanation"], "find": f.get("find", ""),
                "changed": {d: render(c, fr["names"]) for d, c in v["inject"].items()},
                "fix": {d: render(c, fr["names"]) for d, c in v["revert"].items()},
                "silent": fr.get("silent", False)}
        tickets.append(t)
    if a.difficulty == "guided":
        health = last.get("health")
    elif a.difficulty == "standard":
        health = [h for h in last.get("health") or [] if h.get("scope") == "service"] or None
    else:
        health = None
    hl = last.get("health") or []
    return {
        "id": a.id, "scenario_id": a.scenario_id, "topology_id": a.topology_id, "state": a.state,
        "progress": a.progress, "error": a.error, "difficulty": a.difficulty, "fault_count": a.fault_count,
        "max_faults": s.max_faults, "tickets": tickets, "elapsed": _elapsed(r),
        "checks_run": r.get("checks_run", 0), "hints_used": sum(int(x) for x in (r.get("hints") or {}).values()),
        "last_check_at": last.get("at"), "health": health,
        "health_summary": {"ok": sum(1 for h in hl if h["ok"]), "total": len(hl)} if hl else None,
        "docs_md": s.docs_md if a.difficulty == "guided" else "",
        "debrief_ai": r.get("debrief_ai") if finished else None,
        "history": a.history[-10:],
        "outdated": bool(a.scenario_version) and a.scenario_version != s.version,
    }


def debrief_ai(db: Session, a: ScenarioAttempt) -> str | None:
    """A short narrative debrief by the Chaos model (cached in the round)."""
    if a.state not in ("solved", "revealed"):
        raise ScenarioError("The debrief is available once the round is over")
    r = a.round
    if r.get("debrief_ai"):
        return r["debrief_ai"]
    s = db.get(Scenario, a.scenario_id)
    from app.services import chaos_ai
    items = []
    for fr in r["faults"]:
        f, v = _find(s, fr["fault"], fr["variant"])
        items.append({"title": f["title"], "ticket": fr["ticket"], "explanation": f["explanation"],
                      "find": f.get("find", ""), "resolved": bool(fr.get("resolved_at")),
                      "changed": {d: render(c, fr["names"]) for d, c in v["inject"].items()}})
    text = chaos_ai.write_debrief(s.title, a.difficulty, items, a.state == "solved", _elapsed(r) or 0,
                                  sum(int(x) for x in (r.get("hints") or {}).values()))
    if text:
        r["debrief_ai"] = text
        a.round = r
        db.flush()
    return text

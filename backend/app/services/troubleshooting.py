"""Troubleshooting scenarios: "Chaos" breaks a known-good network with faults
built from templates; the learner finds and fixes them with show commands.

A scenario pack (curriculum/troubleshooting/<slug>/ or a zip):
  scenario.yaml  metadata (slug, title, summary, level, track, tags,
                 protocols, max_faults, version, author), `managed` (the
                 routers the learner owns — their configs are diffed for the
                 debrief) and `parties` (who can raise a ticket, and what
                 they own)
  topology.yaml  the healthy baseline (topology_io format)
  health.yaml    checks (lesson check format) that all pass when healthy;
                 `scope: service` marks customer-visible ones
  faults.yaml    the catalog: `targets` and `templates` — see CATALOG below
  docs.md        network documentation (shown at Guided difficulty)

CATALOG
  targets:   {kind: [instance, …]} — parts of the network a fault can hit.
             An instance is a mapping of variables (devices, interfaces,
             addresses, prefixes…). A "link" instance has a_* / b_* fields;
             a template with `sides: [a, b]` binds either end as dev_* and
             the other as peer_*. `devices: [..]` makes a template apply to
             several routers at once.
  templates: id, protocol (bgp | isis | ospf), category (adjacency |
             advertisement), target (a target kind), sides?, device? (which
             router it lands on, default {dev}; the target's own {dev} stays
             available as {target_dev}), requires?
             (instance flags that must be true), title, explanation, find,
             scope [keys — two faults sharing a key never combine], config
             {iosxr | iosxe: {inject, revert}}, symptoms (checks that fail
             while the fault is in place), hints, ticket (fallback text).
  Text uses {var} for target variables (+ per-device: {family}) and
  {POLICY1} {ROUTEMAP1} {PREFIXLIST1} {KEYCHAIN1} {SECRET1} for objects
  Chaos creates: each round fills those with neutral, plausible names, so
  nothing Chaos creates gives the fault away.
  Every template × target (× side) whose device's OS has a config is a
  *candidate*; scripts/chaos_catalog_test.py proves each one live.

A round: wait for the lab → (revert the previous round) → health must pass →
the Chaos model plans the round from the candidates and the learner's
history (or a random draw: disjoint scopes, spread over protocol ×
category) → inject → wait for the symptoms → let the network settle and
confirm the symptoms are still there (a fault the network heals by itself —
a session that re-forms from the other side — is reverted, marked
ineffective and replaced) → snapshot the managed routers → tickets written
from what is *still* broken after the settle → the learner works → Check: every
fault's symptoms clear and every health check passes → solved (however it
was fixed — rebuilding the lab counts). The debrief compares the learner's
changes with what Chaos did.
"""
from __future__ import annotations

import asyncio
import difflib
import io
import json
import logging
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

logger = logging.getLogger(__name__)

DIFFICULTIES = ("guided", "standard", "expert")
PROTOCOLS = ("bgp", "isis", "ospf")
CATEGORIES = ("adjacency", "advertisement")
FAMILIES = ("iosxr", "iosxe")
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
_VAR = re.compile(r"\{([a-z][a-z0-9_]*)\}")
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
            "faults": _yaml(files.get("faults.yaml", ""), "faults.yaml", {}),
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
    m["managed"] = [str(d) for d in (m.get("managed") or [])]
    m["parties"] = [p for p in (m.get("parties") or []) if isinstance(p, dict) and p.get("name")]
    return m


# --- the template engine ---------------------------------------------------------------------

class _Missing(KeyError):
    pass


def _fill(obj, vars: dict):
    """{var} → value, recursively through str / list / dict. {POLICY1}-style
    placeholders (upper case) are left for render()."""
    if isinstance(obj, str):
        def rep(m):
            k = m.group(1)
            if k not in vars:
                raise _Missing(k)
            return str(vars[k])
        return _VAR.sub(rep, obj)
    if isinstance(obj, list):
        return [_fill(x, vars) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, vars) for k, v in obj.items()}
    return obj


def _side_vars(inst: dict, side: str) -> dict:
    """A link instance seen from one end: a_x / b_x → dev_x / peer_x."""
    other = "b" if side == "a" else "a"
    out = {}
    for k, v in inst.items():
        if k == side:
            out["dev"] = v
        elif k == other:
            out["peer"] = v
        elif k.startswith(side + "_"):
            out["dev_" + k[2:]] = v
        elif k.startswith(other + "_"):
            out["peer_" + k[2:]] = v
    return out


def _families(topology_yaml: str) -> dict[str, str]:
    from app.services.device_kinds import get_kind
    out = {}
    for name, n in lesson_svc.topology_devices(topology_yaml).items():
        try:
            out[name] = get_kind(n.get("kind") or "linux").family
        except Exception:  # noqa: BLE001 — unknown kinds are simply not targets
            out[name] = "linux"
    return out


def expand(catalog: dict, families: dict[str, str]) -> tuple[list[dict], list[str]]:
    """Every candidate fault (template × target × side) and the problems
    found on the way (unknown target kinds, missing variables…)."""
    errs, out = [], []
    targets = (catalog or {}).get("targets") or {}
    for t in (catalog or {}).get("templates") or []:
        tid = t.get("id", "?")
        insts = targets.get(t.get("target"))
        if insts is None:
            errs.append(f"template {tid}: unknown target kind {t.get('target')!r}")
            continue
        for i, inst in enumerate(insts):
            if any(not inst.get(flag) for flag in t.get("requires") or []):
                continue
            for side in (t.get("sides") or [None]):
                vars = dict(inst)
                if side:
                    vars.update(_side_vars(inst, side))
                try:
                    if t.get("device"):                 # e.g. "{rr}": the fault goes on the far side
                        vars["target_dev"] = vars.get("dev")
                        vars["dev"] = _fill(t["device"], vars)
                except _Missing as e:
                    errs.append(f"template {tid}: no variable {{{e.args[0]}}} for its device")
                    continue
                devices = [str(d) for d in (inst.get("devices") or [vars.get("dev")]) if d]
                if not devices:
                    errs.append(f"template {tid} / {t.get('target')}[{i}]: no device")
                    continue
                if any(d not in families for d in devices):
                    errs.append(f"template {tid}: devices {devices} not all in the topology")
                    continue
                if any(families[d] not in (t.get("config") or {}) for d in devices):
                    continue                            # this template doesn't apply to that OS
                try:
                    inject, revert = {}, {}
                    for d in devices:
                        dv = {**vars, "dev": d, "family": families[d]}
                        cfg = t["config"][families[d]]
                        inject[d] = _fill(cfg["inject"], dv)
                        revert[d] = _fill(cfg["revert"], dv)
                    base = {**vars, "family": families[devices[0]], "dev": vars.get("dev", devices[0])}
                    label = _fill(t.get("label") or "{dev}", base)
                    cid = f"{tid}@{_fill(t.get('key') or label, base)}"
                    out.append({
                        "id": re.sub(r"\s+", "_", cid), "template": tid, "target": t["target"],
                        "protocol": t.get("protocol"), "category": t.get("category"), "title": t.get("title", tid),
                        "explanation": _fill(t.get("explanation", ""), base), "find": _fill(t.get("find", ""), base),
                        "label": label, "family": base["family"], "devices": devices,
                        "leak_terms": _fill(t.get("leak_terms") or [], base),
                        "scope": _fill(t.get("scope") or [], base), "inject": inject, "revert": revert,
                        "symptoms": _fill(t.get("symptoms") or [], base), "hints": _fill(t.get("hints") or [], base),
                        "ticket": _fill(t.get("ticket", ""), base).strip(),
                        "reporter": _fill(t.get("reporter", "AS monitoring"), base)})
                except _Missing as e:
                    errs.append(f"template {tid} / {t.get('target')}[{i}]{'/' + side if side else ''}: "
                                f"no variable {{{e.args[0]}}}")
    ids = [c["id"] for c in out]
    for dup in {x for x in ids if ids.count(x) > 1}:
        errs.append(f"two candidates share the id {dup!r} — give the template a distinct `key`")
    return out, errs


_CACHE: dict[tuple, list[dict]] = {}


def candidates(s: Scenario) -> list[dict]:
    key = (s.id, s.version, hash(s.faults_json), hash(s.topology_yaml))
    if key not in _CACHE:
        _CACHE[key] = expand(s.catalog, _families(s.topology_yaml))[0]
    return _CACHE[key]


def get_candidate(s: Scenario, cid: str) -> dict:
    for c in candidates(s):
        if c["id"] == cid:
            return c
    raise ScenarioError(f"This scenario has no fault {cid!r} anymore")


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
        families = _families(pack["topology_yaml"])
    except Exception as e:  # noqa: BLE001 — reported
        return errs + [f"topology.yaml: {e}"]
    for d in meta["managed"]:
        if d.lower() not in devices:
            errs.append(f"managed device {d!r} isn't in the topology")
    baseline = pack["topology_yaml"] or ""
    health = pack["health"] if isinstance(pack["health"], list) else []
    if not health:
        errs.append("health.yaml needs at least one check")
    for i, c in enumerate(health, 1):
        for e in lesson_checks.validate_check(c, devices):
            errs.append(f"health check {i}: {e}")
    catalog = pack["faults"] if isinstance(pack["faults"], dict) else {}
    tids = [t.get("id") for t in catalog.get("templates") or []]
    if not tids:
        errs.append("faults.yaml needs `targets` and at least one template")
    for t in catalog.get("templates") or []:
        tid = t.get("id", "?")
        if tids.count(tid) > 1:
            errs.append(f"template {tid}: duplicate id")
        if t.get("protocol") not in PROTOCOLS:
            errs.append(f"template {tid}: protocol must be one of {', '.join(PROTOCOLS)}")
        if t.get("category") not in CATEGORIES:
            errs.append(f"template {tid}: category must be adjacency or advertisement")
        if not t.get("title") or not t.get("explanation") or not t.get("symptoms"):
            errs.append(f"template {tid}: needs a title, an explanation and symptoms")
        for fam, cfg in (t.get("config") or {}).items():
            if fam not in FAMILIES or not isinstance(cfg, dict) or not cfg.get("inject") or not cfg.get("revert"):
                errs.append(f"template {tid}: config.{fam} needs inject and revert (families: {', '.join(FAMILIES)})")
    cands, eerrs = expand(catalog, families)
    errs += eerrs
    for c in cands:
        where = f"fault {c['id']}"
        for chk in c["symptoms"]:
            for e in lesson_checks.validate_check(chk, devices):
                errs.append(f"{where} symptom: {e}")
        for part in ("inject", "revert"):
            for dev, cfg in c[part].items():
                if part == "inject" and re.search(r"^\s*description\b", cfg, re.M | re.I):
                    errs.append(f"{where}: inject must not add descriptions (they give the fault away)")
                for name in _DEFINES.findall(cfg):
                    if not _PLACEHOLDER.fullmatch(name) and name not in baseline:
                        errs.append(f"{where}: {part} defines {name!r} — use a placeholder such as {{POLICY1}} "
                                    "or a name the baseline already has")
    if tids and not cands:
        errs.append("no template applies to any target")
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
    scenario.extra_json = json.dumps({"managed": meta["managed"], "parties": meta["parties"]})
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
            "version": s.version, "author": s.author, "managed": s.managed, "parties": s.parties}
    dump = lambda v: yaml.safe_dump(v, sort_keys=False, allow_unicode=True, width=120)  # noqa: E731
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{s.slug}/scenario.yaml", dump(meta))
        z.writestr(f"{s.slug}/topology.yaml", s.topology_yaml or "")
        z.writestr(f"{s.slug}/health.yaml", dump(s.health))
        z.writestr(f"{s.slug}/faults.yaml", dump(s.catalog))
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
    """What the catalog covers (shown before starting — never the faults):
    distinct breaks per protocol × category."""
    out: dict[str, dict[str, int]] = {}
    for c in candidates(s):
        out.setdefault(c["protocol"], {}).setdefault(c["category"], 0)
        out[c["protocol"]][c["category"]] += 1
    return out


# --- Chaos: choosing and rendering faults ---------------------------------------------------

def compatible(picked: list[dict], c: dict) -> bool:
    """c may join the round: a different template, and nothing it touches
    (its scope) touched already."""
    if any(p["template"] == c["template"] for p in picked):
        return False
    used = {s for p in picked for s in p["scope"]}
    return not (set(c["scope"]) & used)


def live_candidates(s: Scenario) -> list[dict]:
    """The candidates Chaos may draw: minus those a real round found
    ineffective."""
    bad = set(s.ineffective)
    return [c for c in candidates(s) if c["id"] not in bad]


def choose_faults(s: Scenario, n: int, rng: random.Random, preferred: list[str] | None = None,
                  exclude: set[str] | None = None) -> list[dict]:
    """n faults: the preferred ones first (the Chaos model's plan) when they
    fit, then a draw with disjoint scopes, spread over protocol × category."""
    pool = [c for c in live_candidates(s) if c["id"] not in (exclude or set())]
    by_id = {c["id"]: c for c in pool}
    chosen: list[dict] = []
    for cid in preferred or []:
        c = by_id.get(cid)
        if c and len(chosen) < n and compatible(chosen, c):
            chosen.append(c)
    rng.shuffle(pool)
    while len(chosen) < n:
        ok = [c for c in pool if compatible(chosen, c)]
        if not ok:
            break
        count = lambda key: sum(1 for p in chosen if key(p))  # noqa: E731
        ok.sort(key=lambda c: (count(lambda p: (p["protocol"], p["category"]) == (c["protocol"], c["category"])),
                               count(lambda p: p["protocol"] == c["protocol"])))
        chosen.append(ok[0])
    return chosen


def make_names(c: dict, rng: random.Random, taken: set[str]) -> dict[str, str]:
    """Fill a candidate's placeholders with neutral names (unique in the round)."""
    names = {}
    text = "\n".join([*c["inject"].values(), *c["revert"].values()])
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

def _build_spec(s: Scenario) -> dict:
    spec = topology_io.parse(s.topology_yaml or "nodes: []\nlinks: []\n")
    spec["annotations"] = []                      # no design boxes in a troubleshooting lab
    for n in spec.get("nodes") or []:
        n.pop("notes", None)
    return spec


def _build_lab(db: Session, s: Scenario, name: str, role: str) -> Topology:
    t = Topology(name=name, description=s.summary or "", scenario_id=s.id, lesson_role=role)
    db.add(t)
    db.flush()
    topology_io.build_into(db, t, _build_spec(s))
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
    """Wipe the lab and rebuild the healthy baseline. During a round that's
    a (blunt) fix: the round stays open — deploy and Check once the devices
    are up. Otherwise it's a fresh start."""
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
    db.expire(t)
    topology_io.build_into(db, t, _build_spec(s))
    a.scenario_version = s.version
    if a.state == "active":
        r = a.round
        r["rebuilt"] = True                     # the faults are gone with the old devices
        r["reverted"] = True
        a.round = r
        return
    a.state, a.progress, a.error = "idle", "", ""
    a.round = {}


def _archive_round(a: ScenarioAttempt, outcome: str) -> None:
    r = a.round
    if not r.get("faults"):
        return
    h = a.history
    h.append({"difficulty": r.get("difficulty"), "faults": len(r["faults"]), "outcome": outcome,
              "started_at": r.get("started_at"), "ended_at": time.time(),
              "hints": sum(int(x) for x in (r.get("hints") or {}).values()), "checks": r.get("checks_run", 0),
              "rebuilt": bool(r.get("rebuilt")), "theme": r.get("theme", ""),
              "fault_ids": [f["fault"] for f in r["faults"]]})
    a.history = h[-50:]


# --- device helpers -------------------------------------------------------------------------------

_RUNNING: set[int] = set()
_LOCK = threading.Lock()
READY_TIMEOUT = 15 * 60
HEALTH_TIMEOUT = 6 * 60
SYMPTOM_TIMEOUT = 150
# After the symptoms first show, the network gets this long to heal by itself
# (BGP re-forming from the other side, IGP reconverging) before Chaos looks
# again; a fault must still break something on every confirmation pass.
SETTLE_SECONDS = 60
CONFIRM_PASSES = 2
CONFIRM_GAP = 30
MAX_REPLACEMENTS = 4


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


def _target(db: Session, topology_id: int, device: str):
    from app.services import device_exec
    n = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id, NodeInstance.name == device).first()
    if not n:
        raise device_exec.ExecError(f"{device}: not in the lab")
    return device_exec.target_for(db, topology_id, n.id)


def _configure(db: Session, topology_id: int, device: str, cfg: str) -> list[str]:
    from app.services import device_exec
    for attempt in (1, 2):
        try:
            r = asyncio.run(device_exec.configure(_target(db, topology_id, device), cfg, timeout=180))
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


_NOISE = re.compile(r"^(Building configuration|Current configuration|!! Last configuration change|"
                    r"!! IOS XR Configuration|\w{3} \w{3} +\d+ \d\d:\d\d:\d\d|ntp clock-period|"
                    r"! Last configuration change|! NVRAM config last updated)", re.I)


def snapshot(db: Session, topology_id: int, devices: list[str]) -> dict[str, str]:
    """Running configs of the managed routers (noise lines dropped), for the
    debrief's 'what you changed'. A device that can't be read maps to ''."""
    from app.services import device_exec

    async def one(d):
        try:
            return await device_exec.running_config(_target(db, topology_id, d))
        except Exception:  # noqa: BLE001 — the debrief simply lacks that device
            return ""

    async def all_():
        return await asyncio.gather(*(one(d) for d in devices))

    out = {}
    for d, text in zip(devices, asyncio.run(all_()) if devices else []):
        out[d] = "\n".join(l.rstrip() for l in (text or "").splitlines() if l.strip() and not _NOISE.match(l.strip()))
    return out


def config_diff(before: dict[str, str], after: dict[str, str], limit: int = 4000) -> dict[str, str]:
    """Per device, a unified diff of what changed (only devices that did)."""
    out = {}
    for d in sorted(set(before) | set(after)):
        a, b = (before.get(d) or "").splitlines(), (after.get(d) or "").splitlines()
        if not a or not b:
            continue
        lines = [l for l in difflib.unified_diff(a, b, lineterm="", n=1) if not l.startswith(("---", "+++"))]
        if lines:
            text = "\n".join(lines)
            out[d] = text if len(text) <= limit else text[:limit] + "\n…"
    return out


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


# --- the Chaos job ------------------------------------------------------------------------------

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


def _history_summary(s: Scenario, a: ScenarioAttempt) -> list[dict]:
    """The learner's past rounds for the planner: which breaks, how it went."""
    out = []
    for h in a.history[-12:]:
        mins = round(((h.get("ended_at") or 0) - (h.get("started_at") or h.get("ended_at") or 0)) / 60, 1)
        out.append({"faults": h.get("fault_ids", []), "outcome": h.get("outcome"), "minutes": mins,
                    "hints": h.get("hints", 0), "difficulty": h.get("difficulty"), "theme": h.get("theme", ""),
                    "rebuilt": bool(h.get("rebuilt"))})
    return out


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
            try:
                c = get_candidate(s, f["fault"])
            except ScenarioError:
                continue                            # the catalog changed: the health gate decides
            apply_configs(db, a.topology_id, {d: render(cfg, f["names"]) for d, cfg in c["revert"].items()})

    _set(db, a, progress="Checking the network is healthy")
    _wait_healthy(db, s, a, "Checking the network is healthy")

    from app.services import chaos_ai
    _set(db, a, progress="Chaos is planning")
    plan = chaos_ai.plan_round(s, live_candidates(s), a.fault_count, a.difficulty, _history_summary(s, a)) or {}
    seed = secrets.randbelow(2**31)
    rng = random.Random(seed)
    picked = choose_faults(s, a.fault_count, rng, preferred=plan.get("picks"))
    if not picked:
        raise ScenarioError("The fault catalog is empty")
    taken: set[str] = set()
    faults: list[dict] = []
    managed = [d for d in s.managed] or sorted({d for c in picked for d in c["devices"]})
    a.round = {"seed": seed, "difficulty": a.difficulty, "faults": faults, "hints": {}, "checks_run": 0,
               "prepared_at": time.time(), "theme": plan.get("theme", ""), "story": plan.get("story", ""),
               "planned_by": "ai" if plan.get("picks") else "random"}

    tried: set[str] = set()
    replaced = 0
    new = picked
    while True:
        # Record the faults before touching the devices: a failure half-way
        # can still be reverted.
        for c in new:
            tried.add(c["id"])
            faults.append({"fault": c["id"], "names": make_names(c, rng, taken), "reporter": c["reporter"],
                           "ticket": c["ticket"], "resolved_at": None, "silent": False})
        r = a.round
        r["faults"] = faults
        a.round = r
        _set(db, a, progress="Chaos is at work")
        for c in new:
            fr = next(f for f in faults if f["fault"] == c["id"])
            errs = apply_configs(db, a.topology_id, {d: render(cfg, fr["names"]) for d, cfg in c["inject"].items()})
            if errs:
                raise ScenarioError(f"Chaos couldn't apply a fault ({'; '.join(errs)[:300]}). Rebuild the lab and try again.")
        observed, broken = _confirm(db, s, a, [get_candidate(s, f["fault"]) for f in faults],
                                    {c["id"] for c in new})
        dud = [f for f in faults if not observed.get(f["fault"])]
        if not dud:
            break
        # The network healed by itself (or never broke): undo, never draw it again, replace it.
        _set(db, a, progress="That one didn't stick — Chaos tries something else")
        for f in dud:
            c = get_candidate(s, f["fault"])
            apply_configs(db, a.topology_id, {d: render(cfg, f["names"]) for d, cfg in c["revert"].items()})
            s.mark_ineffective(f["fault"])
            logger.warning("Chaos: %s / %s didn't keep the network broken — marked ineffective", s.slug, f["fault"])
            faults.remove(f)
        db.commit()
        keep = [f["fault"] for f in faults]
        replaced += len(dud)
        if replaced > MAX_REPLACEMENTS:
            new = []
        else:
            more = choose_faults(s, a.fault_count, rng, preferred=keep, exclude=tried - set(keep))
            new = [c for c in more if c["id"] not in keep]
        if not new and not faults:
            raise ScenarioError("Chaos couldn't find a fault that keeps this network broken. Rebuild the lab "
                                "and try again (and let an admin run the catalog test).")
        # Loop: inject the replacements (if any) and confirm the whole set again.

    picked = [get_candidate(s, f["fault"]) for f in faults]
    if replaced:
        # The story was written for faults that aren't there anymore: re-tell it
        # for the ones that are (the menu is exactly those).
        again = chaos_ai.plan_round(s, picked, len(picked), a.difficulty, _history_summary(s, a)) or {}
        r = a.round
        r["theme"], r["story"] = again.get("theme", ""), again.get("story", "")
        r["planned_by"] = r["planned_by"] + "+replaced"
        a.round = r

    _set(db, a, progress="Taking notes")
    after = snapshot(db, a.topology_id, managed)

    _set(db, a, progress="Writing up the tickets")
    tickets = chaos_ai.write_tickets(s, a.difficulty, a.round.get("story", ""),
                                     [{"observed": observed[f["fault"]], "fault": c} for f, c in zip(faults, picked)],
                                     broken) or []
    for i, fr in enumerate(faults):
        t = tickets[i] if i < len(tickets) and tickets[i] else None
        if t:
            fr["reporter"], fr["ticket"] = t["reporter"], t["ticket"]
    r = a.round
    r.update(faults=faults, started_at=time.time(), after_chaos=after, managed=managed,
             broken_at_start=[b["describe"] for b in broken])
    a.round = r
    _set(db, a, state="active", progress="")


def _confirm(db: Session, s: Scenario, a: ScenarioAttempt, cands: list[dict], fresh: set[str]):
    """Wait for the fresh faults' symptoms, let the network settle, then
    look again (CONFIRM_PASSES times): a fault is real only if some symptom
    fails on every pass. → ({fault id: symptoms failing on every pass},
    [health checks failing on every pass, as {describe, scope}])."""
    _set(db, a, progress="Waiting for the damage to show")
    deadline = time.time() + SYMPTOM_TIMEOUT
    waiting = [c for c in cands if c["id"] in fresh]
    while waiting and time.time() < deadline:
        time.sleep(10)
        waiting = [c for c in waiting
                   if all(x["ok"] for x in lesson_checks.run_checks(db, a.topology_id, c["symptoms"]))]
    _set(db, a, progress="Letting the network settle")
    time.sleep(SETTLE_SECONDS)
    observed: dict[str, list[str] | None] = {}
    broken: list[dict] | None = None
    for i in range(CONFIRM_PASSES):
        if i:
            time.sleep(CONFIRM_GAP)
        _set(db, a, progress=f"Making sure it stays broken ({i + 1}/{CONFIRM_PASSES})")
        for c in cands:
            bad = [x["describe"] for x in lesson_checks.run_checks(db, a.topology_id, c["symptoms"]) if not x["ok"]]
            if i == 0:
                observed[c["id"]] = bad or None
            elif observed.get(c["id"]):
                prev = observed[c["id"]]
                observed[c["id"]] = ([b for b in bad if b in prev] or bad) if bad else None
        health = run_health(db, s, a.topology_id)
        now = [{"describe": h["describe"], "scope": chk.get("scope", "internal")}
               for h, chk in zip(health, s.health) if not h["ok"]]
        broken = now if broken is None else [b for b in broken if b in now]
    return observed, broken or []


# --- the learner's side ---------------------------------------------------------------------------

def _finish(db: Session, s: Scenario, a: ScenarioAttempt) -> None:
    """Record what the learner changed (managed routers, vs right after Chaos)."""
    r = a.round
    if r.get("after_chaos") and not r.get("changes") and not r.get("rebuilt"):   # rebuilt: nothing to compare
        try:
            final = snapshot(db, a.topology_id, r.get("managed") or [])
            r["changes"] = config_diff(r["after_chaos"], final)
        except Exception:  # noqa: BLE001 — the debrief simply lacks the diff
            r["changes"] = {}
        r.pop("after_chaos", None)                  # big; not needed anymore
    a.round = r


def check(db: Session, a: ScenarioAttempt) -> dict:
    """Run every symptom and health check; solved when all pass — however
    the learner got there."""
    s = db.get(Scenario, a.scenario_id)
    if a.state != "active":
        raise ScenarioError("There's no round in progress")
    r = a.round
    health = run_health(db, s, a.topology_id)
    now = time.time()
    per = []
    for fr in r["faults"]:
        try:
            symptoms = get_candidate(s, fr["fault"])["symptoms"]
        except ScenarioError:
            symptoms = []
        res = lesson_checks.run_checks(db, a.topology_id, symptoms) if symptoms else []
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
    a.round = r
    if solved:
        r = a.round
        r["solved_at"] = now
        a.round = r
        a.state = "solved"
        _finish(db, s, a)
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
    c = get_candidate(s, r["faults"][idx]["fault"])
    hints = r.get("hints") or {}
    n = int(hints.get(str(idx), 0))
    if n < len(c["hints"]):
        hints[str(idx)] = n + 1
    r["hints"] = hints
    a.round = r
    db.flush()
    return view(db, a)


def give_up(db: Session, a: ScenarioAttempt) -> dict:
    s = db.get(Scenario, a.scenario_id)
    if a.state != "active":
        raise ScenarioError("There's no round in progress")
    r = a.round
    r["revealed_at"] = time.time()
    a.round = r
    a.state = "revealed"
    _finish(db, s, a)
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
        try:
            c = get_candidate(s, fr["fault"])
        except ScenarioError:
            c = {"hints": [], "title": fr["fault"], "protocol": "", "category": "", "explanation": "", "find": "",
                 "inject": {}, "revert": {}, "label": ""}
        shown = int((r.get("hints") or {}).get(str(i), 0))
        t = {"reporter": fr.get("reporter", "NOC"), "ticket": fr["ticket"], "resolved": bool(fr.get("resolved_at")),
             "hints": c["hints"][:shown] if a.difficulty != "expert" else [],
             "hints_left": max(0, len(c["hints"]) - shown) if a.difficulty != "expert" else 0}
        if finished:
            t["debrief"] = {
                "title": c["title"], "protocol": c["protocol"], "category": c["category"], "where": c["label"],
                "explanation": c["explanation"], "find": c["find"],
                "changed": {d: render(cfg, fr["names"]) for d, cfg in c["inject"].items()},
                "fix": {d: render(cfg, fr["names"]) for d, cfg in c["revert"].items()},
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
        "rebuilt": bool(r.get("rebuilt")),
        "theme": r.get("theme") if finished else None,
        "story": r.get("story") if finished else None,
        "changes": r.get("changes") if finished else None,
        "debrief_ai": r.get("debrief_ai") if finished else None,
        "history": a.history[-10:],
        "outdated": bool(a.scenario_version) and a.scenario_version != s.version,
    }


def debrief_ai(db: Session, a: ScenarioAttempt) -> str | None:
    """The Chaos model's debrief: what it did, and how the learner fixed it
    (cached in the round)."""
    if a.state not in ("solved", "revealed"):
        raise ScenarioError("The debrief is available once the round is over")
    r = a.round
    if r.get("debrief_ai"):
        return r["debrief_ai"]
    s = db.get(Scenario, a.scenario_id)
    from app.services import chaos_ai
    items = []
    for fr in r["faults"]:
        try:
            c = get_candidate(s, fr["fault"])
        except ScenarioError:
            continue
        items.append({"title": c["title"], "where": c["label"], "ticket": fr["ticket"], "explanation": c["explanation"],
                      "find": c["find"], "resolved": bool(fr.get("resolved_at")),
                      "changed": {d: render(cfg, fr["names"]) for d, cfg in c["inject"].items()}})
    text = chaos_ai.write_debrief(s.title, a.difficulty, items, a.state == "solved", _elapsed(r) or 0,
                                  sum(int(x) for x in (r.get("hints") or {}).values()),
                                  story=r.get("story", ""), changes=r.get("changes") or {},
                                  rebuilt=bool(r.get("rebuilt")), checks=r.get("checks_run", 0))
    if text:
        r["debrief_ai"] = text
        a.round = r
        db.flush()
    return text

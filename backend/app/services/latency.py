"""Effective link latency: a known (measured) value, else an estimate, else unknown.

A link's `latency_source` is "measured" (latency_ms was entered or imported —
an entered value is taken as measured) or "unknown" (latency_ms is ignored and
estimated instead). Estimates are chosen by the link's `transport`, so new
transports (satellite LEO / MEO / GEO — planned) add an estimator to
ESTIMATORS without touching the callers.

Everything that routes or reports on latency goes through here: graph builds
with the latency weight (services/simulation.py), path analysis, and the links
API. Unknown links get `latency_unknown_penalty_ms` when choosing a
lowest-latency route, but never count toward a reported total.
"""
import json
import math
from dataclasses import dataclass, field
from typing import Callable, NamedTuple

from sqlalchemy.orm import Session

from app.models.global_settings import GlobalSettings, DEFAULT_SETTINGS

FIBER_KM_PER_MS = 200.0   # light in fiber ≈ 2/3 c
VACUUM_KM_PER_MS = 299.792  # light in vacuum (satellite legs)
EARTH_RADIUS_KM = 6371.0
# Assumed elevation angles when the geometry isn't known.
SAT_DEFAULT_ELEVATION_DEG = 30.0   # GEO / MEO legs without coordinates
LEO_ELEVATION_DEG = 40.0           # LEO up / down legs
# The new-link default before latency sources existed; a bare 1.0 means "never set".
LEGACY_DEFAULT_MS = 1.0
SOURCES = ("measured", "unknown")


class LatencyInfo(NamedTuple):
    ms: float | None   # None when unknown
    source: str        # "measured" | "estimated" | "unknown"
    method: str        # "measured", "fiber route", "straight line", "same site", "unknown"


@dataclass
class LatencyContext:
    coords: dict[int, tuple[float, float]] = field(default_factory=dict)   # node id → (lat, lon)
    location: dict[int, str] = field(default_factory=dict)                 # node id → site
    route_factor: float = 1.5
    overhead_ms: float = 0.1
    unknown_penalty_ms: float = 10.0
    # Drawn-route lengths by (link id, geo_path_json); shared per DB session
    # because failure analysis rebuilds latency graphs hundreds of times.
    route_km: dict = field(default_factory=dict)
    # Satellite orbit → (altitude km, one-way processing ms).
    orbits: dict = field(default_factory=lambda: {
        o: (DEFAULT_SETTINGS[f"sat_{o}_altitude_km"][0], DEFAULT_SETTINGS[f"sat_{o}_processing_ms"][0])
        for o in ("leo", "meo", "geo")})


def infer_latency_source(value, explicit: str | None = None) -> str:
    """Source for a link written without an explicit one (imports, API calls,
    old export files): a value means measured, except the legacy 1.0 default."""
    if explicit in SOURCES:
        return explicit
    if value is None:
        return "unknown"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    # 0 (or less) has always meant "not set"; 1.0 was the old new-link default.
    return "unknown" if v <= 0 or v == LEGACY_DEFAULT_MS else "measured"


def _setting(db: Session, key: str) -> float:
    # Cached on the session: graph builds run many times per request.
    cache = db.info.setdefault("latency_settings", {})
    if key not in cache:
        row = db.query(GlobalSettings).filter(GlobalSettings.key == key).first()
        cache[key] = row.value if row else DEFAULT_SETTINGS[key][0]
    return cache[key]


def build_context(db: Session, nodes) -> LatencyContext:
    """Node coordinates for estimates. A node with no map position borrows its
    site's (the average of the site's nodes that have one)."""
    ctx = LatencyContext(
        route_factor=_setting(db, "latency_route_factor"),
        overhead_ms=_setting(db, "latency_overhead_ms"),
        unknown_penalty_ms=_setting(db, "latency_unknown_penalty_ms"),
        route_km=db.info.setdefault("latency_route_km", {}),
        orbits={o: (_setting(db, f"sat_{o}_altitude_km"), _setting(db, f"sat_{o}_processing_ms"))
                for o in ("leo", "meo", "geo")},
    )
    site_pts: dict[str, list[tuple[float, float]]] = {}
    for n in nodes:
        loc = (n.location or "").strip()
        ctx.location[n.id] = loc
        if n.geo_latitude is not None and n.geo_longitude is not None:
            ctx.coords[n.id] = (n.geo_latitude, n.geo_longitude)
            if loc:
                site_pts.setdefault(loc, []).append(ctx.coords[n.id])
    for n in nodes:
        pts = site_pts.get(ctx.location[n.id])
        if n.id not in ctx.coords and pts:
            ctx.coords[n.id] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    return ctx


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def _geo_path_km(link) -> float | None:
    """Length of the link's drawn fiber route ([[lng,lat],...] or multi-part)."""
    try:
        path = json.loads(link.geo_path_json or "")
    except (TypeError, ValueError):
        return None
    if not path:
        return None
    parts = path if isinstance(path[0][0], list) else [path]
    km = 0.0
    for part in parts:
        for (lng1, lat1), (lng2, lat2) in zip(part, part[1:]):
            km += haversine_km((lat1, lng1), (lat2, lng2))
    return km or None


def _estimate_terrestrial(link, ctx: LatencyContext) -> tuple[float, str] | None:
    key = (link.id, link.geo_path_json or "")
    if key not in ctx.route_km:
        ctx.route_km[key] = _geo_path_km(link)
    route_km = ctx.route_km[key]
    if route_km:
        return route_km / FIBER_KM_PER_MS + ctx.overhead_ms, "fiber route"
    src, tgt = ctx.location.get(link.source_node_id), ctx.location.get(link.target_node_id)
    if src and src == tgt:
        return ctx.overhead_ms, "same site"
    a, b = ctx.coords.get(link.source_node_id), ctx.coords.get(link.target_node_id)
    if a and b:
        return haversine_km(a, b) * ctx.route_factor / FIBER_KM_PER_MS + ctx.overhead_ms, "straight line"
    return None


def _slant_from_elevation(h: float, elev_deg: float) -> float:
    """Ground station → satellite distance at a given elevation angle."""
    e = math.radians(elev_deg)
    r = EARTH_RADIUS_KM
    return math.sqrt((r + h) ** 2 - (r * math.cos(e)) ** 2) - r * math.sin(e)


def _slant_from_angle(h: float, central_angle: float) -> float:
    """Ground station → satellite distance, given the central angle (radians)
    between the station and the satellite's sub-satellite point."""
    r = EARTH_RADIUS_KM
    return math.sqrt(r * r + (r + h) ** 2 - 2 * r * (r + h) * math.cos(central_angle))


def _unit(p: tuple[float, float]) -> tuple[float, float, float]:
    lat, lon = map(math.radians, p)
    return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))


def _angle(a: tuple[float, float], b: tuple[float, float]) -> float:
    return haversine_km(a, b) / EARTH_RADIUS_KM


def _midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    """Great-circle midpoint of two (lat, lon) points; None if (near) antipodal."""
    va, vb = _unit(a), _unit(b)
    x, y, z = (va[0] + vb[0], va[1] + vb[1], va[2] + vb[2])
    if math.sqrt(x * x + y * y + z * z) < 1e-6:
        return None
    return (math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x)))


def _estimate_satellite(link, ctx: LatencyContext) -> tuple[float, str] | None:
    """One-way dish ↔ PoP latency for a satellite link (light in vacuum).

    - GEO / MEO: one satellite above the dish–PoP midpoint (GEO on the equator
      at the midpoint longitude); up + down slant ranges from the real
      geometry, or at a 30° elevation when coordinates are missing.
    - LEO: up + down at a 40° elevation plus the arc along the orbital shell
      between dish and PoP (bent pipe when close, inter-satellite links when
      far apart).
    Plus the orbit's processing delay. Altitudes / delays are Global Settings.
    """
    orbit = (link.orbit or "").lower()
    if orbit not in ctx.orbits:
        return None
    h, processing = ctx.orbits[orbit]
    a, b = ctx.coords.get(link.source_node_id), ctx.coords.get(link.target_node_id)
    if orbit == "leo":
        km = 2 * _slant_from_elevation(h, LEO_ELEVATION_DEG)
        if a and b:
            km += _angle(a, b) * (EARTH_RADIUS_KM + h)
    elif a and b:
        sub = _midpoint(a, b)
        if sub is None:
            return None
        if orbit == "geo":
            sub = (0.0, sub[1])
        # Both ends must see the satellite (above the horizon); otherwise one
        # satellite can't connect them and the estimate is unknown.
        horizon = math.acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + h))
        ga, gb = _angle(a, sub), _angle(b, sub)
        if ga > horizon or gb > horizon:
            return None
        km = _slant_from_angle(h, ga) + _slant_from_angle(h, gb)
    else:
        km = 2 * _slant_from_elevation(h, SAT_DEFAULT_ELEVATION_DEG)
    return km / VACUUM_KM_PER_MS + processing, f"{orbit.upper()} estimate"


# transport → estimator.
ESTIMATORS: dict[str, Callable[[object, LatencyContext], tuple[float, str] | None]] = {
    "terrestrial": _estimate_terrestrial,
    "satellite": _estimate_satellite,
}


def effective_latency(link, ctx: LatencyContext) -> LatencyInfo:
    # A known value must be positive; 0 / empty has always meant "not set".
    if (link.latency_source or "unknown") == "measured" and (link.latency_ms or 0) > 0:
        return LatencyInfo(float(link.latency_ms), "measured", "measured")
    estimator = ESTIMATORS.get(link.transport or "terrestrial", _estimate_terrestrial)
    est = estimator(link, ctx)
    if est is None:
        return LatencyInfo(None, "unknown", "unknown")
    return LatencyInfo(round(est[0], 3), "estimated", est[1])


def latency_infos(db: Session, nodes, links) -> dict[int, LatencyInfo]:
    ctx = build_context(db, nodes)
    return {l.id: effective_latency(l, ctx) for l in links}


def routing_latencies(db: Session, nodes, links) -> dict[int, float]:
    """Latency to route on: the effective value, or the unknown penalty."""
    ctx = build_context(db, nodes)
    out = {}
    for l in links:
        info = effective_latency(l, ctx)
        out[l.id] = info.ms if info.ms is not None else ctx.unknown_penalty_ms
    return out


def unknown_penalty_ms(db: Session) -> float:
    return _setting(db, "latency_unknown_penalty_ms")


def summarize(infos: list[LatencyInfo]) -> dict:
    """Known + estimated total for a path, with its breakdown."""
    measured = sum(i.ms for i in infos if i.source == "measured")
    estimated = sum(i.ms for i in infos if i.source == "estimated")
    return {
        "latency_ms": round(measured + estimated, 3),
        "latency_measured_ms": round(measured, 3),
        "latency_estimated_ms": round(estimated, 3),
        "latency_estimated_links": sum(1 for i in infos if i.source == "estimated"),
        "latency_unknown_links": sum(1 for i in infos if i.source == "unknown"),
    }

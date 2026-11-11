"""Map overlay data — server-side proxy + cache for third-party geographic
datasets used by the geographic (globe) view.

Right now this serves submarine-cable geometry from TeleGeography's public
Submarine Cable Map GeoJSON. Proxying server-side (rather than fetching from
the browser) sidesteps CORS, lets us cache the ~1 MB payload instead of
re-pulling it per client, and keeps the third-party dependency in one place.

Data © TeleGeography (submarinecablemap.com) — provided for reference; check
their terms before any commercial reuse.
"""
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/map", tags=["map"])

_CABLE_GEO_URL = "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json"
_LANDING_GEO_URL = "https://www.submarinecablemap.com/api/v3/landing-point/landing-point-geo.json"
_CABLE_ALL_URL = "https://www.submarinecablemap.com/api/v3/cable/all.json"
_CABLE_DETAIL_URL = "https://www.submarinecablemap.com/api/v3/cable/{cid}.json"
# Owners come only from the per-cable detail JSON (no bulk endpoint carries
# them), so cable ownership requires ~700 small fetches — done once and cached
# 30 days. We classify TeleGeography's real free-text `owners` string into a
# provider bucket; the popup always shows the full string so nothing is hidden.
# Match priority top→bottom (a cable co-owned by several hyperscalers takes the
# first match for its single line colour; full ownership stays in the popup).
_CABLE_PROVIDERS = [
    ("google",    "Google",            "#ea4335", ("google",)),
    ("meta",      "Meta (Facebook)",   "#a855f7", ("facebook", "meta platforms", "meta,", "meta ", "(meta")),
    ("amazon",    "Amazon",            "#22c55e", ("amazon", "aws")),
    ("microsoft", "Microsoft",         "#0ea5e9", ("microsoft",)),
    ("lumen",     "Lumen",             "#14b8a6", ("lumen", "level 3", "level3", "centurylink")),
]
_CABLE_OTHER = ("other", "Carrier / consortium", "#64748b")
_PEERINGDB_FAC_URL = "https://www.peeringdb.com/api/fac"
_PEERINGDB_IX_URL = "https://www.peeringdb.com/api/ix"
_PEERINGDB_IXFAC_URL = "https://www.peeringdb.com/api/ixfac"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_geocode_cache: dict = {}          # query (lowercased) -> {"data","ts"}
_GEOCODE_TTL_SEC = 30 * 24 * 3600
_geocode_cooldown_until: float = 0.0
_GEOCODE_COOLDOWN_SEC = 60         # Nominatim asks for ≤1 req/s; back off on failure
# Severe-weather alerts (US National Weather Service — free, no key). An outage
# RISK proxy, not actual outages. Alerts are time-sensitive, so a short TTL.
# NWS requires a descriptive User-Agent (generic ones can be throttled).
_NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?status=actual&message_type=alert&severity=Extreme,Severe"
_NWS_USER_AGENT = os.environ.get("NWS_USER_AGENT", "Phanero-NetworkModeler (network resilience map; contact: admin@phanero.local)")
_weather_cache: dict = {"data": None, "ts": 0.0}
_WEATHER_TTL_SEC = 300             # 5 min — alerts change quickly
_weather_cooldown_until: float = 0.0
_WEATHER_COOLDOWN_SEC = 120
# NWS forecast/county zone geometries (static) keyed by zone id. Zone-based
# alerts (winter storm, high wind, red flag, heat…) carry no polygon — they
# reference these zones. Cached permanently on disk; only new zones are fetched.
_zone_geom_cache: dict = {"data": None, "ts": 0.0}
_ZONE_FETCH_CAP = 400              # max new zones resolved per refresh (backstop)
# Active wildfires (NIFC WFIGS public ArcGIS services — free, no key). Real
# "active event" data (actual burning incidents), distinct from weather alerts.
_NIFC_PERIMETERS_URL = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
_NIFC_INCIDENTS_URL = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query"
_fires_cache: dict = {"data": None, "ts": 0.0}
_FIRES_TTL_SEC = 900               # 15 min
_fires_cooldown_until: float = 0.0
_FIRES_COOLDOWN_SEC = 300
# Earthquakes (USGS — free, no key). Recent quakes M2.5+ past day.
_USGS_QUAKES_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
_quakes_cache: dict = {"data": None, "ts": 0.0}
_QUAKES_TTL_SEC = 600
_quakes_cooldown_until: float = 0.0
# Tropical cyclones (NOAA NHC — free). Active storm positions/intensity.
_NHC_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
_cyclones_cache: dict = {"data": None, "ts": 0.0}
_CYCLONES_TTL_SEC = 1800
_cyclones_cooldown_until: float = 0.0
# Internet outages (IODA / Georgia Tech — free). Country-level outage activity.
_IODA_SUMMARY_URL = "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/summary"
_outages_cache: dict = {"data": None, "ts": 0.0}
_OUTAGES_TTL_SEC = 1200
_outages_cooldown_until: float = 0.0
_GENERIC_COOLDOWN_SEC = 300        # shared backoff for the above free sources
# Alert severity → color (highest first); drives both the legend and the fill.
_WEATHER_SEVERITY = [("Extreme", "#dc2626"), ("Severe", "#f97316"), ("Moderate", "#f59e0b")]
_PEERINGDB_NET_URL = "https://www.peeringdb.com/api/net"
_PEERINGDB_NETFAC_URL = "https://www.peeringdb.com/api/netfac"
_PEERINGDB_POC_URL = "https://www.peeringdb.com/api/poc"
# Carrier lookup: which networks (carriers/ISPs/etc.) you can interconnect with
# at a facility or across a region — "who do I contact for a private connection
# here". NSP (transit/carrier) and ISP are the most relevant, so they sort
# first. Region aggregation is capped to the busiest facilities (by net_count)
# to bound the PeeringDB queries; coverage is reported, never silently capped.
_CARRIER_TYPE_ORDER = {"NSP": 0, "Cable/DSL/ISP": 1, "Network Services": 2,
                       "Content": 3, "Enterprise": 4, "Educational/Research": 5}
_REGION_MAX_FACILITIES = 25
# Satellite (LEO) ISPs that expose terrestrial interconnection PoPs via
# PeeringDB. Kuiper/O3b omitted: Kuiper lands into AWS (no public peering yet),
# O3b/SES isn't cleanly represented. `key` matches the satellite-coverage
# constellation keys so the coverage legend can trigger the PoP plot.
_SAT_OPERATORS = [
    {"key": "starlink", "label": "Starlink", "asn": 14593, "color": "#22d3ee"},
    {"key": "oneweb", "label": "OneWeb", "asn": 800, "color": "#f472b6"},
]
# ASNs to flag as satellite operators in the carrier lookup (incl. regional
# Starlink entities) so they're easy to spot.
_SAT_OPERATOR_ASNS = [14593, 800, 34602, 45700, 152110]
_operator_pops_cache: dict = {}    # operator key -> {"data","ts"}

# These datasets change slowly, so a long TTL is fine. 30 days — both
# submarine cables and data centers are slow-moving reference data.
_CACHE_TTL_SEC = 30 * 24 * 3600

# Disk-backed cache so the data survives backend restarts/redeploys (the
# in-memory dict alone would re-fetch on every process start). Lives under
# the persistent data volume. Each in-memory cache lazily hydrates from its
# disk file and writes back on a successful fetch.
_CACHE_DIR = Path(os.environ.get("MAP_CACHE_DIR", "/app/data/map_cache"))
_cache: dict = {"data": None, "ts": 0.0}
_cable_owners_cache: dict = {"data": None, "ts": 0.0}
_cable_owners_cooldown_until: float = 0.0
_CABLE_OWNERS_COOLDOWN_SEC = 600   # 10 min after a failed owners enrichment
# Carrier-lookup caches, keyed by parameter (fac id / country / net id). These
# are small, slow-moving queries; cache 7 days. They share the PeeringDB
# cooldown (_dc_cooldown_until) since they hit the same rate-limited host.
_CARRIER_TTL_SEC = 7 * 24 * 3600
_carrier_fac_cache: dict = {}      # fac_id  -> {"data","ts"}
_carrier_country_cache: dict = {}  # country -> {"data","ts"}
_carrier_net_cache: dict = {}      # net_id  -> {"data","ts"}
_carrier_ix_cache: dict = {}       # ix_id   -> {"data","ts"}  (members peering at an IX)
_dc_cache: dict = {"data": None, "ts": 0.0}
_ix_cache: dict = {"data": None, "ts": 0.0}
# After a failed PeeringDB fetch (esp. a 429), back off until this time
# before trying again — repeatedly retrying an anonymous, rate-limited
# endpoint only prolongs the throttle. Reset to 0 on success.
_dc_cooldown_until: float = 0.0
_DC_COOLDOWN_RATE_LIMIT_SEC = 600   # 10 min after a 429
_DC_COOLDOWN_OTHER_SEC = 60         # 1 min after other transient failures

# Satellite coverage (CelesTrak). CelesTrak GP data updates every 2h and
# rate-limits aggressive querying, so this dataset uses a short TTL + its own
# backoff. We compute only a coverage band per constellation (tiny), not the
# raw element sets.
_CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=json"
# Raw CelesTrak GP element sets, cached per group and SHARED by every
# satellite overlay (coverage bands, ground tracks, GEO caps). CelesTrak
# heavily rate-limits us, so we must fetch each group at most once per TTL and
# never pull the same elements twice — the derived overlays re-derive from
# these cached raw elements without hitting CelesTrak again.
_gp_cache: dict = {}            # group -> {"data": list, "ts": float}
_GP_TTL_SEC = 12 * 3600
_SAT_MIN_ELEV_DEG = 25.0
_sat_cooldown_until: float = 0.0
_SAT_COOLDOWN_SEC = 1800  # 30 min after a CelesTrak failure (they block aggressors)
# Coverage strength: expected number of satellites simultaneously in view as a
# function of LATITUDE, per constellation — the metric that actually
# differentiates fleets at a given place ("who covers here better").
_strength_cache: dict = {"data": None, "ts": 0.0}
_STRENGTH_TTL_SEC = 12 * 3600
_STRENGTH_LAT_STEP = 2.0        # latitude grid resolution (deg)
_STRENGTH_U_SAMPLES = 120       # orbital-phase samples for the dwell distribution
_MU_EARTH = 398600.4418   # km^3/s^2
_RE_KM = 6371.0           # mean Earth radius
# LEO broadband constellations exposed as coverage bands. (Viasat is GEO —
# a different coverage model — so it's intentionally not in this LEO set.)
# `regime` labels the orbit class (LEO/MEO/GEO). `alt_min`/`alt_max` (km)
# window which satellites in the GROUP count toward the band — needed to
# isolate O3b's MEO sats from SES's GEO fleet (both live in the `ses` group).
_SAT_CONSTELLATIONS = [
    {"key": "starlink", "group": "starlink", "vendor": "Starlink (SpaceX)", "regime": "LEO",
     "color": "#22d3ee", "bands": "Ku user · Ka feeder · optical ISL", "alt_min": 100, "alt_max": 3000},
    {"key": "kuiper", "group": "kuiper", "vendor": "Kuiper (Amazon)", "regime": "LEO",
     "color": "#a78bfa", "bands": "Ka user · Ka feeder", "alt_min": 100, "alt_max": 3000},
    {"key": "oneweb", "group": "oneweb", "vendor": "OneWeb", "regime": "LEO",
     "color": "#f472b6", "bands": "Ku user · Ka feeder", "alt_min": 100, "alt_max": 3000},
    # O3b / O3b mPOWER (SES) — near-equatorial MEO at ~8,000 km. Filtered out
    # of SES's GEO birds by the altitude window.
    {"key": "o3b", "group": "ses", "vendor": "O3b / mPOWER (SES)", "regime": "MEO",
     "color": "#fbbf24", "bands": "Ka user · Ka feeder", "alt_min": 5000, "alt_max": 15000},
]


def _footprint_half_angle(alt_km: float, elev_deg: float) -> float:
    """Earth-central angle (rad) of a satellite's footprint above a min
    elevation `elev_deg` — the angular radius of the ground area that can see
    it. Same geometry the coverage bands use."""
    eps = math.radians(elev_deg)
    ratio = (_RE_KM / (_RE_KM + alt_km)) * math.cos(eps)
    ratio = max(-1.0, min(1.0, ratio))
    return max(0.0, math.acos(ratio) - eps)


def _shell_visibility(inc_deg: float, lam: float, lat_grid_rad: list, u_samples: int) -> list:
    """Probability that ONE satellite of inclination `inc_deg` and footprint
    half-angle `lam` (rad) is in view from each ground latitude in
    `lat_grid_rad`, averaged over orbital phase and longitude.

    Method: a satellite's sub-latitude over a circular orbit is
    ψ = asin(sin i · sin u) for argument-of-latitude u — sampling u uniformly
    reproduces the real dwell distribution (satellites linger near ±i). For
    each sampled sub-point (ψ, random longitude) the satellite is visible from
    a ground point at latitude φ iff their great-circle separation ≤ λ;
    averaging over the (uniform) relative longitude Δλ gives the fraction of
    longitudes in view: acos(A)/π with A = (cosλ − sinφ·sinψ)/(cosφ·cosψ).

    Summing this over a fleet gives the expected number simultaneously in view.
    """
    i = math.radians(inc_deg)
    psis = [math.asin(math.sin(i) * math.sin(2 * math.pi * k / u_samples)) for k in range(u_samples)]
    cos_lam = math.cos(lam)
    out = []
    for phi in lat_grid_rad:
        sphi, cphi = math.sin(phi), math.cos(phi)
        acc = 0.0
        for psi in psis:
            spsi, cpsi = math.sin(psi), math.cos(psi)
            denom = cphi * cpsi
            if denom <= 1e-9:   # ground point and/or sub-point at a pole
                acc += 1.0 if abs(phi - psi) <= lam else 0.0
                continue
            A = (cos_lam - sphi * spsi) / denom
            if A <= -1.0:
                acc += 1.0          # in view at every longitude
            elif A < 1.0:
                acc += math.acos(A) / math.pi
            # A >= 1.0 → never in view at this sub-latitude → add 0
        out.append(acc / u_samples)
    return out


def _hydrate(mem: dict, name: str) -> None:
    """Populate an in-memory cache from its disk file if empty. The file's
    mtime is used as the cache timestamp so the TTL spans restarts."""
    if mem["data"] is not None:
        return
    p = _CACHE_DIR / f"{name}.json"
    try:
        if p.exists():
            mem["data"] = json.loads(p.read_text())
            mem["ts"] = p.stat().st_mtime
    except Exception:
        pass


def _persist(mem: dict, name: str, data: dict, now: float) -> None:
    """Update the in-memory cache and write it to disk (best-effort)."""
    mem["data"] = data
    mem["ts"] = now
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then replace, so a crash mid-write can't leave
        # a truncated cache file.
        tmp = _CACHE_DIR / f".{name}.json.tmp"
        tmp.write_text(json.dumps(data))
        os.replace(tmp, _CACHE_DIR / f"{name}.json")
    except Exception:
        pass


def _fetch_json(url: str, headers: dict | None = None) -> dict:
    h = {"User-Agent": "Phanero/1.0 (+geographic-view)"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _peeringdb_headers() -> dict | None:
    """Optional PeeringDB auth header — raises the anonymous rate limit."""
    api_key = os.environ.get("PEERINGDB_API_KEY", "").strip()
    return {"Authorization": f"Api-Key {api_key}"} if api_key else None


def _parse_km(s: str) -> float | None:
    """Parse a length string like '6,400 km' → 6400.0 (None if unparseable)."""
    m = re.search(r"([\d,.]+)", s or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _classify_cable_owner(owners: str) -> str:
    """Map a cable's free-text owners string to a provider bucket key. Returns
    the first matching hyperscaler (priority order) or 'other'."""
    s = (owners or "").lower()
    for key, _label, _color, needles in _CABLE_PROVIDERS:
        if any(n in s for n in needles):
            return key
    return _CABLE_OTHER[0]


def _cache_slot(store: dict, key) -> dict:
    """Get (creating if needed) the per-parameter cache slot in a keyed store."""
    slot = store.get(key)
    if slot is None:
        slot = {"data": None, "ts": 0.0}
        store[key] = slot
    return slot


def _pdb_paginated(url: str, headers: dict | None, page: int = 250, max_pages: int = 60) -> list:
    """Fetch all rows from a PeeringDB list endpoint, following skip/limit
    pagination. Bounded by max_pages (a backstop; logged via the returned
    count so truncation is never silent)."""
    sep = "&" if "?" in url else "?"
    out: list = []
    for p in range(max_pages):
        chunk = _fetch_json(f"{url}{sep}limit={page}&skip={p * page}", headers).get("data", [])
        out.extend(chunk)
        if len(chunk) < page:
            break
    return out


def _resolve_nets(net_ids, headers: dict | None) -> dict:
    """Resolve PeeringDB net ids → {id: {id,name,asn,info_type,website}},
    batching the id__in query to keep URLs sane."""
    ids = list(net_ids)
    out: dict = {}
    for i in range(0, len(ids), 150):
        batch = ids[i:i + 150]
        url = f"{_PEERINGDB_NET_URL}?id__in={','.join(map(str, batch))}&fields=id,name,asn,info_type,website"
        for n in _fetch_json(url, headers).get("data", []):
            out[n["id"]] = n
    return out


def _carrier_row(n: dict, **extra) -> dict:
    """Shape a PeeringDB net record into a carrier-list row."""
    return {
        "net_id": n.get("id"),
        "asn": n.get("asn"),
        "name": n.get("name") or f"AS{n.get('asn')}",
        "type": n.get("info_type") or "Not Disclosed",
        "website": n.get("website") or "",
        "is_satellite": n.get("asn") in _SAT_OPERATOR_ASNS,
        **extra,
    }


def _fetch_gp_cached(group: str) -> list:
    """Return the raw CelesTrak GP element sets for a group, fetching only on a
    cache miss. Shared by every satellite overlay so a group is pulled at most
    once per _GP_TTL_SEC — the key to not double-fetching the same data while
    CelesTrak throttles us.

    Backstop only: callers short-circuit on _sat_cooldown_until before getting
    here, but we also refuse to fetch during cooldown and serve stale instead
    (raising only if we have nothing cached for the group).
    """
    now = time.time()
    mem = _gp_cache.get(group)
    if mem is None:
        mem = {"data": None, "ts": 0.0}
        _gp_cache[group] = mem
    _hydrate(mem, f"gp_{group}")
    if mem["data"] is not None and (now - mem["ts"]) < _GP_TTL_SEC:
        return mem["data"]
    if now < _sat_cooldown_until:
        if mem["data"] is not None:
            return mem["data"]
        raise RuntimeError(f"CelesTrak cooldown active and no cached GP for '{group}'")
    gp = _fetch_json(_CELESTRAK_GP.format(group=group))
    if not isinstance(gp, list):
        raise RuntimeError(f"unexpected GP response for '{group}'")
    _persist(mem, f"gp_{group}", gp, now)
    return gp


@router.get("/submarine-cables")
def submarine_cables():
    """Return submarine cable routes + landing points as GeoJSON.

    Shape: { "cables": <FeatureCollection>, "landing_points": <FeatureCollection>,
             "attribution": "..." }. Cached server-side (disk-backed) for 30
    days; on a refresh failure we serve the last good copy if we have one.
    """
    now = time.time()
    _hydrate(_cache, "submarine_cables")
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL_SEC:
        return _cache["data"]
    try:
        data = {
            "cables": _fetch_json(_CABLE_GEO_URL),
            "landing_points": _fetch_json(_LANDING_GEO_URL),
            "attribution": "© TeleGeography — submarinecablemap.com",
        }
        _persist(_cache, "submarine_cables", data, now)
        return data
    except Exception as e:
        # Serve stale rather than fail if we've fetched successfully before.
        if _cache["data"] is not None:
            return _cache["data"]
        raise HTTPException(502, f"Failed to fetch submarine cable data: {e}")


@router.get("/submarine-cable-owners")
def submarine_cable_owners():
    """Per-cable ownership for the submarine-cable overlay: a map of cable id →
    {owners, suppliers, rfs, provider}, plus a provider summary.

    Owners live only in TeleGeography's per-cable detail JSON (no bulk endpoint
    carries them), so this fetches each cable once (concurrently, politely) and
    caches the result 30 days on disk — it's a one-time, slow enrichment that
    the existing fast geometry endpoint doesn't pay for. `provider` is a bucket
    (google/meta/amazon/microsoft/other) classified from the real `owners`
    string; the string itself is returned verbatim so nothing is fabricated.
    """
    import concurrent.futures

    global _cable_owners_cooldown_until
    now = time.time()
    _hydrate(_cable_owners_cache, "submarine_cable_owners")
    if _cable_owners_cache["data"] is not None and (now - _cable_owners_cache["ts"]) < _CACHE_TTL_SEC:
        return _cable_owners_cache["data"]
    if now < _cable_owners_cooldown_until:
        if _cable_owners_cache["data"] is not None:
            return _cable_owners_cache["data"]
        raise HTTPException(503, f"Cable-owner data temporarily unavailable (upstream cooldown); retry in ~{int(_cable_owners_cooldown_until - now)}s.")
    try:
        cable_list = _fetch_json(_CABLE_ALL_URL)
        if not isinstance(cable_list, list) or not cable_list:
            raise RuntimeError("unexpected cable list response")
        ids = [c["id"] for c in cable_list if isinstance(c, dict) and c.get("id")]

        def _one(cid: str):
            try:
                cd = _fetch_json(_CABLE_DETAIL_URL.format(cid=cid))
                return cid, cd
            except Exception:
                return cid, None

        owners: dict = {}
        counts: dict = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for cid, cd in ex.map(_one, ids):
                if not cd:
                    continue
                owners_str = (cd.get("owners") or "").strip()
                provider = _classify_cable_owner(owners_str)
                length_str = (cd.get("length") or "").strip()
                owners[cid] = {
                    "owners": owners_str,
                    "suppliers": (cd.get("suppliers") or "").strip(),
                    "rfs": cd.get("rfs") or "",
                    "rfs_year": cd.get("rfs_year"),
                    "length": length_str,
                    "length_km": _parse_km(length_str),
                    "provider": provider,
                }
                counts[provider] = counts.get(provider, 0) + 1
        if not owners:
            raise RuntimeError("no cable detail fetched")
        # Provider summary in display order (hyperscalers first, then Other).
        order = [(k, l, c) for k, l, c, _ in _CABLE_PROVIDERS] + [_CABLE_OTHER]
        providers = [
            {"key": k, "label": l, "color": c, "count": counts.get(k, 0)}
            for k, l, c in order if counts.get(k, 0)
        ]
        data = {
            "owners": owners,
            "providers": providers,
            "cable_count": len(owners),
            "note": "Ownership from TeleGeography per-cable data; provider is a bucket classified from the owners text.",
            "attribution": "© TeleGeography — submarinecablemap.com",
        }
        _persist(_cable_owners_cache, "submarine_cable_owners", data, now)
        _cable_owners_cooldown_until = 0.0
        return data
    except Exception as e:
        _cable_owners_cooldown_until = now + _CABLE_OWNERS_COOLDOWN_SEC
        if _cable_owners_cache["data"] is not None:
            return _cable_owners_cache["data"]
        raise HTTPException(502, f"Failed to fetch cable ownership data: {e}")


def _pdb_cooldown_guard(now: float, slot: dict, what: str):
    """Shared PeeringDB cooldown short-circuit for the carrier endpoints."""
    if now < _dc_cooldown_until:
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(503, f"{what} temporarily unavailable (PeeringDB cooldown); retry in ~{int(_dc_cooldown_until - now)}s.")
    return None


def _set_pdb_cooldown(now: float, e: Exception) -> None:
    global _dc_cooldown_until
    is_rl = isinstance(e, urllib.error.HTTPError) and e.code == 429
    _dc_cooldown_until = now + (_DC_COOLDOWN_RATE_LIMIT_SEC if is_rl else _DC_COOLDOWN_OTHER_SEC)


def _facility_brief(fac_id: int, headers: dict | None) -> dict:
    """Facility name/city/country, from the datacenters cache if present
    (no extra fetch), else a small PeeringDB lookup."""
    _hydrate(_dc_cache, "datacenters")
    if _dc_cache["data"] is not None:
        for f in _dc_cache["data"].get("datacenters", {}).get("features", []):
            if f.get("properties", {}).get("id") == fac_id:
                p = f["properties"]
                return {"id": fac_id, "name": p.get("name"), "operator": p.get("operator"),
                        "city": p.get("city"), "country": p.get("country")}
    rows = _fetch_json(f"{_PEERINGDB_FAC_URL}?id={fac_id}&fields=id,name,org_name,city,country", headers).get("data", [])
    f = rows[0] if rows else {}
    return {"id": fac_id, "name": f.get("name"), "operator": f.get("org_name"),
            "city": f.get("city"), "country": f.get("country")}


def _carrier_types_summary(carriers: list) -> list:
    counts: dict = {}
    for c in carriers:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
    return [{"type": t, "count": n} for t, n in
            sorted(counts.items(), key=lambda kv: (_CARRIER_TYPE_ORDER.get(kv[0], 99), -kv[1]))]


@router.get("/facility-carriers")
def facility_carriers(fac_id: int):
    """Networks present at a PeeringDB facility — the carriers you can
    interconnect with there. Returns {facility, carriers[], types[]}, sorted
    carrier/transit-first. Cached 7 days per facility; shares PeeringDB cooldown.
    """
    now = time.time()
    slot = _cache_slot(_carrier_fac_cache, fac_id)
    _hydrate(slot, f"carriers_fac_{fac_id}")
    if slot["data"] is not None and (now - slot["ts"]) < _CARRIER_TTL_SEC:
        return slot["data"]
    guarded = _pdb_cooldown_guard(now, slot, "Carrier data")
    if guarded is not None:
        return guarded
    try:
        h = _peeringdb_headers()
        nf = _fetch_json(f"{_PEERINGDB_NETFAC_URL}?fac_id={fac_id}&fields=net_id", h).get("data", [])
        net_ids = {x["net_id"] for x in nf if x.get("net_id")}
        nets = _resolve_nets(net_ids, h)
        carriers = [_carrier_row(nets[i]) for i in net_ids if i in nets]
        carriers.sort(key=lambda c: (_CARRIER_TYPE_ORDER.get(c["type"], 99), (c["name"] or "").lower()))
        data = {
            "facility": _facility_brief(fac_id, h),
            "carriers": carriers,
            "types": _carrier_types_summary(carriers),
            "count": len(carriers),
            "attribution": "Interconnection data © PeeringDB",
        }
        _persist(slot, f"carriers_fac_{fac_id}", data, now)
        return data
    except Exception as e:
        _set_pdb_cooldown(now, e)
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(502, f"Failed to fetch facility carriers: {e}")


@router.get("/ix-carriers")
def ix_carriers(ix_id: int):
    """Networks present at an internet exchange — who you can peer with over its
    fabric (from PeeringDB netixlan). The IX counterpart of /facility-carriers.
    Cached 7 days per IX; shares the PeeringDB cooldown.
    """
    now = time.time()
    slot = _cache_slot(_carrier_ix_cache, ix_id)
    _hydrate(slot, f"carriers_ix_{ix_id}")
    if slot["data"] is not None and (now - slot["ts"]) < _CARRIER_TTL_SEC:
        return slot["data"]
    guarded = _pdb_cooldown_guard(now, slot, "IX member data")
    if guarded is not None:
        return guarded
    try:
        h = _peeringdb_headers()
        nx = _fetch_json(f"https://www.peeringdb.com/api/netixlan?ix_id={ix_id}&fields=net_id", h).get("data", [])
        net_ids = {x["net_id"] for x in nx if x.get("net_id")}
        nets = _resolve_nets(net_ids, h)
        carriers = [_carrier_row(nets[i]) for i in net_ids if i in nets]
        carriers.sort(key=lambda c: (_CARRIER_TYPE_ORDER.get(c["type"], 99), (c["name"] or "").lower()))
        info = _ix_coords_index().get(ix_id, {})
        data = {
            "ix": {"id": ix_id, "name": info.get("name"), "city": info.get("city"), "country": info.get("country")},
            "carriers": carriers,
            "types": _carrier_types_summary(carriers),
            "count": len(carriers),
            "attribution": "Interconnection data © PeeringDB",
        }
        _persist(slot, f"carriers_ix_{ix_id}", data, now)
        return data
    except Exception as e:
        _set_pdb_cooldown(now, e)
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(502, f"Failed to fetch IX members: {e}")


@router.get("/region-carriers")
def region_carriers(country: str):
    """Carriers across a country, aggregated over its busiest facilities and
    deduped, with a per-carrier site count. Capped to the top
    _REGION_MAX_FACILITIES by net_count; coverage is reported. Cached 7 days.
    """
    now = time.time()
    cc = (country or "").upper()[:2]
    if not cc:
        raise HTTPException(400, "country (2-letter code) required")
    slot = _cache_slot(_carrier_country_cache, cc)
    _hydrate(slot, f"carriers_country_{cc}")
    if slot["data"] is not None and (now - slot["ts"]) < _CARRIER_TTL_SEC:
        return slot["data"]
    guarded = _pdb_cooldown_guard(now, slot, "Carrier data")
    if guarded is not None:
        return guarded
    try:
        h = _peeringdb_headers()
        facs = _fetch_json(f"{_PEERINGDB_FAC_URL}?country={cc}&fields=id,name,net_count", h).get("data", [])
        facs = [f for f in facs if f.get("id")]
        facs.sort(key=lambda f: -(f.get("net_count") or 0))
        chosen = facs[:_REGION_MAX_FACILITIES]
        fac_ids = [f["id"] for f in chosen]
        sites: dict = {}
        if fac_ids:
            csv = ",".join(map(str, fac_ids))
            rows = _pdb_paginated(f"{_PEERINGDB_NETFAC_URL}?fac_id__in={csv}&fields=net_id,fac_id", h)
            for r in rows:
                nid = r.get("net_id")
                if nid:
                    sites.setdefault(nid, set()).add(r.get("fac_id"))
        nets = _resolve_nets(sites.keys(), h)
        carriers = [_carrier_row(nets[i], site_count=len(sites[i])) for i in sites if i in nets]
        # Most-present carriers first (then transit-type, then name).
        carriers.sort(key=lambda c: (-c["site_count"], _CARRIER_TYPE_ORDER.get(c["type"], 99), (c["name"] or "").lower()))
        data = {
            "country": cc,
            "carriers": carriers,
            "types": _carrier_types_summary(carriers),
            "count": len(carriers),
            "facilities_covered": len(chosen),
            "facilities_total": len(facs),
            "attribution": "Interconnection data © PeeringDB",
        }
        _persist(slot, f"carriers_country_{cc}", data, now)
        return data
    except Exception as e:
        _set_pdb_cooldown(now, e)
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(502, f"Failed to fetch region carriers: {e}")


@router.get("/carrier")
def carrier(net_id: int):
    """A single carrier's PeeringDB profile + contact info: ASN, website,
    peering policy, profile link, and any publicly-exposed points of contact.
    Cached 7 days. Contact fields reflect exactly what PeeringDB exposes — many
    networks restrict email/phone, so those may be absent (never fabricated).
    """
    now = time.time()
    slot = _cache_slot(_carrier_net_cache, net_id)
    _hydrate(slot, f"carrier_net_{net_id}")
    if slot["data"] is not None and (now - slot["ts"]) < _CARRIER_TTL_SEC:
        return slot["data"]
    guarded = _pdb_cooldown_guard(now, slot, "Carrier detail")
    if guarded is not None:
        return guarded
    try:
        h = _peeringdb_headers()
        rows = _fetch_json(f"{_PEERINGDB_NET_URL}/{net_id}", h).get("data", [])
        n = rows[0] if rows else {}
        if not n:
            raise RuntimeError("network not found")
        poc = _fetch_json(f"{_PEERINGDB_POC_URL}?net_id={net_id}", h).get("data", [])
        contacts = [
            {"role": p.get("role"), "name": p.get("name"), "email": p.get("email"),
             "phone": p.get("phone"), "url": p.get("url")}
            for p in poc if (p.get("email") or p.get("phone") or p.get("url"))
        ]
        data = {
            "net_id": net_id,
            "asn": n.get("asn"),
            "name": n.get("name"),
            "aka": n.get("aka") or "",
            "type": n.get("info_type") or "Not Disclosed",
            "website": n.get("website") or "",
            "policy_general": n.get("policy_general") or "",
            "policy_url": n.get("policy_url") or "",
            "irr_as_set": n.get("irr_as_set") or "",
            "looking_glass": n.get("looking_glass") or "",
            "notes": n.get("notes") or "",
            "peeringdb_url": f"https://www.peeringdb.com/net/{net_id}",
            "contacts": contacts,
            "attribution": "Interconnection data © PeeringDB",
        }
        _persist(slot, f"carrier_net_{net_id}", data, now)
        return data
    except Exception as e:
        _set_pdb_cooldown(now, e)
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(502, f"Failed to fetch carrier detail: {e}")


def _fac_coords_index() -> dict:
    """{fac_id: {lonlat,name,city,country}} from the cached datacenters."""
    _hydrate(_dc_cache, "datacenters")
    out: dict = {}
    data = _dc_cache["data"]
    if not data:
        return out
    for f in data.get("datacenters", {}).get("features", []):
        p = f.get("properties") or {}
        c = (f.get("geometry") or {}).get("coordinates")
        if p.get("id") is not None and c:
            out[p["id"]] = {"lonlat": c, "name": p.get("name"), "city": p.get("city"),
                            "country": p.get("country"), "operator": p.get("operator")}
    return out


def _ix_coords_index() -> dict:
    """{ix_id: {lonlat,name,city,country}} from the cached IXPs."""
    _hydrate(_ix_cache, "ixps")
    out: dict = {}
    data = _ix_cache["data"]
    if not data:
        return out
    for f in data.get("ixps", {}).get("features", []):
        p = f.get("properties") or {}
        c = (f.get("geometry") or {}).get("coordinates")
        if p.get("id") is not None and c:
            out[p["id"]] = {"lonlat": c, "name": p.get("name"), "city": p.get("city"), "country": p.get("country")}
    return out


@router.get("/operator-pops")
def operator_pops(key: str):
    """A satellite (LEO) operator's terrestrial interconnection PoPs — the
    facilities and IXPs where its network is present (from PeeringDB), as a
    GeoJSON point collection tagged facility vs ix. Coordinates reuse the cached
    data-center + IXP locations, so this is two small PeeringDB calls. Cached
    7 days per operator; shares the PeeringDB cooldown.
    """
    op = next((o for o in _SAT_OPERATORS if o["key"] == key), None)
    if not op:
        raise HTTPException(404, f"Unknown satellite operator: {key}")
    now = time.time()
    slot = _cache_slot(_operator_pops_cache, key)
    _hydrate(slot, f"operator_pops_{key}")
    if slot["data"] is not None and (now - slot["ts"]) < _CARRIER_TTL_SEC:
        return slot["data"]
    guarded = _pdb_cooldown_guard(now, slot, "Operator-PoP data")
    if guarded is not None:
        return guarded
    try:
        h = _peeringdb_headers()
        nets = _fetch_json(f"{_PEERINGDB_NET_URL}?asn={op['asn']}&fields=id", h).get("data", [])
        if not nets:
            raise RuntimeError("operator network not found in PeeringDB")
        nid = nets[0]["id"]
        facs = _fetch_json(f"{_PEERINGDB_NETFAC_URL}?net_id={nid}&fields=fac_id", h).get("data", [])
        ixs = _fetch_json(f"https://www.peeringdb.com/api/netixlan?net_id={nid}&fields=ix_id", h).get("data", [])
        fci, ici = _fac_coords_index(), _ix_coords_index()
        feats = []
        for x in facs:
            info = fci.get(x.get("fac_id"))
            if info:
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": info["lonlat"]},
                              "properties": {"kind": "facility", "fac_id": x.get("fac_id"), "name": info["name"],
                                             "operator": info.get("operator"), "city": info["city"], "country": info["country"]}})
        seen = set()
        for x in ixs:
            iid = x.get("ix_id")
            if iid is None or iid in seen:
                continue
            seen.add(iid)
            info = ici.get(iid)
            if info:
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": info["lonlat"]},
                              "properties": {"kind": "ix", "ix_id": iid, "name": info["name"], "city": info["city"], "country": info["country"]}})
        nfac = sum(1 for f in feats if f["properties"]["kind"] == "facility")
        data = {
            "operator": op["label"], "key": key, "color": op["color"],
            "pops": {"type": "FeatureCollection", "features": feats},
            "counts": {"facilities": nfac, "ixps": len(feats) - nfac},
            "note": "Interconnection PoPs (facilities + IXPs the operator's network is present at), located via cached PeeringDB coordinates.",
            "attribution": "Interconnection data © PeeringDB",
        }
        _persist(slot, f"operator_pops_{key}", data, now)
        return data
    except Exception as e:
        _set_pdb_cooldown(now, e)
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(502, f"Failed to fetch operator PoPs: {e}")


# --- Interconnection path finder (heuristic A-loc → Z-loc) ----------------
# Graph: location nodes (facilities + IXPs) bridged by carrier networks. A
# carrier present at two locations can move traffic between them; weighted so
# transit/carrier types are preferred. NOT a guaranteed/orderable route —
# presence ≠ transit ≠ commercial availability; it's a candidate shortlist.
_PATH_TYPE_WEIGHT = {"NSP": 1.0, "Network Services": 1.3, "Cable/DSL/ISP": 1.6}
_PATH_CARRIER_TYPES = "NSP,Cable/DSL/ISP,Network Services"
_graph_cache: dict = {"data": None, "ts": 0.0}
_GRAPH_TTL_SEC = 7 * 24 * 3600


def _haversine(la1, lo1, la2, lo2):
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _interconnect_graph() -> dict:
    """Build (and cache 7d) the carrier interconnection graph from 3 bulk
    PeeringDB calls: carrier networks + netfac + netixlan. Returns
    {net_meta:{nid:{asn,name,type}}, net_locs:{nid:[locKey,...]}} where locKey
    is 'F<fac_id>' or 'I<ix_id>'. Only carrier-type nets and located sites kept;
    nets touching <2 sites dropped (can't bridge)."""
    now = time.time()
    _hydrate(_graph_cache, "interconnect_graph")
    if _graph_cache["data"] is not None and (now - _graph_cache["ts"]) < _GRAPH_TTL_SEC:
        return _graph_cache["data"]
    h = _peeringdb_headers()
    nets = _fetch_json(f"{_PEERINGDB_NET_URL}?" + urllib.parse.urlencode(
        {"info_type__in": _PATH_CARRIER_TYPES, "fields": "id,asn,name,info_type"}), h).get("data", [])
    carrier = {n["id"]: {"asn": n.get("asn"), "name": n.get("name"), "type": n.get("info_type")} for n in nets}
    fac, ix = _fac_coords_index(), _ix_coords_index()
    netfac = _fetch_json(f"{_PEERINGDB_NETFAC_URL}?" + urllib.parse.urlencode({"fields": "net_id,fac_id"}), h).get("data", [])
    netix = _fetch_json("https://www.peeringdb.com/api/netixlan?" + urllib.parse.urlencode({"fields": "net_id,ix_id"}), h).get("data", [])
    locs: dict = {}
    for r in netfac:
        nid, fid = r.get("net_id"), r.get("fac_id")
        if nid in carrier and fid in fac:
            locs.setdefault(nid, set()).add(f"F{fid}")
    for r in netix:
        nid, iid = r.get("net_id"), r.get("ix_id")
        if nid in carrier and iid in ix:
            locs.setdefault(nid, set()).add(f"I{iid}")
    net_locs = {str(nid): sorted(s) for nid, s in locs.items() if len(s) >= 2}
    data = {"net_meta": {nid: carrier[int(nid)] for nid in net_locs}, "net_locs": net_locs}
    _persist(_graph_cache, "interconnect_graph", data, now)
    return data


def _nearest_fac_loc(lat: float, lon: float, fac: dict):
    best, bd = None, 1e18
    for fid, info in fac.items():
        c = info["lonlat"]
        d = _haversine(lat, lon, c[1], c[0])
        if d < bd:
            bd, best = d, fid
    return best, bd


def _loc_node(key: str, fac: dict, ix: dict) -> dict:
    info = fac.get(int(key[1:])) if key[0] == "F" else ix.get(int(key[1:]))
    return {"key": key, "kind": "facility" if key[0] == "F" else "ix",
            "name": (info or {}).get("name"), "city": (info or {}).get("city"),
            "country": (info or {}).get("country"), "lonlat": (info or {}).get("lonlat")}


@router.get("/path")
def interconnect_path(a_lat: float, a_lon: float, z_lat: float, z_lon: float, alts: int = 2):
    """Heuristic interconnection path(s) from an A-loc to a Z-loc over the
    carrier graph. Snaps each end to its nearest facility, then runs a
    carrier-weighted shortest path (NSP/transit preferred). Returns the snap
    distances (so the caller can flag a remote last mile) and up to `alts`
    diverse candidate paths. A shortlist of plausible provider chains — not a
    guaranteed or orderable route."""
    import heapq
    g = _interconnect_graph()
    fac, ix = _fac_coords_index(), _ix_coords_index()
    if not fac:
        raise HTTPException(503, "Facility data not loaded; enable the Data centers overlay once.")
    net_locs = {int(k): v for k, v in g["net_locs"].items()}
    net_meta = {int(k): v for k, v in g["net_meta"].items()}
    loc_nets: dict = {}
    for nid, ls in net_locs.items():
        for L in ls:
            loc_nets.setdefault(L, []).append(nid)
    aL, aDist = _nearest_fac_loc(a_lat, a_lon, fac)
    zL, zDist = _nearest_fac_loc(z_lat, z_lon, fac)
    aKey, zKey = f"F{aL}", f"F{zL}"

    def shortest(weight):
        # Bipartite Dijkstra over loc nodes (L*) and net nodes (N<id>). Using a
        # carrier costs its weight; arriving at a location costs 0.
        dist = {aKey: 0.0}
        prev: dict = {}
        pq = [(0.0, aKey)]
        seen_net = set()
        while pq:
            d, node = heapq.heappop(pq)
            if node == zKey:
                break
            if d > dist.get(node, 1e18):
                continue
            if node[0] == "N":
                nid = int(node[1:])
                for L in net_locs.get(nid, []):
                    nd = d
                    if nd < dist.get(L, 1e18):
                        dist[L] = nd; prev[L] = node; heapq.heappush(pq, (nd, L))
            else:
                for nid in loc_nets.get(node, []):
                    nn = f"N{nid}"
                    if nn in seen_net:
                        continue
                    nd = d + weight(nid)
                    if nd < dist.get(nn, 1e18):
                        dist[nn] = nd; prev[nn] = node; heapq.heappush(pq, (nd, nn))
        if zKey not in dist:
            return None
        # reconstruct
        chain = []
        cur = zKey
        while cur != aKey:
            p = prev.get(cur)
            if p is None:
                return None
            chain.append((p, cur))
            cur = p
        chain.reverse()
        # chain alternates loc -> net -> loc; build segments (carrier, from, to)
        segs, used = [], []
        loc_seq = [aKey]
        i = 0
        while i < len(chain):
            # chain[i] = (loc, net), chain[i+1] = (net, loc)
            net_node = chain[i][1]
            nid = int(net_node[1:])
            used.append(nid)
            to_loc = chain[i + 1][1] if i + 1 < len(chain) else net_node
            segs.append({"carrier": {"asn": net_meta[nid]["asn"], "name": net_meta[nid]["name"], "type": net_meta[nid]["type"]},
                         "from": _loc_node(loc_seq[-1], fac, ix), "to": _loc_node(to_loc, fac, ix)})
            loc_seq.append(to_loc)
            i += 2
        return {"segments": segs, "used": used}

    paths = []
    penalty: dict = {}
    for _ in range(max(1, min(int(alts), 10))):
        w = lambda nid: _PATH_TYPE_WEIGHT.get(net_meta[nid]["type"], 2.0) * penalty.get(nid, 1.0)
        r = shortest(w)
        if not r:
            break
        if any(p["segments"] == r["segments"] for p in paths):
            break
        paths.append(r)
        for nid in r["used"]:           # penalize this path's carriers → diverse alt
            penalty[nid] = penalty.get(nid, 1.0) + 2.0
    return {
        "a": {"lat": a_lat, "lon": a_lon, "snap": _loc_node(aKey, fac, ix), "snap_km": round(aDist)},
        "z": {"lat": z_lat, "lon": z_lon, "snap": _loc_node(zKey, fac, ix), "snap_km": round(zDist)},
        "paths": [{"segments": p["segments"], "hops": len(p["segments"])} for p in paths],
        "note": ("Heuristic carrier-graph path(s): presence ≠ guaranteed transit/peering, no pricing/SLA. "
                 "Large A/Z snap distance means the first/last mile (e.g. to a remote town) isn't covered — "
                 "use the nearest-hub + satellite answer there."),
        "attribution": "Interconnection data © PeeringDB",
    }


@router.get("/nearest-facilities")
def nearest_facilities(lat: float, lon: float, limit: int = 8):
    """Nearest interconnection facilities to an arbitrary point, by great-circle
    distance — for places with no local colo (e.g. remote towns), this answers
    "where's my nearest hub to backhaul to, and how far". Pure local compute
    from the cached PeeringDB facility coordinates: no upstream call, so it
    never touches the PeeringDB rate limit. Pair the result with
    /facility-carriers to see who's at each hub.
    """
    _hydrate(_dc_cache, "datacenters")
    data = _dc_cache["data"]
    if not data:
        raise HTTPException(503, "Facility locations not loaded yet — enable the Data centers overlay once to populate them.")
    feats = data.get("datacenters", {}).get("features", [])

    def _hav(la1, lo1, la2, lo2):
        r = 6371.0
        p1, p2 = math.radians(la1), math.radians(la2)
        dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    rows = []
    for f in feats:
        c = (f.get("geometry") or {}).get("coordinates")
        p = f.get("properties") or {}
        if not c or len(c) < 2:
            continue
        rows.append((_hav(lat, lon, c[1], c[0]), p))
    rows.sort(key=lambda x: x[0])
    n = max(1, min(int(limit), 25))
    out = [{
        "id": p.get("id"), "name": p.get("name"), "operator": p.get("operator"),
        "city": p.get("city"), "country": p.get("country"),
        "net_count": p.get("net_count"), "distance_km": round(dist),
    } for dist, p in rows[:n]]
    return {"point": {"lat": lat, "lon": lon}, "facilities": out,
            "attribution": "Facility locations © PeeringDB"}


@router.get("/geocode")
def geocode(q: str, limit: int = 5):
    """Address/place lookup → coordinates, so the map can re-orient to a typed
    location. Proxies OpenStreetMap Nominatim (no key) server-side: bypasses
    CORS, caches each query 30 days (results are stable), and keeps the
    third-party call polite (Nominatim asks for ≤1 req/s + a real User-Agent,
    which _fetch_json sets). Returns results with lat/lon and a [w,s,e,n] bbox.
    """
    global _geocode_cooldown_until
    query = (q or "").strip()
    if not query:
        raise HTTPException(400, "q (address or place) is required")
    now = time.time()
    key = query.lower()
    slot = _cache_slot(_geocode_cache, key)
    fname = "geocode_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    _hydrate(slot, fname)
    if slot["data"] is not None and (now - slot["ts"]) < _GEOCODE_TTL_SEC:
        return slot["data"]
    if now < _geocode_cooldown_until:
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(503, "Geocoder cooling down; retry shortly.")
    try:
        url = _NOMINATIM_URL + "?" + urllib.parse.urlencode({
            "q": query, "format": "jsonv2", "limit": max(1, min(int(limit), 10)), "addressdetails": 1,
        })
        raw = _fetch_json(url)
        results = []
        for r in (raw if isinstance(raw, list) else []):
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (KeyError, ValueError, TypeError):
                continue
            bb = r.get("boundingbox")  # Nominatim: [south, north, west, east] as strings
            bbox = None
            if isinstance(bb, list) and len(bb) == 4:
                try:
                    bbox = [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])]  # [w,s,e,n]
                except (ValueError, TypeError):
                    bbox = None
            results.append({
                "display_name": r.get("display_name", ""),
                "lat": lat, "lon": lon,
                "type": r.get("type") or r.get("category") or "",
                "bbox": bbox,
            })
        data = {"query": query, "results": results,
                "attribution": "© OpenStreetMap contributors (Nominatim)"}
        _persist(slot, fname, data, now)
        _geocode_cooldown_until = 0.0
        return data
    except Exception as e:
        _geocode_cooldown_until = now + _GEOCODE_COOLDOWN_SEC
        if slot["data"] is not None:
            return slot["data"]
        raise HTTPException(502, f"Geocoding failed: {e}")


def _zone_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _resolve_zone_geometries(zone_urls: set, headers: dict) -> dict:
    """Return {zone_id: geometry} for the requested NWS zone URLs, fetching only
    the ones not already cached (zones are static, so the cache is permanent)."""
    import concurrent.futures
    _hydrate(_zone_geom_cache, "weather_zones")
    store = _zone_geom_cache["data"] or {}
    missing = [u for u in zone_urls if _zone_id(u) not in store][:_ZONE_FETCH_CAP]
    if missing:
        def _one(u: str):
            try:
                return _zone_id(u), (_fetch_json(u, headers).get("geometry"))
            except Exception:
                return _zone_id(u), None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for zid, geom in ex.map(_one, missing):
                if geom:
                    store[zid] = geom
        _zone_geom_cache["data"] = store
        _persist(_zone_geom_cache, "weather_zones", store, time.time())
    return store


def _merge_polygons(geoms: list) -> dict | None:
    """Combine Polygon/MultiPolygon geometries into one MultiPolygon."""
    polys = []
    for g in geoms:
        if not g:
            continue
        if g.get("type") == "Polygon":
            polys.append(g["coordinates"])
        elif g.get("type") == "MultiPolygon":
            polys.extend(g["coordinates"])
    return {"type": "MultiPolygon", "coordinates": polys} if polys else None


@router.get("/weather-alerts")
def weather_alerts():
    """Active severe/extreme weather warnings (US National Weather Service) as a
    GeoJSON polygon FeatureCollection, colored by severity — an outage RISK
    proxy for the geographic view, not actual outage data.

    Phase 1: only alerts that carry a map polygon (the acute, localized events
    most correlated with outages — severe thunderstorm, tornado, flash flood,
    etc.). Zone-based watches/warnings (winter storm, high wind, red flag…) have
    no geometry from this endpoint and are intentionally omitted for now.

    Free, no key. Cached 5 min (alerts are time-sensitive) + disk-backed; backs
    off on failure and serves stale. US-only (NWS).
    """
    global _weather_cooldown_until
    now = time.time()
    _hydrate(_weather_cache, "weather_alerts")
    if _weather_cache["data"] is not None and (now - _weather_cache["ts"]) < _WEATHER_TTL_SEC:
        return _weather_cache["data"]
    if now < _weather_cooldown_until:
        if _weather_cache["data"] is not None:
            return _weather_cache["data"]
        raise HTTPException(503, "Weather-alert data temporarily unavailable (upstream cooldown); retry shortly.")
    try:
        hdr = {"User-Agent": _NWS_USER_AGENT, "Accept": "application/geo+json"}
        raw = _fetch_json(_NWS_ALERTS_URL, hdr)
        raw_feats = raw.get("features") or []
        # Phase 2: resolve geometry for zone-based alerts (no inline polygon)
        # from their affectedZones. Gather all needed zones in one batch.
        needed_zones = set()
        for f in raw_feats:
            if not f.get("geometry"):
                for z in (f.get("properties", {}).get("affectedZones") or []):
                    needed_zones.add(z)
        zones = _resolve_zone_geometries(needed_zones, hdr) if needed_zones else {}

        features = []
        ev_counts: dict = {}
        sev_counts: dict = {}
        act_counts: dict = {}
        for f in raw_feats:
            p = f.get("properties") or {}
            geom = f.get("geometry")
            zone_based = not geom
            if zone_based:                     # merge this alert's zone polygons
                geom = _merge_polygons([zones.get(_zone_id(z)) for z in (p.get("affectedZones") or [])])
            if not geom:                       # still nothing drawable → skip
                continue
            sev = p.get("severity") or "Unknown"
            event = p.get("event") or "Alert"
            cert = p.get("certainty") or ""
            urg = p.get("urgency") or ""
            # "active" = occurring/imminent (observed, or immediate response);
            # otherwise "possible" = anticipated conditions (watches, forecasts).
            activity = "active" if (cert == "Observed" or urg == "Immediate") else "possible"
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "event": event,
                    "severity": sev,
                    "certainty": cert,
                    "urgency": urg,
                    "activity": activity,
                    "headline": p.get("headline") or "",
                    "areaDesc": p.get("areaDesc") or "",
                    "expires": p.get("expires") or "",
                    "sender": p.get("senderName") or "",
                    "zone_based": zone_based,
                },
            })
            ev_counts[event] = ev_counts.get(event, 0) + 1
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
            act_counts[activity] = act_counts.get(activity, 0) + 1
        severities = [
            {"severity": s, "color": c, "count": sev_counts.get(s, 0)}
            for s, c in _WEATHER_SEVERITY if sev_counts.get(s, 0)
        ]
        events = sorted(({"event": e, "count": n} for e, n in ev_counts.items()), key=lambda x: -x["count"])
        data = {
            "alerts": {"type": "FeatureCollection", "features": features},
            "severities": severities,
            "events": events,
            "activity": {"active": act_counts.get("active", 0), "possible": act_counts.get("possible", 0)},
            "count": len(features),
            "note": "US severe/extreme warnings (NWS), incl. zone-based alerts. Outage RISK proxy, not actual outages.",
            "attribution": "Alerts © NOAA / National Weather Service",
        }
        _persist(_weather_cache, "weather_alerts", data, now)
        _weather_cooldown_until = 0.0
        return data
    except Exception as e:
        _weather_cooldown_until = now + _WEATHER_COOLDOWN_SEC
        if _weather_cache["data"] is not None:
            return _weather_cache["data"]
        raise HTTPException(502, f"Failed to fetch weather alerts: {e}")


def _fire_pick(props: dict, *keys):
    for k in keys:
        v = props.get(k)
        if v not in (None, ""):
            return v
    return None


@router.get("/active-fires")
def active_fires():
    """Current US wildfires from NIFC WFIGS (free, no key): incident-perimeter
    polygons + incident-location points. Real "active event" data — actual
    burning incidents — distinct from the (anticipatory) weather alerts. Points
    are filtered to wildfires (WF), excluding prescribed burns. Cached 15 min.
    """
    global _fires_cooldown_until
    now = time.time()
    _hydrate(_fires_cache, "active_fires")
    if _fires_cache["data"] is not None and (now - _fires_cache["ts"]) < _FIRES_TTL_SEC:
        return _fires_cache["data"]
    if now < _fires_cooldown_until:
        if _fires_cache["data"] is not None:
            return _fires_cache["data"]
        raise HTTPException(503, "Active-fire data temporarily unavailable (upstream cooldown); retry shortly.")
    try:
        hdr = {"User-Agent": _NWS_USER_AGENT}
        inc_q = urllib.parse.urlencode({
            "where": "IncidentTypeCategory='WF'",
            "outFields": "IncidentName,IncidentSize,FireCause,FireDiscoveryDateTime",
            "f": "geojson", "returnGeometry": "true",
        })
        per_q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": "geojson", "returnGeometry": "true"})
        inc = _fetch_json(f"{_NIFC_INCIDENTS_URL}?{inc_q}", hdr)
        per = _fetch_json(f"{_NIFC_PERIMETERS_URL}?{per_q}", hdr)

        incidents = []
        for f in (inc.get("features") or []):
            if not f.get("geometry"):
                continue
            pr = f.get("properties") or {}
            incidents.append({"type": "Feature", "geometry": f["geometry"], "properties": {
                "name": _fire_pick(pr, "IncidentName") or "Wildfire",
                "acres": _fire_pick(pr, "IncidentSize"),
                "cause": _fire_pick(pr, "FireCause") or "",
                "discovered": _fire_pick(pr, "FireDiscoveryDateTime") or "",
            }})
        perimeters = []
        for f in (per.get("features") or []):
            g = f.get("geometry")
            if not g:
                continue
            pr = f.get("properties") or {}
            t = _fire_pick(pr, "attr_IncidentTypeCategory", "IncidentTypeCategory", "FeatureCategory")
            if t and str(t).upper() in ("RX", "PRESCRIBED FIRE"):   # drop prescribed burns
                continue
            perimeters.append({"type": "Feature", "geometry": g, "properties": {
                "name": _fire_pick(pr, "attr_IncidentName", "poly_IncidentName", "IncidentName") or "Fire perimeter",
                "acres": _fire_pick(pr, "poly_GISAcres", "GISAcres", "attr_IncidentSize", "IncidentSize"),
            }})
        data = {
            "perimeters": {"type": "FeatureCollection", "features": perimeters},
            "incidents": {"type": "FeatureCollection", "features": incidents},
            "counts": {"perimeters": len(perimeters), "incidents": len(incidents)},
            "note": "Current US wildfire perimeters + incident locations (NIFC WFIGS) — active incidents.",
            "attribution": "© NIFC / WFIGS (wildfire.gov)",
        }
        _persist(_fires_cache, "active_fires", data, now)
        _fires_cooldown_until = 0.0
        return data
    except Exception as e:
        _fires_cooldown_until = now + _FIRES_COOLDOWN_SEC
        if _fires_cache["data"] is not None:
            return _fires_cache["data"]
        raise HTTPException(502, f"Failed to fetch active-fire data: {e}")


@router.get("/earthquakes")
def earthquakes():
    """Recent earthquakes (USGS, M2.5+ past day) as a GeoJSON point collection.
    Free, no key. Cached 10 min."""
    global _quakes_cooldown_until
    now = time.time()
    _hydrate(_quakes_cache, "earthquakes")
    if _quakes_cache["data"] is not None and (now - _quakes_cache["ts"]) < _QUAKES_TTL_SEC:
        return _quakes_cache["data"]
    if now < _quakes_cooldown_until and _quakes_cache["data"] is not None:
        return _quakes_cache["data"]
    try:
        raw = _fetch_json(_USGS_QUAKES_URL, {"User-Agent": _NWS_USER_AGENT})
        feats = []
        for f in (raw.get("features") or []):
            g = f.get("geometry") or {}
            c = g.get("coordinates") or []
            p = f.get("properties") or {}
            if len(c) < 2 or p.get("mag") is None:
                continue
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [c[0], c[1]]},
                "properties": {
                    "mag": p.get("mag"), "place": p.get("place") or "",
                    "time": p.get("time"), "depth_km": c[2] if len(c) > 2 else None,
                    "url": p.get("url") or "", "tsunami": p.get("tsunami") or 0,
                },
            })
        data = {"earthquakes": {"type": "FeatureCollection", "features": feats},
                "count": len(feats), "note": "USGS, magnitude ≥ 2.5, past 24h.",
                "attribution": "© USGS Earthquake Hazards Program"}
        _persist(_quakes_cache, "earthquakes", data, now)
        _quakes_cooldown_until = 0.0
        return data
    except Exception as e:
        _quakes_cooldown_until = now + _GENERIC_COOLDOWN_SEC
        if _quakes_cache["data"] is not None:
            return _quakes_cache["data"]
        raise HTTPException(502, f"Failed to fetch earthquakes: {e}")


def _parse_coord(num, txt):
    """Numeric coord if present, else parse '12.3N' / '45.6W' strings."""
    try:
        if num is not None:
            return float(num)
    except (TypeError, ValueError):
        pass
    if not txt:
        return None
    try:
        v = float(str(txt)[:-1])
        return -v if str(txt)[-1] in ("S", "W") else v
    except (TypeError, ValueError):
        return None


@router.get("/cyclones")
def cyclones():
    """Active tropical cyclones (NOAA NHC) as point positions with intensity.
    Free. NHC covers the Atlantic + E/C Pacific; the forecast cone is a
    shapefile product so only storm positions are included. Cached 30 min."""
    global _cyclones_cooldown_until
    now = time.time()
    _hydrate(_cyclones_cache, "cyclones")
    if _cyclones_cache["data"] is not None and (now - _cyclones_cache["ts"]) < _CYCLONES_TTL_SEC:
        return _cyclones_cache["data"]
    if now < _cyclones_cooldown_until and _cyclones_cache["data"] is not None:
        return _cyclones_cache["data"]
    try:
        raw = _fetch_json(_NHC_STORMS_URL, {"User-Agent": _NWS_USER_AGENT})
        feats = []
        for s in (raw.get("activeStorms") or []):
            lat = _parse_coord(s.get("latitudeNumeric"), s.get("latitude"))
            lon = _parse_coord(s.get("longitudeNumeric"), s.get("longitude"))
            if lat is None or lon is None:
                continue
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "name": s.get("name") or "Tropical cyclone",
                    "classification": s.get("classification") or "",
                    "intensity_kt": s.get("intensity") or "",
                    "pressure_mb": s.get("pressure") or "",
                    "movement": f"{s.get('movementDir') or ''} {s.get('movementSpeed') or ''}".strip(),
                    "last_update": s.get("lastUpdate") or "",
                },
            })
        data = {"cyclones": {"type": "FeatureCollection", "features": feats},
                "count": len(feats),
                "note": "Active storm positions (NOAA NHC — Atlantic + E/C Pacific). Forecast cone not included.",
                "attribution": "© NOAA National Hurricane Center"}
        _persist(_cyclones_cache, "cyclones", data, now)
        _cyclones_cooldown_until = 0.0
        return data
    except Exception as e:
        _cyclones_cooldown_until = now + _GENERIC_COOLDOWN_SEC
        if _cyclones_cache["data"] is not None:
            return _cyclones_cache["data"]
        raise HTTPException(502, f"Failed to fetch cyclones: {e}")


def _country_centroids() -> dict:
    """{ISO2: [lon, lat]} from cached PeeringDB facilities (mean per country) —
    self-contained, no bundled centroid table. Used to place country-level
    outage markers."""
    _hydrate(_dc_cache, "datacenters")
    out: dict = {}
    data = _dc_cache["data"]
    if not data:
        return out
    acc: dict = {}
    for f in data.get("datacenters", {}).get("features", []):
        c = (f.get("geometry") or {}).get("coordinates")
        cc = (f.get("properties") or {}).get("country")
        if not c or not cc:
            continue
        s = acc.setdefault(cc, [0.0, 0.0, 0])
        s[0] += c[0]; s[1] += c[1]; s[2] += 1
    for cc, (sx, sy, n) in acc.items():
        if n:
            out[cc] = [sx / n, sy / n]
    return out


@router.get("/internet-outages")
def internet_outages():
    """Countries with recent internet-outage activity (IODA, last 12h) as point
    markers at each country's centroid (derived from cached facilities), sized
    by severity. Free, no key. Cached 20 min."""
    global _outages_cooldown_until
    now = time.time()
    _hydrate(_outages_cache, "internet_outages")
    if _outages_cache["data"] is not None and (now - _outages_cache["ts"]) < _OUTAGES_TTL_SEC:
        return _outages_cache["data"]
    if now < _outages_cooldown_until and _outages_cache["data"] is not None:
        return _outages_cache["data"]
    try:
        frm = int(now) - 24 * 3600
        url = f"{_IODA_SUMMARY_URL}?from={frm}&until={int(now)}&entityType=country&limit=150"
        rows = _fetch_json(url, {"User-Agent": _NWS_USER_AGENT}).get("data") or []
        centroids = _country_centroids()
        feats = []
        for r in rows:
            e = r.get("entity") or {}
            code = e.get("code")
            cen = centroids.get(code)
            if not cen:
                continue
            score = (r.get("scores") or {}).get("overall") or 0
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": cen},
                "properties": {
                    "country": e.get("name") or code, "code": code,
                    "score": round(score), "events": r.get("event_cnt") or 0,
                },
            })
        data = {"outages": {"type": "FeatureCollection", "features": feats},
                "count": len(feats),
                "note": "Countries with internet-outage activity, last 24h (IODA). Marker at country centroid (approx); countries without a cached facility may not plot.",
                "attribution": "© IODA, Georgia Tech"}
        _persist(_outages_cache, "internet_outages", data, now)
        _outages_cooldown_until = 0.0
        return data
    except Exception as e:
        _outages_cooldown_until = now + _GENERIC_COOLDOWN_SEC
        if _outages_cache["data"] is not None:
            return _outages_cache["data"]
        raise HTTPException(502, f"Failed to fetch internet outages: {e}")


@router.get("/datacenters")
def datacenters():
    """Colocation / data-center facilities from PeeringDB, as a trimmed
    GeoJSON point FeatureCollection.

    Each feature carries the operator (org_name → "operator", e.g. Equinix),
    so the frontend can color/filter by vendor. Facilities without
    coordinates are dropped. Also returns the operator list (name + count)
    for legends/filters. Cached 30 days (disk-backed); serves stale on a
    refresh failure.

    PeeringDB data is subject to their Acceptable Use Policy — fine to
    display; review before redistribution.
    """
    global _dc_cooldown_until
    now = time.time()
    _hydrate(_dc_cache, "datacenters")
    if _dc_cache["data"] is not None and (now - _dc_cache["ts"]) < _CACHE_TTL_SEC:
        return _dc_cache["data"]
    # Backing off after a recent failure — don't hammer the rate-limited
    # endpoint. Serve stale if we have it, else tell the client to wait.
    if now < _dc_cooldown_until:
        if _dc_cache["data"] is not None:
            return _dc_cache["data"]
        wait = int(_dc_cooldown_until - now)
        raise HTTPException(
            503,
            f"PeeringDB is rate-limiting requests. Set PEERINGDB_API_KEY to avoid this, "
            f"or retry in ~{wait}s.",
        )
    try:
        # Anonymous reads are rate-limited; an optional PeeringDB API key
        # (PEERINGDB_API_KEY env) raises the limit. The 30-day disk cache
        # keeps real usage well under the anonymous threshold regardless.
        raw = _fetch_json(_PEERINGDB_FAC_URL, _peeringdb_headers()).get("data", [])
        features = []
        op_counts: dict = {}
        for f in raw:
            lat, lng = f.get("latitude"), f.get("longitude")
            if lat is None or lng is None:
                continue
            operator = f.get("org_name") or "Unknown"
            op_counts[operator] = op_counts.get(operator, 0) + 1
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": {
                    "id": f.get("id"),
                    "name": f.get("name") or "",
                    "operator": operator,
                    "city": f.get("city") or "",
                    "country": f.get("country") or "",
                    "net_count": f.get("net_count") or 0,
                    "ix_count": f.get("ix_count") or 0,
                },
            })
        operators = sorted(
            ({"name": k, "count": v} for k, v in op_counts.items()),
            key=lambda o: o["count"], reverse=True,
        )
        data = {
            "datacenters": {"type": "FeatureCollection", "features": features},
            "operators": operators,
            "attribution": "© PeeringDB — peeringdb.com",
        }
        _persist(_dc_cache, "datacenters", data, now)
        _dc_cooldown_until = 0.0  # success clears any backoff
        return data
    except Exception as e:
        # Set a cooldown so we stop re-hitting a throttled endpoint. A 429
        # gets a longer backoff than other transient errors.
        is_rate_limited = isinstance(e, urllib.error.HTTPError) and e.code == 429
        _dc_cooldown_until = now + (_DC_COOLDOWN_RATE_LIMIT_SEC if is_rate_limited else _DC_COOLDOWN_OTHER_SEC)
        if _dc_cache["data"] is not None:
            return _dc_cache["data"]
        if is_rate_limited:
            raise HTTPException(
                503,
                "PeeringDB is rate-limiting requests. Set PEERINGDB_API_KEY in the backend "
                f"environment to avoid this (recommended), or retry in ~{_DC_COOLDOWN_RATE_LIMIT_SEC}s.",
            )
        raise HTTPException(502, f"Failed to fetch data-center data: {e}")


@router.get("/ixps")
def ixps():
    """Internet Exchange Points from PeeringDB, as a GeoJSON point
    FeatureCollection. IXPs carry no coordinates of their own, so each IXP is
    placed at the centroid of its associated facilities (ixfac → fac coords).
    IXPs with no located facility are dropped.

    Properties per feature: name, city, country, net_count (peers), fac_count.
    Cached 30 days (disk-backed); shares the PeeringDB cooldown on 429.
    """
    global _dc_cooldown_until
    now = time.time()
    _hydrate(_ix_cache, "ixps")
    if _ix_cache["data"] is not None and (now - _ix_cache["ts"]) < _CACHE_TTL_SEC:
        return _ix_cache["data"]
    # Never fetch while in cooldown — serve stale or 503 (don't extend a 429).
    if now < _dc_cooldown_until:
        if _ix_cache["data"] is not None:
            return _ix_cache["data"]
        raise HTTPException(503, f"IXP data temporarily unavailable (upstream cooldown); retry in ~{int(_dc_cooldown_until - now)}s.")
    try:
        h = _peeringdb_headers()
        facs = _fetch_json(_PEERINGDB_FAC_URL, h).get("data", [])
        fac_coord = {
            f["id"]: (f["longitude"], f["latitude"])
            for f in facs if f.get("latitude") is not None and f.get("longitude") is not None
        }
        # ix_id → list of facility coords
        ix_facs: dict = {}
        for link in _fetch_json(_PEERINGDB_IXFAC_URL, h).get("data", []):
            c = fac_coord.get(link.get("fac_id"))
            if c:
                ix_facs.setdefault(link.get("ix_id"), []).append(c)
        features = []
        for ix in _fetch_json(_PEERINGDB_IX_URL, h).get("data", []):
            coords = ix_facs.get(ix.get("id"))
            if not coords:
                continue
            lon = sum(c[0] for c in coords) / len(coords)   # facility centroid
            lat = sum(c[1] for c in coords) / len(coords)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                "properties": {
                    "id": ix.get("id"), "name": ix.get("name") or "",
                    "city": ix.get("city") or "", "country": ix.get("country") or "",
                    "net_count": ix.get("net_count") or 0, "fac_count": ix.get("fac_count") or 0,
                },
            })
        data = {
            "ixps": {"type": "FeatureCollection", "features": features},
            "attribution": "© PeeringDB — peeringdb.com",
        }
        _persist(_ix_cache, "ixps", data, now)
        _dc_cooldown_until = 0.0
        return data
    except Exception as e:
        is_rate_limited = isinstance(e, urllib.error.HTTPError) and e.code == 429
        _dc_cooldown_until = now + (_DC_COOLDOWN_RATE_LIMIT_SEC if is_rate_limited else _DC_COOLDOWN_OTHER_SEC)
        if _ix_cache["data"] is not None:
            return _ix_cache["data"]
        if is_rate_limited:
            raise HTTPException(503, "PeeringDB is rate-limiting requests. Set PEERINGDB_API_KEY, or retry shortly.")
        raise HTTPException(502, f"Failed to fetch IXP data: {e}")


# --- Geostationary (GEO) coverage ---
_GEO_TTL_SEC = 24 * 3600   # GEO birds are station-kept; longitude is very stable
_geo_cache: dict = {"data": None, "ts": 0.0}
# Nested footprint rings (min-elevation → strength). Smaller, more-opaque
# rings sit near nadir (overhead = strongest); the wide faint ring is the
# low-elevation edge of coverage.
_GEO_RING_LEVELS = [(5.0, 0.15), (20.0, 0.18), (35.0, 0.22), (50.0, 0.26), (65.0, 0.30)]
# Major GEO operators matched by satellite name (heuristic); the rest are
# lumped as "Other GEO". Viasat called out so it gets its own caps + color.
_GEO_OPERATORS = [
    ("viasat", "Viasat", "#f59e0b", ("VIASAT",)),
    ("intelsat", "Intelsat", "#60a5fa", ("INTELSAT", "GALAXY")),
    ("ses", "SES", "#34d399", ("SES", "ASTRA", "NSS")),
    ("eutelsat", "Eutelsat", "#a78bfa", ("EUTELSAT", "HOTBIRD")),
    ("inmarsat", "Inmarsat", "#f472b6", ("INMARSAT",)),
]
_GEO_OTHER = ("other", "Other GEO", "#94a3b8")


def _geo_operator(name: str) -> tuple:
    u = (name or "").upper()
    for key, label, color, prefixes in _GEO_OPERATORS:
        if any(p in u for p in prefixes):
            return key, label, color
    return _GEO_OTHER


def _julian_date(epoch_iso: str) -> float:
    from datetime import datetime
    dt = datetime.fromisoformat(epoch_iso.replace("Z", "+00:00"))
    y, mo, d = dt.year, dt.month, dt.day
    frac = dt.hour + dt.minute / 60 + dt.second / 3600 + dt.microsecond / 3.6e9
    if mo <= 2:
        y -= 1; mo += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (mo + 1)) + d + b - 1524.5 + frac / 24


def _gmst_deg(jd: float) -> float:
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - (t ** 3) / 38710000.0
    return g % 360.0


def _geo_sublon(o: dict) -> float | None:
    """Sub-satellite (East) longitude of a near-geostationary satellite:
    RA_OF_ASC_NODE + ARG_OF_PERICENTER + MEAN_ANOMALY − GMST(epoch). Good to
    ~a degree for a parked GEO bird (e≈0, i≈0)."""
    try:
        raan = float(o["RA_OF_ASC_NODE"]); argp = float(o["ARG_OF_PERICENTER"])
        ma = float(o["MEAN_ANOMALY"]); epoch = o["EPOCH"]
    except (KeyError, ValueError, TypeError):
        return None
    lon = (raan + argp + ma - _gmst_deg(_julian_date(epoch))) % 360.0
    return ((lon + 540) % 360) - 180


def _geo_footprint_radius_deg(elev_deg: float, h: float = 35786.0) -> float:
    eps = math.radians(elev_deg)
    ratio = (_RE_KM / (_RE_KM + h)) * math.cos(eps)
    return math.degrees(math.acos(min(1.0, ratio)) - eps)


def _cap_ring(center_lon: float, radius_deg: float, n: int = 64) -> list:
    """Ring of a small circle of angular radius `radius_deg` centered on the
    equator at center_lon. Longitudes normalized to [-180,180]."""
    r = math.radians(radius_deg)
    ring = []
    for i in range(n + 1):
        th = 2 * math.pi * i / n
        lat = math.degrees(math.asin(math.sin(r) * math.cos(th)))
        dlon = math.degrees(math.atan2(math.sin(th) * math.sin(r), math.cos(r)))
        lon = ((center_lon + dlon + 540) % 360) - 180
        ring.append([round(lon, 3), round(lat, 3)])
    return ring


def _sat_altitude_km(o: dict) -> float | None:
    """Mean altitude (km) from MEAN_MOTION, for shell-windowing a fleet."""
    try:
        n = float(o["MEAN_MOTION"])
    except (KeyError, ValueError, TypeError):
        return None
    if n <= 0:
        return None
    n_rad_s = n * 2 * math.pi / 86400.0
    return (_MU_EARTH / (n_rad_s ** 2)) ** (1.0 / 3.0) - _RE_KM


_CLOUD_REGIONS_FILE = Path(__file__).parent / "cloud_regions.json"
_CLOUD_PROVIDERS = {
    "aws": {"label": "AWS", "color": "#ff9900"},
    "azure": {"label": "Azure", "color": "#0078d4"},
    "gcp": {"label": "Google Cloud", "color": "#ea4335"},
}


@router.get("/cloud-regions")
def cloud_regions():
    """Cloud provider regions (AWS/Azure/GCP) from a bundled curated dataset,
    as a GeoJSON point FeatureCollection tagged by provider for coloring.

    Coordinates are approximate (region's primary metro). This is a local
    static file — no external fetch — so the frontend can color by provider
    and the set is trivially extended by editing cloud_regions.json.
    """
    try:
        raw = json.loads(_CLOUD_REGIONS_FILE.read_text()).get("regions", [])
    except Exception as e:
        raise HTTPException(500, f"Cloud-regions dataset unreadable: {e}")
    features = []
    counts: dict = {}
    for r in raw:
        prov = r.get("provider")
        if prov not in _CLOUD_PROVIDERS or r.get("lat") is None or r.get("lon") is None:
            continue
        counts[prov] = counts.get(prov, 0) + 1
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "provider": prov, "code": r.get("code", ""), "name": r.get("name", ""),
                "city": r.get("city", ""), "country": r.get("country", ""),
            },
        })
    providers = [
        {"key": k, "label": v["label"], "color": v["color"], "count": counts.get(k, 0)}
        for k, v in _CLOUD_PROVIDERS.items() if counts.get(k, 0)
    ]
    return {
        "cloud_regions": {"type": "FeatureCollection", "features": features},
        "providers": providers,
        "note": "Approximate region locations (primary metro), curated dataset.",
    }


@router.get("/geo-coverage")
def geo_coverage():
    """Approximate geostationary (GEO) coverage as nested footprint caps per
    operator. Each GEO satellite's sub-longitude is computed from its orbital
    elements; a stack of concentric rings (opaque near nadir, faint at the
    low-elevation edge) gives a "stronger overhead, fading outward" look.

    Geometric reach only — not actual service (beams/capacity/regulation
    aren't in the data). Cached 24h (GEO longitudes are station-kept/stable)
    + disk-backed; shares the CelesTrak cooldown.
    """
    global _sat_cooldown_until
    now = time.time()
    _hydrate(_geo_cache, "geo_coverage")
    if _geo_cache["data"] is not None and (now - _geo_cache["ts"]) < _GEO_TTL_SEC:
        return _geo_cache["data"]
    # Backing off after a recent CelesTrak failure (e.g. a 403 block). Do NOT
    # fetch while in cooldown — repeatedly hitting a rate-limiter only extends
    # the block. Serve stale if we have it, else tell the client to wait.
    if now < _sat_cooldown_until:
        if _geo_cache["data"] is not None:
            return _geo_cache["data"]
        raise HTTPException(503, f"GEO data temporarily unavailable (upstream cooldown); retry in ~{int(_sat_cooldown_until - now)}s.")
    try:
        gp = _fetch_gp_cached("geo")
        if not isinstance(gp, list):
            raise RuntimeError("unexpected GEO response")
        # Dedupe co-located birds: one set of caps per (operator, ~2° slot).
        slots: dict = {}
        op_counts: dict = {}
        for o in gp:
            try:
                mm = float(o["MEAN_MOTION"]); inc = float(o["INCLINATION"])
            except (KeyError, ValueError, TypeError):
                continue
            if not (0.9 <= mm <= 1.1) or inc > 15:   # keep true geostationary
                continue
            lon = _geo_sublon(o)
            if lon is None:
                continue
            key, label, color = _geo_operator(o.get("OBJECT_NAME", ""))
            slot = (key, round(lon / 2.0) * 2.0)
            if slot not in slots:
                slots[slot] = {"lon": lon, "label": label, "color": color}
                op_counts[key] = op_counts.get(key, 0) + 1
        features = []
        for (key, _slot), s in slots.items():
            for lvl, (elev, strength) in enumerate(_GEO_RING_LEVELS):
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [_cap_ring(s["lon"], _geo_footprint_radius_deg(elev))]},
                    "properties": {"operator": key, "label": s["label"], "color": s["color"], "level": lvl, "strength": strength},
                })
        # Operators present, in the configured order, then Other.
        order = [o[0] for o in _GEO_OPERATORS] + ["other"]
        label_color = {o[0]: (o[1], o[2]) for o in _GEO_OPERATORS}
        label_color["other"] = (_GEO_OTHER[1], _GEO_OTHER[2])
        operators = [
            {"key": k, "label": label_color[k][0], "color": label_color[k][1], "count": op_counts[k]}
            for k in order if op_counts.get(k)
        ]
        data = {
            "geo_coverage": {"type": "FeatureCollection", "features": features},
            "operators": operators,
            "ring_levels": len(_GEO_RING_LEVELS),
            "note": "Approximate geometric footprints (stronger overhead, fading to the low-elevation edge); not a service map.",
            "attribution": "Orbital data © CelesTrak",
        }
        _persist(_geo_cache, "geo_coverage", data, now)
        _sat_cooldown_until = 0.0
        return data
    except Exception as e:
        _sat_cooldown_until = now + _SAT_COOLDOWN_SEC
        if _geo_cache["data"] is not None:
            return _geo_cache["data"]
        raise HTTPException(502, f"Failed to fetch GEO coverage data: {e}")


@router.get("/coverage-strength")
def coverage_strength():
    """Expected number of satellites simultaneously in view vs. LATITUDE, per
    constellation — the metric that answers "who covers here better" anywhere
    on the globe.

    For each constellation we bucket its on-orbit satellites into orbital
    shells (inclination + altitude), compute each shell's per-satellite
    visibility probability across a latitude grid (see _shell_visibility), and
    sum over the fleet. The result is an expected in-view count by latitude;
    the frontend interpolates it at any clicked point and ranks constellations.

    Longitude-averaged (these LEO/MEO fleets aren't longitude-specific, unlike
    GEO), based on what's actually on orbit now, geometric above a min
    elevation — an EXPECTATION, not a live instantaneous count.

    Derives from the SHARED raw-GP cache (no extra CelesTrak hits if coverage
    or tracks already loaded). Cached 12h + disk-backed; shares the cooldown.
    """
    global _sat_cooldown_until
    now = time.time()
    _hydrate(_strength_cache, "coverage_strength")
    if _strength_cache["data"] is not None and (now - _strength_cache["ts"]) < _STRENGTH_TTL_SEC:
        return _strength_cache["data"]
    if now < _sat_cooldown_until:
        if _strength_cache["data"] is not None:
            return _strength_cache["data"]
        raise HTTPException(503, f"Coverage-strength data temporarily unavailable (upstream cooldown); retry in ~{int(_sat_cooldown_until - now)}s.")
    try:
        n = int(round(180.0 / _STRENGTH_LAT_STEP))
        lat_grid_deg = [-90.0 + i * _STRENGTH_LAT_STEP for i in range(n + 1)]
        lat_grid_rad = [math.radians(d) for d in lat_grid_deg]
        constellations = []
        for c in _SAT_CONSTELLATIONS:
            gp = _fetch_gp_cached(c["group"])
            amin, amax = c.get("alt_min", 100.0), c.get("alt_max", 5000.0)
            # Bucket the fleet into shells: inclination (~1°) × altitude (~25 km).
            shells: dict = {}
            for o in gp:
                alt = _sat_altitude_km(o)
                if alt is None or not (amin < alt <= amax):
                    continue
                try:
                    inc = float(o["INCLINATION"])
                except (KeyError, ValueError, TypeError):
                    continue
                key = (round(inc), round(alt / 25.0) * 25)
                shells[key] = shells.get(key, 0) + 1
            if not shells:
                continue
            total = sum(shells.values())
            nview = [0.0] * len(lat_grid_rad)
            for (inc, alt), cnt in shells.items():
                lam = _footprint_half_angle(float(alt), _SAT_MIN_ELEV_DEG)
                vis = _shell_visibility(float(inc), lam, lat_grid_rad, _STRENGTH_U_SAMPLES)
                for j, v in enumerate(vis):
                    nview[j] += cnt * v
            peak = max(nview)
            constellations.append({
                "key": c["key"], "vendor": c["vendor"], "color": c["color"], "regime": c["regime"],
                "satellite_count": total,
                "peak_in_view": round(peak, 1),
                "by_latitude": [{"lat": lat_grid_deg[j], "in_view": round(nview[j], 2)} for j in range(len(nview))],
            })
        if not constellations:
            raise RuntimeError("no constellation data returned")
        data = {
            "constellations": constellations,
            "min_elevation_deg": _SAT_MIN_ELEV_DEG,
            "latitude_step": _STRENGTH_LAT_STEP,
            "note": ("Expected satellites in view by latitude (longitude-averaged, on-orbit now, "
                     f"geometric ≥{int(_SAT_MIN_ELEV_DEG)}° elevation). An estimate of relative coverage, not a live count."),
            "attribution": "Orbital data © CelesTrak",
        }
        _persist(_strength_cache, "coverage_strength", data, now)
        _sat_cooldown_until = 0.0
        return data
    except Exception as e:
        _sat_cooldown_until = now + _SAT_COOLDOWN_SEC
        if _strength_cache["data"] is not None:
            return _strength_cache["data"]
        raise HTTPException(502, f"Failed to compute coverage strength: {e}")

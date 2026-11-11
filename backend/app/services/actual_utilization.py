"""Actual (measured) link utilization from per-interface metrics.

Metrics arrive per interface — (instance, interface, peak_30d_bps,
peak_90d_bps, link_delay_us) — today from a CSV import, later from a live
source. A link has two endpoint interfaces, which usually report slightly
different numbers; the link's actual utilization and delay are the HIGHER of
the two, per value. On import the link delay is also written to each local
link's latency_ms (apply_link_delays).

Matching:
  * instance → node: the node's name, prometheus_id or mgmt_ip
    (case-insensitive), also trying the instance with a ":port" suffix
    stripped and as a short hostname (text before the first ".").
  * interface → link endpoint: case/whitespace-insensitive, then by a
    canonical short form so "GigabitEthernet0/0/1" matches "Gi0/0/1".

Child topologies see metrics imported on any ancestor; rows imported on the
child win for the same (instance, interface).
"""
import csv
import io
import ipaddress
import math
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.interface_metric import InterfaceMetric
from app.services.topology_chain import get_topology_chain, get_merged_topology_data
from app.services.latency import latency_infos

IMPORT_HEADERS = ("instance", "interface", "peak_30d_bps", "peak_90d_bps")
# Optional trailing column; older exports without it still import.
OPTIONAL_IMPORT_HEADERS = ("link_delay_us",)

EXPORT_HEADERS = (
    "Circuit ID", "Links Speed Gbps", "Aloc", "Zloc",
    "30 Day Max BW (Mbps)", "90 Day Max BW (Mbps)", "Latency(ms)", "Latency Source",
)

# Long interface-type prefix → canonical short form (longest first so
# "tengigabitethernet" isn't eaten by "gigabitethernet").
_IFACE_ABBREV = [
    ("fourhundredgigabitethernet", "fh"), ("fourhundredgige", "fh"),
    ("hundredgigabitethernet", "hu"), ("hundredgige", "hu"),
    ("fiftygigabitethernet", "fi"), ("fiftygige", "fi"),
    ("fortygigabitethernet", "fo"), ("fortygige", "fo"),
    ("twentyfivegigabitethernet", "twe"), ("twentyfivegige", "twe"),
    ("tengigabitethernet", "te"), ("tengige", "te"),
    ("gigabitethernet", "gi"), ("fastethernet", "fa"),
    ("bundle-ether", "be"), ("port-channel", "po"),
    ("ethernet", "et"), ("loopback", "lo"), ("management", "ma"),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def canonical_iface(name: str) -> str:
    n = _norm(name)
    for long, short in _IFACE_ABBREV:
        if n.startswith(long):
            return short + n[len(long):]
    return n


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def instance_candidates(instance: str) -> list[str]:
    """Lookup keys to try for a metrics `instance`, most specific first."""
    raw = _norm(instance)
    out = [raw]
    host = raw
    # Strip a ":port" suffix (but leave bare IPv6 addresses alone).
    if raw.startswith("[") and "]" in raw:
        host = raw[1:raw.index("]")]
    elif raw.count(":") == 1:
        host = raw.split(":", 1)[0]
    if host not in out:
        out.append(host)
    if not _is_ip(host) and "." in host:
        short = host.split(".", 1)[0]
        if short and short not in out:
            out.append(short)
    return out


# ── CSV import ──────────────────────────────────────────────────────────────

def _parse_number(v: str) -> Optional[float]:
    v = (v or "").strip().replace(",", "")
    if v == "" or v.lower() in ("nan", "null", "none", "-"):
        return None
    f = float(v)
    # inf/negative would poison every later read (JSON can't encode inf).
    if not math.isfinite(f) or f < 0:
        raise ValueError(v)
    return f


def parse_metrics_csv(text: str) -> tuple[list[dict], list[str]]:
    """Parse the import CSV. Returns (rows, errors). Header names are matched
    case-insensitively; extra columns are ignored."""
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], ["CSV is empty"]
    cols = {h.strip().lower(): i for i, h in enumerate(header)}
    missing = [h for h in IMPORT_HEADERS if h not in cols]
    if missing:
        return [], [f"Missing required column(s): {', '.join(missing)}"]

    rows: list[dict] = []
    errors: list[str] = []
    for lineno, rec in enumerate(reader, start=2):
        if not any(c.strip() for c in rec):
            continue

        def cell(h: str) -> str:
            i = cols.get(h)
            return rec[i].strip() if i is not None and i < len(rec) else ""

        instance, interface = cell("instance"), cell("interface")
        if not instance or not interface:
            errors.append(f"Line {lineno}: instance and interface are required")
            continue
        try:
            p30 = _parse_number(cell("peak_30d_bps"))
            p90 = _parse_number(cell("peak_90d_bps"))
        except ValueError:
            errors.append(f"Line {lineno}: peak values must be finite, non-negative numbers (bps)")
            continue
        try:
            delay = _parse_number(cell("link_delay_us"))
        except ValueError:
            errors.append(f"Line {lineno}: link_delay_us must be a finite, non-negative number (µs)")
            continue
        rows.append({"instance": instance, "interface": interface,
                     "peak_30d_bps": p30, "peak_90d_bps": p90, "link_delay_us": delay})
    return rows, errors


def import_metrics(db: Session, topology_id: int, rows: list[dict],
                   replace: bool = True, source: str = "csv") -> int:
    """Store metric rows on this topology. replace=True drops the topology's
    existing rows first (a fresh export is a complete snapshot); otherwise rows
    are upserted by (instance, interface). Duplicate keys in `rows`: last wins."""
    if replace:
        db.query(InterfaceMetric).filter(InterfaceMetric.topology_id == topology_id).delete()
        existing = {}
    else:
        existing = {(m.instance, m.interface): m for m in
                    db.query(InterfaceMetric).filter(InterfaceMetric.topology_id == topology_id).all()}
    for r in rows:
        key = (r["instance"], r["interface"])
        m = existing.get(key)
        if m is None:
            m = InterfaceMetric(topology_id=topology_id, instance=r["instance"], interface=r["interface"])
            db.add(m)
            existing[key] = m
        m.peak_30d_bps = r["peak_30d_bps"]
        m.peak_90d_bps = r["peak_90d_bps"]
        m.link_delay_us = r.get("link_delay_us")
        m.source = source
    db.commit()
    return len(existing)


def clear_metrics(db: Session, topology_id: int) -> int:
    n = db.query(InterfaceMetric).filter(InterfaceMetric.topology_id == topology_id).delete()
    db.commit()
    return n


# ── Link-level derivation ───────────────────────────────────────────────────

def _chain_metrics(db: Session, topology_id: int) -> list[InterfaceMetric]:
    """Metrics visible to this topology: ancestors' rows, overridden by
    nearer topologies' rows for the same (instance, interface)."""
    chain = get_topology_chain(db, topology_id)
    pos = {tid: i for i, tid in enumerate(chain)}
    rows = db.query(InterfaceMetric).filter(InterfaceMetric.topology_id.in_(chain)).all()
    rows.sort(key=lambda m: pos.get(m.topology_id, 0))
    by_key: dict[tuple[str, str], InterfaceMetric] = {}
    for m in rows:
        # Canonical iface so a child's "Gi0/0/1" overrides a parent's
        # "GigabitEthernet0/0/1" for the same port.
        by_key[(_norm(m.instance), canonical_iface(m.interface))] = m
    return list(by_key.values())


def _max_opt(a: Optional[float], b: Optional[float]) -> Optional[float]:
    vals = [v for v in (a, b) if v is not None]
    return max(vals) if vals else None


def compute_actual_link_utilization(db: Session, topology_id: int) -> dict:
    nodes, links, _flows, _ = get_merged_topology_data(db, topology_id)
    metrics = _chain_metrics(db, topology_id)

    # node lookup key → node id (first claim wins)
    node_keys: dict[str, int] = {}
    for n in nodes:
        for k in (n.name, getattr(n, "prometheus_id", ""), getattr(n, "mgmt_ip", "")):
            k = _norm(k)
            if k and k not in node_keys:
                node_keys[k] = n.id

    # (node_id, normalized iface) and (node_id, canonical iface) → metric
    exact: dict[tuple[int, str], InterfaceMetric] = {}
    canon: dict[tuple[int, str], InterfaceMetric] = {}
    unmatched_instances: set[str] = set()
    for m in metrics:
        nid = next((node_keys[c] for c in instance_candidates(m.instance) if c in node_keys), None)
        if nid is None:
            unmatched_instances.add(m.instance)
            continue
        exact.setdefault((nid, _norm(m.interface)), m)
        canon.setdefault((nid, canonical_iface(m.interface)), m)

    def lookup(node_id: int, iface: str) -> Optional[InterfaceMetric]:
        if not iface:
            return None
        return exact.get((node_id, _norm(iface))) or canon.get((node_id, canonical_iface(iface)))

    used_metric_ids: set[int] = set()
    out = []
    for l in links:
        a = lookup(l.source_node_id, l.source_interface)
        z = lookup(l.target_node_id, l.target_interface)
        for m in (a, z):
            if m is not None:
                used_metric_ids.add(m.id)
        p30 = _max_opt(a.peak_30d_bps if a else None, z.peak_30d_bps if z else None)
        p90 = _max_opt(a.peak_90d_bps if a else None, z.peak_90d_bps if z else None)
        delay = _max_opt(a.link_delay_us if a else None, z.link_delay_us if z else None)
        out.append({
            "link_id": l.id,
            "source_node_id": l.source_node_id,
            "target_node_id": l.target_node_id,
            "bandwidth_gbps": l.bandwidth_gbps or 0,
            "peak_30d_bps": p30,
            "peak_90d_bps": p90,
            "link_delay_us": delay,
            "source_matched": a is not None,
            "target_matched": z is not None,
            "has_data": a is not None or z is not None,
        })

    # Metrics on a matched node whose interface didn't line up with any link.
    unmatched_rows = [
        {"instance": m.instance, "interface": m.interface}
        for m in metrics
        if m.id not in used_metric_ids and m.instance not in unmatched_instances
    ]
    latest = max((m.updated_at for m in metrics if m.updated_at), default=None)
    return {
        "links": out,
        "metric_rows": len(metrics),
        "links_with_data": sum(1 for r in out if r["has_data"]),
        "total_links": len(out),
        "unmatched_instances": sorted(unmatched_instances),
        "unmatched_interfaces": unmatched_rows[:200],
        "unmatched_interface_count": len(unmatched_rows),
        "updated_at": latest.isoformat() if latest else None,
    }


def apply_link_delays(db: Session, topology_id: int, actual_links: list[dict]) -> dict:
    """Copy each link's measured delay (µs, max of its two interfaces) into
    its latency_ms. A missing or 0 measurement never overwrites: the link
    keeps its existing latency. Only links owned by this topology are written
    — inherited links are read-only here and are counted as skipped."""
    from app.models.link import Link

    delays = {r["link_id"]: r["link_delay_us"] for r in actual_links if r.get("link_delay_us")}
    if not delays:
        return {"updated": 0, "skipped_inherited": 0}
    owned = {l.id: l for l in db.query(Link).filter(
        Link.topology_id == topology_id, Link.id.in_(list(delays))).all()}
    updated = 0
    for link_id, us in delays.items():
        link = owned.get(link_id)
        if link is None:
            continue
        ms = us / 1000.0
        if link.latency_ms != ms or link.latency_source != "measured":
            link.latency_ms = ms
            link.latency_source = "measured"
            updated += 1
    db.commit()
    return {"updated": updated, "skipped_inherited": len(delays) - len(owned)}


# ── CSV export ──────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], digits: int = 3) -> str:
    if v is None:
        return ""
    s = f"{round(v, digits):.{digits}f}".rstrip("0").rstrip(".")
    return s or "0"


def _sum_opt(vals: list[Optional[float]]) -> Optional[float]:
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def export_links_csv(db: Session, topology_id: int) -> str:
    """One row per node PAIR. Parallel links between the same two nodes
    (either direction) are combined: each link contributes the higher of its
    two interfaces' peaks, and those per-link peaks are summed, as are the link
    speeds. Circuit IDs are joined with "; "; latency is the highest of the
    links (worst case). Aloc/Zloc follow the first link's direction."""
    nodes, links, _flows, _ = get_merged_topology_data(db, topology_id)
    node_by_id = {n.id: n for n in nodes}
    actual = {r["link_id"]: r for r in compute_actual_link_utilization(db, topology_id)["links"]}
    lat = latency_infos(db, nodes, links)

    def name(node_id: int) -> str:
        n = node_by_id.get(node_id)
        return n.name if n is not None else ""

    groups: dict[frozenset, list] = {}
    for l in links:
        groups.setdefault(frozenset((l.source_node_id, l.target_node_id)), []).append(l)

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EXPORT_HEADERS)
    for group in groups.values():
        first = group[0]
        p30 = _sum_opt([actual.get(l.id, {}).get("peak_30d_bps") for l in group])
        p90 = _sum_opt([actual.get(l.id, {}).get("peak_90d_bps") for l in group])
        # Effective latency (measured, else estimated); worst case of the group.
        infos = [lat[l.id] for l in group if lat[l.id].ms is not None]
        worst = max(infos, key=lambda i: i.ms, default=None)
        w.writerow([
            "; ".join(l.circuit_id for l in group if l.circuit_id),
            _fmt(sum(l.bandwidth_gbps or 0 for l in group)),
            name(first.source_node_id),
            name(first.target_node_id),
            _fmt(p30 / 1e6 if p30 is not None else None, 2),
            _fmt(p90 / 1e6 if p90 is not None else None, 2),
            _fmt(worst.ms if worst else None),
            worst.source if worst else "unknown",
        ])
    return buf.getvalue()

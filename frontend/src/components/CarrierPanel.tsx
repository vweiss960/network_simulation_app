import { useEffect, useMemo, useState } from 'react';
import { api } from '../utils/api';

// What opens the panel: either a specific facility click, or an arbitrary
// point (e.g. clicking the outback) → find the nearest hubs.
export type CarrierLookup =
  | { kind: 'facility'; facId: number; facName?: string; operator?: string; city?: string; country?: string }
  | { kind: 'point'; lat: number; lon: number; label?: string }
  | { kind: 'ix'; ixId: number; ixName?: string; city?: string; country?: string };

type Carrier = { net_id: number; asn: number; name: string; type: string; website: string; site_count?: number; is_satellite?: boolean };
type Facility = { id: number; name: string; operator?: string; city?: string; country?: string; net_count?: number; distance_km?: number };
type CarrierDetail = {
  net_id: number; asn: number; name: string; aka: string; type: string; website: string;
  policy_general: string; policy_url: string; irr_as_set: string; looking_glass: string;
  notes: string; peeringdb_url: string;
  contacts: { role: string; name: string; email: string; phone: string; url: string }[];
};

const TYPE_COLOR: Record<string, string> = {
  'NSP': '#34d399',
  'Cable/DSL/ISP': '#60a5fa',
  'Network Services': '#a78bfa',
  'Content': '#f472b6',
  'Enterprise': '#fbbf24',
};
const typeColor = (t: string) => TYPE_COLOR[t] || '#94a3b8';

export default function CarrierPanel({ lookup, onClose }: { lookup: CarrierLookup; onClose: () => void }) {
  // For a point lookup we first show nearest hubs; selecting one (or a facility
  // lookup) sets `fac`, which drives the carrier list.
  const initialFac: Facility | null = lookup.kind === 'facility'
    ? { id: lookup.facId, name: lookup.facName || `Facility ${lookup.facId}`, operator: lookup.operator, city: lookup.city, country: lookup.country }
    : null;
  const [fac, setFac] = useState<Facility | null>(initialFac);

  // Nearest-hubs state (point mode only).
  const [hubs, setHubs] = useState<Facility[]>([]);
  const [hubsLoading, setHubsLoading] = useState(false);
  const [hubsError, setHubsError] = useState('');

  // Carrier list state.
  const [mode, setMode] = useState<'facility' | 'country'>('facility');
  const [carriers, setCarriers] = useState<Carrier[]>([]);
  const [types, setTypes] = useState<{ type: string; count: number }[]>([]);
  const [coverage, setCoverage] = useState<{ covered: number; total: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  const [selected, setSelected] = useState<CarrierDetail | null>(null);
  const [selecting, setSelecting] = useState<number | null>(null);
  const [detailError, setDetailError] = useState('');

  const country = fac?.country || '';
  const isPoint = lookup.kind === 'point';
  const isIx = lookup.kind === 'ix';

  // Point mode: load nearest hubs.
  useEffect(() => {
    if (lookup.kind !== 'point') return;
    let cancelled = false;
    setHubsLoading(true); setHubsError('');
    api.getNearestFacilities(lookup.lat, lookup.lon, 8)
      .then(d => { if (!cancelled) setHubs(d.facilities); })
      .catch(e => { if (!cancelled) setHubsError(e?.message || String(e)); })
      .finally(() => { if (!cancelled) setHubsLoading(false); });
    return () => { cancelled = true; };
  }, [lookup]);

  // Carrier list — for an IX (members peering there) or the active facility /
  // country.
  useEffect(() => {
    if (!isIx && !fac) return;
    let cancelled = false;
    setLoading(true); setError(''); setSelected(null); setTypeFilter(null);
    const p = isIx && lookup.kind === 'ix'
      ? api.getIxCarriers(lookup.ixId).then(d => ({ carriers: d.carriers as Carrier[], types: d.types, cov: null as any }))
      : mode === 'facility'
        ? api.getFacilityCarriers(fac!.id).then(d => ({ carriers: d.carriers as Carrier[], types: d.types, cov: null as any }))
        : api.getRegionCarriers(country).then(d => ({ carriers: d.carriers as Carrier[], types: d.types, cov: { covered: d.facilities_covered, total: d.facilities_total } }));
    p.then(d => { if (cancelled) return; setCarriers(d.carriers); setTypes(d.types); setCoverage(d.cov); })
      .catch(e => { if (!cancelled) setError(e?.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [fac, mode, country, isIx, lookup]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return carriers.filter(c =>
      (!typeFilter || c.type === typeFilter) &&
      (!q || c.name.toLowerCase().includes(q) || String(c.asn).includes(q)),
    );
  }, [carriers, typeFilter, search]);

  const openCarrier = (netId: number) => {
    setSelecting(netId); setDetailError('');
    api.getCarrier(netId).then(d => setSelected(d))
      .catch(e => setDetailError(e?.message || String(e)))
      .finally(() => setSelecting(null));
  };

  const headerTitle = isIx && lookup.kind === 'ix'
    ? (lookup.ixName || `IX ${lookup.ixId}`)
    : !fac
      ? (lookup.kind === 'point' ? `Nearest hubs to ${pointLabel(lookup)}` : '')
      : mode === 'facility' ? fac.name : `${country || '—'} (country-wide)`;
  const headerSub = isIx && lookup.kind === 'ix'
    ? `Internet exchange — peer over the fabric${[lookup.city, lookup.country].filter(Boolean).length ? ` · ${[lookup.city, lookup.country].filter(Boolean).join(', ')}` : ''}`
    : !fac
      ? 'No local colo? Backhaul to one of these — or use the Satellite overlay for the last mile.'
      : mode === 'facility'
        ? [fac.city, fac.country, fac.distance_km != null ? `${fac.distance_km} km away` : ''].filter(Boolean).join(' · ')
        : coverage ? `across ${coverage.covered} of ${coverage.total} facilities (busiest first)` : '';

  return (
    <div className="absolute top-3 right-3 z-20 w-[330px] max-h-[calc(100%-1.5rem)] flex flex-col bg-surface-900/95 border border-surface-700 rounded-lg shadow-2xl backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-start gap-2 px-3 py-2 border-b border-surface-700">
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-wide text-surface-500">{isIx ? 'Networks to peer with here' : 'Carriers for a private connection'}</div>
          <div className="text-sm text-surface-100 font-medium truncate">{headerTitle}</div>
          <div className="text-[10px] text-surface-400 leading-snug">{headerSub}</div>
        </div>
        <button onClick={onClose} className="text-surface-400 hover:text-surface-100 text-lg leading-none px-1" title="Close">×</button>
      </div>

      {/* Mode toggle — only once a facility is in focus (not for an IX). */}
      {fac && !isIx && (
        <div className="flex gap-1 px-3 pt-2 items-center">
          {isPoint && (
            <button onClick={() => setFac(null)} className="text-[11px] text-surface-400 hover:text-surface-100 pr-1" title="Back to nearest hubs">‹</button>
          )}
          <button
            onClick={() => setMode('facility')}
            className={`flex-1 text-[11px] py-1 rounded border transition-colors ${mode === 'facility' ? 'bg-surface-700 border-surface-600 text-surface-100' : 'border-surface-700 text-surface-400 hover:text-surface-200'}`}
          >This facility</button>
          <button
            onClick={() => setMode('country')}
            disabled={!country}
            className={`flex-1 text-[11px] py-1 rounded border transition-colors disabled:opacity-40 ${mode === 'country' ? 'bg-surface-700 border-surface-600 text-surface-100' : 'border-surface-700 text-surface-400 hover:text-surface-200'}`}
            title={country ? `All carriers across ${country}` : 'No country for this facility'}
          >Country{country ? ` (${country})` : ''}</button>
        </div>
      )}

      {!fac && !isIx ? (
        /* ---- Nearest-hubs list (point mode) ---- */
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {hubsLoading && <div className="text-[11px] text-surface-400 px-1 py-2">Finding nearest hubs…</div>}
          {hubsError && <div className="text-[11px] text-red-300 px-1 py-2">Failed: {hubsError}</div>}
          {!hubsLoading && !hubsError && hubs.length === 0 && (
            <div className="text-[11px] text-surface-500 px-1 py-2">No facilities found.</div>
          )}
          {hubs.map(h => (
            <button
              key={h.id}
              onClick={() => { setMode('facility'); setFac(h); }}
              className="w-full flex items-center gap-2 text-left px-2 py-1.5 rounded hover:bg-surface-800 transition-colors group"
            >
              <span className="text-[11px] text-surface-500 w-12 shrink-0 tabular-nums">{h.distance_km} km</span>
              <span className="flex-1 min-w-0">
                <span className="block text-[11px] text-surface-200 truncate group-hover:text-surface-50">{h.name}</span>
                <span className="block text-[9px] text-surface-500">{[h.city, h.country].filter(Boolean).join(', ')} · {h.net_count || 0} networks</span>
              </span>
              <span className="text-surface-600 group-hover:text-surface-300 text-xs shrink-0">›</span>
            </button>
          ))}
          <div className="text-[9px] text-surface-500 px-2 pt-2 leading-snug">
            Distances are great-circle to the nearest interconnection facilities. Pick one to see its carriers · Facility locations © PeeringDB
          </div>
        </div>
      ) : selected ? (
        <CarrierDetailView detail={selected} onBack={() => setSelected(null)} />
      ) : (
        <>
          {/* Search + type filter */}
          <div className="px-3 pt-2 space-y-1.5">
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search name or ASN…"
              className="w-full text-[11px] bg-surface-950 border border-surface-700 rounded px-2 py-1 text-surface-200 placeholder-surface-600 focus:outline-none focus:border-surface-500"
            />
            <div className="flex flex-wrap gap-1">
              <FilterChip label={`All (${carriers.length})`} active={!typeFilter} onClick={() => setTypeFilter(null)} color="#94a3b8" />
              {types.map(t => (
                <FilterChip key={t.type} label={`${shortType(t.type)} (${t.count})`} active={typeFilter === t.type} onClick={() => setTypeFilter(typeFilter === t.type ? null : t.type)} color={typeColor(t.type)} />
              ))}
            </div>
          </div>

          {/* Carrier list */}
          <div className="flex-1 overflow-y-auto px-2 py-2 mt-1">
            {loading && <div className="text-[11px] text-surface-400 px-1 py-2">Loading carriers…</div>}
            {error && <div className="text-[11px] text-red-300 px-1 py-2">Failed: {error}</div>}
            {!loading && !error && filtered.length === 0 && (
              <div className="text-[11px] text-surface-500 px-1 py-2">No carriers match.</div>
            )}
            {filtered.map(c => (
              <button
                key={c.net_id}
                onClick={() => openCarrier(c.net_id)}
                className="w-full flex items-center gap-2 text-left px-2 py-1.5 rounded hover:bg-surface-800 transition-colors group"
              >
                <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: typeColor(c.type) }} />
                <span className="flex-1 min-w-0">
                  <span className="block text-[11px] truncate group-hover:text-surface-50">
                    {c.is_satellite && <span title="Satellite (LEO) operator" className="mr-1">🛰️</span>}
                    <span className={c.is_satellite ? 'text-cyan-300 font-medium' : 'text-surface-200'}>{c.name}</span>
                  </span>
                  <span className="block text-[9px] text-surface-500">AS{c.asn} · {shortType(c.type)}{c.site_count ? ` · ${c.site_count} sites` : ''}</span>
                </span>
                {selecting === c.net_id
                  ? <span className="text-[9px] text-surface-500 shrink-0">…</span>
                  : <span className="text-surface-600 group-hover:text-surface-300 text-xs shrink-0">›</span>}
              </button>
            ))}
          </div>
          {detailError && <div className="text-[10px] text-red-300 px-3 pb-2">Carrier detail failed: {detailError}</div>}
          <div className="px-3 py-1.5 border-t border-surface-700 text-[9px] text-surface-500">
            Interconnection data © PeeringDB · click a carrier for contact details
          </div>
        </>
      )}
    </div>
  );
}

function CarrierDetailView({ detail, onBack }: { detail: CarrierDetail; onBack: () => void }) {
  return (
    <div className="flex-1 overflow-y-auto px-3 py-2">
      <button onClick={onBack} className="text-[11px] text-surface-400 hover:text-surface-100 mb-2">‹ Back to list</button>
      <div className="text-sm text-surface-100 font-medium">{detail.name}</div>
      {detail.aka && <div className="text-[10px] text-surface-400">aka {detail.aka}</div>}
      <div className="flex items-center gap-1.5 mt-1 mb-2">
        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded" style={{ background: typeColor(detail.type) + '22', color: typeColor(detail.type) }}>{detail.type}</span>
        <span className="text-[11px] text-surface-300">AS{detail.asn}</span>
      </div>

      <Row label="Website" value={detail.website} href={detail.website} />
      <Row label="PeeringDB" value={detail.peeringdb_url} href={detail.peeringdb_url} />
      {detail.policy_general && <Row label="Peering policy" value={detail.policy_general} href={detail.policy_url || undefined} />}
      {detail.irr_as_set && <Row label="IRR AS-SET" value={detail.irr_as_set} />}
      {detail.looking_glass && <Row label="Looking glass" value={detail.looking_glass} href={detail.looking_glass} />}

      <div className="text-[10px] uppercase tracking-wide text-surface-500 mt-3 mb-1">Contacts</div>
      {detail.contacts.length === 0 ? (
        <div className="text-[11px] text-surface-500">No public contacts in PeeringDB — use the website above.</div>
      ) : (
        <div className="space-y-1.5">
          {detail.contacts.map((c, i) => (
            <div key={i} className="bg-surface-950/60 border border-surface-800 rounded px-2 py-1.5">
              <div className="text-[10px] text-surface-400">{c.role}{c.name && c.name !== c.email ? ` · ${c.name}` : ''}</div>
              {c.email && <a href={`mailto:${c.email}`} className="block text-[11px] text-sky-300 hover:underline break-all">{c.email}</a>}
              {c.phone && <a href={`tel:${c.phone}`} className="block text-[11px] text-surface-200 hover:underline">{c.phone}</a>}
              {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="block text-[11px] text-sky-300 hover:underline break-all">{c.url}</a>}
            </div>
          ))}
        </div>
      )}
      {detail.notes && <div className="text-[10px] text-surface-500 mt-2 whitespace-pre-line">{detail.notes}</div>}
    </div>
  );
}

function Row({ label, value, href }: { label: string; value: string; href?: string }) {
  if (!value) return null;
  return (
    <div className="flex gap-2 text-[11px] py-0.5">
      <span className="text-surface-500 w-[88px] shrink-0">{label}</span>
      {href
        ? <a href={href} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline break-all flex-1">{value}</a>
        : <span className="text-surface-200 break-all flex-1">{value}</span>}
    </div>
  );
}

function FilterChip({ label, active, onClick, color }: { label: string; active: boolean; onClick: () => void; color: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded border transition-colors ${active ? 'bg-surface-700 border-surface-500 text-surface-100' : 'border-surface-700 text-surface-400 hover:text-surface-200'}`}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
      {label}
    </button>
  );
}

function shortType(t: string): string {
  switch (t) {
    case 'Cable/DSL/ISP': return 'ISP';
    case 'Network Services': return 'NetSvc';
    case 'Educational/Research': return 'Edu/Research';
    case 'Not Disclosed': return 'Other';
    default: return t;
  }
}

function pointLabel(l: { lat: number; lon: number; label?: string }): string {
  if (l.label) return l.label;
  const ns = `${Math.abs(l.lat).toFixed(2)}°${l.lat >= 0 ? 'N' : 'S'}`;
  const ew = `${Math.abs(l.lon).toFixed(2)}°${l.lon >= 0 ? 'E' : 'W'}`;
  return `${ns}, ${ew}`;
}

import { useEffect, useMemo, useState } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import type { AnalyzedPath, PathAnalysisResult, PathEnd } from '../../types';
import { ArrowUpDown, Loader2, MapPin, Server, X, AlertTriangle, Eye } from 'lucide-react';

// One color per drawn path (sky, pink, lime, amber).
const PATH_COLORS = ['#38bdf8', '#f472b6', '#a3e635', '#fbbf24'];

type Mode = 'shortest' | 'disjoint';
type Independence = 'link' | 'node' | 'srlg';

/** A-end / Z-end picker: type to filter sites (locations) and nodes. */
function EndPicker({ label, value, onChange }: { label: string; value: PathEnd | null; onChange: (e: PathEnd | null) => void }) {
  const nodes = useStore(s => s.nodes);
  const selectedNodeId = useStore(s => s.selectedNodeId);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const sites = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of nodes) if (n.location) m.set(n.location, (m.get(n.location) || 0) + 1);
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [nodes]);
  const q = query.trim().toLowerCase();
  const siteHits = sites.filter(([loc]) => !q || loc.toLowerCase().includes(q)).slice(0, 8);
  const nodeHits = nodes
    .filter(n => !q || n.name.toLowerCase().includes(q))
    .sort((a, b) => a.name.localeCompare(b.name))
    .slice(0, 10);
  const pick = (e: PathEnd) => { onChange(e); setQuery(''); setOpen(false); };
  const valueLabel = value?.node_id != null
    ? nodes.find(n => n.id === value.node_id)?.name || `Node ${value.node_id}`
    : value?.location;

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-surface-500">{label}</span>
        {selectedNodeId != null && (
          <button className="text-[10px] text-primary-400 hover:text-primary-300" onClick={() => pick({ node_id: selectedNodeId })}>
            Use selected node
          </button>
        )}
      </div>
      {value ? (
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-surface-800 border border-surface-700 text-xs">
          {value.node_id != null ? <Server size={12} className="text-surface-400" /> : <MapPin size={12} className="text-cyan-400" />}
          <span className="flex-1 truncate text-surface-100">{valueLabel}</span>
          {value.location && <span className="text-[10px] text-surface-500">site</span>}
          <button onClick={() => onChange(null)} className="text-surface-500 hover:text-surface-200" title="Clear"><X size={12} /></button>
        </div>
      ) : (
        <div className="relative">
          <input
            className="w-full text-xs"
            placeholder="Search a site or node…"
            value={query}
            onChange={e => { setQuery(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onBlur={() => setTimeout(() => setOpen(false), 150)}
          />
          {open && (siteHits.length > 0 || nodeHits.length > 0) && (
            <div className="absolute z-30 left-0 right-0 mt-1 max-h-64 overflow-y-auto bg-surface-800 border border-surface-600 rounded-lg shadow-xl py-1">
              {siteHits.length > 0 && <div className="px-2.5 pt-1 pb-0.5 text-[9px] uppercase tracking-wider text-surface-500">Sites</div>}
              {siteHits.map(([loc, count]) => (
                <button key={`s-${loc}`} onMouseDown={() => pick({ location: loc })}
                  className="w-full flex items-center gap-2 px-2.5 py-1 text-xs text-surface-200 hover:bg-surface-700">
                  <MapPin size={11} className="text-cyan-400 shrink-0" />
                  <span className="flex-1 text-left truncate">{loc}</span>
                  <span className="text-[10px] text-surface-500">{count} node{count === 1 ? '' : 's'}</span>
                </button>
              ))}
              {nodeHits.length > 0 && <div className="px-2.5 pt-1.5 pb-0.5 text-[9px] uppercase tracking-wider text-surface-500">Nodes</div>}
              {nodeHits.map(n => (
                <button key={`n-${n.id}`} onMouseDown={() => pick({ node_id: n.id })}
                  className="w-full flex items-center gap-2 px-2.5 py-1 text-xs text-surface-200 hover:bg-surface-700">
                  <Server size={11} className="text-surface-400 shrink-0" />
                  <span className="flex-1 text-left truncate">{n.name}</span>
                  {n.location && <span className="text-[10px] text-surface-500 truncate max-w-[90px]">{n.location}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PathCard({ title, path, color, focused, onFocus }: {
  title: string; path: AnalyzedPath; color: string; focused: boolean; onFocus: () => void;
}) {
  return (
    <button
      onClick={onFocus}
      title="Highlight only this path"
      className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${focused ? 'bg-surface-800 border-surface-500' : 'bg-surface-900/60 border-surface-700 hover:border-surface-600'}`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="w-3 h-1 rounded-full shrink-0" style={{ background: color }} />
        <span className="text-xs font-medium text-surface-100 flex-1">{title}</span>
        {focused && <Eye size={11} className="text-surface-400" />}
      </div>
      <div className="grid grid-cols-4 gap-1 text-[10px] mb-1.5">
        <Stat label="Hops" value={String(path.hops)} />
        <Stat label="Cost" value={String(path.cost)} />
        <Stat label="Latency" value={`${path.latency_estimated_links > 0 ? '≈ ' : ''}${fmtMs(path.latency_ms)} ms`} warn={path.latency_unknown_links > 0} />
        <Stat label="Min BW" value={`${path.bottleneck_gbps}G`} />
      </div>
      {(path.latency_estimated_links > 0 || path.latency_unknown_links > 0) && (
        <div className="text-[10px] text-surface-400 mb-1">
          {fmtMs(path.latency_measured_ms)} ms measured
          {path.latency_estimated_links > 0 && (
            <> · <span className="text-surface-300">~{fmtMs(path.latency_estimated_ms)} ms estimated</span> ({path.latency_estimated_links} link{path.latency_estimated_links === 1 ? '' : 's'})</>
          )}
          {path.latency_unknown_links > 0 && (
            <span className="text-amber-300 inline-flex items-center gap-0.5">
              {' · '}<AlertTriangle size={9} /> {path.latency_unknown_links} link{path.latency_unknown_links === 1 ? '' : 's'} unknown (not counted)
            </span>
          )}
        </div>
      )}
      <div className="text-[10px] text-surface-400 leading-snug">{path.node_names.join(' → ')}</div>
    </button>
  );
}

const fmtMs = (ms: number) => String(Math.round(ms * 10) / 10);

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <div className="text-surface-500">{label}</div>
      <div className={`font-mono ${warn ? 'text-amber-300' : 'text-surface-200'}`}>{value}</div>
    </div>
  );
}

export default function PathAnalysisPanel() {
  const currentTopologyId = useStore(s => s.currentTopologyId);
  const pathEnds = useStore(s => s.pathEnds);
  const setPathEnd = useStore(s => s.setPathEnd);
  const setHighlightedFlowPaths = useStore(s => s.setHighlightedFlowPaths);
  const [mode, setMode] = useState<Mode>('shortest');
  const [metric, setMetric] = useState<'cost' | 'latency'>('cost');
  const [independence, setIndependence] = useState<Independence>('link');
  const [maxPaths, setMaxPaths] = useState(2);
  const [includePlanned, setIncludePlanned] = useState(false);
  const [result, setResult] = useState<PathAnalysisResult | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  // Which path is shown alone on the canvas (null = all of them).
  const [focus, setFocus] = useState<number | null>(null);

  // Paths to draw, with their titles, in color order.
  const drawn = useMemo(() => {
    if (!result) return [] as { title: string; path: AnalyzedPath }[];
    if (result.shortest) {
      const s = result.shortest;
      if (s.same_path || !s.lowest_latency) return [{ title: 'Lowest cost & lowest latency', path: s.lowest_cost }];
      return [{ title: 'Lowest cost', path: s.lowest_cost }, { title: 'Lowest latency', path: s.lowest_latency }];
    }
    return (result.disjoint?.paths || []).map((p, i) => ({ title: `Path ${i + 1}`, path: p }));
  }, [result]);

  // Run whenever both ends are set and an option changes.
  useEffect(() => {
    if (!currentTopologyId || !pathEnds.a || !pathEnds.z) { setResult(null); setError(''); return; }
    let cancelled = false;
    setLoading(true);
    setError('');
    api.analyzePaths(currentTopologyId, {
      a: pathEnds.a, z: pathEnds.z, mode, metric, disjoint: independence, max_paths: maxPaths, include_planned: includePlanned,
    })
      .then(r => { if (!cancelled) { setResult(r); setFocus(null); } })
      .catch((e: any) => {
        if (cancelled) return;
        setResult(null);
        // Surface the API's {detail} message (e.g. "No path between A-end and Z-end").
        const m = String(e?.message || e).match(/"detail"\s*:\s*"([^"]+)"/);
        setError(m ? m[1] : String(e?.message || e));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [currentTopologyId, pathEnds.a, pathEnds.z, mode, metric, independence, maxPaths, includePlanned]);

  // Remove this panel's highlight (never a flow's highlight someone else set).
  const clearOwnHighlight = () => {
    if (useStore.getState().highlightedFlowPaths?.flow_name === 'path-analysis') setHighlightedFlowPaths(null);
  };
  // Switching to another panel drops the paths; closing the panel (e.g. the F
  // "full screen" key) keeps them on the canvas.
  useEffect(() => () => {
    if (useStore.getState().sidePanel !== 'none') clearOwnHighlight();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // Draw the paths on the canvas, one color each (or only the focused one).
  useEffect(() => {
    if (!drawn.length) { clearOwnHighlight(); return; }
    const shown = drawn.map((d, i) => ({ ...d, color: PATH_COLORS[i % PATH_COLORS.length] }))
      .filter((_, i) => focus === null || i === focus);
    setHighlightedFlowPaths({
      flow_id: -2,
      flow_name: 'path-analysis',
      paths: shown.map(d => d.path.node_ids),
      link_ids_per_path: shown.map(d => d.path.link_ids),
      bandwidth_per_path_gbps: shown.map(() => 0),
      latency_per_path_ms: shown.map(d => d.path.latency_ms),
      path_colors: shown.map(d => d.color),
    });
  }, [drawn, focus, setHighlightedFlowPaths]);

  const clear = () => {
    setPathEnd('a', null);
    setPathEnd('z', null);
    setResult(null);
    setError('');
    clearOwnHighlight();
  };
  const swap = () => { const { a, z } = pathEnds; setPathEnd('a', z); setPathEnd('z', a); };
  const d = result?.disjoint;
  const tab = (active: boolean) => `flex-1 py-1 rounded-md text-[11px] font-medium transition-colors ${active ? 'bg-surface-700 text-white' : 'text-surface-400 hover:text-surface-200'}`;

  return (
    <div className="space-y-3">
      <EndPicker label="A-end" value={pathEnds.a} onChange={e => setPathEnd('a', e)} />
      <div className="flex justify-center -my-1">
        <button onClick={swap} className="p-1 rounded text-surface-500 hover:text-surface-200 hover:bg-surface-800" title="Swap A and Z">
          <ArrowUpDown size={13} />
        </button>
      </div>
      <EndPicker label="Z-end" value={pathEnds.z} onChange={e => setPathEnd('z', e)} />

      <div className="flex bg-surface-950/60 border border-surface-700 rounded-lg p-0.5">
        <button className={tab(mode === 'shortest')} onClick={() => setMode('shortest')}>Shortest</button>
        <button className={tab(mode === 'disjoint')} onClick={() => setMode('disjoint')}>Disjoint</button>
      </div>

      {mode === 'disjoint' && (
        <div className="grid grid-cols-3 gap-2 text-[10px]">
          <label className="space-y-0.5">
            <span className="text-surface-500">Metric</span>
            <select className="w-full text-xs" value={metric} onChange={e => setMetric(e.target.value as any)}>
              <option value="cost">Cost</option>
              <option value="latency">Latency</option>
            </select>
          </label>
          <label className="space-y-0.5">
            <span className="text-surface-500">Independent by</span>
            <select className="w-full text-xs" value={independence} onChange={e => setIndependence(e.target.value as Independence)}>
              <option value="link">Link</option>
              <option value="node">Node</option>
              <option value="srlg">SRLG</option>
            </select>
          </label>
          <label className="space-y-0.5">
            <span className="text-surface-500">Max paths</span>
            <select className="w-full text-xs" value={maxPaths} onChange={e => setMaxPaths(Number(e.target.value))}>
              {[2, 3, 4].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>
      )}

      <div className="flex items-center justify-between">
        <label className="flex items-center gap-1.5 text-[11px] text-surface-400 cursor-pointer">
          <input type="checkbox" checked={includePlanned} onChange={e => setIncludePlanned(e.target.checked)} />
          Include planned
        </label>
        {(pathEnds.a || pathEnds.z || result) && (
          <button className="text-[11px] text-surface-400 hover:text-surface-200" onClick={clear}>Clear</button>
        )}
      </div>

      {!pathEnds.a || !pathEnds.z ? (
        <div className="text-[11px] text-surface-500 leading-relaxed border border-dashed border-surface-700 rounded-lg p-3">
          Pick an A-end and a Z-end — a node or a whole site — or right-click a node on the canvas and choose
          <span className="text-surface-300"> Path from here</span> / <span className="text-surface-300">Path to here</span>.
        </div>
      ) : loading ? (
        <div className="flex items-center gap-2 text-[11px] text-surface-400"><Loader2 size={12} className="animate-spin" /> Analyzing…</div>
      ) : error ? (
        <div className="text-[11px] text-red-300 bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">{error}</div>
      ) : result && (
        <div className="space-y-2 fade-in">
          {d && (
            <div className="text-[11px] text-surface-300">
              Found <span className="text-white font-medium">{d.paths.length}</span> {d.independence}-disjoint path{d.paths.length === 1 ? '' : 's'}
              {d.paths.length < d.requested && <span className="text-surface-500"> (asked for {d.requested})</span>}
              {' '}· lowest total {d.metric}
            </div>
          )}
          {result.shortest?.same_path && (
            <div className="text-[10px] text-surface-500">The lowest-cost path is also the lowest-latency path.</div>
          )}
          {drawn.map((dp, i) => (
            <PathCard key={i} title={dp.title} path={dp.path} color={PATH_COLORS[i % PATH_COLORS.length]}
              focused={focus === i} onFocus={() => setFocus(focus === i ? null : i)} />
          ))}
          {focus !== null && (
            <button className="text-[10px] text-primary-400 hover:text-primary-300" onClick={() => setFocus(null)}>Show all paths</button>
          )}
          {d && d.shared_srlgs.length > 0 && (
            <div className="text-[10px] text-amber-300 bg-amber-900/15 border border-amber-800/40 rounded-lg px-3 py-2 space-y-0.5">
              <div className="flex items-center gap-1 font-medium"><AlertTriangle size={10} /> Shared risk groups</div>
              {d.shared_srlgs.map((s, i) => (
                <div key={i}>Paths {s.paths[0] + 1} and {s.paths[1] + 1} share {s.srlgs.join(', ')}</div>
              ))}
            </div>
          )}
          {d?.method === 'greedy' && (
            <div className="text-[10px] text-surface-500 leading-snug">
              SRLG-disjoint paths are found greedily (best path first, then everything sharing a risk group with it is
              excluded), so some diverse combinations can be missed.
            </div>
          )}
          <div className="text-[10px] text-surface-600 leading-snug">
            Customer sites are never used as transit. Click a path to show it alone.
          </div>
        </div>
      )}
    </div>
  );
}

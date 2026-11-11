import { useState, useEffect, useRef, startTransition } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import { latencyLabel, latencyFormValue } from '../../utils/latency';
import { SAT_DISH, SAT_POP, ORBITS, ORBIT_COLORS, isSatelliteLink, satLinkLabel } from '../../utils/satellite';
import toast from 'react-hot-toast';
import { Plus, Trash2, Edit3, Save, ChevronDown, ChevronRight, Palette } from 'lucide-react';
import { InterfaceSpec, ActualLinkUtilization, LinkData } from '../../types';
import { formatBandwidth } from '../../utils/format';
import { formatBps } from '../../utils/actualUtilization';
import ActualUtilizationSection from './ActualUtilizationSection';
import SearchBox, { MatchedIn } from '../SearchBox';
import Highlight from '../Highlight';
import { matchItem, type SearchField } from '../../utils/search';

const LINK_COLOR_PRESETS = [
  '#ef4444', '#f97316', '#f59e0b', '#eab308',
  '#84cc16', '#22c55e', '#14b8a6', '#06b6d4',
  '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7',
  '#d946ef', '#ec4899', '#f43f5e', '#b1b1b7',
];

function ColorPalette({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1">
      {LINK_COLOR_PRESETS.map(c => (
        <button key={c} className="w-5 h-5 rounded-sm border border-surface-600 hover:scale-110 transition-transform"
          style={{ backgroundColor: c, outline: value === c ? '2px solid white' : 'none', outlineOffset: '1px' }}
          onClick={() => onChange(c)} />
      ))}
    </div>
  );
}

export default function LinksPanel() {
  const {
    links, nodes, selectedLinkId, setSelectedLink, currentTopologyId,
    addLink, updateLink, deleteLink, simulationResult, flows,
    pendingLink, setPendingLink, editRequested,
    setHighlightedFlowPaths, highlightedFlowPaths,
    setSelectedFlow, setSidePanel,
    showInterfaceNames, toggleInterfaceNames, linkLabelFields, setLinkLabelFields,
    selectedLinkIds, setSelectedLinkIds,
    linkWidthBySpeed, toggleLinkWidthBySpeed,
    linkColorByUtilization, toggleLinkColorByUtilization,
    showSatelliteLinks, toggleShowSatelliteLinks,
    actualUtilization,
  } = useStore();
  const canEdit = useStore(s => (s as any).canEditCurrentTopology()) as boolean;
  const [editingId, setEditingId] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<any>({});
  const [showAdd, setShowAdd] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchScope, setSearchScope] = useState('all');
  const [bulkColor, setBulkColor] = useState('#3b82f6');
  const selectedRef = useRef<HTMLDivElement>(null);
  // Latency field value the open edit form was loaded with ('' = unknown). If
  // the link's latency changes underneath the form (e.g. a Link Metrics import)
  // and the user hasn't touched the field, follow it — otherwise saving would
  // write the old value back.
  const editBaseLatencyRef = useRef<string | null>(null);
  useEffect(() => {
    if (editingId == null) return;
    const link = links.find(l => l.id === editingId);
    if (!link) return;
    const cur = latencyFormValue(link);
    if (cur === editBaseLatencyRef.current) return;
    const base = editBaseLatencyRef.current;
    editBaseLatencyRef.current = cur;
    setEditForm((f: any) => (String(f.latency_ms) === base ? { ...f, latency_ms: cur } : f));
  }, [links, editingId]);

  // Auto-expand (left-click) or auto-edit (right-click Edit) and scroll to selected link
  useEffect(() => {
    if (selectedLinkId) {
      const link = links.find(l => l.id === selectedLinkId);
      if (link) {
        setExpandedId(selectedLinkId);
        if (editRequested) {
          startEdit(link);
        } else {
          setEditingId(null);
        }
      }
      setTimeout(() => {
        selectedRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 50);
    }
  }, [selectedLinkId, editRequested]);

  // Add link form state
  const [addForm, setAddForm] = useState({
    source_node_id: '', target_node_id: '',
    source_interface: '', target_interface: '',
    latency_ms: '', carrier: '', is_planned: false,
    // Satellite (dish ↔ PoP) links: no interfaces; orbit + bandwidth + cost instead.
    orbit: 'leo', sat_bandwidth: '', sat_cost: '',
  });
  const [srcAvailable, setSrcAvailable] = useState<InterfaceSpec[]>([]);
  const [tgtAvailable, setTgtAvailable] = useState<InterfaceSpec[]>([]);
  const [editSrcAvailable, setEditSrcAvailable] = useState<InterfaceSpec[]>([]);
  const [editTgtAvailable, setEditTgtAvailable] = useState<InterfaceSpec[]>([]);

  const loadEditInterfaces = async (srcNodeId: number, tgtNodeId: number) => {
    if (!currentTopologyId) return;
    try {
      const [src, tgt] = await Promise.all([
        api.getAvailableInterfaces(currentTopologyId, srcNodeId),
        api.getAvailableInterfaces(currentTopologyId, tgtNodeId),
      ]);
      setEditSrcAvailable(src.available || []);
      setEditTgtAvailable(tgt.available || []);
    } catch { /* ignore */ }
  };
  const [selectedSrcSpeed, setSelectedSrcSpeed] = useState<number | null>(null);

  const nodeName = (id: number) => nodes.find(n => n.id === id)?.name ?? `Node ${id}`;
  // Searchable fields (the chips under the search box).
  const nodeLocation = (id: number) => nodes.find(n => n.id === id)?.location;
  const linkFields: SearchField<LinkData>[] = [
    { key: 'nodes', label: 'Nodes', get: l => [nodeName(l.source_node_id), nodeName(l.target_node_id)] },
    { key: 'interfaces', label: 'Interfaces', get: l => [l.source_interface, l.target_interface] },
    { key: 'carrier', label: 'Carrier', get: l => l.carrier },
    { key: 'circuit', label: 'Circuit ID', get: l => l.circuit_id },
    { key: 'link_id', label: 'Link ID', get: l => l.link_id_label },
    { key: 'ip', label: 'IP', get: l => [l.source_ip, l.target_ip, l.source_ipv6, l.target_ipv6] },
    { key: 'site', label: 'Site', get: l => [nodeLocation(l.source_node_id), nodeLocation(l.target_node_id)] },
  ];
  const searchedLinks = links
    .map(l => ({ l, match: matchItem(l, linkFields, searchQuery, searchScope) }))
    .filter(x => x.match.ok);

  // Consume pending link from canvas — pre-fill form but keep pendingLink alive for temp edge
  useEffect(() => {
    if (pendingLink) {
      setAddForm(f => ({
        ...f,
        source_node_id: String(pendingLink.source_node_id),
        target_node_id: String(pendingLink.target_node_id),
        source_interface: '', target_interface: '',
      }));
      setShowAdd(true);
    }
  }, [pendingLink]);

  // Load available interfaces when source/target node changes
  useEffect(() => {
    if (addForm.source_node_id && currentTopologyId) {
      api.getAvailableInterfaces(currentTopologyId, parseInt(addForm.source_node_id))
        .then(r => setSrcAvailable(r.available))
        .catch(() => setSrcAvailable([]));
    } else {
      setSrcAvailable([]);
    }
    setAddForm(f => ({ ...f, source_interface: '' }));
    setSelectedSrcSpeed(null);
  }, [addForm.source_node_id, currentTopologyId]);

  useEffect(() => {
    if (addForm.target_node_id && currentTopologyId) {
      api.getAvailableInterfaces(currentTopologyId, parseInt(addForm.target_node_id))
        .then(r => setTgtAvailable(r.available))
        .catch(() => setTgtAvailable([]));
    } else {
      setTgtAvailable([]);
    }
    setAddForm(f => ({ ...f, target_interface: '' }));
  }, [addForm.target_node_id, currentTopologyId]);

  // When source interface is selected, record its speed for filtering target interfaces
  useEffect(() => {
    if (addForm.source_interface) {
      const iface = srcAvailable.find(i => i.name === addForm.source_interface);
      setSelectedSrcSpeed(iface?.speed_gbps ?? null);
    } else {
      setSelectedSrcSpeed(null);
    }
    setAddForm(f => ({ ...f, target_interface: '' }));
  }, [addForm.source_interface, srcAvailable]);

  // Filter target interfaces to only show matching speed
  const filteredTgtAvailable = selectedSrcSpeed !== null
    ? tgtAvailable.filter(i => i.speed_gbps === selectedSrcSpeed)
    : tgtAvailable;

  // Satellite pairing: dish ↔ PoP links are satellite links (no interfaces);
  // dishes can't link to each other.
  const addSrcRole = nodes.find(n => String(n.id) === addForm.source_node_id)?.role;
  const addTgtRole = nodes.find(n => String(n.id) === addForm.target_node_id)?.role;
  const addIsSat = new Set([addSrcRole, addTgtRole]).size === 2 && [addSrcRole, addTgtRole].every(r => r === SAT_DISH || r === SAT_POP);
  const addIsDishPair = addSrcRole === SAT_DISH && addTgtRole === SAT_DISH;
  const addSatBwMissing = addIsSat && !(parseFloat(addForm.sat_bandwidth) > 0);
  // Why "Add Link" is disabled, shown beside it so the form isn't a silent dead end.
  const addBlockedReason = !addForm.source_node_id || !addForm.target_node_id ? 'Select both nodes'
    : addIsDishPair ? '' // has its own message above
    : addSatBwMissing ? 'Enter the satellite link bandwidth'
    : !addIsSat && (!addForm.source_interface || !addForm.target_interface) ? 'Select an interface on both nodes'
    : '';
  // Same for the edit form, from its (possibly changed) endpoints.
  const editRoles = [editForm.source_node_id, editForm.target_node_id].map(id => nodes.find(n => n.id === Number(id))?.role);
  const editIsSat = new Set(editRoles).size === 2 && editRoles.every(r => r === SAT_DISH || r === SAT_POP);

  const handleAdd = async () => {
    if (!addForm.source_node_id || !addForm.target_node_id) {
      toast.error('Select source and target nodes'); return;
    }
    if (addIsDishPair) {
      toast.error("Satellite dishes can't link to each other"); return;
    }
    if (addIsSat && !(parseFloat(addForm.sat_bandwidth) > 0)) {
      toast.error('Enter the satellite link bandwidth'); return;
    }
    if (!addIsSat && (!addForm.source_interface || !addForm.target_interface)) {
      toast.error('Select interfaces on both nodes'); return;
    }
    try {
      await addLink({
        source_node_id: parseInt(addForm.source_node_id),
        target_node_id: parseInt(addForm.target_node_id),
        ...(addIsSat
          ? {
              transport: 'satellite', orbit: addForm.orbit,
              bandwidth_gbps: parseFloat(addForm.sat_bandwidth),
              ...(addForm.sat_cost.trim() !== '' ? { cost: parseFloat(addForm.sat_cost) } : {}),
            }
          : { source_interface: addForm.source_interface, target_interface: addForm.target_interface }),
        // Blank latency = unknown: the backend estimates it from geography.
        ...(addForm.latency_ms.trim() === ''
          ? { latency_source: 'unknown' }
          : { latency_ms: parseFloat(addForm.latency_ms) || 0, latency_source: 'measured' }),
        carrier: addForm.carrier,
        is_planned: addForm.is_planned,
      });
      setAddForm({ source_node_id: '', target_node_id: '', source_interface: '', target_interface: '', latency_ms: '', carrier: '', is_planned: false, orbit: 'leo', sat_bandwidth: '', sat_cost: '' });
      setShowAdd(false);
      setPendingLink(null);
      toast.success('Link created');
    } catch (err: any) {
      const msg = err?.message || 'Failed to create link';
      const match = msg.match(/API error \d+: (.+)/);
      toast.error(match ? match[1] : msg);
    }
  };

  const handleCancelAdd = () => {
    setShowAdd(false);
    setAddForm({ source_node_id: '', target_node_id: '', source_interface: '', target_interface: '', latency_ms: '', carrier: '', is_planned: false, orbit: 'leo', sat_bandwidth: '', sat_cost: '' });
    setPendingLink(null);
  };

  const utilMap: Record<number, any> = {};
  if (simulationResult) {
    for (const lu of simulationResult.link_utilizations) {
      utilMap[lu.link_id] = lu;
    }
  }

  const actualMap: Record<number, ActualLinkUtilization> = {};
  for (const a of actualUtilization?.links || []) actualMap[a.link_id] = a;

  const startEdit = (l: any) => {
    setEditingId(l.id);
    editBaseLatencyRef.current = latencyFormValue(l);
    setEditForm({
      source_node_id: l.source_node_id,
      target_node_id: l.target_node_id,
      source_interface: l.source_interface,
      target_interface: l.target_interface,
      cost: l.cost,
      latency_ms: latencyFormValue(l),
      carrier: l.carrier,
      ...(isSatelliteLink(l) ? { orbit: l.orbit || 'leo', bandwidth_gbps: l.bandwidth_gbps } : {}),
      mtbf_hours: l.mtbf_hours ?? '',
      mttr_hours: l.mttr_hours ?? '',
      circuit_cost: l.circuit_cost || '',
      circuit_id: l.circuit_id || '',
      link_id_label: l.link_id_label || '',
      is_planned: l.is_planned || false,
      source_ip: l.source_ip || '',
      target_ip: l.target_ip || '',
      source_ipv6: l.source_ipv6 || '',
      target_ipv6: l.target_ipv6 || '',
      color: l.color || '',
    });
    // Load available interfaces for both current endpoints
    loadEditInterfaces(l.source_node_id, l.target_node_id);
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      const data = { ...editForm };
      data.cost = parseFloat(data.cost);
      // Blank latency = unknown (estimated); a value is known (measured).
      if (String(data.latency_ms).trim() === '') {
        delete data.latency_ms;
        data.latency_source = 'unknown';
      } else {
        data.latency_ms = parseFloat(data.latency_ms);
        data.latency_source = 'measured';
      }
      data.mtbf_hours = data.mtbf_hours === '' ? null : parseFloat(data.mtbf_hours);
      data.mttr_hours = data.mttr_hours === '' ? null : parseFloat(data.mttr_hours);
      // The endpoints decide the transport: dish ↔ PoP is satellite (orbit +
      // bandwidth), anything else terrestrial.
      if (editIsSat) {
        data.transport = 'satellite';
        data.orbit = data.orbit || 'leo';
        if (String(data.bandwidth_gbps ?? '').trim() === '') delete data.bandwidth_gbps;
        else data.bandwidth_gbps = parseFloat(data.bandwidth_gbps);
      } else {
        data.transport = 'terrestrial';
        delete data.orbit;
        delete data.bandwidth_gbps;
      }
      data.circuit_cost = data.circuit_cost === '' ? 0 : parseFloat(data.circuit_cost) || 0;
      await updateLink(editingId, data);
      setEditingId(null);
      toast.success('Link updated');
    } catch (err: any) {
      const msg = err?.message || 'Failed to update';
      const match = msg.match(/API error \d+: (.+)/);
      let detail = match ? match[1] : msg;
      try { detail = JSON.parse(detail).detail || detail; } catch {}
      toast.error(detail);
    }
  };

  return (
    <div className="space-y-3">
      <ActualUtilizationSection />

      {/* Top controls */}
      {canEdit && (
        <div className="flex items-center gap-2">
          <button className="btn btn-primary btn-sm flex-1" onClick={() => setShowAdd(!showAdd)}>
            <Plus size={14} /> Add Link
          </button>
        </div>
      )}

      {/* Width-by-speed toggle */}
      <div
        className="bg-surface-800/60 rounded-lg px-3 py-2 flex items-center justify-between cursor-pointer select-none hover:opacity-80 transition-opacity"
        onClick={() => startTransition(toggleLinkWidthBySpeed)}
        title="Render faster links thicker so capacity differences are visible at a glance"
      >
        <span className="text-[11px] text-surface-300">Width by Speed</span>
        <div className={`relative w-8 h-[18px] rounded-full transition-colors ${linkWidthBySpeed ? 'bg-primary-600' : 'bg-surface-600'}`}>
          <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${linkWidthBySpeed ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
        </div>
      </div>

      {/* Color-by-utilization toggle */}
      <div
        className="bg-surface-800/60 rounded-lg px-3 py-2 flex items-center justify-between cursor-pointer select-none hover:opacity-80 transition-opacity"
        onClick={() => startTransition(toggleLinkColorByUtilization)}
        title="Color each link by its simulated utilization (run a simulation to populate)"
      >
        <span className="text-[11px] text-surface-300">Color by Utilization</span>
        <div className={`relative w-8 h-[18px] rounded-full transition-colors ${linkColorByUtilization ? 'bg-primary-600' : 'bg-surface-600'}`}>
          <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${linkColorByUtilization ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
        </div>
      </div>

      {/* Show satellite links (hidden by default; Ctrl+Shift+S) */}
      <div
        className="bg-surface-800/60 rounded-lg px-3 py-2 flex items-center justify-between cursor-pointer select-none hover:opacity-80 transition-opacity"
        onClick={() => startTransition(toggleShowSatelliteLinks)}
        title="Draw every satellite link. Otherwise they appear only when their dish or PoP is selected, or on a highlighted path (Ctrl+Shift+S)"
      >
        <span className="text-[11px] text-surface-300">Show Satellite Links</span>
        <div className={`relative w-8 h-[18px] rounded-full transition-colors ${showSatelliteLinks ? 'bg-primary-600' : 'bg-surface-600'}`}>
          <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${showSatelliteLinks ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
        </div>
      </div>

      {/* Link labels toggle + field picker */}
      <div className="bg-surface-800/60 rounded-lg px-3 py-2">
        <div
          className="flex items-center justify-between cursor-pointer select-none hover:opacity-80 transition-opacity"
          onClick={toggleInterfaceNames}
        >
          <span className="text-[11px] text-surface-300">Link Labels</span>
          <div className={`relative w-8 h-[18px] rounded-full transition-colors ${showInterfaceNames ? 'bg-primary-600' : 'bg-surface-600'}`}>
            <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${showInterfaceNames ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
          </div>
        </div>
        {showInterfaceNames && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {[
              { key: 'interface', label: 'Interface' },
              { key: 'ip', label: 'IP Address' },
              { key: 'bandwidth', label: 'Bandwidth' },
              { key: 'cost', label: 'Cost' },
              { key: 'latency', label: 'Latency' },
              { key: 'carrier', label: 'Carrier' },
              { key: 'circuit_cost', label: 'Circuit Cost' },
              { key: 'circuit_id', label: 'Circuit ID' },
              { key: 'link_id_label', label: 'Link ID' },
            ].map(({ key, label }) => {
              const active = linkLabelFields.includes(key);
              return (
                <button key={key}
                  className={`text-[9px] px-1.5 py-0.5 rounded border transition-colors ${
                    active
                      ? 'bg-primary-600/30 border-primary-500 text-primary-300'
                      : 'bg-surface-800 border-surface-600 text-surface-400 hover:border-surface-500'
                  }`}
                  onClick={() => {
                    if (active) {
                      setLinkLabelFields(linkLabelFields.filter(f => f !== key));
                    } else {
                      setLinkLabelFields([...linkLabelFields, key]);
                    }
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Search */}
      <SearchBox
        query={searchQuery} onQuery={setSearchQuery}
        scope={searchScope} onScope={setSearchScope}
        fields={linkFields} shown={searchedLinks.length} total={links.length} noun="links"
      />

      {/* Bulk color apply for multi-selected links */}
      {canEdit && selectedLinkIds.length > 0 && (
        <div className="bg-sky-900/30 border border-sky-500/40 rounded-lg p-3 space-y-2 fade-in">
          <div className="text-[11px] text-sky-300 font-medium">{selectedLinkIds.length} links selected</div>
          <ColorPalette value={bulkColor} onChange={setBulkColor} />
          <div className="flex items-center gap-2">
            <button className="btn btn-primary btn-sm text-[10px] flex-1" onClick={async () => {
              if (!currentTopologyId) return;
              try {
                const updated = await api.bulkUpdateLinks(currentTopologyId, [...selectedLinkIds], { color: bulkColor });
                useStore.setState((s: any) => ({
                  links: s.links.map((l: any) => {
                    const u = updated.find((x: any) => x.id === l.id);
                    return u || l;
                  }),
                }));
                toast.success(`Color applied to ${updated.length} links`);
                setSelectedLinkIds([]);
              } catch { toast.error('Failed to apply color'); }
            }}>
              <Palette size={11} /> Apply
            </button>
            <button className="btn btn-secondary btn-sm text-[10px]" onClick={async () => {
              if (!currentTopologyId) return;
              try {
                const updated = await api.bulkUpdateLinks(currentTopologyId, [...selectedLinkIds], { color: '' });
                useStore.setState((s: any) => ({
                  links: s.links.map((l: any) => {
                    const u = updated.find((x: any) => x.id === l.id);
                    return u || l;
                  }),
                }));
                toast.success('Color cleared');
                setSelectedLinkIds([]);
              } catch { toast.error('Failed to clear color'); }
            }}>
              Clear
            </button>
          </div>
          <button className="text-[10px] text-surface-500 hover:text-surface-300" onClick={() => setSelectedLinkIds([])}>
            Deselect all
          </button>
        </div>
      )}

      {/* Add Form */}
      {showAdd && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2 fade-in"
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAdd(); } }}>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">Source Node</label>
              <select className="w-full" value={addForm.source_node_id}
                onChange={e => setAddForm({ ...addForm, source_node_id: e.target.value })}>
                <option value="">Select node...</option>
                {nodes.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-surface-500">Target Node</label>
              <select className="w-full" value={addForm.target_node_id}
                onChange={e => setAddForm({ ...addForm, target_node_id: e.target.value })}>
                <option value="">Select node...</option>
                {nodes.filter(n => String(n.id) !== addForm.source_node_id).map(n =>
                  <option key={n.id} value={n.id}>{n.name}</option>
                )}
              </select>
            </div>
          </div>

          {addIsDishPair && (
            <p className="text-[10px] text-red-400">Satellite dishes can't link to each other — connect a dish to a Satellite PoP.</p>
          )}
          {addIsSat ? (
            <div className="space-y-2">
              <div className="text-[10px] text-sky-300">🛰 Satellite link — no interfaces needed</div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-[10px] text-surface-500">Orbit</label>
                  <select className="w-full" value={addForm.orbit} onChange={e => setAddForm({ ...addForm, orbit: e.target.value })}>
                    {ORBITS.map(o => <option key={o} value={o}>{o.toUpperCase()}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-surface-500">Bandwidth (Gbps) <span className="text-amber-400">*</span></label>
                  <input className={`w-full ${addSatBwMissing ? 'border-amber-500/70' : ''}`} type="number" step="any" placeholder="e.g. 0.2"
                    value={addForm.sat_bandwidth} autoFocus required aria-invalid={addSatBwMissing}
                    onChange={e => setAddForm({ ...addForm, sat_bandwidth: e.target.value })} />
                  {addSatBwMissing && <p className="text-[9px] text-amber-400 mt-0.5">Required</p>}
                </div>
                <div>
                  <label className="text-[10px] text-surface-500">Cost</label>
                  <input className="w-full" type="number" step="any" placeholder="auto" value={addForm.sat_cost}
                    title="Routing cost; blank = derived from bandwidth"
                    onChange={e => setAddForm({ ...addForm, sat_cost: e.target.value })} />
                </div>
              </div>
            </div>
          ) : (<>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">Source Interface</label>
              <select className="w-full font-mono text-[11px]" value={addForm.source_interface}
                onChange={e => setAddForm({ ...addForm, source_interface: e.target.value })}
                disabled={!addForm.source_node_id}>
                <option value="">Select interface...</option>
                {srcAvailable.map(i =>
                  <option key={i.name} value={i.name}>{i.name} ({i.speed_gbps}G)</option>
                )}
              </select>
              {addForm.source_node_id && srcAvailable.length === 0 && (
                <p className="text-[9px] text-red-400 mt-0.5">No available interfaces</p>
              )}
            </div>
            <div>
              <label className="text-[10px] text-surface-500">
                Target Interface
                {selectedSrcSpeed !== null && <span className="text-primary-400 ml-1">({selectedSrcSpeed}G only)</span>}
              </label>
              <select className="w-full font-mono text-[11px]" value={addForm.target_interface}
                onChange={e => setAddForm({ ...addForm, target_interface: e.target.value })}
                disabled={!addForm.target_node_id || !addForm.source_interface}>
                <option value="">Select interface...</option>
                {filteredTgtAvailable.map(i =>
                  <option key={i.name} value={i.name}>{i.name} ({i.speed_gbps}G)</option>
                )}
              </select>
              {addForm.target_node_id && addForm.source_interface && filteredTgtAvailable.length === 0 && (
                <p className="text-[9px] text-red-400 mt-0.5">No matching {selectedSrcSpeed}G interfaces available</p>
              )}
            </div>
          </div>

          {selectedSrcSpeed && addForm.target_interface && (
            <div className="text-[10px] text-green-400 px-1">
              Link bandwidth: {selectedSrcSpeed} Gbps (from interface speed)
            </div>
          )}
          </>)}

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">Latency (ms)</label>
              <input className="w-full" type="number" value={addForm.latency_ms} placeholder="Blank = estimate"
                title="Enter a known latency, or leave blank to estimate it from the map"
                onChange={e => setAddForm({ ...addForm, latency_ms: e.target.value })} />
            </div>
            <div>
              <label className="text-[10px] text-surface-500">{addIsSat ? 'Provider' : 'Carrier'}</label>
              <input className="w-full" value={addForm.carrier} placeholder={addIsSat ? 'e.g. Starlink' : ''}
                onChange={e => setAddForm({ ...addForm, carrier: e.target.value })} />
            </div>
          </div>

          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={addForm.is_planned}
              onChange={e => setAddForm({ ...addForm, is_planned: e.target.checked })}
              className="accent-indigo-500" />
            Planned (not yet built)
          </label>

          {addBlockedReason && <p className="text-[10px] text-amber-400">{addBlockedReason} to add the link.</p>}
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleAdd}
              disabled={addIsDishPair || !!addBlockedReason} title={addBlockedReason || undefined}>
              Add Link
            </button>
            <button className="btn btn-secondary btn-sm" onClick={handleCancelAdd}>Cancel</button>
          </div>
        </div>
      )}

      <p className="text-[10px] text-surface-500 px-1">
        You can also drag between node handles on the canvas — you'll be prompted to select interfaces.
      </p>

      {/* Separator */}
      <div className="border-t border-surface-700" />

      {/* Link List — scrollable */}
      <div className="space-y-1 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 320px)' }}>
        {searchedLinks.map(({ l, match }) => {
          // Highlight a field only when the search actually matched it.
          const hl = (key: string) => match.hits.some(h => h.field === key) ? searchQuery : '';
          const util = utilMap[l.id];
          const actual = actualMap[l.id];
          const flowsOnLink = util ? flows.filter(f => util.flow_ids.includes(f.id)) : [];

          return (
            <div
              key={l.id}
              ref={l.id === selectedLinkId ? selectedRef : undefined}
              className={`rounded-lg border transition-colors ${
                l.id === selectedLinkId
                  ? 'border-primary-500 bg-surface-800'
                  : selectedLinkIds.includes(l.id)
                  ? 'border-sky-500 bg-sky-900/20'
                  : 'border-surface-700 bg-surface-800/50 hover:bg-surface-800'
              }`}
            >
              <div
                className="flex items-center justify-between px-3 py-2 cursor-pointer"
                onClick={(e) => {
                  if (e.ctrlKey || e.metaKey) {
                    const current = useStore.getState().selectedLinkIds;
                    if (current.includes(l.id)) {
                      setSelectedLinkIds(current.filter(id => id !== l.id));
                    } else {
                      setSelectedLinkIds([...current, l.id]);
                    }
                    return;
                  }
                  setSelectedLink(l.id === selectedLinkId ? null : l.id);
                  setExpandedId(l.id === expandedId ? null : l.id);
                }}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    {expandedId === l.id ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    {l.color && <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: l.color }} />}
                    <span className="text-sm truncate"><Highlight text={nodeName(l.source_node_id)} query={hl('nodes')} /></span>
                    <span className="text-surface-500 text-xs shrink-0">⟷</span>
                    <span className="text-sm truncate"><Highlight text={nodeName(l.target_node_id)} query={hl('nodes')} /></span>
                    <span className="text-[10px] text-primary-400 shrink-0">{formatBandwidth(l.bandwidth_gbps)}</span>
                    {isSatelliteLink(l) && (
                      <span className="text-[8px] px-1 py-0.5 rounded shrink-0 border"
                        style={{ color: ORBIT_COLORS[l.orbit || ''], borderColor: `${ORBIT_COLORS[l.orbit || ''] || '#38bdf8'}66` }}
                        title="Satellite link (hidden on the canvas unless its dish / PoP is selected)">
                        🛰 {satLinkLabel(l)}
                      </span>
                    )}
                    {l.is_inherited && <span className="text-[8px] bg-cyan-600/30 text-cyan-300 border border-cyan-500/40 px-1 py-0.5 rounded shrink-0">INHERITED</span>}
                  </div>
                  <div className="text-[9px] text-surface-500 font-mono ml-5 truncate">
                    <Highlight text={l.source_interface || '?'} query={hl('interfaces')} /> ↔ <Highlight text={l.target_interface || '?'} query={hl('interfaces')} />
                  </div>
                  <div className="ml-5">
                    <MatchedIn hits={match.hits} hidden={['carrier', 'circuit', 'link_id', 'ip', 'site']} query={searchQuery} />
                  </div>
                </div>
                <div className="flex gap-1 shrink-0 ml-2">
                  {canEdit && !l.is_inherited && (
                    <>
                      <button className="btn btn-ghost btn-sm p-1" onClick={e => { e.stopPropagation(); startEdit(l); setExpandedId(l.id); }}>
                        <Edit3 size={12} />
                      </button>
                      <button className="btn btn-ghost btn-sm p-1 text-red-400"
                        onClick={e => { e.stopPropagation(); if (confirm('Delete link?')) { deleteLink(l.id); toast.success('Deleted'); } }}>
                        <Trash2 size={12} />
                      </button>
                    </>
                  )}
                </div>
              </div>

              {expandedId === l.id && editingId !== l.id && (
                <div className="px-3 pb-2 text-xs space-y-1 border-t border-surface-700 pt-2">
                  <div className="grid grid-cols-2 gap-x-4">
                    <span className="text-surface-500">Bandwidth:</span><span>{l.bandwidth_gbps} Gbps</span>
                    <span className="text-surface-500">Source IF:</span><span className="font-mono">{l.source_interface || '-'}</span>
                    <span className="text-surface-500">Source IP:</span><span className="font-mono">{l.source_ip || '-'}</span>
                    <span className="text-surface-500">Source IPv6:</span><span className="font-mono">{l.source_ipv6 || '-'}</span>
                    <span className="text-surface-500">Target IF:</span><span className="font-mono">{l.target_interface || '-'}</span>
                    <span className="text-surface-500">Target IP:</span><span className="font-mono">{l.target_ip || '-'}</span>
                    <span className="text-surface-500">Target IPv6:</span><span className="font-mono">{l.target_ipv6 || '-'}</span>
                    <span className="text-surface-500">Cost:</span><span>{l.cost}</span>
                    <span className="text-surface-500">Latency:</span><span>{latencyLabel(l, true)}</span>
                    <span className="text-surface-500">Carrier:</span><span>{l.carrier || '-'}</span>
                    <span className="text-surface-500">Circuit Cost:</span><span>{l.circuit_cost ? `$${l.circuit_cost.toLocaleString()}` : '-'}</span>
                    <span className="text-surface-500">Circuit ID:</span><span>{l.circuit_id || '-'}</span>
                    <span className="text-surface-500">Link ID:</span><span>{l.link_id_label || '-'}</span>
                    <span className="text-surface-500">MTBF:</span><span>{l.mtbf_hours ?? 'Default'} hrs</span>
                    <span className="text-surface-500">MTTR:</span><span>{l.mttr_hours ?? 'Default'} hrs</span>
                  </div>
                  {util && (
                    <div className="mt-2 p-2 bg-surface-900 rounded">
                      <div className="grid grid-cols-2 gap-x-4">
                        <span className="text-surface-500">Utilization:</span>
                        <span className={util.utilization_pct > 80 ? 'text-red-400' : 'text-green-400'}>{util.utilization_pct}%</span>
                        <span className="text-surface-500">Used:</span><span>{util.used_gbps} Gbps</span>
                        <span className="text-surface-500">Cost/GB:</span><span>${util.cost_per_gb}</span>
                      </div>
                    </div>
                  )}
                  {actual?.has_data && (
                    <div className="mt-2 p-2 bg-surface-900 rounded">
                      <div className="grid grid-cols-2 gap-x-4">
                        {([['30d peak', actual.peak_30d_bps], ['90d peak', actual.peak_90d_bps]] as const).map(([label, bps]) => (
                          <span key={label} className="contents">
                            <span className="text-surface-500">Actual {label}:</span>
                            <span>
                              {formatBps(bps)}
                              {bps != null && l.bandwidth_gbps > 0 && (
                                <span className={bps / (l.bandwidth_gbps * 1e9) > 0.8 ? ' text-red-400' : ' text-green-400'}>
                                  {' '}({Math.round(bps / (l.bandwidth_gbps * 1e9) * 1000) / 10}%)
                                </span>
                              )}
                            </span>
                          </span>
                        ))}
                        {actual.link_delay_us != null && (
                          <>
                            <span className="text-surface-500">Measured delay:</span>
                            <span>{actual.link_delay_us} µs</span>
                          </>
                        )}
                      </div>
                    </div>
                  )}
                  {flowsOnLink.length > 0 && (
                    <div className="mt-2">
                      <span className="text-surface-500">Flows on this link:</span>
                      <div className="mt-1 space-y-0.5">
                        {flowsOnLink.map(f => {
                          const isHighlighted = highlightedFlowPaths?.flow_id === f.id;
                          return (
                            <div
                              key={f.id}
                              className={`text-[10px] flex justify-between px-1.5 py-0.5 rounded cursor-pointer transition-colors ${
                                isHighlighted ? 'bg-amber-900/30 text-amber-300' : 'hover:bg-surface-700'
                              }`}
                              onClick={async () => {
                                if (isHighlighted) {
                                  setHighlightedFlowPaths(null);
                                  return;
                                }
                                // Use the sim result first if we have one;
                                // otherwise compute this single flow's path
                                // on demand so the user can preview without
                                // running a full simulation.
                                const fp = simulationResult?.flow_paths.find(p => p.flow_id === f.id);
                                if (fp) { setHighlightedFlowPaths(fp); return; }
                                if (!currentTopologyId) return;
                                try {
                                  const computed = await api.computeFlowPath(currentTopologyId, f.id);
                                  setHighlightedFlowPaths(computed);
                                } catch (e: any) {
                                  toast.error(`Path compute failed: ${e?.message || e}`);
                                }
                              }}
                              onContextMenu={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                setSelectedFlow(f.id, canEdit);  // read-only (no edit form) when not editable
                                setSidePanel('flows');
                              }}
                              title={canEdit ? 'Right-click to edit this flow' : 'Right-click to view this flow'}
                            >
                              <span>{f.name}</span>
                              <span className="text-surface-500">{formatBandwidth(f.bandwidth_gbps)}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {editingId === l.id && (
                <div className="px-3 pb-3 space-y-2 border-t border-surface-700 pt-2"
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); } }}>
                  <div className="text-[10px] text-surface-500 font-mono">
                    {l.source_interface} ↔ {l.target_interface} ({formatBandwidth(l.bandwidth_gbps)})
                  </div>

                  {/* Endpoint editing */}
                  <div className="bg-surface-900/50 rounded p-2 space-y-2">
                    <div className="text-[10px] text-surface-400 font-medium">Endpoints</div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[9px] text-surface-500">Source Node</label>
                        <select className="w-full text-xs" value={String(editForm.source_node_id || '')}
                          onChange={async e => {
                            const newSrc = parseInt(e.target.value);
                            setEditForm({ ...editForm, source_node_id: newSrc, source_interface: '' });
                            if (currentTopologyId) {
                              try { const r = await api.getAvailableInterfaces(currentTopologyId, newSrc); setEditSrcAvailable(r.available || []); } catch {}
                            }
                          }}>
                          {[...nodes].sort((a, b) => a.name.localeCompare(b.name)).map(n => (
                            <option key={n.id} value={String(n.id)}>{n.name}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="text-[9px] text-surface-500">Target Node</label>
                        <select className="w-full text-xs" value={String(editForm.target_node_id || '')}
                          onChange={async e => {
                            const newTgt = parseInt(e.target.value);
                            setEditForm({ ...editForm, target_node_id: newTgt, target_interface: '' });
                            if (currentTopologyId) {
                              try { const r = await api.getAvailableInterfaces(currentTopologyId, newTgt); setEditTgtAvailable(r.available || []); } catch {}
                            }
                          }}>
                          {[...nodes].sort((a, b) => a.name.localeCompare(b.name)).map(n => (
                            <option key={n.id} value={String(n.id)}>{n.name}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[9px] text-surface-500">Source Interface</label>
                        <select className="w-full text-xs font-mono" value={editForm.source_interface || ''}
                          onChange={e => setEditForm({ ...editForm, source_interface: e.target.value })}>
                          {editForm.source_interface && !editSrcAvailable.find(i => i.name === editForm.source_interface) && (
                            <option value={editForm.source_interface}>{editForm.source_interface} (current)</option>
                          )}
                          {editSrcAvailable.map(i => (
                            <option key={i.name} value={i.name}>{i.name} ({i.speed_gbps}G)</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="text-[9px] text-surface-500">Target Interface</label>
                        <select className="w-full text-xs font-mono" value={editForm.target_interface || ''}
                          onChange={e => setEditForm({ ...editForm, target_interface: e.target.value })}>
                          {editForm.target_interface && !editTgtAvailable.find(i => i.name === editForm.target_interface) && (
                            <option value={editForm.target_interface}>{editForm.target_interface} (current)</option>
                          )}
                          {editTgtAvailable.map(i => (
                            <option key={i.name} value={i.name}>{i.name} ({i.speed_gbps}G)</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] text-surface-500">Cost</label>
                      <input className="w-full" type="number" value={editForm.cost}
                        onChange={e => setEditForm({ ...editForm, cost: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Latency (ms)</label>
                      <input className="w-full" type="number" value={editForm.latency_ms} placeholder="Blank = estimate"
                        title="Enter a known latency, or leave blank to estimate it from the map"
                        onChange={e => setEditForm({ ...editForm, latency_ms: e.target.value })} />
                      {String(editForm.latency_ms).trim() === '' && (
                        <div className="text-[9px] text-surface-500 mt-0.5">
                          {(() => { const cur = links.find(x => x.id === l.id); return cur && cur.latency_source !== 'measured' ? `Now: ${latencyLabel(cur, true)}` : 'Will be estimated'; })()}
                        </div>
                      )}
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">{editIsSat ? 'Provider' : 'Carrier'}</label>
                      <input className="w-full" value={editForm.carrier}
                        onChange={e => setEditForm({ ...editForm, carrier: e.target.value })} />
                    </div>
                    {editIsSat && (<>
                      <div>
                        <label className="text-[10px] text-surface-500">Orbit</label>
                        <select className="w-full" value={editForm.orbit || 'leo'} onChange={e => setEditForm({ ...editForm, orbit: e.target.value })}>
                          {ORBITS.map(o => <option key={o} value={o}>{o.toUpperCase()}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] text-surface-500">Bandwidth (Gbps)</label>
                        <input className="w-full" type="number" step="any" value={editForm.bandwidth_gbps ?? ''} placeholder="e.g. 0.2"
                          onChange={e => setEditForm({ ...editForm, bandwidth_gbps: e.target.value })} />
                      </div>
                    </>)}
                    <div>
                      <label className="text-[10px] text-surface-500">Circuit Cost ($)</label>
                      <input className="w-full" type="number" step="any" placeholder="0" value={editForm.circuit_cost}
                        onChange={e => setEditForm({ ...editForm, circuit_cost: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Circuit ID</label>
                      <input className="w-full" value={editForm.circuit_id || ''}
                        onChange={e => setEditForm({ ...editForm, circuit_id: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Link ID</label>
                      <input className="w-full" value={editForm.link_id_label || ''}
                        onChange={e => setEditForm({ ...editForm, link_id_label: e.target.value })} />
                    </div>
                    <div className="col-span-2">
                      <label className="flex items-center gap-2 text-xs">
                        <input type="checkbox" checked={editForm.is_planned || false}
                          onChange={e => setEditForm({ ...editForm, is_planned: e.target.checked })}
                          className="accent-indigo-500" />
                        Planned (not yet built)
                      </label>
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">MTBF (hrs)</label>
                      <input className="w-full" type="number" placeholder="Default" value={editForm.mtbf_hours}
                        onChange={e => setEditForm({ ...editForm, mtbf_hours: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">MTTR (hrs)</label>
                      <input className="w-full" type="number" placeholder="Default" value={editForm.mttr_hours}
                        onChange={e => setEditForm({ ...editForm, mttr_hours: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Source IP</label>
                      <input className="w-full font-mono" placeholder="e.g. 192.168.1.0" value={editForm.source_ip || ''}
                        onChange={e => setEditForm({ ...editForm, source_ip: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Target IP</label>
                      <input className="w-full font-mono" placeholder="e.g. 192.168.1.1" value={editForm.target_ip || ''}
                        onChange={e => setEditForm({ ...editForm, target_ip: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Source IPv6</label>
                      <input className="w-full font-mono" placeholder="e.g. 2001:db8::1/127" value={editForm.source_ipv6 || ''}
                        onChange={e => setEditForm({ ...editForm, source_ipv6: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Target IPv6</label>
                      <input className="w-full font-mono" placeholder="e.g. 2001:db8::2/127" value={editForm.target_ipv6 || ''}
                        onChange={e => setEditForm({ ...editForm, target_ipv6: e.target.value })} />
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="text-[10px] text-surface-500">Link Color</label>
                      {editForm.color && (
                        <button className="text-[10px] text-red-400 hover:text-red-300" onClick={() => setEditForm({ ...editForm, color: '' })}>Clear</button>
                      )}
                    </div>
                    <ColorPalette value={editForm.color || ''} onChange={c => setEditForm({ ...editForm, color: c })} />
                  </div>
                  <div className="flex gap-2">
                    <button className="btn btn-primary btn-sm flex-1" onClick={saveEdit}><Save size={12} /> Save</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {links.length === 0 && (
          <div className="text-center py-8 text-surface-500 text-sm">
            No links yet. Use the form above or drag between nodes on the canvas.
          </div>
        )}
      </div>
    </div>
  );
}

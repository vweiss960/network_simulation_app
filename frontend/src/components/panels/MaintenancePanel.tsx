import { useState, useEffect, useMemo } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import toast from 'react-hot-toast';
import { Wrench, Play, AlertTriangle, CheckCircle2, XCircle, ArrowRight, Loader2, RotateCcw, Search, Save, Trash2, FolderOpen, ChevronDown, ChevronRight, FileText, Download } from 'lucide-react';
import { groupImpactByCustomer, downloadImpactHtml, downloadImpactCsv } from '../../utils/maintenanceImpact';

interface Preset {
  id: number;
  name: string;
  description: string;
  node_ids: number[];
  link_ids: number[];
}

export default function MaintenancePanel() {
  const {
    nodes, links, flows, customers, topologies, maintenanceResult, isMaintenanceRunning, isSimulating,
    maintenanceNodeIds, maintenanceLinkIds, currentTopologyId,
    toggleMaintenanceNode, toggleMaintenanceLink, clearMaintenance, applyMaintenance,
    setHighlightedFlowPaths, setSelectedLink, setSelectedFlow,
  } = useStore();
  const [searchQuery, setSearchQuery] = useState('');
  // Hide customer access nodes/links from the selection by default — maintenance
  // is normally scheduled on network infrastructure, not customer drops.
  const [includeCustomers, setIncludeCustomers] = useState(false);
  // Keyed "<group>:<flowId>" — a flow under two customers expands independently.
  const [expandedFlowKey, setExpandedFlowKey] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [presets, setPresets] = useState<Preset[]>([]);
  const [showPresets, setShowPresets] = useState(false);
  const [savePresetName, setSavePresetName] = useState('');
  const [showSaveForm, setShowSaveForm] = useState(false);

  const nodeMap = Object.fromEntries(nodes.map(n => [n.id, n.name]));
  const linkMap = Object.fromEntries(links.map(l => [l.id, `${nodeMap[l.source_node_id] || '?'} \u2194 ${nodeMap[l.target_node_id] || '?'}`]));

  const hasMaintenance = maintenanceNodeIds.size > 0 || maintenanceLinkIds.size > 0;

  // Load presets
  useEffect(() => {
    if (currentTopologyId) {
      api.listMaintenancePresets(currentTopologyId).then(setPresets).catch(() => {});
    }
  }, [currentTopologyId]);

  const handleSavePreset = async () => {
    if (!currentTopologyId || !savePresetName.trim()) { toast.error('Enter a preset name'); return; }
    try {
      const preset = await api.createMaintenancePreset(currentTopologyId, {
        name: savePresetName.trim(),
        node_ids: Array.from(maintenanceNodeIds),
        link_ids: Array.from(maintenanceLinkIds),
      });
      setPresets(prev => [...prev, preset]);
      setSavePresetName('');
      setShowSaveForm(false);
      toast.success(`Preset "${preset.name}" saved`);
    } catch { toast.error('Failed to save preset'); }
  };

  const handleLoadPreset = (preset: Preset) => {
    // Clear current and apply preset
    clearMaintenance();
    // Use setTimeout to ensure clear completes before setting new values
    setTimeout(() => {
      const store = useStore.getState();
      const newNodes = new Set(preset.node_ids.filter(id => nodes.some(n => n.id === id)));
      const newLinks = new Set(preset.link_ids.filter(id => links.some(l => l.id === id)));
      useStore.setState({ maintenanceNodeIds: newNodes, maintenanceLinkIds: newLinks });
      toast.success(`Loaded preset "${preset.name}"`);
    }, 50);
  };

  const handleDeletePreset = async (preset: Preset) => {
    if (!currentTopologyId) return;
    if (!confirm(`Delete preset "${preset.name}"?`)) return;
    try {
      await api.deleteMaintenancePreset(currentTopologyId, preset.id);
      setPresets(prev => prev.filter(p => p.id !== preset.id));
      toast.success('Preset deleted');
    } catch { toast.error('Failed to delete preset'); }
  };

  const handleApply = async () => {
    try {
      await applyMaintenance();
      toast.success('Maintenance applied — simulation updated');
    } catch {
      toast.error('Maintenance analysis failed');
    }
  };

  const handleClear = () => {
    clearMaintenance();
    toast('Maintenance cleared — run simulation to restore normal state');
  };

  const customerIds = new Set(nodes.filter(n => n.role === 'customer').map(n => n.id));
  const customerNodeCount = customerIds.size;
  const customerLinkCount = links.filter(l => customerIds.has(l.source_node_id) || customerIds.has(l.target_node_id)).length;
  const q = searchQuery.toLowerCase();
  const filteredNodes = nodes.filter(n =>
    (includeCustomers || !customerIds.has(n.id))
    && (!q || n.name.toLowerCase().includes(q)));
  const filteredLinks = links.filter(l =>
    (includeCustomers || (!customerIds.has(l.source_node_id) && !customerIds.has(l.target_node_id)))
    && (!q || (linkMap[l.id] || '').toLowerCase().includes(q)));

  const r = maintenanceResult;
  // Mirror the includeCustomers toggle in the RESULTS too: with it off,
  // customer-attached links are hidden from the selection lists above, so the
  // overload report shouldn't surface them either (customer drops are low-
  // bandwidth and would otherwise dominate the list).
  const customerLinkIds = new Set(
    links.filter(l => customerIds.has(l.source_node_id) || customerIds.has(l.target_node_id)).map(l => l.id));
  const visibleOverloaded: any[] = r
    ? (includeCustomers ? r.overloaded_links : r.overloaded_links.filter((l: any) => !customerLinkIds.has(l.link_id)))
    : [];

  const impactGroups = useMemo(
    () => (r ? groupImpactByCustomer(r.affected_flows, flows, nodes, customers) : []),
    [r, flows, nodes, customers]);
  const failedPathByFlow = useMemo(
    () => new Map((r?.failed_flow_paths ?? []).map(fp => [fp.flow_id, fp])),
    [r]);
  const flowById = useMemo(() => new Map(flows.map(fl => [fl.id, fl])), [flows]);
  const reportContext = () => r && {
    topologyName: topologies.find(t => t.id === currentTopologyId)?.name || 'Topology',
    result: r,
    groups: impactGroups,
  };
  const toggleGroup = (key: string) => setCollapsedGroups(prev => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-surface-500">
        Toggle nodes and links into maintenance mode. They turn red on the map, are excluded from simulations and Monte Carlo, and traffic is rerouted around them.
      </p>

      {/* Active maintenance banner */}
      {hasMaintenance && (
        <div className="bg-red-900/20 border border-red-700/40 rounded-lg px-3 py-2 flex items-center justify-between">
          <div className="text-xs text-red-400">
            <Wrench size={12} className="inline mr-1" />
            {maintenanceNodeIds.size} node{maintenanceNodeIds.size !== 1 ? 's' : ''}, {maintenanceLinkIds.size} link{maintenanceLinkIds.size !== 1 ? 's' : ''} in maintenance
          </div>
          <div className="flex items-center gap-2">
            <button className="text-[10px] text-primary-300 hover:text-primary-100 flex items-center gap-1"
              onClick={() => setShowSaveForm(true)} title="Save as preset">
              <Save size={10} /> Save
            </button>
            <button className="text-[10px] text-red-300 hover:text-red-100 flex items-center gap-1" onClick={handleClear}>
              <RotateCcw size={10} /> Clear
            </button>
          </div>
        </div>
      )}

      {/* Save preset form */}
      {showSaveForm && (
        <div className="bg-surface-800 rounded-lg p-2 space-y-2 fade-in"
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleSavePreset(); } }}>
          <div className="text-[10px] text-surface-400 font-medium">Save Failure Preset</div>
          <input className="w-full text-xs" placeholder="Preset name..."
            value={savePresetName} onChange={e => setSavePresetName(e.target.value)} autoFocus />
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1 text-[10px]" onClick={handleSavePreset}>
              <Save size={10} /> Save
            </button>
            <button className="btn btn-secondary btn-sm text-[10px]" onClick={() => { setShowSaveForm(false); setSavePresetName(''); }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Presets section */}
      {presets.length > 0 && (
        <div className="bg-surface-800 rounded-lg p-2">
          <div className="flex items-center justify-between cursor-pointer"
            onClick={() => setShowPresets(!showPresets)}>
            <div className="flex items-center gap-1 text-[10px] text-surface-400 font-medium">
              {showPresets ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              <FolderOpen size={10} />
              Failure Presets ({presets.length})
            </div>
          </div>
          {showPresets && (
            <div className="mt-1.5 space-y-1">
              {presets.map(p => {
                const nodeCount = p.node_ids.filter(id => nodes.some(n => n.id === id)).length;
                const linkCount = p.link_ids.filter(id => links.some(l => l.id === id)).length;
                return (
                  <div key={p.id} className="flex items-center justify-between bg-surface-900 rounded px-2 py-1.5 hover:bg-surface-700 transition-colors">
                    <div className="cursor-pointer flex-1" onClick={() => handleLoadPreset(p)}>
                      <div className="text-[11px] font-medium text-surface-200">{p.name}</div>
                      <div className="text-[9px] text-surface-500">
                        {nodeCount} node{nodeCount !== 1 ? 's' : ''}, {linkCount} link{linkCount !== 1 ? 's' : ''}
                      </div>
                    </div>
                    <button className="text-surface-500 hover:text-red-400 p-0.5 transition-colors"
                      onClick={() => handleDeletePreset(p)} title="Delete preset">
                      <Trash2 size={10} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Search */}
      <div className="relative">
        <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-surface-500" />
        <input className="w-full pl-8 text-xs" placeholder="Search nodes/links..."
          value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
      </div>

      {/* Include customer access in the selection (off by default) */}
      {(customerNodeCount > 0 || customerLinkCount > 0) && (
        <label className="flex items-center gap-2 text-[11px] text-surface-400 px-1 cursor-pointer">
          <input type="checkbox" checked={includeCustomers} onChange={e => setIncludeCustomers(e.target.checked)} className="accent-blue-500" />
          Include customer nodes &amp; links
          <span className="text-surface-600">({customerNodeCount} node{customerNodeCount !== 1 ? 's' : ''}, {customerLinkCount} link{customerLinkCount !== 1 ? 's' : ''} hidden)</span>
        </label>
      )}

      {/* Node selection */}
      <div className="bg-surface-800 rounded-lg p-2">
        <div className="text-[10px] text-surface-400 font-medium mb-1">
          Nodes
          {maintenanceNodeIds.size > 0 && <span className="text-red-400 ml-1">({maintenanceNodeIds.size} in maintenance)</span>}
        </div>
        <div className="max-h-32 overflow-y-auto space-y-0.5">
          {filteredNodes.map(n => {
            const inMaint = maintenanceNodeIds.has(n.id);
            return (
              <div key={n.id}
                className={`flex items-center gap-1.5 text-[11px] cursor-pointer px-1 rounded transition-colors ${
                  inMaint ? 'bg-red-900/30 text-red-400' : 'hover:bg-surface-700 text-surface-300'
                }`}
                onClick={() => toggleMaintenanceNode(n.id)}
              >
                <div className={`w-2.5 h-2.5 rounded border ${inMaint ? 'bg-red-500 border-red-400' : 'border-surface-500'}`} />
                {n.name}
                {inMaint && <span className="text-[9px] text-red-500 ml-auto">MAINT</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Link selection */}
      <div className="bg-surface-800 rounded-lg p-2">
        <div className="text-[10px] text-surface-400 font-medium mb-1">
          Links
          {maintenanceLinkIds.size > 0 && <span className="text-red-400 ml-1">({maintenanceLinkIds.size} in maintenance)</span>}
        </div>
        <div className="max-h-32 overflow-y-auto space-y-0.5">
          {filteredLinks.map(l => {
            const inMaint = maintenanceLinkIds.has(l.id);
            return (
              <div key={l.id}
                className={`flex items-center gap-1.5 text-[11px] cursor-pointer px-1 rounded transition-colors ${
                  inMaint ? 'bg-red-900/30 text-red-400' : 'hover:bg-surface-700 text-surface-300'
                }`}
                onClick={() => toggleMaintenanceLink(l.id)}
              >
                <div className={`w-2.5 h-2.5 rounded border ${inMaint ? 'bg-red-500 border-red-400' : 'border-surface-500'}`} />
                <span className="truncate">{linkMap[l.id]}</span>
                {inMaint && <span className="text-[9px] text-red-500 ml-auto shrink-0">MAINT</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Apply button */}
      <button className="btn btn-primary btn-sm w-full" onClick={async () => {
          if (hasMaintenance) {
            await handleApply();
          } else {
            // No maintenance — run normal simulation
            try {
              await useStore.getState().runSimulation();
              toast.success('Simulation updated — all elements active');
            } catch { toast.error('Simulation failed'); }
          }
        }}
        disabled={isMaintenanceRunning || isSimulating}>
        {isMaintenanceRunning || isSimulating
          ? <><Loader2 size={14} className="animate-spin" /> Simulating...</>
          : <><Play size={14} /> Apply and Re-simulate</>
        }
      </button>

      {/* Results */}
      {r && (
        <div className="space-y-3 fade-in">
          {/* Summary cards */}
          <div className="grid grid-cols-3 gap-2">
            <div className="bg-surface-800 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-red-400">{r.broken_count}</div>
              <div className="text-[10px] text-surface-500">Broken</div>
            </div>
            <div className="bg-surface-800 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-amber-400">{r.rerouted_count}</div>
              <div className="text-[10px] text-surface-500">Rerouted</div>
            </div>
            <div className="bg-surface-800 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-green-400">{r.unaffected_count}</div>
              <div className="text-[10px] text-surface-500">Unaffected</div>
            </div>
          </div>

          {/* Capacity status */}
          <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg ${visibleOverloaded.length ? 'bg-red-900/30 text-red-400' : 'bg-green-900/30 text-green-400'}`}>
            {visibleOverloaded.length ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
            {visibleOverloaded.length
              ? `Capacity exceeded on ${visibleOverloaded.length} link${visibleOverloaded.length !== 1 ? 's' : ''}`
              : 'Remaining capacity handles all rerouted traffic'
            }
          </div>

          {/* Impact report */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-surface-400 font-medium flex items-center gap-1"><FileText size={11} /> Impact report:</span>
            <button className="btn btn-secondary btn-sm text-[10px] flex-1"
              title="Printable report: maintenance scope, impacted customers with contact details, and their flows"
              onClick={() => { const ctx = reportContext(); if (ctx) downloadImpactHtml(ctx); }}>
              <Download size={10} /> HTML
            </button>
            <button className="btn btn-secondary btn-sm text-[10px] flex-1"
              title="One row per customer / impacted flow, for spreadsheets"
              onClick={() => { const ctx = reportContext(); if (ctx) downloadImpactCsv(ctx); }}>
              <Download size={10} /> CSV
            </button>
          </div>

          {/* Affected flows, grouped by customer. A flow between two customers'
              nodes appears under both; flows on no customer node go under Unassigned. */}
          {impactGroups.length > 0 && (
            <div className="bg-surface-800 rounded-lg p-2 space-y-1">
              <div className="text-[10px] text-surface-400 font-medium">
                Affected Flows by Customer: <span className="text-surface-600 font-normal">(click a flow to expand, highlight on map)</span>
              </div>
              {impactGroups.map(g => {
                const collapsed = collapsedGroups.has(g.key);
                const c = g.customer;
                return (
                  <div key={g.key} className="bg-surface-900/50 rounded">
                    <div className="flex items-center gap-1.5 px-1.5 py-1 cursor-pointer hover:bg-surface-700/60 rounded"
                      onClick={() => toggleGroup(g.key)}
                      title={c ? [c.contact_email, c.contact_phone].filter(Boolean).join(' · ') || undefined : undefined}>
                      {collapsed ? <ChevronRight size={10} className="text-surface-500" /> : <ChevronDown size={10} className="text-surface-500" />}
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: c ? (c.color || '#f59e0b') : '#64748b' }} />
                      <span className={`text-[11px] font-medium truncate ${c ? 'text-surface-200' : 'text-surface-400 italic'}`}>{c ? c.name : 'Unassigned'}</span>
                      <span className="ml-auto text-[9px] shrink-0">
                        {g.brokenCount > 0 && <span className="text-red-400">{g.brokenCount} broken</span>}
                        {g.brokenCount > 0 && g.reroutedCount > 0 && <span className="text-surface-600"> · </span>}
                        {g.reroutedCount > 0 && <span className="text-amber-400">{g.reroutedCount} rerouted</span>}
                      </span>
                    </div>
                    {!collapsed && (
                      <div className="pl-3 pr-1 pb-1 space-y-0.5">
                        {g.flows.map(f => {
                          const flowKey = `${g.key}:${f.flow_id}`;
                          const flowPath = failedPathByFlow.get(f.flow_id);
                          const flowData = flowById.get(f.flow_id);
                          const isExpanded = expandedFlowKey === flowKey;
                          return (
                            <div key={flowKey}>
                              <div
                                className={`text-[11px] flex items-center gap-1.5 cursor-pointer hover:bg-surface-700 px-1 rounded transition-colors ${isExpanded ? 'bg-surface-700/50' : ''}`}
                                onClick={() => {
                                  setExpandedFlowKey(isExpanded ? null : flowKey);
                                  if (flowPath) setHighlightedFlowPaths(flowPath);
                                  setSelectedFlow(f.flow_id);
                                }}
                              >
                                {f.status === 'broken'
                                  ? <XCircle size={10} className="text-red-400 shrink-0" />
                                  : <ArrowRight size={10} className="text-amber-400 shrink-0" />
                                }
                                <span className={`truncate ${f.status === 'broken' ? 'text-red-400' : 'text-amber-400'}`}>
                                  {f.flow_name}
                                </span>
                                <span className="text-surface-500 text-[10px] shrink-0">({f.status})</span>
                              </div>
                              {isExpanded && (
                                <div className="ml-5 mt-0.5 mb-1 text-[10px] space-y-0.5 bg-surface-900/60 rounded px-2 py-1">
                                  <div className="flex gap-2">
                                    <span className="text-surface-500">A-loc:</span>
                                    <span className="text-surface-300">{f.aLoc}</span>
                                  </div>
                                  <div className="flex gap-2">
                                    <span className="text-surface-500">Z-loc:</span>
                                    <span className="text-surface-300">{f.zLoc}</span>
                                  </div>
                                  {f.bandwidth_gbps != null && (
                                    <div className="flex gap-2">
                                      <span className="text-surface-500">Bandwidth:</span>
                                      <span className="text-surface-300">{f.bandwidth_gbps} Gbps</span>
                                    </div>
                                  )}
                                  {flowData?.description && (
                                    <div className="flex gap-2">
                                      <span className="text-surface-500">Description:</span>
                                      <span className="text-surface-300 break-words">{flowData.description}</span>
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Overloaded links */}
          {visibleOverloaded.length > 0 && (
            <div className="bg-surface-800 rounded-lg p-2 space-y-1">
              <div className="text-[10px] text-surface-400 font-medium">
                Overloaded Links: <span className="text-surface-600 font-normal">(newly overloaded by this window)</span>
              </div>
              {visibleOverloaded.map((l: any) => (
                <div
                  key={l.link_id}
                  className="text-[11px] text-red-400 flex items-center justify-between cursor-pointer hover:text-red-300 transition-colors"
                  onClick={() => setSelectedLink(l.link_id)}
                >
                  <span>{l.name}</span>
                  <span className="text-[10px]">
                    {l.used_gbps.toFixed(1)}G / {l.bandwidth_gbps}G
                    {l.baseline_utilization_pct != null
                      ? ` (${l.baseline_utilization_pct.toFixed(0)}% → ${l.utilization_pct.toFixed(0)}%)`
                      : ` (${l.utilization_pct.toFixed(0)}%)`}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Isolated nodes */}
          {r.isolated_node_names.length > 0 && (
            <div className="bg-surface-800 rounded-lg p-2 space-y-1">
              <div className="text-[10px] text-surface-400 font-medium">Isolated Nodes:</div>
              {r.isolated_node_names.map((name: string, i: number) => (
                <div key={i} className="text-[11px] text-amber-400">{name}</div>
              ))}
            </div>
          )}

          {/* Link utilization comparison */}
          {r.failed_utilizations.length > 0 && (() => {
            const baselineMap = Object.fromEntries(r.baseline_utilizations.map((l: any) => [l.link_id, l]));
            const changed = r.failed_utilizations
              .map((fl: any) => {
                const bl = baselineMap[fl.link_id];
                const beforePct = bl ? bl.utilization_pct : 0;
                const beforeGbps = bl ? bl.used_gbps : 0;
                const delta = fl.utilization_pct - beforePct;
                return { ...fl, beforePct, beforeGbps, delta };
              })
              .filter((l: any) => Math.abs(l.delta) > 0.01)
              .sort((a: any, b: any) => b.delta - a.delta);

            if (changed.length === 0) return null;

            return (
              <div className="bg-surface-800 rounded-lg p-2 space-y-1.5">
                <div className="text-[10px] text-surface-400 font-medium">Link Utilization Changes:</div>
                <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-2 gap-y-0.5 text-[10px]">
                  <div className="text-surface-500 font-medium">Link</div>
                  <div className="text-surface-500 font-medium text-right">Before</div>
                  <div className="text-surface-500 font-medium text-right">After</div>
                  <div className="text-surface-500 font-medium text-right">Delta</div>
                  {changed.map((l: any) => {
                    const utilColor = l.utilization_pct > 100 ? 'text-red-400'
                      : l.utilization_pct > 75 ? 'text-amber-400'
                      : 'text-surface-300';
                    const deltaColor = l.delta > 0 ? (l.utilization_pct > 100 ? 'text-red-400' : 'text-amber-400') : 'text-green-400';
                    return (
                      <div key={l.link_id} className="contents text-[11px] cursor-pointer group"
                        onClick={() => setSelectedLink(l.link_id)}>
                        <div className="text-surface-300 truncate group-hover:text-primary-400 transition-colors">
                          {linkMap[l.link_id] || `Link ${l.link_id}`}
                        </div>
                        <div className="text-right text-surface-400">
                          {l.beforeGbps.toFixed(1)}G <span className="text-surface-600">({l.beforePct.toFixed(0)}%)</span>
                        </div>
                        <div className={`text-right ${utilColor}`}>
                          {l.used_gbps.toFixed(1)}G <span className="opacity-70">({l.utilization_pct.toFixed(0)}%)</span>
                        </div>
                        <div className={`text-right ${deltaColor}`}>
                          {l.delta > 0 ? '+' : ''}{l.delta.toFixed(1)}%
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

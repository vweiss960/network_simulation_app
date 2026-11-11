import { useState, useRef, useEffect, useMemo } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import toast from 'react-hot-toast';
import { Plus, Trash2, Edit3, Save, Upload, Eye, Check, X, Route, Undo2, Activity, BarChart3, RefreshCw } from 'lucide-react';
import AvailabilityReportModal from '../AvailabilityReportModal';
import { SimulationResult, FlowData } from '../../types';
import { formatBandwidth, toGbps, fromGbps } from '../../utils/format';
import { nodeCustomerMap, flowCustomerIds } from '../../utils/customerFlows';
import FlowImportWizard from './FlowImportWizard';
import SearchBox, { MatchedIn } from '../SearchBox';
import Highlight from '../Highlight';
import { matchItem, type SearchField } from '../../utils/search';

const ROUTING_OPTIONS = [
  { value: 'shortest_path', label: 'Shortest Path (Cost)' },
  { value: 'optimized', label: 'Optimized (Dynamic Cost)' },
  { value: 'lowest_latency', label: 'Lowest Latency' },
] as const;

export default function FlowsPanel() {
  const {
    flows, nodes, links, customers, addFlow, updateFlow, deleteFlow, deleteAllFlows, includePlanned, setIncludePlanned, hidePlanned,
    selectedFlowId, setSelectedFlow, simulationResult, setHighlightedFlowPaths, highlightedFlowPaths,
    currentTopologyId, editRequested, analyzeFlowAvailability,
    isSimulating, isMonteCarlo, isMaintenanceRunning, isAnalyzingAvailability,
    pathBuilder, startPathBuilder, pathBuilderUndo, cancelPathBuilder, finishPathBuilder,
  } = useStore();
  // Protected topology not in edit mode → read-only: hide editing affordances
  // (Add/Edit/Delete, ECMP toggle, Apply routing). Analysis stays available.
  const canEdit = useStore(s => (s as any).canEditCurrentTopology()) as boolean;
  // Global analysis lock — block starting a new availability run while any
  // analysis (here or elsewhere) is in flight.
  const analysisBusy = isSimulating || isMonteCarlo || isMaintenanceRunning || isAnalyzingAvailability;
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showImportWizard, setShowImportWizard] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchScope, setSearchScope] = useState('all');
  const selectedRef = useRef<HTMLDivElement>(null);
  // Availability analysis state. analyzingId = flow currently running MC;
  // reportFlowId = flow whose cached report modal is open.
  const [analyzingId, setAnalyzingId] = useState<number | null>(null);
  const [reportFlowId, setReportFlowId] = useState<number | null>(null);

  const runAvailability = async (flowId: number) => {
    setAnalyzingId(flowId);
    try {
      await analyzeFlowAvailability(flowId);
      setReportFlowId(flowId);  // open the report when done
      toast.success('Availability analysis complete');
    } catch (e: any) {
      toast.error(`Analysis failed: ${e?.message || e}`);
    } finally {
      setAnalyzingId(null);
    }
  };

  // Auto-enter edit mode when requested (e.g. from right-click on flow in LinksPanel)
  useEffect(() => {
    if (selectedFlowId && editRequested) {
      const flow = flows.find(f => f.id === selectedFlowId);
      if (flow) startEdit(flow);
      setTimeout(() => selectedRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
    } else if (selectedFlowId) {
      setEditingId(null);
      setTimeout(() => selectedRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
    }
  }, [selectedFlowId, editRequested]);

  // Preview state
  const [previewMode, setPreviewMode] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<{ routing_behavior: string; flow_paths: any[]; link_utilizations: any[] } | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  const [form, setForm] = useState({
    name: '', source_node_id: '', dest_node_id: '',
    bandwidth_value: '1', bandwidth_unit: 'gbps' as 'gbps' | 'mbps',
    routing_behavior: 'shortest_path', ecmp_enabled: false,
  });
  const [editForm, setEditForm] = useState<any>({});

  const nodeName = (id: number) => nodes.find(n => n.id === id)?.name ?? `Node ${id}`;
  // Customer(s) owning the flow's endpoint nodes, e.g. "Acme / Globex".
  const nodeCustomer = useMemo(() => nodeCustomerMap(nodes), [nodes]);
  const customerNameById = useMemo(() => new Map(customers.map(c => [c.id, c.name])), [customers]);
  const flowCustomerNames = (f: FlowData) => flowCustomerIds(f, nodeCustomer)
    .map(cid => customerNameById.get(cid)).filter(Boolean).join(' / ');
  // Searchable fields (the chips under the search box).
  const nodeLocation = (id: number) => nodes.find(n => n.id === id)?.location;
  const flowFields: SearchField<FlowData>[] = [
    { key: 'name', label: 'Name', get: f => f.name },
    { key: 'description', label: 'Description', get: f => f.description },
    { key: 'source', label: 'Source', get: f => nodeName(f.source_node_id) },
    { key: 'dest', label: 'Destination', get: f => nodeName(f.dest_node_id) },
    { key: 'site', label: 'Site', get: f => [nodeLocation(f.source_node_id), nodeLocation(f.dest_node_id)] },
    { key: 'customer', label: 'Customer', get: f => flowCustomerNames(f) },
  ];
  const searchedFlows = flows
    .map(f => ({ f, match: matchItem(f, flowFields, searchQuery, searchScope) }))
    .filter(x => x.match.ok);

  const handleAdd = async () => {
    if (!form.name || !form.source_node_id || !form.dest_node_id) {
      toast.error('Name, source, and destination required');
      return;
    }
    try {
      const flowData: any = {
        name: form.name,
        source_node_id: parseInt(form.source_node_id),
        dest_node_id: parseInt(form.dest_node_id),
        bandwidth_gbps: toGbps(parseFloat(form.bandwidth_value) || 0, form.bandwidth_unit),
        routing_behavior: form.routing_behavior,
        ecmp_enabled: form.ecmp_enabled,
      };
      // Include explicit path if path builder was used
      if (form.routing_behavior === 'explicit') {
        const result = finishPathBuilder();
        if (result) {
          flowData.explicit_path = result.nodeIds;
          flowData.explicit_link_ids = result.linkIds;
        }
      }
      await addFlow(flowData);
      setForm({ name: '', source_node_id: '', dest_node_id: '', bandwidth_value: '1', bandwidth_unit: 'gbps', routing_behavior: 'shortest_path', ecmp_enabled: false });
      setShowAdd(false);
      toast.success('Flow added');
    } catch (e: any) {
      // Surface the backend's detail message (e.g. parent-flow name conflict, validation errors)
      let msg = 'Failed to add flow';
      try {
        const body = e?.message?.split(': ').slice(1).join(': ');
        const parsed = body ? JSON.parse(body) : null;
        if (parsed?.detail && typeof parsed.detail === 'string') msg = parsed.detail;
      } catch { /* fall through to default message */ }
      toast.error(msg);
    }
  };

  const startEdit = (f: any) => {
    setEditingId(f.id);
    const { value, unit } = fromGbps(f.bandwidth_gbps);
    setEditForm({
      name: f.name, description: f.description || '', source_node_id: String(f.source_node_id), dest_node_id: String(f.dest_node_id),
      source_interface: f.source_interface || '', dest_interface: f.dest_interface || '',
      bandwidth_value: String(value), bandwidth_unit: unit,
      routing_behavior: f.routing_behavior, ecmp_enabled: f.ecmp_enabled,
    });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      const original = flows.find(fl => fl.id === editingId);
      const srcNodeChanged = original && original.source_node_id !== parseInt(editForm.source_node_id);
      const dstNodeChanged = original && original.dest_node_id !== parseInt(editForm.dest_node_id);
      const data: any = {
        name: editForm.name,
        description: editForm.description,
        source_node_id: parseInt(editForm.source_node_id),
        dest_node_id: parseInt(editForm.dest_node_id),
        source_interface: srcNodeChanged ? '' : (editForm.source_interface || ''),
        dest_interface: dstNodeChanged ? '' : (editForm.dest_interface || ''),
        bandwidth_gbps: toGbps(parseFloat(editForm.bandwidth_value) || 0, editForm.bandwidth_unit),
        routing_behavior: editForm.routing_behavior,
        ecmp_enabled: editForm.ecmp_enabled,
      };
      if (editForm.routing_behavior === 'explicit' && pathBuilder.active) {
        const result = finishPathBuilder();
        if (result) {
          data.explicit_path = result.nodeIds;
          data.explicit_link_ids = result.linkIds;
        }
      } else if (editForm.routing_behavior !== 'explicit') {
        // Clear explicit path if switching away from explicit
        data.explicit_path = [];
        data.explicit_link_ids = [];
      }
      await updateFlow(editingId, data);
      setEditingId(null);
      cancelPathBuilder();
      toast.success('Flow updated');
    } catch (e: any) {
      let msg = 'Failed to update';
      try {
        const body = e?.message?.split(': ').slice(1).join(': ');
        const parsed = body ? JSON.parse(body) : null;
        if (parsed?.detail && typeof parsed.detail === 'string') msg = parsed.detail;
      } catch { /* fall through */ }
      toast.error(msg);
    }
  };

  // Preview routing
  const handlePreview = async (behavior: string) => {
    if (!currentTopologyId) return;
    if (previewMode === behavior) {
      // Toggle off
      setPreviewMode(null);
      setPreviewResult(null);
      return;
    }
    setIsPreviewLoading(true);
    setPreviewMode(behavior);
    try {
      const result = await api.previewRouting(currentTopologyId, behavior);
      setPreviewResult(result);
    } catch {
      toast.error('Preview failed');
      setPreviewMode(null);
      setPreviewResult(null);
    }
    setIsPreviewLoading(false);
  };

  // Apply routing to all flows
  const handleApplyRouting = async () => {
    if (!previewMode) return;
    try {
      await Promise.all(
        flows.map(f => updateFlow(f.id, { routing_behavior: previewMode }))
      );
      toast.success(`All flows switched to ${ROUTING_OPTIONS.find(r => r.value === previewMode)?.label}`);
      setPreviewMode(null);
      setPreviewResult(null);
    } catch {
      toast.error('Failed to apply routing');
    }
  };

  // Determine which flow paths to show — preview or actual simulation
  const activeFlowPaths = previewResult?.flow_paths || simulationResult?.flow_paths || [];

  // Get path info for a specific flow
  const getFlowPathInfo = (flowId: number) => {
    const fp = activeFlowPaths.find((p: any) => p.flow_id === flowId);
    if (!fp || !fp.paths.length) return null;
    return fp;
  };

  return (
    <div className="space-y-3">
      {canEdit && (
        <div className="flex gap-2">
          <button className="btn btn-primary btn-sm flex-1" onClick={() => setShowAdd(!showAdd)}>
            <Plus size={14} /> Add Flow
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => setShowImportWizard(true)}>
            <Upload size={14} /> Import
          </button>
        </div>
      )}

      {/* Include planned toggle */}
      <div
        className={`flex items-center justify-between bg-surface-800/60 rounded-lg px-3 py-2 cursor-pointer select-none transition-colors ${hidePlanned ? 'opacity-50' : 'hover:bg-surface-700/60'}`}
        onClick={() => { if (hidePlanned) { toast('Turn off "Hide Planned" in the Nodes panel first', { icon: 'i' }); } else setIncludePlanned(!includePlanned); }}
        title={hidePlanned ? 'Turn off "Hide Planned" in the Nodes panel to enable this toggle' : ''}
      >
        <span className="text-[11px] text-surface-300">Include Planned Nodes/Links {hidePlanned && <span className="text-[9px] text-surface-500">(hidden)</span>}</span>
        <div className={`relative w-8 h-[18px] rounded-full transition-colors ${includePlanned ? 'bg-indigo-600' : 'bg-surface-600'}`}>
          <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${includePlanned ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
        </div>
      </div>

      {showImportWizard && <FlowImportWizard onClose={() => setShowImportWizard(false)} />}

      {/* Search + Delete All */}
      <SearchBox
        query={searchQuery} onQuery={setSearchQuery}
        scope={searchScope} onScope={setSearchScope}
        fields={flowFields} shown={searchedFlows.length} total={flows.length} noun="flows"
      >
        {canEdit && flows.length > 0 && (
          <button
            className="btn btn-danger btn-sm text-[10px] shrink-0"
            onClick={() => {
              if (confirm(`Delete all ${flows.length} flows? This cannot be undone.`)) {
                deleteAllFlows();
                toast.success('All flows deleted');
              }
            }}
          >
            <Trash2 size={12} /> All
          </button>
        )}
      </SearchBox>

      {showAdd && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2 fade-in"
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAdd(); } }}>
          <input className="w-full" placeholder="Flow name" value={form.name}
            onChange={e => setForm({ ...form, name: e.target.value })} />
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">Source (A)</label>
              <select className="w-full" value={form.source_node_id}
                onChange={e => setForm({ ...form, source_node_id: e.target.value })}>
                <option value="">Select node...</option>
                {[...nodes].sort((a, b) => a.name.localeCompare(b.name)).map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-surface-500">Dest (Z)</label>
              <select className="w-full" value={form.dest_node_id}
                onChange={e => setForm({ ...form, dest_node_id: e.target.value })}>
                <option value="">Select node...</option>
                {[...nodes].sort((a, b) => a.name.localeCompare(b.name)).map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">Bandwidth</label>
              <div className="flex gap-1">
                <input className="flex-1 min-w-0" type="number" step="any" value={form.bandwidth_value}
                  onChange={e => setForm({ ...form, bandwidth_value: e.target.value })} />
                <select className="w-[72px] text-[11px]" value={form.bandwidth_unit}
                  onChange={e => setForm({ ...form, bandwidth_unit: e.target.value as 'gbps' | 'mbps' })}>
                  <option value="gbps">Gbps</option>
                  <option value="mbps">Mbps</option>
                </select>
              </div>
            </div>
            <div>
              <label className="text-[10px] text-surface-500">Routing</label>
              <select className="w-full" value={form.routing_behavior}
                onChange={e => setForm({ ...form, routing_behavior: e.target.value })}>
                <option value="shortest_path">Shortest Path</option>
                <option value="lowest_latency">Lowest Latency</option>
                <option value="optimized">Optimized (Dynamic Cost)</option>
                <option value="explicit">Explicit Path</option>
              </select>
            </div>
          </div>
          {/* Explicit path builder */}
          {form.routing_behavior === 'explicit' && form.source_node_id && form.dest_node_id && (
            <div className="bg-surface-900 rounded p-2 space-y-2">
              {!pathBuilder.active ? (
                <button
                  className="btn btn-sm w-full bg-emerald-600 text-white hover:bg-emerald-500"
                  onClick={() => startPathBuilder(parseInt(form.source_node_id), parseInt(form.dest_node_id))}
                >
                  <Route size={12} /> Build Path on Map
                </button>
              ) : (
                <div className="space-y-2">
                  <div className="text-[10px] text-emerald-400">
                    Path: {pathBuilder.nodeIds.map(id => nodeName(id)).join(' → ')}
                  </div>
                  <div className="text-[9px] text-surface-500">
                    Click links on the map to build the path from source to destination.
                  </div>
                  <div className="flex gap-1.5">
                    <button
                      className="btn btn-sm btn-secondary flex-1 text-[10px]"
                      onClick={pathBuilderUndo}
                      disabled={pathBuilder.nodeIds.length <= 1}
                    >
                      <Undo2 size={10} /> Undo
                    </button>
                    <button
                      className="btn btn-sm flex-1 text-[10px] bg-red-600/80 text-white hover:bg-red-500"
                      onClick={cancelPathBuilder}
                    >
                      Cancel
                    </button>
                  </div>
                  {pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1] === pathBuilder.destNodeId && (
                    <div className="text-[10px] text-emerald-300 font-medium">
                      Path complete! Click "Add" below to create the flow.
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {form.routing_behavior === 'explicit' && (!form.source_node_id || !form.dest_node_id) && (
            <div className="text-[9px] text-surface-500">Select source and destination nodes first to build an explicit path.</div>
          )}
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={form.ecmp_enabled}
              onChange={e => setForm({ ...form, ecmp_enabled: e.target.checked })}
              className="accent-primary-500" />
            Enable ECMP
          </label>
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleAdd}
              disabled={form.routing_behavior === 'explicit' && (!pathBuilder.nodeIds.length || pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1] !== parseInt(form.dest_node_id || '0'))}
            >Add</button>
            <button className="btn btn-secondary btn-sm" onClick={() => { setShowAdd(false); cancelPathBuilder(); }}>Cancel</button>
          </div>
        </div>
      )}

      {/* Preview routing section */}
      {flows.length > 0 && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2">
          <div className="text-[10px] text-surface-400 font-medium uppercase tracking-wide">Preview All Paths As</div>
          <div className="flex gap-1.5">
            {ROUTING_OPTIONS.map(opt => (
              <button
                key={opt.value}
                className={`btn btn-sm flex-1 text-[10px] px-2 ${
                  previewMode === opt.value
                    ? 'bg-violet-600 text-white'
                    : 'btn-secondary'
                }`}
                onClick={() => handlePreview(opt.value)}
                disabled={isPreviewLoading}
              >
                <Eye size={10} />
                {opt.label.split(' ')[0]}
              </button>
            ))}
          </div>

          {previewMode && previewResult && (
            <div className="space-y-2 fade-in">
              <div className="flex items-center gap-2 text-[10px]">
                <span className="text-violet-400 font-medium">
                  Previewing: {ROUTING_OPTIONS.find(r => r.value === previewMode)?.label}
                </span>
              </div>

              {/* Preview path summary */}
              <div className="max-h-[200px] overflow-y-auto space-y-0.5">
                {previewResult.flow_paths.map((fp: any) => {
                  const currentFp = simulationResult?.flow_paths.find((p: any) => p.flow_id === fp.flow_id);
                  const currentPath = currentFp?.paths?.[0]?.map((id: number) => nodeName(id)).join(' → ') || '-';
                  const previewPath = fp.paths?.[0]?.map((id: number) => nodeName(id)).join(' → ') || 'No path';
                  const changed = currentPath !== previewPath;

                  return (
                    <div key={fp.flow_id} className={`text-[10px] p-1.5 rounded ${changed ? 'bg-violet-900/20 border border-violet-800/30' : 'bg-surface-900/50'}`}>
                      <div className="flex justify-between">
                        <span className="font-medium">{fp.flow_name}</span>
                        {changed && <span className="text-violet-400 text-[9px]">CHANGED</span>}
                      </div>
                      {changed && (
                        <div className="text-surface-500 mt-0.5">
                          <div>Now: {currentPath}</div>
                          <div className="text-violet-300">New: {previewPath}</div>
                        </div>
                      )}
                      {!changed && (
                        <div className="text-surface-600">{previewPath}</div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Utilization comparison */}
              {previewResult.link_utilizations.some((lu: any) => lu.used_gbps > 0) && (
                <div className="text-[10px] text-surface-500">
                  Max util: {Math.max(...previewResult.link_utilizations.map((lu: any) => lu.utilization_pct))}%
                </div>
              )}

              <div className="flex gap-2">
                {canEdit && (
                  <button className="btn btn-sm flex-1 bg-violet-600 text-white hover:bg-violet-500" onClick={handleApplyRouting}>
                    <Check size={12} /> Apply to All Flows
                  </button>
                )}
                <button className="btn btn-secondary btn-sm" onClick={() => { setPreviewMode(null); setPreviewResult(null); }}>
                  <X size={12} /> Dismiss
                </button>
              </div>
            </div>
          )}

          {isPreviewLoading && (
            <div className="text-[10px] text-surface-500 text-center py-2">Loading preview...</div>
          )}
        </div>
      )}

      {/* Separator */}
      <div className="border-t border-surface-700" />

      {/* Flow list — scrollable */}
      <div className="space-y-1 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 320px)' }}>
        {searchedFlows.map(({ f, match }) => {
          // Highlight a field only when the search actually matched it.
          const hl = (key: string) => match.hits.some(h => h.field === key) ? searchQuery : '';
          const pathInfo = getFlowPathInfo(f.id);
          const custNames = flowCustomerNames(f);
          const hasNoPath = simulationResult && (!pathInfo || pathInfo.paths.length === 0 || pathInfo.paths.every((p: number[]) => p.length === 0));
          const srcNode = nodes.find(nd => nd.id === f.source_node_id);
          const dstNode = nodes.find(nd => nd.id === f.dest_node_id);
          const dependsOnPlanned = srcNode?.is_planned || dstNode?.is_planned;
          // Find planned links in this flow's path
          const plannedDeps: string[] = [];
          if (dependsOnPlanned) {
            if (srcNode?.is_planned) plannedDeps.push(`Node: ${srcNode.name}`);
            if (dstNode?.is_planned) plannedDeps.push(`Node: ${dstNode.name}`);
          }
          if (pathInfo) {
            for (const lids of (pathInfo.link_ids_per_path || [])) {
              for (const lid of lids) {
                const lnk = links.find(l => l.id === lid);
                if (lnk?.is_planned) {
                  const lname = `${nodeName(lnk.source_node_id)} \u2194 ${nodeName(lnk.target_node_id)}`;
                  if (!plannedDeps.includes(`Link: ${lname}`)) plannedDeps.push(`Link: ${lname}`);
                }
              }
            }
          }
          const hasPlannedDeps = dependsOnPlanned || plannedDeps.length > 0;

          return (
            <div
              key={f.id}
              ref={f.id === selectedFlowId ? selectedRef : undefined}
              className={`rounded-lg border px-3 py-2 ${
                hasNoPath
                  ? 'border-red-500 bg-red-900/20'
                  : hasPlannedDeps
                    ? 'border-indigo-500/50 bg-indigo-900/10'
                    : f.id === selectedFlowId
                      ? 'border-primary-500 bg-surface-800'
                    : 'border-surface-700 bg-surface-800/50'
              }`}
            >
              {editingId === f.id ? (
                <div className="space-y-2"
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); } }}>
                  <input className="w-full text-sm" value={editForm.name}
                    onChange={e => setEditForm({ ...editForm, name: e.target.value })} />
                  <textarea className="w-full text-xs resize-none" rows={2} placeholder="Description (optional)"
                    value={editForm.description}
                    onChange={e => setEditForm({ ...editForm, description: e.target.value })} />
                  <div className="grid grid-cols-2 gap-2">
                    <select className="w-full" value={editForm.source_node_id}
                      onChange={e => setEditForm({ ...editForm, source_node_id: e.target.value, source_interface: '' })}>
                      {[...nodes].sort((a, b) => a.name.localeCompare(b.name)).map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
                    </select>
                    <select className="w-full" value={editForm.dest_node_id}
                      onChange={e => setEditForm({ ...editForm, dest_node_id: e.target.value, dest_interface: '' })}>
                      {[...nodes].sort((a, b) => a.name.localeCompare(b.name)).map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
                    </select>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] text-surface-500">Source Interface</label>
                      <select className="w-full text-[11px]" value={editForm.source_interface || ''}
                        onChange={e => setEditForm({ ...editForm, source_interface: e.target.value })}>
                        <option value="">None</option>
                        {(() => {
                          const srcNode = nodes.find(nd => nd.id === parseInt(editForm.source_node_id));
                          return srcNode?.interfaces.filter(i => (i.status || 'free') !== 'reserved').map(i =>
                            <option key={i.name} value={i.name}>{i.name} ({i.speed_gbps}G)</option>
                          ) || [];
                        })()}
                      </select>
                    </div>
                    <div>
                      <label className="text-[10px] text-surface-500">Dest Interface</label>
                      <select className="w-full text-[11px]" value={editForm.dest_interface || ''}
                        onChange={e => setEditForm({ ...editForm, dest_interface: e.target.value })}>
                        <option value="">None</option>
                        {(() => {
                          const dstNode = nodes.find(nd => nd.id === parseInt(editForm.dest_node_id));
                          return dstNode?.interfaces.filter(i => (i.status || 'free') !== 'reserved').map(i =>
                            <option key={i.name} value={i.name}>{i.name} ({i.speed_gbps}G)</option>
                          ) || [];
                        })()}
                      </select>
                    </div>
                  </div>
                  <div className="space-y-2">
                    <div>
                      <label className="text-[10px] text-surface-500">Bandwidth</label>
                      <div className="flex gap-1">
                        <input className="flex-1 min-w-0" type="number" step="any" value={editForm.bandwidth_value}
                          onChange={e => setEditForm({ ...editForm, bandwidth_value: e.target.value })} />
                        <select className="w-[72px] text-[11px]" value={editForm.bandwidth_unit}
                          onChange={e => setEditForm({ ...editForm, bandwidth_unit: e.target.value })}>
                          <option value="gbps">Gbps</option>
                          <option value="mbps">Mbps</option>
                        </select>
                      </div>
                    </div>
                    <select className="w-full" value={editForm.routing_behavior}
                      onChange={e => setEditForm({ ...editForm, routing_behavior: e.target.value })}>
                      <option value="shortest_path">Shortest Path</option>
                      <option value="lowest_latency">Lowest Latency</option>
                      <option value="optimized">Optimized</option>
                      <option value="explicit">Explicit</option>
                    </select>
                  </div>
                  {/* Explicit path builder in edit mode */}
                  {editForm.routing_behavior === 'explicit' && editForm.source_node_id && editForm.dest_node_id && (
                    <div className="bg-surface-900 rounded p-2 space-y-2">
                      {f.explicit_path && f.explicit_path.length > 0 && !pathBuilder.active && (
                        <div className="text-[10px] text-surface-400">
                          Current path: {f.explicit_path.map((id: number) => nodeName(id)).join(' → ')}
                        </div>
                      )}
                      {!pathBuilder.active ? (
                        <button
                          className="btn btn-sm w-full bg-emerald-600 text-white hover:bg-emerald-500"
                          onClick={() => startPathBuilder(parseInt(editForm.source_node_id), parseInt(editForm.dest_node_id))}
                        >
                          <Route size={12} /> {f.explicit_path?.length ? 'Redefine Path on Map' : 'Build Path on Map'}
                        </button>
                      ) : (
                        <div className="space-y-2">
                          <div className="text-[10px] text-emerald-400">
                            Path: {pathBuilder.nodeIds.map(id => nodeName(id)).join(' → ')}
                          </div>
                          <div className="text-[9px] text-surface-500">
                            Click links on the map to build the path.
                          </div>
                          <div className="flex gap-1.5">
                            <button className="btn btn-sm btn-secondary flex-1 text-[10px]"
                              onClick={pathBuilderUndo} disabled={pathBuilder.nodeIds.length <= 1}>
                              <Undo2 size={10} /> Undo
                            </button>
                            <button className="btn btn-sm flex-1 text-[10px] bg-red-600/80 text-white hover:bg-red-500"
                              onClick={cancelPathBuilder}>
                              Cancel
                            </button>
                          </div>
                          {pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1] === pathBuilder.destNodeId && (
                            <div className="text-[10px] text-emerald-300 font-medium">
                              Path complete! Click "Save" below.
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  <label className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={editForm.ecmp_enabled}
                      onChange={e => setEditForm({ ...editForm, ecmp_enabled: e.target.checked })}
                      className="accent-primary-500" />
                    ECMP (split across equal-cost paths)
                  </label>
                  <div className="flex gap-2">
                    <button className="btn btn-primary btn-sm flex-1" onClick={saveEdit}><Save size={12} /> Save</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => { setEditingId(null); cancelPathBuilder(); }}>Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-between">
                  <div
                    className="cursor-pointer flex-1"
                    onClick={async () => {
                      setSelectedFlow(f.id === selectedFlowId ? null : f.id);
                      if (highlightedFlowPaths?.flow_id === f.id) {
                        setHighlightedFlowPaths(null);
                        return;
                      }
                      // Prefer a simulation/maintenance result if we have one
                      // for this flow — it already has utilization-aware
                      // routing and matches what the rest of the analytics show.
                      const fp = activeFlowPaths.find((p: any) => p.flow_id === f.id);
                      if (fp) { setHighlightedFlowPaths(fp); return; }
                      // No simulation has run (or no entry for this flow):
                      // ask the backend to route just this one flow so the
                      // user gets an instant preview without paying for a
                      // full topology-wide simulation.
                      if (!currentTopologyId) return;
                      try {
                        const computed = await api.computeFlowPath(currentTopologyId, f.id);
                        setHighlightedFlowPaths(computed);
                      } catch (e: any) {
                        toast.error(`Path compute failed: ${e?.message || e}`);
                      }
                    }}
                  >
                    <div className={`text-sm font-medium ${hasNoPath ? 'text-red-400' : highlightedFlowPaths?.flow_id === f.id ? 'text-amber-300' : ''}`}>
                      <Highlight text={f.name} query={hl('name')} />
                      {hasNoPath && <span className="text-[9px] ml-2 text-red-400 font-normal">NO PATH</span>}
                    </div>
                    {f.description && (
                      <div className="text-[9px] text-surface-500 mt-0.5"><Highlight text={f.description} query={hl('description')} /></div>
                    )}
                    <div className="text-[10px] text-surface-500 flex items-center gap-1 flex-wrap">
                      <span>
                        <Highlight text={nodeName(f.source_node_id)} query={hl('source')} />
                        {f.source_interface && <span className="text-primary-400 font-mono text-[9px]"> [{f.source_interface}]</span>}
                        {' → '}
                        <Highlight text={nodeName(f.dest_node_id)} query={hl('dest')} />
                        {f.dest_interface && <span className="text-primary-400 font-mono text-[9px]"> [{f.dest_interface}]</span>}
                      </span>
                      <span>| {formatBandwidth(f.bandwidth_gbps)} | {f.routing_behavior}</span>
                      {custNames && <span className="text-primary-400"> | <Highlight text={custNames} query={hl('customer')} /></span>}
                    </div>
                    <MatchedIn hits={match.hits} hidden={['site']} query={searchQuery} />
                    {pathInfo && pathInfo.paths.length > 0 && (
                      <div className="text-[9px] text-surface-600 mt-0.5">
                        Path: {pathInfo.paths[0].map((id: number) => nodeName(id)).join(' → ')}
                        {pathInfo.latency_per_path_ms?.[0] !== undefined && (
                          <span className="text-surface-500 ml-1">({pathInfo.latency_per_path_ms[0]}ms)</span>
                        )}
                      </div>
                    )}
                    {hasNoPath && (
                      <div className="text-[9px] text-red-400 mt-0.5">
                        {f.routing_behavior === 'explicit' ? 'Explicit path no longer valid — redefine the path' : 'No available path between source and destination'}
                      </div>
                    )}
                    {hasPlannedDeps && (
                      <div className="text-[9px] text-indigo-400 mt-0.5">
                        Depends on planned: {plannedDeps.join(', ')}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    {/* Availability analysis — available for all flows
                        (read-only Monte Carlo against the current network).
                        A green report button appears once a report exists. */}
                    {f.availability_report && (
                      <button
                        className="btn btn-ghost btn-sm p-1 text-green-400"
                        onClick={(e) => { e.stopPropagation(); setReportFlowId(f.id); }}
                        title={`View availability report (${(f.availability_report.availability * 100).toFixed(3)}%)`}
                      >
                        <BarChart3 size={12} />
                      </button>
                    )}
                    <button
                      className="btn btn-ghost btn-sm p-1 text-primary-400"
                      onClick={(e) => { e.stopPropagation(); runAvailability(f.id); }}
                      disabled={analysisBusy}
                      title={analysisBusy && analyzingId !== f.id
                        ? 'Another analysis is running — wait for it to finish'
                        : 'Analyze availability (Monte Carlo between source and destination)'}
                    >
                      {analyzingId === f.id ? <RefreshCw size={12} className="animate-spin" /> : <Activity size={12} />}
                    </button>
                    {f.is_inherited && (
                      <span className="text-[8px] bg-cyan-600/30 text-cyan-300 border border-cyan-500/40 px-1 py-0.5 rounded">INHERITED</span>
                    )}
                    {canEdit && !f.is_inherited && (
                      <>
                        <button
                          className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium transition-colors ${
                            f.ecmp_enabled
                              ? 'bg-emerald-600/30 text-emerald-300 border border-emerald-500/40'
                              : 'bg-surface-700/50 text-surface-500 border border-surface-600/40 hover:text-surface-300'
                          }`}
                          onClick={(e) => {
                            e.stopPropagation();
                            updateFlow(f.id, { ecmp_enabled: !f.ecmp_enabled });
                            toast.success(f.ecmp_enabled ? 'ECMP disabled' : 'ECMP enabled');
                          }}
                          title={f.ecmp_enabled ? 'ECMP enabled — click to disable' : 'ECMP disabled — click to enable'}
                        >
                          ECMP
                        </button>
                        <button className="btn btn-ghost btn-sm p-1" onClick={() => startEdit(f)}><Edit3 size={12} /></button>
                        <button
                          className="btn btn-ghost btn-sm p-1 text-red-400"
                          onClick={() => { if (confirm('Delete flow?')) { deleteFlow(f.id); toast.success('Deleted'); } }}
                        >
                          <Trash2 size={12} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {flows.length === 0 && (
          <div className="text-center py-8 text-surface-500 text-sm">
            No flows yet. Add demands to model traffic.
          </div>
        )}
      </div>

      <AvailabilityReportModal
        open={reportFlowId != null}
        report={flows.find(f => f.id === reportFlowId)?.availability_report || null}
        onClose={() => setReportFlowId(null)}
        onRerun={reportFlowId != null ? () => runAvailability(reportFlowId) : undefined}
        rerunning={analyzingId != null && analyzingId === reportFlowId}
      />
    </div>
  );
}

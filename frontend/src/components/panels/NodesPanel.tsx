import { useState, useEffect, useRef } from 'react';
import { useStore } from '../../stores/useStore';
import toast from 'react-hot-toast';
import { Plus, Trash2, Edit3, Save, X, ChevronDown, ChevronRight, Lock, Unlock } from 'lucide-react';
import { InterfaceSpec, NodeInstance } from '../../types';
import SearchBox, { MatchedIn } from '../SearchBox';
import Highlight from '../Highlight';
import { matchItem, type SearchField, type SearchHit } from '../../utils/search';
import { api } from '../../utils/api';
import NodeConfigAttributes from './NodeConfigAttributes';
import GeneratedConfigModal from '../GeneratedConfigModal';
import { FileText } from 'lucide-react';

const NODE_COLOR_PRESETS = [
  '#ef4444', '#f97316', '#f59e0b', '#eab308',
  '#84cc16', '#22c55e', '#14b8a6', '#06b6d4',
  '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7',
  '#d946ef', '#ec4899', '#f43f5e', '#b1b1b7',
];

export default function NodesPanel() {
  const {
    nodes, nodeTemplates, selectedNodeId, setSelectedNode,
    addNode, updateNode, deleteNode, editRequested,
    groupByLocation, toggleGroupByLocation, hidePlanned, setHidePlanned,
    links, setSelectedLink, setSidePanel,
    flows, setSelectedFlow, simulationResult,
    setHighlightedFlowPaths, highlightedFlowPaths,
    updateFlow, customers, currentTopologyId, multiSelectedNodeIds,
  } = useStore();
  // Protected topology not in edit mode → read-only: hide editing affordances.
  const canEdit = useStore(s => (s as any).canEditCurrentTopology()) as boolean;
  const [bulkColor, setBulkColor] = useState('#3b82f6');
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  // Feature flags drive whether the Config Design fields appear in the editor.
  const [features, setFeatures] = useState<{ config_design_enabled?: boolean } | null>(null);
  useEffect(() => { api.getFeatures().then(setFeatures).catch(() => setFeatures({})); }, []);
  // Open node id for the "View generated config" modal — null = closed.
  const [viewConfigNode, setViewConfigNode] = useState<{ id: number; name: string } | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchScope, setSearchScope] = useState('all');
  const selectedRef = useRef<HTMLDivElement>(null);
  // Interface action menu state: { nodeId, ifaceName }
  const [ifaceMenu, setIfaceMenu] = useState<{ nodeId: number; ifaceName: string } | null>(null);

  // Auto-expand (left-click) or auto-edit (right-click Edit) and scroll to selected node
  useEffect(() => {
    if (selectedNodeId) {
      const node = nodes.find(n => n.id === selectedNodeId);
      if (node) {
        setExpandedId(selectedNodeId);
        if (editRequested) {
          // Seed via startEdit — the ONE full-form seeder. A partial copy here
          // once drifted from it (missing RR/custom-attr fields), so canvas
          // right-click -> Edit showed RR unchecked and could wipe peers on save.
          startEdit(node);
        } else {
          setEditingId(null);
        }
      }
      setTimeout(() => {
        selectedRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 50);
    }
  }, [selectedNodeId, editRequested]);
  const [form, setForm] = useState({
    name: '', template_id: '',
    latitude: '', longitude: '',
    mtbf_hours: '', mttr_hours: '', location: '',
  });
  const [editForm, setEditForm] = useState<any>({});
  const [editInterfaces, setEditInterfaces] = useState<InterfaceSpec[]>([]);

  const handleAdd = async () => {
    if (!form.name.trim()) { toast.error('Name required'); return; }
    try {
      const data: any = { name: form.name };
      if (form.template_id) data.template_id = parseInt(form.template_id);
      if (form.latitude && form.longitude) {
        data.latitude = parseFloat(form.latitude);
        data.longitude = parseFloat(form.longitude);
      }
      if (form.mtbf_hours) data.mtbf_hours = parseFloat(form.mtbf_hours);
      if (form.mttr_hours) data.mttr_hours = parseFloat(form.mttr_hours);
      if (form.location) data.location = form.location;
      await addNode(data);
      setForm({ name: '', template_id: '', latitude: '', longitude: '', mtbf_hours: '', mttr_hours: '', location: '' });
      setShowAdd(false);
      toast.success('Node added');
    } catch (e: any) { toast.error(e?.message?.includes('409') ? 'A node with that name already exists' : 'Failed to add node'); }
  };

  // Searchable fields (the chips under the search box).
  const customerName = (n: NodeInstance) => n.customer_id ? customers.find(c => c.id === n.customer_id)?.name : undefined;
  const nodeFields: SearchField<NodeInstance>[] = [
    { key: 'name', label: 'Name', get: n => n.name },
    { key: 'site', label: 'Site', get: n => n.location },
    { key: 'vendor', label: 'Vendor / Model', get: n => `${n.vendor || ''} ${n.model || ''}` },
    { key: 'role', label: 'Role', get: n => n.role },
    { key: 'mgmt_ip', label: 'Mgmt IP', get: n => n.mgmt_ip },
    { key: 'customer', label: 'Customer', get: n => n.role === 'customer' ? customerName(n) : undefined },
  ];
  const searchedNodes = nodes
    .map(n => ({ n, match: matchItem(n, nodeFields, searchQuery, searchScope) }))
    .filter(x => x.match.ok);
  // Highlight a field only when the search actually matched it.
  const hl = (hits: SearchHit[], key: string) => hits.some(h => h.field === key) ? searchQuery : '';

  const startEdit = (n: any) => {
    setEditingId(n.id);
    setEditForm({
      name: n.name, vendor: n.vendor, model: n.model, platform: n.platform,
      latitude: n.latitude ?? '', longitude: n.longitude ?? '',
      geo_latitude: n.geo_latitude ?? '', geo_longitude: n.geo_longitude ?? '',
      mtbf_hours: n.mtbf_hours ?? '', mttr_hours: n.mttr_hours ?? '',
      capacity_gbps: n.capacity_gbps, location: n.location || '',
      prometheus_id: n.prometheus_id || '',
      mgmt_ip: n.mgmt_ip || '',
      is_planned: n.is_planned || false,
      role: n.role || 'router',
      customer_id: n.customer_id || '',
      color: n.color || '',
      // Config Design fields — round-trip through the edit form.
      is_route_reflector: !!n.is_route_reflector,
      is_super_route_reflector: !!(n as any).is_super_route_reflector,
      rr_peer_ids: Array.isArray(n.rr_peer_ids) ? [...n.rr_peer_ids] : [],
      custom_attrs: { ...(n.custom_attrs || {}) },
      custom_iface_refs: { ...(n.custom_iface_refs || {}) },
    });
    setEditInterfaces([...n.interfaces]);
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      const data: any = { ...editForm, interfaces: editInterfaces };
      if (data.mtbf_hours === '') data.mtbf_hours = null;
      else data.mtbf_hours = parseFloat(data.mtbf_hours);
      if (data.mttr_hours === '') data.mttr_hours = null;
      else data.mttr_hours = parseFloat(data.mttr_hours);
      if (data.latitude === '') data.latitude = null;
      else if (data.latitude != null) data.latitude = parseFloat(data.latitude);
      if (data.longitude === '') data.longitude = null;
      else if (data.longitude != null) data.longitude = parseFloat(data.longitude);
      if (data.geo_latitude === '') data.geo_latitude = null;
      else if (data.geo_latitude != null) data.geo_latitude = parseFloat(data.geo_latitude);
      if (data.geo_longitude === '') data.geo_longitude = null;
      else if (data.geo_longitude != null) data.geo_longitude = parseFloat(data.geo_longitude);
      data.capacity_gbps = parseFloat(data.capacity_gbps);
      data.customer_id = data.customer_id ? parseInt(data.customer_id) : 0;
      await updateNode(editingId, data);
      setEditingId(null);
      toast.success('Node updated');
    } catch (e: any) {
      // Surface the server's detail message verbatim when we hit a 409 — the
      // backend phrases conflicts well ("Value '100' for 'sr_node_sid' is
      // already used by 'PE3'") and the user needs that specificity.
      const msg = String(e?.message || '');
      if (msg.includes('409')) {
        const detail = msg.match(/"detail":"([^"]+)"/)?.[1];
        toast.error(detail || 'Save conflicts with another node — pick a different value');
      } else {
        toast.error('Failed to update');
      }
    }
  };

  const selected = nodes.find(n => n.id === selectedNodeId);

  return (
    <div className="space-y-3">
      {/* Add Node Button */}
      {canEdit && (
        <button className="btn btn-primary btn-sm w-full" onClick={() => setShowAdd(!showAdd)}>
          <Plus size={14} /> Add Node
        </button>
      )}

      {/* Group by location toggle */}
      <div
        className="flex items-center justify-between bg-surface-800/60 rounded-lg px-3 py-2 cursor-pointer select-none hover:bg-surface-700/60 transition-colors"
        onClick={toggleGroupByLocation}
      >
        <span className="text-[11px] text-surface-300">Group by Location</span>
        <div className={`relative w-8 h-[18px] rounded-full transition-colors ${groupByLocation ? 'bg-primary-600' : 'bg-surface-600'}`}>
          <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${groupByLocation ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
        </div>
      </div>

      {/* Hide planned toggle */}
      <div
        className="flex items-center justify-between bg-surface-800/60 rounded-lg px-3 py-2 cursor-pointer select-none hover:bg-surface-700/60 transition-colors"
        onClick={() => setHidePlanned(!hidePlanned)}
      >
        <span className="text-[11px] text-surface-300">Hide Planned</span>
        <div className={`relative w-8 h-[18px] rounded-full transition-colors ${hidePlanned ? 'bg-indigo-600' : 'bg-surface-600'}`}>
          <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${hidePlanned ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
        </div>
      </div>

      {/* Search */}
      <SearchBox
        query={searchQuery} onQuery={setSearchQuery}
        scope={searchScope} onScope={setSearchScope}
        fields={nodeFields} shown={searchedNodes.length} total={nodes.length} noun="nodes"
      />

      {/* Bulk color apply for multi-selected nodes (selection comes from the
          canvas — shift/ctrl-click or right-drag a box). Mirrors the Links panel. */}
      {canEdit && multiSelectedNodeIds.length > 1 && (
        <div className="bg-primary-900/30 border border-primary-500/40 rounded-lg p-3 space-y-2 fade-in">
          <div className="text-[11px] text-primary-200 font-medium">{multiSelectedNodeIds.length} nodes selected</div>
          <div className="flex flex-wrap gap-1">
            {NODE_COLOR_PRESETS.map(c => (
              <button key={c} className="w-5 h-5 rounded-sm border border-surface-600 hover:scale-110 transition-transform"
                style={{ backgroundColor: c, outline: bulkColor === c ? '2px solid white' : 'none', outlineOffset: '1px' }}
                onClick={() => setBulkColor(c)} />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button className="btn btn-primary btn-sm text-[10px] flex-1" onClick={async () => {
              if (!currentTopologyId) return;
              try {
                const updated = await api.bulkUpdateNodes(currentTopologyId, [...multiSelectedNodeIds], { color: bulkColor });
                useStore.setState((s: any) => ({
                  nodes: s.nodes.map((n: any) => updated.find((x: any) => x.id === n.id) || n),
                }));
                toast.success(`Color applied to ${updated.length} node${updated.length === 1 ? '' : 's'}`);
              } catch { toast.error('Failed to apply color'); }
            }}>
              Apply
            </button>
            <button className="btn btn-secondary btn-sm text-[10px]" onClick={async () => {
              if (!currentTopologyId) return;
              try {
                const updated = await api.bulkUpdateNodes(currentTopologyId, [...multiSelectedNodeIds], { color: '' });
                useStore.setState((s: any) => ({
                  nodes: s.nodes.map((n: any) => updated.find((x: any) => x.id === n.id) || n),
                }));
                toast.success('Color cleared');
              } catch { toast.error('Failed to clear color'); }
            }}>
              Clear
            </button>
          </div>
        </div>
      )}

      {/* Add Form */}
      {showAdd && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2 fade-in"
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAdd(); } }}>
          <input
            className="w-full" placeholder="Node name"
            value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
          />
          <select
            className="w-full"
            value={form.template_id}
            onChange={e => setForm({ ...form, template_id: e.target.value })}
          >
            <option value="">No template</option>
            {nodeTemplates.map(t => (
              <option key={t.id} value={t.id}>{t.vendor} {t.model} ({t.platform})</option>
            ))}
          </select>
          <input
            className="w-full" placeholder="Location (e.g. NYC, LAX, Dallas-DC1)"
            value={form.location} onChange={e => setForm({ ...form, location: e.target.value })}
          />
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">Latitude</label>
              <input
                className="w-full" type="number" step="0.0001" placeholder="e.g. 40.7128"
                value={form.latitude} onChange={e => setForm({ ...form, latitude: e.target.value })}
              />
            </div>
            <div>
              <label className="text-[10px] text-surface-500">Longitude</label>
              <input
                className="w-full" type="number" step="0.0001" placeholder="e.g. -74.0060"
                value={form.longitude} onChange={e => setForm({ ...form, longitude: e.target.value })}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">MTBF (hrs)</label>
              <input
                className="w-full" type="number" placeholder="Default"
                value={form.mtbf_hours} onChange={e => setForm({ ...form, mtbf_hours: e.target.value })}
              />
            </div>
            <div>
              <label className="text-[10px] text-surface-500">MTTR (hrs)</label>
              <input
                className="w-full" type="number" placeholder="Default"
                value={form.mttr_hours} onChange={e => setForm({ ...form, mttr_hours: e.target.value })}
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleAdd}>Add</button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowAdd(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Drag hint */}
      <p className="text-[10px] text-surface-500 px-1">
        Tip: Drag templates from the Templates panel onto the canvas to add nodes
      </p>

      {/* Separator */}
      <div className="border-t border-surface-700" />

      {/* Node List — scrollable */}
      <div className="space-y-1 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 320px)' }}>
        {searchedNodes.map(({ n, match }) => (
          <div
            key={n.id}
            ref={n.id === selectedNodeId ? selectedRef : undefined}
            className={`rounded-lg border transition-colors ${
              n.id === selectedNodeId
                ? 'border-primary-500 bg-surface-800'
                : 'border-surface-700 bg-surface-800/50 hover:bg-surface-800'
            }`}
          >
            <div
              className="flex items-center justify-between px-3 py-2 cursor-pointer"
              onClick={() => {
                setSelectedNode(n.id === selectedNodeId ? null : n.id);
                setExpandedId(n.id === expandedId ? null : n.id);
              }}
            >
              <div className="min-w-0">
              <div className="flex items-center gap-2">
                {expandedId === n.id ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                {n.color && <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: n.color }} />}
                <span className="text-sm font-medium"><Highlight text={n.name} query={hl(match.hits, 'name')} /></span>
                {n.role === 'customer' && <span className="text-[8px] bg-amber-600/30 text-amber-300 border border-amber-500/40 px-1 py-0.5 rounded"><Highlight text={customerName(n) || 'CUSTOMER'} query={hl(match.hits, 'customer')} /></span>}
                {n.is_planned && <span className="text-[8px] bg-indigo-600/30 text-indigo-300 border border-indigo-500/40 px-1 py-0.5 rounded">PLANNED</span>}
                {n.is_inherited && <span className="text-[8px] bg-cyan-600/30 text-cyan-300 border border-cyan-500/40 px-1 py-0.5 rounded">INHERITED</span>}
                {n.location && <span className="text-[10px] text-primary-400"><Highlight text={n.location} query={hl(match.hits, 'site')} /></span>}
                <span className="text-[10px] text-surface-500"><Highlight text={`${n.vendor || ''} ${n.model || ''}`} query={hl(match.hits, 'vendor')} /></span>
              </div>
              <div className="ml-5">
                <MatchedIn hits={match.hits} hidden={['role', 'mgmt_ip']} query={searchQuery} />
              </div>
              </div>
              <div className="flex gap-1">
                {canEdit && !n.is_inherited && (
                  <>
                    <button className="btn btn-ghost btn-sm p-1" onClick={e => { e.stopPropagation(); startEdit(n); }}>
                      <Edit3 size={12} />
                    </button>
                    <button
                      className="btn btn-ghost btn-sm p-1 text-red-400"
                      onClick={e => {
                        e.stopPropagation();
                        if (confirm('Delete node?')) { deleteNode(n.id); toast.success('Deleted'); }
                      }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </>
                )}
              </div>
            </div>

            {/* Expanded details */}
            {expandedId === n.id && editingId !== n.id && (
              <div className="px-3 pb-2 text-xs space-y-1 border-t border-surface-700 pt-2">
                <div className="grid grid-cols-2 gap-x-4">
                  <span className="text-surface-500">Location:</span><span>{n.location || '-'}</span>
                  <span className="text-surface-500">Lat/Lng:</span>
                  <span>{n.latitude != null ? `${n.latitude}, ${n.longitude}` : '-'}</span>
                  <span className="text-surface-500">Geo Lat/Lng:</span>
                  <span>{n.geo_latitude != null ? `${n.geo_latitude}, ${n.geo_longitude}` : '-'}</span>
                  <span className="text-surface-500">Mgmt IP:</span><span className="font-mono">{n.mgmt_ip || '-'}</span>
                  <span className="text-surface-500">Platform:</span><span>{n.platform || '-'}</span>
                  <span className="text-surface-500">Capacity:</span><span>{n.capacity_gbps} Gbps</span>
                  <span className="text-surface-500">MTBF:</span><span>{n.mtbf_hours ?? 'Default'} hrs</span>
                  <span className="text-surface-500">MTTR:</span><span>{n.mttr_hours ?? 'Default'} hrs</span>
                </div>
                {n.interfaces.length > 0 && (() => {
                  // Build interface → link mapping for this node
                  const ifaceLinkMap: Record<string, { linkId: number; peerName: string }> = {};
                  for (const l of links) {
                    if (l.source_node_id === n.id && l.source_interface) {
                      const peer = nodes.find(nd => nd.id === l.target_node_id);
                      ifaceLinkMap[l.source_interface] = { linkId: l.id, peerName: peer?.name || `Node ${l.target_node_id}` };
                    }
                    if (l.target_node_id === n.id && l.target_interface) {
                      const peer = nodes.find(nd => nd.id === l.source_node_id);
                      ifaceLinkMap[l.target_interface] = { linkId: l.id, peerName: peer?.name || `Node ${l.source_node_id}` };
                    }
                  }
                  // Build interface → flow mapping for this node
                  const ifaceFlowMap: Record<string, { flowId: number; flowName: string; direction: string }[]> = {};
                  for (const f of flows) {
                    if (f.source_node_id === n.id && f.source_interface) {
                      if (!ifaceFlowMap[f.source_interface]) ifaceFlowMap[f.source_interface] = [];
                      const dest = nodes.find(nd => nd.id === f.dest_node_id);
                      ifaceFlowMap[f.source_interface].push({ flowId: f.id, flowName: f.name, direction: `→ ${dest?.name || '?'}` });
                    }
                    if (f.dest_node_id === n.id && f.dest_interface) {
                      if (!ifaceFlowMap[f.dest_interface]) ifaceFlowMap[f.dest_interface] = [];
                      const src = nodes.find(nd => nd.id === f.source_node_id);
                      ifaceFlowMap[f.dest_interface].push({ flowId: f.id, flowName: f.name, direction: `← ${src?.name || '?'}` });
                    }
                  }

                  // Flows that touch this node (for association menu)
                  const nodeFlowsForAssoc = flows.filter(f => f.source_node_id === n.id || f.dest_node_id === n.id);

                  const setReserved = async (iface: InterfaceSpec, reserved: boolean) => {
                    const updatedInterfaces = n.interfaces.map(i =>
                      i.name === iface.name ? { ...i, status: reserved ? 'reserved' : 'free' } : i
                    );
                    try {
                      await updateNode(n.id, { interfaces: updatedInterfaces });
                    } catch { toast.error('Failed to update interface'); }
                  };

                  const associateFlow = async (flowId: number, ifaceName: string) => {
                    const f = flows.find(fl => fl.id === flowId);
                    if (!f) return;
                    const isSource = f.source_node_id === n.id;
                    try {
                      await updateFlow(flowId, isSource
                        ? { source_interface: ifaceName }
                        : { dest_interface: ifaceName }
                      );
                      toast.success(`Associated "${f.name}" with ${ifaceName}`);
                    } catch { toast.error('Failed to associate flow'); }
                  };

                  const disassociateFlow = async (flowId: number) => {
                    const f = flows.find(fl => fl.id === flowId);
                    if (!f) return;
                    const isSource = f.source_node_id === n.id;
                    try {
                      await updateFlow(flowId, isSource
                        ? { source_interface: '' }
                        : { dest_interface: '' }
                      );
                    } catch { toast.error('Failed to disassociate flow'); }
                  };

                  const isMenuOpen = (ifaceName: string) =>
                    ifaceMenu?.nodeId === n.id && ifaceMenu?.ifaceName === ifaceName;

                  return (
                    <div className="mt-2">
                      <span className="text-surface-500">Interfaces:</span>
                      <div className="mt-1 space-y-0.5">
                        {n.interfaces.map((iface, i) => {
                          const linkInfo = ifaceLinkMap[iface.name];
                          const flowInfo = ifaceFlowMap[iface.name];
                          const isReserved = (iface.status || 'free') === 'reserved';
                          const hasFlows = !!flowInfo && flowInfo.length > 0;
                          // Color: reserved=amber, link=emerald, flow-associated=primary, free=default
                          const nameColor = isReserved ? 'text-amber-400' : linkInfo ? 'text-emerald-400' : hasFlows ? 'text-primary-400' : '';
                          const speedColor = isReserved ? 'text-amber-500/70' : linkInfo ? 'text-emerald-500/70' : hasFlows ? 'text-primary-500/70' : 'text-surface-500';
                          const menuOpen = isMenuOpen(iface.name);
                          // Unassociated flows on this node for this interface
                          const unassociatedFlows = nodeFlowsForAssoc.filter(f => {
                            const isSource = f.source_node_id === n.id;
                            const currentIface = isSource ? f.source_interface : f.dest_interface;
                            return !currentIface; // only show flows not yet assigned to any interface
                          });
                          return (
                            <div key={i} className="relative">
                              <div
                                className={`flex justify-between items-center rounded px-1 -mx-1 cursor-pointer hover:bg-surface-700/60 transition-colors`}
                                onClick={linkInfo ? (e) => {
                                  e.stopPropagation();
                                  setSelectedLink(linkInfo.linkId);
                                  setSidePanel('nodes');
                                } : (e) => {
                                  e.stopPropagation();
                                  // Toggle action menu (reserve / associate flow) —
                                  // all mutations, so only when editable.
                                  if (menuOpen) {
                                    setIfaceMenu(null);
                                  } else if (canEdit) {
                                    setIfaceMenu({ nodeId: n.id, ifaceName: iface.name });
                                  }
                                }}
                                title={linkInfo ? `Link → ${linkInfo.peerName}` : 'Click for options'}
                              >
                                <div className="flex items-center gap-1">
                                  {isReserved && <Lock size={9} className="text-amber-400" />}
                                  <span className={`font-mono text-[10px] ${nameColor}`}>
                                    {iface.name}
                                  </span>
                                </div>
                                <div className="flex items-center gap-2">
                                  {isReserved && !hasFlows && <span className="text-[9px] text-amber-500">reserved</span>}
                                  {hasFlows && <span className="text-[9px] text-primary-500">{flowInfo!.length} demand{flowInfo!.length > 1 ? 's' : ''}</span>}
                                  {linkInfo && (
                                    <span className="text-[9px] text-emerald-600 truncate max-w-[80px]">
                                      → {linkInfo.peerName}
                                    </span>
                                  )}
                                  <span className={`text-[10px] ${speedColor}`}>
                                    {iface.speed_gbps}G
                                  </span>
                                </div>
                              </div>

                              {/* Action dropdown menu */}
                              {menuOpen && !linkInfo && (
                                <div className="ml-3 mt-0.5 mb-1 bg-surface-700 rounded border border-surface-600 shadow-lg overflow-hidden z-10"
                                  onClick={e => e.stopPropagation()}>
                                  {/* Reserve / Free */}
                                  <button
                                    className="w-full text-left px-2.5 py-1.5 text-[10px] hover:bg-surface-600 transition-colors flex items-center gap-1.5"
                                    onClick={() => {
                                      setReserved(iface, !isReserved);
                                      setIfaceMenu(null);
                                    }}
                                  >
                                    {isReserved ? <Unlock size={10} className="text-surface-400" /> : <Lock size={10} className="text-amber-400" />}
                                    <span>{isReserved ? 'Free interface' : 'Reserve interface'}</span>
                                  </button>

                                  {/* Associate with demand — shows submenu of unassociated flows */}
                                  {unassociatedFlows.length > 0 && (
                                    <>
                                      <div className="border-t border-surface-600" />
                                      <div className="px-2.5 py-1 text-[9px] text-surface-400 uppercase tracking-wide">Associate with demand</div>
                                      {unassociatedFlows.map(f => {
                                        const isSource = f.source_node_id === n.id;
                                        const otherNode = nodes.find(nd => nd.id === (isSource ? f.dest_node_id : f.source_node_id));
                                        return (
                                          <button
                                            key={f.id}
                                            className="w-full text-left px-2.5 py-1 text-[10px] hover:bg-surface-600 transition-colors flex items-center justify-between"
                                            onClick={() => {
                                              associateFlow(f.id, iface.name);
                                              setIfaceMenu(null);
                                            }}
                                          >
                                            <span className="text-primary-300 truncate max-w-[120px]">{f.name}</span>
                                            <span className="text-[8px] text-surface-500">{isSource ? '→' : '←'} {otherNode?.name || '?'}</span>
                                          </button>
                                        );
                                      })}
                                    </>
                                  )}

                                  {/* Disassociate existing flows */}
                                  {hasFlows && (
                                    <>
                                      <div className="border-t border-surface-600" />
                                      <div className="px-2.5 py-1 text-[9px] text-surface-400 uppercase tracking-wide">Disassociate demand</div>
                                      {flowInfo!.map(fi => (
                                        <button
                                          key={fi.flowId}
                                          className="w-full text-left px-2.5 py-1 text-[10px] hover:bg-surface-600 transition-colors flex items-center justify-between"
                                          onClick={() => {
                                            disassociateFlow(fi.flowId);
                                            setIfaceMenu(null);
                                          }}
                                        >
                                          <span className="text-red-400 truncate max-w-[120px]">{fi.flowName}</span>
                                          <span className="text-[8px] text-surface-500">{fi.direction}</span>
                                        </button>
                                      ))}
                                    </>
                                  )}
                                </div>
                              )}

                              {/* Show associated flows summary under the interface */}
                              {hasFlows && !menuOpen && (() => {
                                const MAX_SHOW = 2;
                                const shown = flowInfo!.slice(0, MAX_SHOW);
                                const remaining = flowInfo!.length - MAX_SHOW;
                                return (
                                  <>
                                    {shown.map(fi => (
                                      <div
                                        key={fi.flowId}
                                        className="flex items-center gap-1.5 pl-4 cursor-pointer hover:bg-surface-700/40 rounded transition-colors"
                                        onClick={e => {
                                          e.stopPropagation();
                                          setSelectedFlow(fi.flowId, true);
                                          setSidePanel('flows');
                                        }}
                                      >
                                        <span className="text-[9px] text-primary-400 truncate max-w-[100px]">{fi.flowName}</span>
                                        <span className="text-[8px] text-surface-500">{fi.direction}</span>
                                      </div>
                                    ))}
                                    {remaining > 0 && (
                                      <div className="pl-4 text-[8px] text-surface-500">+{remaining} more demand{remaining > 1 ? 's' : ''}</div>
                                    )}
                                  </>
                                );
                              })()}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}
                {/* Flows/Demands connected to this node */}
                {(() => {
                  const nodeFlows = flows.filter(f => f.source_node_id === n.id || f.dest_node_id === n.id);
                  if (!nodeFlows.length) return null;
                  return (
                    <div className="mt-2">
                      <span className="text-surface-500">Flows / Demands ({nodeFlows.length}):</span>
                      <div className="mt-1 space-y-0.5">
                        {nodeFlows.map(f => {
                          const srcNode = nodes.find(nd => nd.id === f.source_node_id);
                          const dstNode = nodes.find(nd => nd.id === f.dest_node_id);
                          const isSource = f.source_node_id === n.id;
                          const isHighlighted = highlightedFlowPaths?.flow_id === f.id;
                          // Check if flow is associated with an interface on this node
                          const hasIfaceAssoc = isSource ? !!f.source_interface : !!f.dest_interface;
                          const ifaceName = isSource ? f.source_interface : f.dest_interface;
                          return (
                            <div key={f.id}>
                              <div
                                className={`flex justify-between items-center rounded px-1 -mx-1 cursor-pointer transition-colors ${
                                  isHighlighted ? 'bg-amber-900/30 text-amber-300' : 'hover:bg-surface-700/60'
                                }`}
                                onClick={e => {
                                  e.stopPropagation();
                                  if (isHighlighted) {
                                    setHighlightedFlowPaths(null);
                                  } else if (simulationResult) {
                                    const fp = simulationResult.flow_paths.find((p: any) => p.flow_id === f.id);
                                    if (fp) setHighlightedFlowPaths(fp);
                                  }
                                }}
                                onContextMenu={e => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  setSelectedFlow(f.id, canEdit);  // read-only (no edit form) when not editable
                                  setSidePanel('flows');
                                }}
                              >
                                <span className={`text-[10px] truncate max-w-[140px] ${
                                  isHighlighted ? 'text-amber-300' : hasIfaceAssoc ? 'text-primary-400' : 'text-orange-400'
                                }`}>{f.name}</span>
                                <span className={`text-[9px] truncate max-w-[120px] ${
                                  isHighlighted ? 'text-amber-400/70' : 'text-surface-500'
                                }`}>
                                  {hasIfaceAssoc && <span className="font-mono text-[8px] text-primary-500 mr-1">[{ifaceName}]</span>}
                                  {isSource ? `→ ${dstNode?.name || '?'}` : `← ${srcNode?.name || '?'}`}
                                </span>
                              </div>
                              {!hasIfaceAssoc && (
                                <div className="pl-4 text-[8px] text-orange-400/70">not associated with an interface</div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}

            {/* Edit form */}
            {editingId === n.id && (
              <div className="px-3 pb-3 space-y-2 border-t border-surface-700 pt-2"
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); } }}>
                <input
                  className="w-full text-sm" value={editForm.name}
                  onChange={e => setEditForm({ ...editForm, name: e.target.value })}
                />
                <div className="grid grid-cols-2 gap-2">
                  <input className="w-full" placeholder="Vendor" value={editForm.vendor}
                    onChange={e => setEditForm({ ...editForm, vendor: e.target.value })} />
                  <input className="w-full" placeholder="Model" value={editForm.model}
                    onChange={e => setEditForm({ ...editForm, model: e.target.value })} />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input className="w-full" placeholder="Platform" value={editForm.platform}
                    onChange={e => setEditForm({ ...editForm, platform: e.target.value })} />
                  <input className="w-full" placeholder="Location" value={editForm.location}
                    onChange={e => setEditForm({ ...editForm, location: e.target.value })} />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-surface-500">Prometheus ID</label>
                    <input className="w-full font-mono" placeholder="e.g. 10.24.110.11"
                      value={editForm.prometheus_id || ''}
                      onChange={e => setEditForm({ ...editForm, prometheus_id: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-[10px] text-surface-500">Management IP</label>
                    <input className="w-full font-mono" placeholder="e.g. 10.24.110.11"
                      value={editForm.mgmt_ip || ''}
                      onChange={e => setEditForm({ ...editForm, mgmt_ip: e.target.value })} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 items-center">
                  <label className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={editForm.is_planned || false}
                      onChange={e => setEditForm({ ...editForm, is_planned: e.target.checked })}
                      className="accent-indigo-500" />
                    Planned (not yet built)
                  </label>
                  <div>
                    <label className="text-[10px] text-surface-500">Role</label>
                    <select className="w-full text-xs" value={editForm.role || 'router'}
                      onChange={e => setEditForm({ ...editForm, role: e.target.value })}>
                      <option value="router">Router</option>
                      <option value="customer">Customer</option>
                      <option value="sat_dish">Satellite Dish</option>
                      <option value="sat_pop">Satellite PoP</option>
                    </select>
                  </div>
                </div>
                {editForm.role === 'customer' && customers.length > 0 && (
                  <div>
                    <label className="text-[10px] text-surface-500">Assigned Customer</label>
                    <select className="w-full text-xs" value={editForm.customer_id || ''}
                      onChange={e => setEditForm({ ...editForm, customer_id: e.target.value })}>
                      <option value="">No customer assigned</option>
                      {[...customers].sort((a, b) => a.name.localeCompare(b.name)).map(c => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                    <p className="text-[9px] text-surface-500 mt-0.5">Flows that start or end on this node belong to this customer.</p>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-surface-500">Latitude</label>
                    <input className="w-full" type="number" step="0.0001" placeholder="e.g. 40.7128" value={editForm.latitude}
                      onChange={e => setEditForm({ ...editForm, latitude: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-[10px] text-surface-500">Longitude</label>
                    <input className="w-full" type="number" step="0.0001" placeholder="e.g. -74.0060" value={editForm.longitude}
                      onChange={e => setEditForm({ ...editForm, longitude: e.target.value })} />
                  </div>
                </div>
                {/* Geographic (real-world) coordinates for the map view —
                    independent of the logical lat/long above. */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-surface-500">Geo Latitude</label>
                    <input className="w-full" type="number" step="0.0001" placeholder="map placement" value={editForm.geo_latitude}
                      onChange={e => setEditForm({ ...editForm, geo_latitude: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-[10px] text-surface-500">Geo Longitude</label>
                    <input className="w-full" type="number" step="0.0001" placeholder="map placement" value={editForm.geo_longitude}
                      onChange={e => setEditForm({ ...editForm, geo_longitude: e.target.value })} />
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="text-[10px] text-surface-500">Capacity</label>
                    <input className="w-full" type="number" value={editForm.capacity_gbps}
                      onChange={e => setEditForm({ ...editForm, capacity_gbps: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-[10px] text-surface-500">MTBF</label>
                    <input className="w-full" type="number" placeholder="Default" value={editForm.mtbf_hours}
                      onChange={e => setEditForm({ ...editForm, mtbf_hours: e.target.value })} />
                  </div>
                  <div>
                    <label className="text-[10px] text-surface-500">MTTR</label>
                    <input className="w-full" type="number" placeholder="Default" value={editForm.mttr_hours}
                      onChange={e => setEditForm({ ...editForm, mttr_hours: e.target.value })} />
                  </div>
                </div>

                {/* Color */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-[10px] text-surface-500">Color</label>
                    {editForm.color && (
                      // preventDefault on mousedown keeps focus inside the edit
                      // form (this button unmounts on clear), so Enter still
                      // reaches the form's save-on-Enter handler.
                      <button className="text-[10px] text-red-400 hover:text-red-300"
                        onMouseDown={e => e.preventDefault()}
                        onClick={() => setEditForm({ ...editForm, color: '' })}>Clear</button>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {NODE_COLOR_PRESETS.map(c => (
                      <button
                        key={c}
                        className="w-5 h-5 rounded-sm border border-surface-600 hover:scale-110 transition-transform"
                        style={{ backgroundColor: c, outline: editForm.color === c ? '2px solid white' : 'none', outlineOffset: '1px' }}
                        onMouseDown={e => e.preventDefault()}
                        onClick={() => setEditForm({ ...editForm, color: c })}
                      />
                    ))}
                  </div>
                </div>

                {/* Interface editor */}
                <div>
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-[10px] text-surface-500">Interfaces</span>
                    <button
                      className="btn btn-ghost btn-sm text-[10px] p-0.5"
                      onClick={() => setEditInterfaces([...editInterfaces, { name: '', speed_gbps: 10, description: '' }])}
                    >
                      <Plus size={10} /> Add
                    </button>
                  </div>
                  {editInterfaces.map((iface, i) => {
                    // Full flow list for this interface in edit mode
                    const ifaceFlows = flows.filter(f => {
                      if (f.source_node_id === n.id && f.source_interface === iface.name) return true;
                      if (f.dest_node_id === n.id && f.dest_interface === iface.name) return true;
                      return false;
                    });
                    return (
                      <div key={i} className="mb-1.5">
                        <div className="flex gap-1 items-center">
                          <input
                            className="flex-1 text-[11px]" placeholder="Interface name"
                            value={iface.name}
                            onChange={e => {
                              const updated = [...editInterfaces];
                              updated[i] = { ...updated[i], name: e.target.value };
                              setEditInterfaces(updated);
                            }}
                          />
                          <input
                            className="w-16 text-[11px]" type="number" placeholder="Gbps"
                            value={iface.speed_gbps}
                            onChange={e => {
                              const updated = [...editInterfaces];
                              updated[i] = { ...updated[i], speed_gbps: parseFloat(e.target.value) || 0 };
                              setEditInterfaces(updated);
                            }}
                          />
                          <button
                            className={`btn btn-ghost btn-sm p-0.5 ${(iface.status || 'free') === 'reserved' ? 'text-amber-400' : 'text-surface-500'}`}
                            title={(iface.status || 'free') === 'reserved' ? 'Reserved — click to free' : 'Free — click to reserve'}
                            onClick={() => {
                              const updated = [...editInterfaces];
                              updated[i] = { ...updated[i], status: (iface.status || 'free') === 'reserved' ? 'free' : 'reserved' };
                              setEditInterfaces(updated);
                            }}
                          >
                            {(iface.status || 'free') === 'reserved' ? <Lock size={10} /> : <Unlock size={10} />}
                          </button>
                          <button
                            className="btn btn-ghost btn-sm p-0.5 text-red-400"
                            onClick={() => setEditInterfaces(editInterfaces.filter((_, j) => j !== i))}
                          >
                            <X size={10} />
                          </button>
                        </div>
                        {/* Full flow list for this interface */}
                        {ifaceFlows.length > 0 && (
                          <div className="ml-2 mt-0.5 space-y-0.5">
                            {ifaceFlows.map(f => {
                              const isSource = f.source_node_id === n.id;
                              const otherNode = nodes.find(nd => nd.id === (isSource ? f.dest_node_id : f.source_node_id));
                              return (
                                <div key={f.id} className="flex items-center justify-between text-[9px] pl-1 rounded hover:bg-surface-700/40">
                                  <span className="text-primary-400 truncate max-w-[100px]">{f.name}</span>
                                  <span className="text-surface-500">{isSource ? '→' : '←'} {otherNode?.name || '?'}</span>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                <NodeConfigAttributes
                  enabled={!!features?.config_design_enabled}
                  nodeId={n.id}
                  allNodes={nodes}
                  interfaces={editInterfaces}
                  editForm={editForm}
                  setEditForm={updater => setEditForm(prev => updater(prev))}
                />

                {features?.config_design_enabled && editForm.role !== 'customer' && (
                  <button
                    className="btn btn-ghost btn-sm text-[11px] w-full text-amber-300 border border-amber-700/40 hover:bg-amber-900/20"
                    onClick={() => setViewConfigNode({ id: n.id, name: n.name })}
                    title="Open the most recent rendered config saved on this device"
                  >
                    <FileText size={11} /> View generated config
                  </button>
                )}

                <div className="flex gap-2">
                  <button className="btn btn-primary btn-sm flex-1" onClick={saveEdit}>
                    <Save size={12} /> Save
                  </button>
                  <button className="btn btn-secondary btn-sm" onClick={() => setEditingId(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        {nodes.length === 0 && (
          <div className="text-center py-8 text-surface-500 text-sm">
            No nodes yet. Add one above or drag a template onto the canvas.
          </div>
        )}
      </div>

      <GeneratedConfigModal
        nodeId={viewConfigNode?.id ?? null}
        nodeName={viewConfigNode?.name}
        onClose={() => setViewConfigNode(null)}
      />
    </div>
  );
}

import { useState } from 'react';
import { useStore } from '../../stores/useStore';
import toast from 'react-hot-toast';
import { api } from '../../utils/api';
import { nodeCustomerMap, flowCustomerIds, customerLinkUtilization } from '../../utils/customerFlows';
import { formatBandwidth } from '../../utils/format';
import { Plus, Trash2, Edit3, Save, X, Search, ChevronDown, ChevronRight, Eye, EyeOff } from 'lucide-react';

export default function CustomerPanel() {
  const {
    customers, flows, nodes, addCustomer, updateCustomer, deleteCustomer, hideCustomers, setHideCustomers,
    hiddenCustomerIds, toggleCustomerVisibility, currentTopologyId,
    simulationResult, highlightedFlowPaths, setHighlightedFlowPaths, setSelectedFlow, setSidePanel, requestCenterNode, viewMode,
  } = useStore();
  const canEdit = useStore(s => s.canEditCurrentTopology());
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const [form, setForm] = useState({ name: '', contact_email: '', contact_phone: '', notes: '', color: '#f59e0b' });
  const [editForm, setEditForm] = useState<any>({});

  // Ancestor-owned customers (chain-scoped list): visible with their merged
  // nodes/flows, but edit/delete belongs to the parent topology.
  const isInherited = (c: { topology_id: number }) => currentTopologyId != null && c.topology_id !== currentTopologyId;

  // All nodes with role=customer
  const allCustomerNodes = nodes.filter(n => n.role === 'customer');
  const customersHidden = hideCustomers && hiddenCustomerIds.size > 0;
  // Nodes for a specific customer
  const customerNodes = (custId: number) => nodes.filter(n => n.customer_id === custId && n.role === 'customer');

  const handleAdd = async () => {
    if (!form.name.trim()) { toast.error('Customer name required'); return; }
    try {
      await addCustomer(form);
      setForm({ name: '', contact_email: '', contact_phone: '', notes: '', color: '#f59e0b' });
      setShowAdd(false);
      toast.success('Customer added');
    } catch { toast.error('Failed to add customer'); }
  };

  const startEdit = (c: any) => {
    setEditingId(c.id);
    setEditForm({ name: c.name, contact_email: c.contact_email, contact_phone: c.contact_phone, notes: c.notes, color: c.color || '' });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      await updateCustomer(editingId, editForm);
      setEditingId(null);
      toast.success('Customer updated');
    } catch { toast.error('Failed to update'); }
  };

  const nodeName = (id: number) => nodes.find(n => n.id === id)?.name ?? `Node ${id}`;

  // A customer's flows are those starting or ending on one of its nodes — an
  // A-end/Z-end flow between two customers shows under both.
  const nodeCustomer = nodeCustomerMap(nodes);
  const flowsForCustomer = (custId: number) => flows.filter(f => flowCustomerIds(f, nodeCustomer).includes(custId));

  // Same toggle/compute behavior as the "Flows on this link" list in LinksPanel.
  const toggleFlowHighlight = async (flowId: number) => {
    if (highlightedFlowPaths?.flow_id === flowId) { setHighlightedFlowPaths(null); return; }
    const fp = simulationResult?.flow_paths.find(p => p.flow_id === flowId);
    if (fp) { setHighlightedFlowPaths(fp); return; }
    if (!currentTopologyId) return;
    try {
      setHighlightedFlowPaths(await api.computeFlowPath(currentTopologyId, flowId));
    } catch (e: any) {
      toast.error(`Path compute failed: ${e?.message || e}`);
    }
  };

  const filtered = searchQuery
    ? customers.filter(c => c.name.toLowerCase().includes(searchQuery.toLowerCase()))
    : customers;

  return (
    <div className="space-y-3">
      <button className="btn btn-primary btn-sm w-full" onClick={() => setShowAdd(!showAdd)}>
        <Plus size={14} /> Add Customer
      </button>

      {showAdd && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2 fade-in"
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAdd(); } }}>
          <input className="w-full" placeholder="Customer name" value={form.name}
            onChange={e => setForm({ ...form, name: e.target.value })} />
          <div className="grid grid-cols-2 gap-2">
            <input className="w-full" placeholder="Email" value={form.contact_email}
              onChange={e => setForm({ ...form, contact_email: e.target.value })} />
            <input className="w-full" placeholder="Phone" value={form.contact_phone}
              onChange={e => setForm({ ...form, contact_phone: e.target.value })} />
          </div>
          <input className="w-full" placeholder="Notes" value={form.notes}
            onChange={e => setForm({ ...form, notes: e.target.value })} />
          <div className="flex items-center gap-2">
            <label className="text-[10px] text-surface-500 whitespace-nowrap">Color</label>
            <input type="color" value={form.color} className="w-8 h-6 rounded cursor-pointer border-0 bg-transparent"
              onChange={e => setForm({ ...form, color: e.target.value })} />
            <span className="text-[10px] text-surface-500">{form.color}</span>
          </div>
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleAdd}>Add</button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowAdd(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Search */}
      <div className="relative">
        <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-surface-500" />
        <input className="w-full pl-8 text-xs" placeholder="Search customers..."
          value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
      </div>

      {/* Visibility toggle — reflects what is actually hidden: the master flag
          can be on (it defaults on) while the snapshot set is empty, e.g. when
          the topology had no customer nodes at load time. */}
      {allCustomerNodes.length > 0 && (
        <div
          className="flex items-center justify-between bg-surface-800/60 rounded-lg px-3 py-2 cursor-pointer select-none hover:bg-surface-700/60 transition-colors"
          onClick={() => setHideCustomers(!customersHidden)}
        >
          <span className="text-[11px] text-surface-300">Hide Customer Nodes</span>
          <div className={`relative w-8 h-[18px] rounded-full transition-colors ${customersHidden ? 'bg-amber-600' : 'bg-surface-600'}`}>
            <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${customersHidden ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
          </div>
        </div>
      )}

      {customers.length > 0 && <CustomerUtilizationSection defaultId={expandedId} />}

      {/* Customer list */}
      <div className="space-y-1">
        {filtered.map(c => {
          const customerFlows = flowsForCustomer(c.id);
          const custNodes = customerNodes(c.id);
          const isExpanded = expandedId === c.id;
          const isHidden = hiddenCustomerIds.has(c.id);

          return (
            <div key={c.id} className={`rounded-lg border px-3 py-2 ${
              isExpanded ? 'border-primary-500 bg-surface-800' : 'border-surface-700 bg-surface-800/50'
            }`}>
              {editingId === c.id ? (
                <div className="space-y-2"
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(); } }}>
                  <input className="w-full text-sm" value={editForm.name}
                    onChange={e => setEditForm({ ...editForm, name: e.target.value })} />
                  <div className="grid grid-cols-2 gap-2">
                    <input className="w-full text-xs" placeholder="Email" value={editForm.contact_email}
                      onChange={e => setEditForm({ ...editForm, contact_email: e.target.value })} />
                    <input className="w-full text-xs" placeholder="Phone" value={editForm.contact_phone}
                      onChange={e => setEditForm({ ...editForm, contact_phone: e.target.value })} />
                  </div>
                  <input className="w-full text-xs" placeholder="Notes" value={editForm.notes}
                    onChange={e => setEditForm({ ...editForm, notes: e.target.value })} />
                  <div className="flex items-center gap-2">
                    <label className="text-[10px] text-surface-500 whitespace-nowrap">Color</label>
                    <input type="color" value={editForm.color || '#f59e0b'} className="w-8 h-6 rounded cursor-pointer border-0 bg-transparent"
                      onChange={e => setEditForm({ ...editForm, color: e.target.value })} />
                    <span className="text-[10px] text-surface-500">{editForm.color || '#f59e0b'}</span>
                  </div>
                  <div className="flex gap-2">
                    <button className="btn btn-primary btn-sm flex-1" onClick={saveEdit}><Save size={12} /> Save</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between">
                    <div className="cursor-pointer flex-1" onClick={() => setExpandedId(isExpanded ? null : c.id)}>
                      <div className="flex items-center gap-1">
                        {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                        {c.color && <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: c.color }} />}
                        <span className="text-sm font-medium">{c.name}</span>
                        {isInherited(c) && (
                          <span className="text-[9px] px-1 py-0.5 rounded bg-surface-700 text-surface-400" title="Owned by the parent topology — edit it there">
                            inherited
                          </span>
                        )}
                        <span className="text-[10px] text-surface-500 ml-1">({custNodes.length} nodes, {customerFlows.length} flows)</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      {custNodes.length > 0 && (
                        <button className={`btn btn-ghost btn-sm p-1 ${isHidden ? 'text-surface-600' : 'text-amber-400'}`}
                          title={isHidden ? `Show ${c.name} nodes` : `Hide ${c.name} nodes`}
                          onClick={() => toggleCustomerVisibility(c.id)}>
                          {isHidden ? <EyeOff size={12} /> : <Eye size={12} />}
                        </button>
                      )}
                      {!isInherited(c) && (
                        <>
                          <button className="btn btn-ghost btn-sm p-1" onClick={() => startEdit(c)}><Edit3 size={12} /></button>
                          <button className="btn btn-ghost btn-sm p-1 text-red-400"
                            onClick={() => { if (confirm(`Delete ${c.name}? Flows will be unassigned.`)) { deleteCustomer(c.id); toast.success('Deleted'); } }}>
                            <Trash2 size={12} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="mt-2 space-y-2 text-xs border-t border-surface-700 pt-2">
                      <div className="grid grid-cols-2 gap-x-4">
                        {c.contact_email && <><span className="text-surface-500">Email:</span><span>{c.contact_email}</span></>}
                        {c.contact_phone && <><span className="text-surface-500">Phone:</span><span>{c.contact_phone}</span></>}
                      </div>
                      {c.notes && <div className="text-surface-400 text-[10px]">{c.notes}</div>}

                      {/* Assigned nodes */}
                      {custNodes.length > 0 && (
                        <div>
                          <div className="text-[10px] text-surface-400 font-medium mb-1">Customer Nodes ({custNodes.length}):</div>
                          <div className="space-y-0.5 max-h-24 overflow-y-auto">
                            {custNodes.map(n => (
                              <div key={n.id}
                                className={`text-[10px] px-1 rounded hover:bg-surface-700 flex justify-between ${viewMode === 'logical' ? 'cursor-pointer' : ''}`}
                                title={viewMode === 'logical' ? 'Click to center on this node' : undefined}
                                onClick={() => { if (viewMode === 'logical') requestCenterNode(n.id); }}>
                                <span>{n.name}</span>
                                <span className="text-surface-500">{n.location || ''}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Flows on this customer's nodes */}
                      <div>
                        <div className="text-[10px] text-surface-400 font-medium mb-1">Flows ({customerFlows.length}):</div>
                        {customerFlows.length > 0 ? (
                          <div className="space-y-0.5 max-h-32 overflow-y-auto">
                            {customerFlows.map(f => {
                              const isHighlighted = highlightedFlowPaths?.flow_id === f.id;
                              return (
                                <div key={f.id}
                                  className={`flex justify-between text-[10px] px-1 rounded cursor-pointer transition-colors ${
                                    isHighlighted ? 'bg-amber-900/30 text-amber-300' : 'hover:bg-surface-700'
                                  }`}
                                  onClick={() => toggleFlowHighlight(f.id)}
                                  onContextMenu={e => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    setSelectedFlow(f.id, canEdit);  // read-only (no edit form) when not editable
                                    setSidePanel('flows');
                                  }}
                                  title={canEdit ? 'Click to highlight · right-click to edit this flow' : 'Click to highlight · right-click to view this flow'}>
                                  <span>{f.name}</span>
                                  <span className="text-surface-500">{nodeName(f.source_node_id)} → {nodeName(f.dest_node_id)}</span>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div className="text-[10px] text-surface-500">No flows start or end on this customer's nodes.</div>
                        )}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
        {customers.length === 0 && (
          <div className="text-center py-8 text-surface-500 text-sm">
            No customers yet. Add customers, then assign customer nodes to them in the Nodes panel — flows on those nodes belong to the customer.
          </div>
        )}
      </div>
    </div>
  );
}


/** Switch + customer picker: color the canvas by the load from only the
 *  selected customers' flows (TopologyEditor dims the other links). */
function CustomerUtilizationSection({ defaultId }: { defaultId: number | null }) {
  const {
    customers, flows, nodes, links, simulationResult,
    customerUtilEnabled, customerUtilIds, setCustomerUtilEnabled, setCustomerUtilIds,
    setSelectedLink, setSidePanel,
  } = useStore();
  const nodeCustomer = nodeCustomerMap(nodes);
  const flowCount = (cid: number) => flows.filter(f => flowCustomerIds(f, nodeCustomer).includes(cid)).length;
  const selected = new Set(customerUtilIds);
  const toggle = (cid: number) => setCustomerUtilIds(
    selected.has(cid) ? customerUtilIds.filter(id => id !== cid) : [...customerUtilIds, cid]);
  const nodeName = (id: number) => nodes.find(n => n.id === id)?.name ?? `Node ${id}`;

  const util = customerUtilEnabled ? customerLinkUtilization(simulationResult, flows, nodes, links, customerUtilIds) : {};
  const used = Object.values(util);
  const flowIds = new Set(used.flatMap(u => u.flow_ids));
  const totalGbps = flows.filter(f => flowIds.has(f.id)).reduce((sum, f) => sum + f.bandwidth_gbps, 0);
  const busiest = used.reduce<typeof used[number] | null>((b, u) => (!b || u.utilization_pct > b.utilization_pct ? u : b), null);

  return (
    <div className="bg-surface-800/60 rounded-lg">
      <div
        className="flex items-center justify-between px-3 py-2 cursor-pointer select-none hover:bg-surface-700/60 rounded-lg transition-colors"
        onClick={() => {
          const on = !customerUtilEnabled;
          setCustomerUtilEnabled(on);
          if (on && customerUtilIds.length === 0 && defaultId != null) setCustomerUtilIds([defaultId]);
        }}
      >
        <span className="text-[11px] text-surface-300">Show Customer Link Utilization</span>
        <div className={`relative w-8 h-[18px] rounded-full transition-colors ${customerUtilEnabled ? 'bg-primary-600' : 'bg-surface-600'}`}>
          <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${customerUtilEnabled ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
        </div>
      </div>
      {customerUtilEnabled && (
        <div className="px-3 pb-2 space-y-2">
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-surface-500">Customers</span>
            <button className="text-primary-400 hover:text-primary-300" onClick={() => setCustomerUtilIds(customers.map(c => c.id))}>All</button>
            <button className="text-primary-400 hover:text-primary-300" onClick={() => setCustomerUtilIds([])}>None</button>
          </div>
          <div className="max-h-40 overflow-y-auto space-y-0.5">
            {[...customers].sort((a, b) => a.name.localeCompare(b.name)).map(c => (
              <label key={c.id} className="flex items-center gap-2 text-xs cursor-pointer hover:bg-surface-700/40 rounded px-1 py-0.5">
                <input type="checkbox" className="accent-primary-500" checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                {c.color && <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: c.color }} />}
                <span className="flex-1 truncate">{c.name}</span>
                <span className="text-[10px] text-surface-500">{flowCount(c.id)} flows</span>
              </label>
            ))}
          </div>
          {customerUtilIds.length === 0 ? (
            <p className="text-[10px] text-surface-500">Select customers to color the links their flows use.</p>
          ) : !simulationResult ? (
            <p className="text-[10px] text-amber-400">Run the simulation to see utilization.</p>
          ) : used.length === 0 ? (
            <p className="text-[10px] text-surface-500">No routed flows for the selected customers.</p>
          ) : (
            <div className="text-[10px] text-surface-400 space-y-0.5 border-t border-surface-700 pt-1.5">
              <div>{flowIds.size} flows · {formatBandwidth(totalGbps)} · {used.length} links (others dimmed)</div>
              {busiest && (
                <button className="text-left hover:text-surface-200" title="Select this link"
                  onClick={() => { setSelectedLink(busiest.link_id); setSidePanel('links'); }}>
                  Busiest: {nodeName(busiest.source_node_id)} ↔ {nodeName(busiest.target_node_id)}{' '}
                  <span className={busiest.utilization_pct >= 100 ? 'text-red-400' : 'text-surface-200'}>{busiest.utilization_pct}%</span>
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

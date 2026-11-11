import { useState } from 'react';
import { useStore } from '../../stores/useStore';
import toast from 'react-hot-toast';
import { Plus, Trash2, Edit3, Save, X, ChevronDown, ChevronRight, Layers, AlertTriangle, Play } from 'lucide-react';
import { SrlgGroup } from '../../types';

const SRLG_TYPES = ['conduit', 'building', 'power', 'fiber_span', 'custom'];

export default function SrlgPanel() {
  const { srlgs, nodes, links, addSrlg, updateSrlg, deleteSrlg, srlgAnalysisResult, runSrlgAnalysis, setIsolatedNodeIds } = useStore();
  const [showAdd, setShowAdd] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [form, setForm] = useState({ name: '', description: '', srlg_type: 'custom', memberNodes: [] as number[], memberLinks: [] as number[] });
  const [editForm, setEditForm] = useState({ name: '', description: '', srlg_type: 'custom', memberNodes: [] as number[], memberLinks: [] as number[] });

  const nodeMap = Object.fromEntries(nodes.map(n => [n.id, n.name]));
  const linkMap = Object.fromEntries(links.map(l => [l.id, `${nodeMap[l.source_node_id] || '?'} - ${nodeMap[l.target_node_id] || '?'}`]));

  const handleAdd = async () => {
    if (!form.name.trim()) { toast.error('Name required'); return; }
    try {
      const members = [
        ...form.memberNodes.map(id => ({ member_type: 'node' as const, member_id: id })),
        ...form.memberLinks.map(id => ({ member_type: 'link' as const, member_id: id })),
      ];
      await addSrlg({ name: form.name, description: form.description, srlg_type: form.srlg_type, members });
      setForm({ name: '', description: '', srlg_type: 'custom', memberNodes: [], memberLinks: [] });
      setShowAdd(false);
      toast.success('SRLG created');
    } catch { toast.error('Failed to create SRLG'); }
  };

  const startEdit = (s: SrlgGroup) => {
    setEditingId(s.id);
    setEditForm({
      name: s.name, description: s.description, srlg_type: s.srlg_type,
      memberNodes: s.members.filter(m => m.member_type === 'node').map(m => m.member_id),
      memberLinks: s.members.filter(m => m.member_type === 'link').map(m => m.member_id),
    });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      const members = [
        ...editForm.memberNodes.map(id => ({ member_type: 'node' as const, member_id: id })),
        ...editForm.memberLinks.map(id => ({ member_type: 'link' as const, member_id: id })),
      ];
      await updateSrlg(editingId, { name: editForm.name, description: editForm.description, srlg_type: editForm.srlg_type, members });
      setEditingId(null);
      toast.success('SRLG updated');
    } catch { toast.error('Failed to update'); }
  };

  const handleDelete = async (s: SrlgGroup) => {
    if (!confirm(`Delete SRLG "${s.name}"?`)) return;
    try { await deleteSrlg(s.id); toast.success('Deleted'); }
    catch { toast.error('Failed to delete'); }
  };

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try { await runSrlgAnalysis(); toast.success('SRLG analysis complete'); }
    catch { toast.error('Analysis failed'); }
    finally { setAnalyzing(false); }
  };

  const toggleMember = (list: number[], id: number, setter: (val: number[]) => void) => {
    setter(list.includes(id) ? list.filter(x => x !== id) : [...list, id]);
  };

  const MemberPicker = ({ memberNodes, memberLinks, onNodeChange, onLinkChange }: {
    memberNodes: number[]; memberLinks: number[];
    onNodeChange: (v: number[]) => void; onLinkChange: (v: number[]) => void;
  }) => (
    <div className="space-y-2">
      <div>
        <div className="text-[10px] text-surface-400 font-medium mb-1">Nodes</div>
        <div className="max-h-28 overflow-y-auto space-y-0.5">
          {nodes.map(n => (
            <label key={n.id} className="flex items-center gap-1.5 text-[11px] cursor-pointer hover:bg-surface-700 px-1 rounded">
              <input type="checkbox" checked={memberNodes.includes(n.id)}
                onChange={() => toggleMember(memberNodes, n.id, onNodeChange)} className="accent-primary-500" />
              {n.name}
            </label>
          ))}
        </div>
      </div>
      <div>
        <div className="text-[10px] text-surface-400 font-medium mb-1">Links</div>
        <div className="max-h-28 overflow-y-auto space-y-0.5">
          {links.map(l => (
            <label key={l.id} className="flex items-center gap-1.5 text-[11px] cursor-pointer hover:bg-surface-700 px-1 rounded">
              <input type="checkbox" checked={memberLinks.includes(l.id)}
                onChange={() => toggleMember(memberLinks, l.id, onLinkChange)} className="accent-primary-500" />
              {linkMap[l.id]}
            </label>
          ))}
        </div>
      </div>
    </div>
  );

  const getAnalysis = (srlgId: number) => srlgAnalysisResult?.find(a => a.srlg_id === srlgId);

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm flex-1" onClick={() => setShowAdd(!showAdd)}>
          <Plus size={14} /> Add SRLG
        </button>
        <button className="btn btn-secondary btn-sm" onClick={handleAnalyze} disabled={analyzing || srlgs.length === 0}>
          <Play size={14} /> {analyzing ? 'Analyzing...' : 'Analyze'}
        </button>
      </div>

      <p className="text-[10px] text-surface-500">
        Shared Risk Groups identify infrastructure that fails together (shared conduit, building, power).
        Run analysis to see the impact of each group failing as a correlated event.
      </p>

      {showAdd && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2 fade-in">
          <input className="w-full" placeholder="SRLG name" value={form.name}
            onChange={e => setForm({ ...form, name: e.target.value })} />
          <input className="w-full" placeholder="Description (optional)" value={form.description}
            onChange={e => setForm({ ...form, description: e.target.value })} />
          <select className="w-full" value={form.srlg_type}
            onChange={e => setForm({ ...form, srlg_type: e.target.value })}>
            {SRLG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <MemberPicker memberNodes={form.memberNodes} memberLinks={form.memberLinks}
            onNodeChange={v => setForm({ ...form, memberNodes: v })}
            onLinkChange={v => setForm({ ...form, memberLinks: v })} />
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleAdd}>Create</button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowAdd(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="space-y-1">
        {srlgs.map(s => {
          const analysis = getAnalysis(s.id);
          const isExpanded = expandedId === s.id;
          return (
            <div key={s.id} className="rounded-lg border border-surface-700 bg-surface-800/50">
              {editingId === s.id ? (
                <div className="p-3 space-y-2">
                  <input className="w-full text-sm" value={editForm.name}
                    onChange={e => setEditForm({ ...editForm, name: e.target.value })} />
                  <input className="w-full text-sm" placeholder="Description" value={editForm.description}
                    onChange={e => setEditForm({ ...editForm, description: e.target.value })} />
                  <select className="w-full text-sm" value={editForm.srlg_type}
                    onChange={e => setEditForm({ ...editForm, srlg_type: e.target.value })}>
                    {SRLG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <MemberPicker memberNodes={editForm.memberNodes} memberLinks={editForm.memberLinks}
                    onNodeChange={v => setEditForm({ ...editForm, memberNodes: v })}
                    onLinkChange={v => setEditForm({ ...editForm, memberLinks: v })} />
                  <div className="flex gap-2">
                    <button className="btn btn-primary btn-sm flex-1" onClick={saveEdit}><Save size={12} /> Save</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between px-3 py-2 cursor-pointer"
                    onClick={() => setExpandedId(isExpanded ? null : s.id)}>
                    <div className="flex items-center gap-2">
                      {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      <Layers size={12} className="text-primary-400" />
                      <div>
                        <div className="text-sm font-medium">{s.name}</div>
                        <div className="text-[10px] text-surface-500">{s.srlg_type} &middot; {s.members.length} members</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                      {analysis?.is_spof && <AlertTriangle size={12} className="text-amber-400" title="SPOF" />}
                      <button className="btn btn-ghost btn-sm p-1" onClick={() => startEdit(s)}><Edit3 size={12} /></button>
                      <button className="btn btn-ghost btn-sm p-1 text-red-400" onClick={() => handleDelete(s)}><Trash2 size={12} /></button>
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="px-3 pb-2 space-y-1 border-t border-surface-700 pt-2">
                      {s.description && <div className="text-[10px] text-surface-500">{s.description}</div>}
                      <div className="text-[10px] text-surface-400 font-medium">Members:</div>
                      {s.members.map((m, i) => (
                        <div key={i} className="text-[11px] text-surface-300 pl-2">
                          {m.member_type === 'node' ? `Node: ${nodeMap[m.member_id] || m.member_id}` : `Link: ${linkMap[m.member_id] || m.member_id}`}
                        </div>
                      ))}
                      {s.members.length === 0 && <div className="text-[10px] text-surface-600 pl-2">No members assigned</div>}
                      {analysis && (
                        <div className="mt-2 pt-2 border-t border-surface-700 space-y-1">
                          <div className="text-[10px] font-medium text-surface-400">Failure Impact:</div>
                          <div className={`text-[11px] ${analysis.affected_flow_count > 0 ? 'text-red-400' : 'text-green-400'}`}>
                            {analysis.affected_flow_count} flow{analysis.affected_flow_count !== 1 ? 's' : ''} broken
                          </div>
                          <div className="text-[11px] text-surface-300">
                            Max utilization: {analysis.max_utilization_pct}%
                            {analysis.capacity_exceeded && <span className="text-red-400 ml-1">(overloaded)</span>}
                          </div>
                          {analysis.isolated_node_names.length > 0 && (
                            <div className="text-[11px] text-amber-400">
                              Isolated: {analysis.isolated_node_names.join(', ')}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
        {srlgs.length === 0 && (
          <div className="text-center py-4 text-surface-500 text-sm">No shared risk groups defined</div>
        )}
      </div>
    </div>
  );
}

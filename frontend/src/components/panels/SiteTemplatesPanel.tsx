import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Copy, PlusSquare, Pencil, Plus, Trash2 } from 'lucide-react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import { apiErrorMessage } from '../../utils/siteTemplates';
import SiteTemplateEditor, { EMPTY_DEFINITION } from '../SiteTemplateEditor';
import type { SiteTemplate } from '../../types';

/** Global list of site templates. Templates are dropped into a topology from
 *  the canvas right-click menu (Add Site Template). */
export default function SiteTemplatesPanel() {
  const siteTemplates = useStore(s => s.siteTemplates);
  const loadSiteTemplates = useStore(s => s.loadSiteTemplates);
  const nodeTemplates = useStore(s => s.nodeTemplates);
  const [editing, setEditing] = useState<SiteTemplate | null>(null);
  const hasTopology = useStore(s => s.currentTopologyId != null);
  const canEdit = useStore(s => s.canEditCurrentTopology());
  const labMode = useStore(s => (s as any).labMode) as boolean;
  const viewMode = useStore(s => (s as any).viewMode) as 'logical' | 'geographic';
  // Why "Add to topology" is unavailable right now, if it is.
  const placeBlocked = !hasTopology ? 'Open a topology first'
    : !canEdit ? 'This topology is protected — enter edit mode first'
    : labMode ? 'Not available in lab mode'
    : viewMode === 'geographic' ? 'Switch to the logical view to add a site'
    : null;

  useEffect(() => { loadSiteTemplates().catch(() => toast.error('Could not load site templates')); }, [loadSiteTemplates]);

  const remove = async (t: SiteTemplate) => {
    if (!confirm(`Delete site template "${t.name}"? Sites already added to topologies are not affected.`)) return;
    try {
      await api.deleteSiteTemplate(t.id);
      await loadSiteTemplates();
    } catch (e) { toast.error(apiErrorMessage(e)); }
  };
  const blank = (): SiteTemplate => ({ id: 0, name: '', description: '', definition: structuredClone(EMPTY_DEFINITION), updated_at: null });

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-surface-400">
        A site template is a small, reusable site — devices, the links between their ports, and patch panels —
        with <code className="text-primary-300">{'{{variables}}'}</code> for names and locations.
        Add one with its <span className="text-surface-200">Add to topology</span> button (drops it in the middle of
        the view), or right-click the canvas → <span className="text-surface-200">Add Site Template</span> to drop it where you clicked.
      </div>
      <button className="btn btn-primary btn-sm text-xs w-full justify-center" onClick={() => setEditing(blank())}>
        <Plus size={12} /> New site template
      </button>

      {siteTemplates.length === 0 && <div className="text-xs text-surface-500 text-center py-4">No site templates yet.</div>}
      {siteTemplates.map(t => {
        const d = t.definition;
        const models = d.nodes.map(n => nodeTemplates.find(x => x.id === n.template_id)?.model ?? '?');
        return (
          <div key={t.id} className="bg-surface-800 border border-surface-700 rounded-lg p-2.5">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{t.name}</div>
                {t.description && <div className="text-[11px] text-surface-400">{t.description}</div>}
              </div>
              <div className="flex gap-0.5 shrink-0">
                <button className="btn btn-ghost btn-sm p-1" title="Edit" onClick={() => setEditing(t)}><Pencil size={12} /></button>
                <button className="btn btn-ghost btn-sm p-1" title="Duplicate"
                  onClick={() => setEditing({ ...structuredClone(t), id: 0, name: `${t.name} (copy)` })}><Copy size={12} /></button>
                <button className="btn btn-ghost btn-sm p-1 hover:text-red-400" title="Delete" onClick={() => remove(t)}><Trash2 size={12} /></button>
              </div>
            </div>
            <div className="text-[10px] text-surface-400 mt-1.5">
              {d.nodes.length} device{d.nodes.length !== 1 ? 's' : ''} ({models.join(', ')}) · {d.links.length} link{d.links.length !== 1 ? 's' : ''}
              {d.patch_panels.length > 0 && ` · ${d.patch_panels.length} patch panel${d.patch_panels.length !== 1 ? 's' : ''}`}
            </div>
            <button className="btn btn-secondary btn-sm text-xs w-full justify-center mt-2"
              disabled={!!placeBlocked} title={placeBlocked ?? 'Fill in the variables and add this site to the topology'}
              onClick={() => useStore.getState().setSiteTemplateToPlace(t.id)}>
              <PlusSquare size={12} /> Add to topology
            </button>
            {placeBlocked && hasTopology && <div className="text-[10px] text-surface-500 mt-1 text-center">{placeBlocked}</div>}
            {d.variables.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {d.variables.map(v => (
                  <span key={v.name} className="text-[10px] font-mono bg-surface-900 border border-surface-700 rounded px-1.5 py-0.5 text-primary-300">{`{{${v.name}}}`}</span>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {editing && <SiteTemplateEditor template={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

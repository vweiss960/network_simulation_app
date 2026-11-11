import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Plus, Trash2 } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import type { NodeTemplate } from '../../types';
import { DEVICE_ICONS, deviceIcon } from '../canvas/icons';

const field = 'w-full px-2 py-1.5 text-sm rounded bg-surface-900 border border-surface-700 focus:border-primary-500 outline-none';
const label = 'block text-[11px] uppercase tracking-wider text-surface-500 mb-1 mt-3';

const BLANK: Partial<NodeTemplate> = {
  name: 'New device', vendor: '', model: '', kind: 'linux', image_ref: '', icon: 'router', interface_count: 4,
  min_memory_mb: 0, min_cpu_cores: 0, requires_hugepages: false, startup_config: '', description: '',
};

/** Palette templates: kind + image + sizing defaults for new devices. */
export default function TemplatesPage() {
  const { templates, kinds, loadTemplates } = useLabStore();
  const [sel, setSel] = useState<number | 'new' | null>(templates[0]?.id ?? null);
  const [draft, setDraft] = useState<Partial<NodeTemplate>>(BLANK);
  const [images, setImages] = useState<string[]>([]);

  useEffect(() => { api.listDockerImages().then(r => setImages(r.images)).catch(() => {}); }, []);
  useEffect(() => {
    if (sel === 'new') setDraft({ ...BLANK });
    else { const t = templates.find(x => x.id === sel); if (t) setDraft({ ...t }); }
  }, [sel, templates]);

  const k = kinds[draft.kind ?? 'linux'];
  const set = (p: Partial<NodeTemplate>) => setDraft(d => ({ ...d, ...p }));

  const save = async () => {
    try {
      const { id: _id, created_by: _c, image_available: _a, ...body } = draft as NodeTemplate;
      const t = sel === 'new' ? await api.createTemplate(body) : await api.updateTemplate(sel as number, body);
      toast.success('Saved');
      await loadTemplates();
      setSel(t.id);
    } catch (e) { toast.error((e as Error).message); }
  };

  const remove = async () => {
    if (typeof sel !== 'number' || !confirm(`Delete template "${draft.name}"? Existing devices keep their settings.`)) return;
    await api.deleteTemplate(sel);
    await loadTemplates();
    setSel(null);
  };

  return (
    <div className="h-full flex">
      <aside className="w-72 border-r border-surface-700 overflow-y-auto">
        <div className="p-3 flex items-center justify-between border-b border-surface-700">
          <span className="font-semibold">Device templates</span>
          <button onClick={() => setSel('new')} className="p-1 rounded hover:bg-surface-700" title="New template"><Plus size={16} /></button>
        </div>
        {templates.map(t => {
          const Icon = deviceIcon(t.icon);
          return (
            <button key={t.id} onClick={() => setSel(t.id)}
              className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm ${sel === t.id ? 'bg-surface-700' : 'hover:bg-surface-800'}`}>
              <Icon size={16} className="text-surface-300" />
              <span className="flex-1 truncate">{t.name}</span>
              {(!t.image_ref || t.image_available === false) && <span className="text-[10px] text-amber-400">no image</span>}
            </button>
          );
        })}
      </aside>
      {sel !== null && (
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-2xl p-6">
            <div className="grid grid-cols-2 gap-x-4">
              <div className="col-span-2"><label className={label}>Name</label>
                <input className={field} value={draft.name ?? ''} onChange={e => set({ name: e.target.value })} /></div>
              <div><label className={label}>Vendor</label>
                <input className={field} value={draft.vendor ?? ''} onChange={e => set({ vendor: e.target.value })} /></div>
              <div><label className={label}>Model</label>
                <input className={field} value={draft.model ?? ''} onChange={e => set({ model: e.target.value })} /></div>
              <div><label className={label}>Kind</label>
                <select className={field} value={draft.kind} onChange={e => {
                  const nk = kinds[e.target.value];
                  set({ kind: e.target.value, interface_count: nk?.default_interfaces ?? draft.interface_count });
                }}>
                  {Object.values(kinds).map(x => <option key={x.kind} value={x.kind}>{x.label} ({x.kind})</option>)}
                </select></div>
              <div><label className={label}>Icon</label>
                <select className={field} value={draft.icon} onChange={e => set({ icon: e.target.value })}>
                  {Object.keys(DEVICE_ICONS).map(i => <option key={i} value={i}>{i}</option>)}
                </select></div>
              <div className="col-span-2"><label className={label}>Image</label>
                <input className={`${field} font-mono text-xs`} list="tmpl-images" value={draft.image_ref ?? ''}
                  onChange={e => set({ image_ref: e.target.value })} placeholder="e.g. vrnetlab/cisco_c8000v:17.15.01a" />
                <datalist id="tmpl-images">{images.map(i => <option key={i} value={i} />)}</datalist></div>
              <div><label className={label}>Data interfaces</label>
                <input type="number" className={field} min={0} max={k?.max_interfaces} value={draft.interface_count ?? 0}
                  onChange={e => set({ interface_count: Number(e.target.value) })} /></div>
              <div><label className={label}>Memory MB (0 = {k?.min_memory_mb ?? '?'})</label>
                <input type="number" className={field} value={draft.min_memory_mb ?? 0} onChange={e => set({ min_memory_mb: Number(e.target.value) })} /></div>
              <div><label className={label}>vCPU (0 = {k?.min_cpu_cores ?? '?'})</label>
                <input type="number" className={field} value={draft.min_cpu_cores ?? 0} onChange={e => set({ min_cpu_cores: Number(e.target.value) })} /></div>
              <div className="flex items-end pb-2"><label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!!draft.requires_hugepages} onChange={e => set({ requires_hugepages: e.target.checked })} />
                Needs hugepages</label></div>
              <div className="col-span-2"><label className={label}>Description</label>
                <input className={field} value={draft.description ?? ''} onChange={e => set({ description: e.target.value })} /></div>
              <div className="col-span-2"><label className={label}>Default startup config</label>
                <textarea className={`${field} font-mono text-xs h-40`} value={draft.startup_config ?? ''}
                  onChange={e => set({ startup_config: e.target.value })} /></div>
            </div>
            {k && (
              <div className="mt-4 p-3 rounded bg-surface-800 text-xs text-surface-300">
                <div className="font-semibold mb-1">{k.label}</div>
                <div>{k.description}</div>
                <div className="mt-1">Interfaces: {k.example_interfaces.map(i => `${i.name} (${i.kernel})`).join(', ')}…
                  · mgmt {k.mgmt_iface}{k.default_user ? ` · user ${k.default_user}` : ''}</div>
              </div>
            )}
            <div className="mt-4 flex gap-2">
              <button onClick={save} className="px-4 py-1.5 text-sm rounded bg-primary-600 hover:bg-primary-500 text-white">Save</button>
              {typeof sel === 'number' && (
                <button onClick={remove} className="flex items-center gap-1 px-3 py-1.5 text-sm rounded text-red-300 hover:bg-red-900/30">
                  <Trash2 size={14} /> Delete
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

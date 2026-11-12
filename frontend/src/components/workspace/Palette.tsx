import { useMemo, useState } from 'react';
import { useLabStore } from '../../stores/useLabStore';
import { deviceIcon } from '../canvas/icons';
import { TEMPLATE_DND } from '../canvas/Canvas';

export default function Palette() {
  const { templates, kinds, setPage } = useLabStore();
  const [q, setQ] = useState('');
  const groups = useMemo(() => {
    const f = q.trim().toLowerCase();
    const list = templates.filter(t => !f || `${t.name} ${t.vendor} ${t.model} ${t.kind}`.toLowerCase().includes(f));
    const by: Record<string, typeof list> = {};
    for (const t of list) (by[t.vendor || 'Other'] ??= []).push(t);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [templates, q]);

  return (
    <div className="h-full flex flex-col">
      <div className="p-2 border-b border-surface-700">
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search devices"
          className="w-full px-2 py-1.5 text-sm rounded bg-surface-900 border border-surface-700 focus:border-primary-500 outline-none" />
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        {groups.map(([vendor, ts]) => (
          <div key={vendor}>
            <div className="px-1 mb-1 text-[10px] uppercase tracking-wider text-surface-500">{vendor}</div>
            <div className="space-y-1">
              {ts.map(t => {
                const Icon = deviceIcon(t.icon);
                const isNet = t.kind === 'bridge';
                const missing = !isNet && (!t.image_ref || t.image_available === false);
                return (
                  <div key={t.id} draggable
                    onDragStart={e => { e.dataTransfer.setData(TEMPLATE_DND, String(t.id)); e.dataTransfer.effectAllowed = 'copy'; }}
                    title={`${t.description}\n\nKind: ${t.kind}\nImage: ${t.image_ref || '(none — build or load one under Images)'}`}
                    className="flex items-center gap-2 px-2 py-1.5 rounded cursor-grab active:cursor-grabbing bg-surface-800 hover:bg-surface-700 border border-surface-700">
                    <Icon size={18} className={missing ? 'text-surface-500' : 'text-surface-200'} />
                    <div className="min-w-0 flex-1">
                      <div className={`text-xs truncate ${missing ? 'text-surface-400' : 'text-surface-100'}`}>{t.name}</div>
                      <div className="text-[10px] text-surface-500 truncate">
                        {isNet ? 'network' : kinds[t.kind]?.is_vm ? 'VM' : 'container'}
                        {missing && <span className="text-amber-400"> · {t.image_ref ? 'image not loaded' : 'no image'}</span>}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <div className="p-2 border-t border-surface-700 text-[11px] text-surface-400 flex justify-between">
        <button className="hover:text-surface-100" onClick={() => setPage('templates')}>Edit templates</button>
        <button className="hover:text-surface-100" onClick={() => setPage('images')}>Images</button>
      </div>
    </div>
  );
}

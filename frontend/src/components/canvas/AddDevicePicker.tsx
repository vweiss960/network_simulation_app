/**
 * "Add device" picker, opened from the canvas right-click menu: a search
 * box over every device template, grouped by vendor. Picking one drops it
 * where the canvas was right-clicked. Templates without a usable image are
 * listed but can't be added (with a hint to the Images page).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { deviceIcon } from './icons';
import type { NodeTemplate } from '../../types';

interface Props {
  x: number; y: number;                 // screen position of the right-click
  onPick: (t: NodeTemplate) => void;
  onClose: () => void;
}

export default function AddDevicePicker({ x, y, onPick, onClose }: Props) {
  const { templates, kinds, setPage } = useLabStore();
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  const usable = (t: NodeTemplate) => t.kind === 'bridge' || (!!t.image_ref && t.image_available !== false);
  const list = useMemo(() => {
    const f = q.trim().toLowerCase();
    return templates
      .filter(t => !f || `${t.name} ${t.vendor} ${t.model} ${t.kind} ${kinds[t.kind]?.label ?? ''}`.toLowerCase().includes(f))
      .sort((a, b) => Number(usable(b)) - Number(usable(a)) || (a.vendor || '').localeCompare(b.vendor || '') || a.name.localeCompare(b.name));
  }, [templates, kinds, q]);
  const pickable = list.filter(usable);

  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    const close = (e: Event) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    window.addEventListener('pointerdown', close, true);
    return () => window.removeEventListener('pointerdown', close, true);
  }, [onClose]);

  const choose = (t?: NodeTemplate) => { if (t && usable(t)) { onPick(t); onClose(); } };

  // Keep the panel inside the window.
  const W = 340, H = 440;
  const left = Math.min(x, window.innerWidth - W - 12), top = Math.min(y, window.innerHeight - H - 12);

  let lastVendor = '';
  return (
    <div ref={ref} style={{ left, top, width: W, maxHeight: H }}
      className="fixed z-50 flex flex-col rounded-xl bg-white border border-slate-200 shadow-2xl text-slate-800">
      <div className="p-2.5 border-b border-slate-200">
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-slate-300 focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
          <Search size={15} className="text-slate-400" />
          <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="Search devices"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400"
            onKeyDown={e => {
              if (e.key === 'Escape') onClose();
              if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(i + 1, pickable.length - 1)); }
              if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
              if (e.key === 'Enter') choose(pickable[active]);
            }} />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {list.length === 0 && <div className="px-4 py-6 text-center text-sm text-slate-500">No device matches “{q}”.</div>}
        {list.map(t => {
          const Sym = deviceIcon(t.icon);
          const ok = usable(t);
          const vendor = ok ? (t.vendor || 'Other') : 'Needs an image';
          const header = vendor !== lastVendor ? (lastVendor = vendor) : null;
          const isActive = ok && pickable[active]?.id === t.id;
          return (
            <div key={t.id}>
              {header && <div className="px-3.5 pt-2.5 pb-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">{header}</div>}
              <button disabled={!ok} onClick={() => choose(t)} onMouseEnter={() => ok && setActive(pickable.indexOf(t))}
                title={ok ? t.description : `${t.description}\n\nNo image yet — build or load one under Images.`}
                className={`w-full flex items-center gap-3 px-3.5 py-1.5 text-left ${isActive ? 'bg-blue-50' : ''}
                  ${ok ? 'hover:bg-blue-50' : 'opacity-50 cursor-not-allowed'}`}>
                <Sym size={26} fill="#fff" className="text-slate-700 shrink-0" />
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{t.name}</div>
                  <div className="text-[11px] text-slate-500 truncate">
                    {t.kind === 'bridge' ? 'network' : kinds[t.kind]?.is_vm ? 'VM' : 'container'}
                    {!ok && (t.image_ref ? ' · image not loaded' : ' · no image')}
                  </div>
                </div>
              </button>
            </div>
          );
        })}
      </div>
      <div className="px-3.5 py-2 border-t border-slate-200 flex justify-between text-xs text-slate-500">
        <button className="hover:text-slate-900" onClick={() => setPage('templates')}>Edit templates</button>
        <span className="text-slate-400">↑↓ Enter</span>
        <button className="hover:text-slate-900" onClick={() => setPage('images')}>Images</button>
      </div>
    </div>
  );
}

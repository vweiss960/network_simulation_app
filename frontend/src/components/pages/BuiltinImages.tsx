/**
 * The open-source lab images that ship with Phanero Labs (Linux host, FRR,
 * traffic generator, automation workstation). The backend builds missing or
 * outdated ones at startup (services/builtin_images.py); this shows their
 * state and rebuilds one on demand.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Loader2, RotateCw } from 'lucide-react';
import { api, type BuiltinImage } from '../../utils/api';
import { useAuthStore } from '../../stores/useAuthStore';

const LABEL: Record<string, [string, string]> = {
  present: ['ready', 'text-emerald-300'], missing: ['not built', 'text-amber-300'],
  outdated: ['update available', 'text-amber-300'], queued: ['queued', 'text-sky-300'],
  building: ['building…', 'text-sky-300'], failed: ['build failed', 'text-red-400'],
  'no source': ['source missing', 'text-surface-400'],
};

export default function BuiltinImages() {
  const [items, setItems] = useState<BuiltinImage[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const isAdmin = useAuthStore(s => s.user?.is_admin);
  const busy = items.some(i => i.state === 'building' || i.state === 'queued');

  const refresh = () => api.listBuiltinImages().then(setItems).catch(() => {});
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [busy]);

  const build = async (image: string) => {
    try { setItems(await api.buildBuiltinImage(image)); toast.success(`Building ${image}`); }
    catch (e) { toast.error((e as Error).message); }
  };

  return (
    <section>
      <h2 className="font-semibold mb-1">Built-in images</h2>
      <p className="text-xs text-surface-400 mb-2">
        Open-source images built from the Phanero Labs source. Missing or outdated ones are built automatically when the
        backend starts (a first start takes several minutes).
      </p>
      <div className="rounded border border-surface-700 divide-y divide-surface-700">
        {items.map(i => {
          const [text, cls] = LABEL[i.state] ?? [i.state, 'text-surface-400'];
          return (
            <div key={i.image} className="p-3 text-sm">
              <div className="flex items-center gap-3">
                <span className="font-mono w-56 shrink-0">{i.image}</span>
                <span className="flex-1 text-xs text-surface-400 truncate">{i.description}</span>
                <span className={`text-xs flex items-center gap-1 ${cls}`}>
                  {(i.state === 'building' || i.state === 'queued') && <Loader2 size={12} className="animate-spin" />}{text}
                </span>
                {i.log && (
                  <button className="text-xs text-surface-400 hover:text-surface-200" onClick={() => setOpen(open === i.image ? null : i.image)}>
                    {open === i.image ? 'hide output' : 'output'}
                  </button>
                )}
                {isAdmin && (
                  <button onClick={() => build(i.image)} disabled={busy || i.state === 'no source'}
                    className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-surface-700 hover:bg-surface-600 disabled:opacity-40"
                    title={items.some(x => x.depends_on === i.image)
                      ? `Rebuild from source (the images built FROM it are rebuilt after it)` : 'Rebuild from source'}>
                    <RotateCw size={12} />{i.state === 'missing' ? 'Build' : 'Rebuild'}
                  </button>
                )}
              </div>
              {open === i.image && i.log && (
                <pre className="mt-2 max-h-56 overflow-auto rounded bg-surface-900 p-2 text-[11px] text-surface-300 whitespace-pre-wrap">{i.log}</pre>
              )}
            </div>
          );
        })}
        {!items.length && <div className="p-3 text-sm text-surface-400">Loading…</div>}
      </div>
    </section>
  );
}

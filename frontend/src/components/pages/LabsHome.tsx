import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Copy, Download, MoreHorizontal, Pencil, Plus, Search, Trash2, Upload } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import type { Topology } from '../../types';

type Filter = 'all' | 'running' | 'idle';

const STATE: Record<string, { dot: string; label: string; text: string }> = {
  idle: { dot: 'border-2 border-slate-400 bg-transparent', label: 'Not deployed', text: 'text-slate-500' },
  running: { dot: 'bg-emerald-500', label: 'Running', text: 'text-emerald-700' },
  deploying: { dot: 'bg-sky-500 animate-pulse', label: 'Deploying', text: 'text-sky-700' },
  syncing: { dot: 'bg-sky-500 animate-pulse', label: 'Applying changes', text: 'text-sky-700' },
  destroying: { dot: 'bg-amber-500 animate-pulse', label: 'Wiping', text: 'text-amber-700' },
  failed: { dot: 'bg-red-500', label: 'Error', text: 'text-red-600' },
  stopped: { dot: 'bg-amber-400', label: 'Stopped', text: 'text-amber-700' },
};
const stateOf = (t: Topology) => STATE[t.lab_status] ?? STATE.idle;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

export default function LabsHome() {
  const { topologies, loadTopologies, openTopology } = useLabStore();
  const [name, setName] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  // Lab being opened: the page plays its exit, then the lab grows from that card.
  const [leaving, setLeaving] = useState<number | null>(null);
  const EXIT_MS = 380;
  const openLab = (t: Topology, el: HTMLElement | null) => {
    if (leaving !== null) return;
    const go = () => {
      const b = el?.getBoundingClientRect();
      openTopology(t.id, b ? { x: b.left, y: b.top, w: b.width, h: b.height, name: t.name } : undefined);
    };
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) { go(); return; }
    setLeaving(t.id);
    setTimeout(go, EXIT_MS);
  };
  const [q, setQ] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { loadTopologies(); }, [loadTopologies]);

  const create = async () => {
    try {
      const t = await api.createTopology(name.trim() || 'Untitled lab');
      setName('');
      openTopology(t.id);
    } catch (e) { toast.error((e as Error).message); }
  };

  const onImport = async (f: File | undefined) => {
    if (!f) return;
    try {
      const t = await api.importTopology(f);
      toast.success(`Imported ${t.name}`);
      loadTopologies();
    } catch (e) { toast.error((e as Error).message); }
    if (fileRef.current) fileRef.current.value = '';
  };

  const counts = useMemo(() => ({
    all: topologies.length,
    running: topologies.filter(t => t.lab_status !== 'idle').length,
    idle: topologies.filter(t => t.lab_status === 'idle').length,
  }), [topologies]);
  const dupNames = useMemo(() => {
    const seen = new Map<string, number>();
    for (const t of topologies) seen.set(t.name.trim().toLowerCase(), (seen.get(t.name.trim().toLowerCase()) ?? 0) + 1);
    return new Set([...seen].filter(([, n]) => n > 1).map(([k]) => k));
  }, [topologies]);
  const shown = topologies
    .filter(t => filter === 'all' || (filter === 'running' ? t.lab_status !== 'idle' : t.lab_status === 'idle'))
    .filter(t => !q.trim() || `${t.name} ${t.description}`.toLowerCase().includes(q.trim().toLowerCase()))
    // running labs first, then by name
    .sort((a, b) => Number(b.lab_status !== 'idle') - Number(a.lab_status !== 'idle') || a.name.localeCompare(b.name));

  return (
    <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800">
      <div className="max-w-[1140px] mx-auto px-8 py-9">
        <div className={`flex flex-wrap items-start justify-between gap-4 mb-7 ${leaving !== null ? 'plabs-page-out' : ''}`}>
          <div>
            <h1 className="text-[28px] leading-tight font-bold text-slate-900">Labs</h1>
            <p className="mt-1 text-sm text-slate-500">Each lab is a canvas of real devices. Deploy it and every device gets its own management IP.</p>
          </div>
          <div className="flex gap-2">
            <input value={name} onChange={e => setName(e.target.value)} onKeyDown={e => e.key === 'Enter' && create()}
              placeholder="New lab name"
              className="w-60 px-3.5 py-2 text-sm rounded-lg bg-white border border-slate-300 placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none" />
            <button onClick={create}
              className="whitespace-nowrap flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-700 hover:bg-blue-600 text-white shadow-sm">
              <Plus size={16} /> New lab
            </button>
            <button onClick={() => fileRef.current?.click()} title="Import a Phanero Labs or containerlab YAML file"
              className="whitespace-nowrap flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-white border border-slate-300 hover:bg-slate-50 text-slate-800">
              <Upload size={15} /> Import
            </button>
            <input ref={fileRef} type="file" accept=".yml,.yaml" className="hidden" onChange={e => onImport(e.target.files?.[0])} />
          </div>
        </div>

        <div className={`flex flex-wrap items-center justify-between gap-3 mb-5 ${leaving !== null ? 'plabs-page-out' : ''}`}>
          <div className="flex flex-wrap gap-2">
            {([['all', 'All'], ['running', 'Running'], ['idle', 'Not deployed']] as [Filter, string][]).map(([f, label]) => (
              <button key={f} onClick={() => setFilter(f)}
                className={`px-3.5 py-1.5 rounded-full text-[13px] border transition-colors
                  ${filter === f ? 'bg-slate-900 border-slate-900 text-white font-semibold' : 'bg-white border-slate-300 text-slate-700 hover:border-slate-400'}`}>
                {label} · {counts[f]}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 w-60 px-3 py-2 rounded-lg bg-white border border-slate-300 focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
            <Search size={14} className="text-slate-400" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search labs"
              className="flex-1 min-w-0 bg-transparent text-sm outline-none placeholder:text-slate-400" />
          </div>
        </div>

        {topologies.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white/60 p-14 text-center text-slate-500">
            <div className="text-base font-semibold text-slate-700">No labs yet</div>
            <div className="text-sm mt-1">Name one above and click New lab, or import a containerlab <code>.clab.yml</code>.</div>
          </div>
        ) : shown.length === 0 ? (
          <div className="py-12 text-center text-sm text-slate-500">No labs match.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {shown.map(t => (
              <LabCard key={t.id} t={t} duplicate={dupNames.has(t.name.trim().toLowerCase())}
                index={shown.indexOf(t)}
                exit={leaving === null ? undefined : leaving === t.id ? 'chosen' : 'out'}
                onOpen={(el) => openLab(t, el)} onChanged={loadTopologies} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function LabCard({ t, duplicate, index, exit, onOpen: open, onChanged }:
  { t: Topology; duplicate: boolean; index: number; exit?: 'chosen' | 'out';
    onOpen: (el: HTMLElement | null) => void; onChanged: () => void }) {
  const cardRef = useRef<HTMLDivElement>(null);
  const onOpen = () => open(cardRef.current);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const st = stateOf(t);

  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    window.addEventListener('pointerdown', close, true);
    return () => window.removeEventListener('pointerdown', close, true);
  }, [menu]);

  const rename = async () => {
    setMenu(false);
    if (t.lab_status !== 'idle') { toast.error('Wipe the lab before renaming it (its containers are named after it).'); return; }
    const n = prompt('Rename lab', t.name);
    if (!n?.trim() || n.trim() === t.name) return;
    try { await api.updateTopology(t.id, { name: n.trim() }); onChanged(); } catch (e) { toast.error((e as Error).message); }
  };
  const duplicateLab = async () => {
    setMenu(false);
    try { await api.duplicateTopology(t.id); toast.success(`Duplicated ${t.name}`); onChanged(); }
    catch (e) { toast.error((e as Error).message); }
  };
  const exportYaml = async () => {
    setMenu(false);
    const text = await api.exportTopology(t.id);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'application/x-yaml' }));
    a.download = `${t.name.replace(/[^\w.-]+/g, '_')}.plabs.yml`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const remove = async () => {
    const msg = t.lab_status !== 'idle' ? `Delete "${t.name}"? Its running lab will be destroyed.` : `Delete "${t.name}"?`;
    if (!confirm(msg)) return;
    const id = toast.loading(`Deleting ${t.name}…`);
    try { await api.deleteTopology(t.id); toast.success('Deleted', { id }); onChanged(); }
    catch (e) { toast.error((e as Error).message, { id }); }
  };

  const contents = t.node_count === 0 ? 'Empty canvas' : `${plural(t.node_count, 'device')} · ${plural(t.link_count, 'cable')}`;
  return (
    <div ref={cardRef} style={{ animationDelay: `${Math.min(index, 12) * 35}ms` }}
      className={`plabs-card-in rounded-xl bg-white border border-slate-200 shadow-sm hover:shadow-md hover:border-slate-300 transition-shadow flex flex-col
        ${exit === 'chosen' ? 'plabs-card-chosen' : exit === 'out' ? 'plabs-card-out' : ''}`}>
      <button className="text-left px-4 pt-4 pb-3 flex-1" onClick={onOpen} onDoubleClick={onOpen}>
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${st.dot}`} />
          <span className="text-[15px] font-semibold text-slate-900 truncate">{t.name}</span>
          {t.lesson_role && (
            <span title={t.lesson_role === 'authoring' ? 'The topology of a lesson being edited' : 'A lesson lab'}
              className={`shrink-0 px-1.5 py-px rounded text-[11px] font-medium border ${t.lesson_role === 'authoring'
                ? 'bg-violet-50 text-violet-700 border-violet-200' : 'bg-blue-50 text-blue-700 border-blue-200'}`}>
              {t.lesson_role === 'authoring' ? 'Lesson draft' : 'Lesson'}
            </span>
          )}
          {duplicate && <span className="shrink-0 px-1.5 py-px rounded text-[11px] font-medium bg-amber-100 text-amber-800">Duplicate name</span>}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span className="text-slate-500">{contents}</span>
          <span className={`font-medium ${st.text}`}>{st.label}</span>
        </div>
        {t.description && <div className="mt-2 text-xs text-slate-500 line-clamp-2">{t.description}</div>}
      </button>
      <div className="mx-4 border-t border-slate-200" />
      <div className="px-4 py-3 flex items-center gap-1">
        <button onClick={onOpen} className="px-3 py-1.5 rounded-md bg-blue-50 hover:bg-blue-100 text-blue-700 text-sm font-semibold">Open</button>
        <div className="flex-1" />
        <div className="relative" ref={menuRef}>
          <button onClick={() => setMenu(m => !m)} title="More" className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800">
            <MoreHorizontal size={17} />
          </button>
          {menu && (
            <div className="absolute right-0 bottom-full mb-1 z-20 w-44 py-1 rounded-lg bg-white border border-slate-200 shadow-lg text-sm text-slate-700">
              <MenuItem icon={<Pencil size={14} />} onClick={rename}>Rename</MenuItem>
              <MenuItem icon={<Copy size={14} />} onClick={duplicateLab}>Duplicate</MenuItem>
              <MenuItem icon={<Download size={14} />} onClick={exportYaml}>Export YAML</MenuItem>
            </div>
          )}
        </div>
        <button onClick={remove} title="Delete lab" className="p-1.5 rounded-md text-slate-500 hover:bg-red-50 hover:text-red-600">
          <Trash2 size={16} />
        </button>
      </div>
    </div>
  );
}

function MenuItem({ icon, onClick, children }: { icon: React.ReactNode; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="w-full flex items-center gap-2.5 px-3 py-1.5 hover:bg-slate-50 text-left">
      <span className="text-slate-400">{icon}</span>{children}
    </button>
  );
}

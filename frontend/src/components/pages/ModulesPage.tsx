/**
 * Modules catalogue: collections of lessons built around one topic — e.g. a
 * section of a certification blueprint — with each learner's progress.
 * Admins import module packs (which carry their lessons) and create modules.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { BookOpen, Clock, Layers, Plus, Upload } from 'lucide-react';
import { api, importModule, ApiError } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import { useAuthStore } from '../../stores/useAuthStore';
import type { LessonTrack, ModuleSummary } from '../../types';
import { DraftBadge, LevelBadge, TRACK_LABEL, TrackBadge } from '../lesson/meta';

export const hours = (min: number) => (min >= 90 ? `~${Math.round(min / 30) / 2} h` : `${min} min`);

export default function ModulesPage() {
  const { openModule, editModule } = useLabStore();
  const isAdmin = !!useAuthStore(s => s.user?.is_admin);
  const [modules, setModules] = useState<ModuleSummary[] | null>(null);
  const [track, setTrack] = useState<'' | LessonTrack>('');
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api.listModules().then(setModules).catch(e => { toast.error((e as Error).message); setModules([]); });
  useEffect(() => { load(); }, []);

  const all = modules ?? [];
  const tracks = useMemo(() => [...new Set(all.map(m => m.track))], [all]);
  const shown = all.filter(m => !track || m.track === track);

  const onImport = async (f: File | undefined, mode: 'new' | 'replace' | 'copy' = 'new') => {
    if (!f) return;
    try {
      const m = await importModule(f, mode);
      const n = m.lessons.imported.length + m.lessons.updated.length;
      toast.success(`Imported ${m.title}${n ? ` with ${n} lesson${n === 1 ? '' : 's'}` : ''}`);
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const replace = confirm(`${e.message}.\n\nOK replaces it with this pack. Cancel imports it as a copy.`);
        return onImport(f, replace ? 'replace' : 'copy');
      }
      toast.error((e as Error).message);
    } finally { if (fileRef.current) fileRef.current.value = ''; }
  };

  return (
    <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800">
      <div className="max-w-[1160px] mx-auto px-8 py-9">
        <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
          <div>
            <h1 className="text-[28px] leading-tight font-bold text-slate-900">Modules</h1>
            <p className="mt-1 text-sm text-slate-500">Lessons grouped around one topic, in the order to take them — for example a section of a certification blueprint.</p>
          </div>
          {isAdmin && (
            <div className="flex gap-2">
              <button onClick={() => fileRef.current?.click()} title="Import a module pack (.zip) — its lessons come with it"
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-white border border-slate-300 hover:bg-slate-50">
                <Upload size={15} /> Import module
              </button>
              <button onClick={() => editModule(null)}
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-700 hover:bg-blue-600 text-white shadow-sm">
                <Plus size={16} /> New module
              </button>
              <input ref={fileRef} type="file" accept=".zip" className="hidden" onChange={e => onImport(e.target.files?.[0])} />
            </div>
          )}
        </div>

        {tracks.length > 1 && (
          <div className="flex p-1 mb-4 w-fit rounded-lg bg-white border border-slate-300">
            {(['', ...tracks] as ('' | LessonTrack)[]).map(t => (
              <button key={t || 'all'} onClick={() => setTrack(t)}
                className={`px-3 py-1 rounded-md text-[13px] ${track === t ? 'bg-slate-100 text-slate-900 font-semibold' : 'text-slate-600 hover:text-slate-900'}`}>
                {t ? TRACK_LABEL[t] : 'All tracks'}
              </button>
            ))}
          </div>
        )}

        {modules === null ? <div className="py-10 text-center text-sm text-slate-400">Loading…</div>
          : shown.length === 0 ? (
            <div className="rounded-xl bg-white border border-slate-200 py-12 text-center text-sm text-slate-500">
              {isAdmin ? 'No modules yet — create one from existing lessons or import a pack.' : 'No modules yet.'}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {shown.map(m => {
                const pct = m.lesson_count ? Math.round((100 * m.completed) / m.lesson_count) : 0;
                return (
                  <button key={m.id} onClick={() => openModule(m.id)}
                    className="text-left rounded-xl bg-white border border-slate-200 shadow-sm p-5 hover:border-blue-300 hover:shadow transition">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="w-8 h-8 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center"><Layers size={17} /></span>
                      <TrackBadge track={m.track} /><LevelBadge level={m.level} />{m.status === 'draft' && <DraftBadge />}
                    </div>
                    <div className="text-[16.5px] font-semibold text-slate-900 leading-snug">{m.title}</div>
                    {m.blueprint && <div className="text-[12px] font-medium text-indigo-700 mt-0.5">{m.blueprint}</div>}
                    {m.summary && <p className="mt-1.5 text-[13px] text-slate-600 leading-relaxed line-clamp-3">{m.summary}</p>}
                    <div className="mt-3 flex items-center gap-4 text-[12.5px] text-slate-500">
                      <span className="flex items-center gap-1"><BookOpen size={13} /> {m.lesson_count} lesson{m.lesson_count === 1 ? '' : 's'}</span>
                      <span className="flex items-center gap-1"><Clock size={13} /> {hours(m.minutes)}</span>
                    </div>
                    <div className="mt-3 flex items-center gap-2.5">
                      <div className="flex-1 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                        <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-[12px] text-slate-600 tabular-nums">
                        {m.completed === m.lesson_count && m.lesson_count ? 'Completed' : `${m.completed}/${m.lesson_count} done`}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
      </div>
    </div>
  );
}

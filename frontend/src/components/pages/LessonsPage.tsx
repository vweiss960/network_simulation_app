/**
 * Lessons catalogue: every lesson is a self-contained pack (topology + tasks
 * with automatic checks + written content). Filter by level, track, topic
 * and tags; import packs; create new ones (admins).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { BookOpen, ChevronDown, ListChecks, Network, Plus, Search, Upload } from 'lucide-react';
import { api, importLesson, ApiError } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import { useAuthStore } from '../../stores/useAuthStore';
import type { LessonLevel, LessonSummary, LessonTrack } from '../../types';
import { DraftBadge, LEVEL_LABEL, LevelBadge, TRACK_LABEL, TrackBadge } from '../lesson/meta';

type LevelFilter = 'all' | LessonLevel;

export default function LessonsPage() {
  const { openLesson, editLesson } = useLabStore();
  const isAdmin = !!useAuthStore(s => s.user?.is_admin);
  const [lessons, setLessons] = useState<LessonSummary[] | null>(null);
  const [q, setQ] = useState('');
  const [level, setLevel] = useState<LevelFilter>('all');
  const [track, setTrack] = useState<'' | LessonTrack>('');
  const [topic, setTopic] = useState('');
  const [tag, setTag] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api.listLessons().then(setLessons).catch(e => { toast.error((e as Error).message); setLessons([]); });
  useEffect(() => { load(); }, []);

  const all = lessons ?? [];
  const topics = useMemo(() => [...new Set(all.map(l => l.topic).filter(Boolean))].sort(), [all]);
  const tracks = useMemo(() => [...new Set(all.map(l => l.track))], [all]);
  const tags = useMemo(() => {
    const n = new Map<string, number>();
    all.forEach(l => l.tags.forEach(t => n.set(t, (n.get(t) ?? 0) + 1)));
    return [...n].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 14).map(([t]) => t);
  }, [all]);
  const shown = all.filter(l =>
    (level === 'all' || l.level === level) && (!track || l.track === track) && (!topic || l.topic === topic)
    && (!tag || l.tags.includes(tag))
    && (!q.trim() || `${l.title} ${l.summary} ${l.topic} ${l.tags.join(' ')}`.toLowerCase().includes(q.trim().toLowerCase())));

  const onImport = async (f: File | undefined, mode: 'new' | 'replace' | 'copy' = 'new') => {
    if (!f) return;
    try {
      const l = await importLesson(f, mode);
      toast.success(`Imported ${l.title}`);
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const replace = confirm(`${e.message}.\n\nOK replaces it with this pack (new version). Cancel imports it as a copy.`);
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
            <h1 className="text-[28px] leading-tight font-bold text-slate-900">Lessons</h1>
            <p className="mt-1 text-sm text-slate-500">The building blocks. Every lesson is a self-contained pack you can run on its own.</p>
          </div>
          {isAdmin && (
            <div className="flex gap-2">
              <button onClick={() => fileRef.current?.click()} title="Import a lesson pack (.zip)"
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-white border border-slate-300 hover:bg-slate-50">
                <Upload size={15} /> Import lesson
              </button>
              <button onClick={() => editLesson(null)}
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-700 hover:bg-blue-600 text-white shadow-sm">
                <Plus size={16} /> New lesson
              </button>
              <input ref={fileRef} type="file" accept=".zip" className="hidden" onChange={e => onImport(e.target.files?.[0])} />
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <Explainer icon={<Network size={17} />} tint="bg-blue-50 text-blue-600" title="Topology"
            text="Devices, cables and starting configs — a blank slate or a partly built network." />
          <Explainer icon={<ListChecks size={17} />} tint="bg-emerald-50 text-emerald-600" title="Tasks & checks"
            text="Steps the learner completes, each graded by automatic checks on the live devices." />
          <Explainer icon={<BookOpen size={17} />} tint="bg-purple-50 text-purple-600" title="Lesson content"
            text="Theory and guidance written for the lesson's level, plus hints and an AI assistant policy." />
        </div>

        <div className="flex flex-wrap items-center gap-2.5 mb-3">
          <div className="flex items-center gap-2 w-64 px-3 py-2 rounded-lg bg-white border border-slate-300 focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
            <Search size={14} className="text-slate-400" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search lessons"
              className="flex-1 min-w-0 bg-transparent text-sm outline-none placeholder:text-slate-400" />
          </div>
          <div className="flex p-1 rounded-lg bg-white border border-slate-300">
            {(['all', 'beginner', 'intermediate', 'advanced'] as LevelFilter[]).map(l => (
              <button key={l} onClick={() => setLevel(l)}
                className={`px-3 py-1 rounded-md text-[13px] ${level === l ? 'bg-slate-100 text-slate-900 font-semibold' : 'text-slate-600 hover:text-slate-900'}`}>
                {l === 'all' ? 'All levels' : LEVEL_LABEL[l]}
              </button>
            ))}
          </div>
          <Select value={track} onChange={v => setTrack(v as LessonTrack | '')} label="Track"
            options={[['', 'Any'], ...tracks.map(t => [t, TRACK_LABEL[t] ?? t] as [string, string])]} />
          <Select value={topic} onChange={setTopic} label="Topic" options={[['', 'Any'], ...topics.map(t => [t, t] as [string, string])]} />
          <div className="flex-1" />
          <span className="text-[13px] text-slate-500">{shown.length} of {all.length} lessons</span>
        </div>
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-4">
            {tags.map(t => (
              <button key={t} onClick={() => setTag(tag === t ? '' : t)}
                className={`px-2 py-0.5 rounded-md text-[12px] border ${tag === t ? 'bg-slate-900 border-slate-900 text-white' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-400'}`}>
                #{t}
              </button>
            ))}
          </div>
        )}

        <div className="rounded-xl bg-white border border-slate-200 shadow-sm overflow-hidden">
          <div className="grid grid-cols-[minmax(0,2.4fr)_110px_130px_minmax(0,1.2fr)_60px_70px_110px] gap-3 px-5 py-2.5 text-[11.5px] font-semibold uppercase tracking-wide text-slate-500 border-b border-slate-200">
            <div>Lesson</div><div>Level</div><div>Track</div><div>Topology</div><div>Tasks</div><div>Time</div><div />
          </div>
          {lessons === null ? <div className="px-5 py-10 text-center text-sm text-slate-400">Loading…</div>
            : shown.length === 0 ? (
              <div className="px-5 py-12 text-center text-sm text-slate-500">
                {all.length ? 'No lessons match.' : isAdmin ? 'No lessons yet — create one or import a pack.' : 'No lessons yet.'}
              </div>
            ) : shown.map(l => (
              <div key={l.id} onClick={() => openLesson(l.id)}
                className="grid grid-cols-[minmax(0,2.4fr)_110px_130px_minmax(0,1.2fr)_60px_70px_110px] gap-3 items-center px-5 py-3 border-b last:border-b-0 border-slate-100 hover:bg-slate-50 cursor-pointer">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-[14px] text-slate-900 truncate">{l.title}</span>
                    {l.status === 'draft' && <DraftBadge />}
                  </div>
                  <div className="text-[12.5px] text-slate-500 truncate">{l.topic}{l.tags.length ? ` · ${l.tags.slice(0, 4).map(t => '#' + t).join(' ')}` : ''}</div>
                </div>
                <div><LevelBadge level={l.level} /></div>
                <div><TrackBadge track={l.track} /></div>
                <div className="text-[13px] text-slate-600 truncate">{l.topology_summary || '—'}</div>
                <div className="text-[13px] text-slate-600">{l.task_count}</div>
                <div className="text-[13px] text-slate-600">{l.minutes} min</div>
                <div className="text-right">
                  {l.attempt
                    ? <span className="text-[13px] font-semibold text-blue-700">
                        {l.attempt.completed_at ? 'Review' : `Resume · ${l.attempt.passed}/${l.attempt.total}`}
                      </span>
                    : <span className="text-[13px] font-semibold text-blue-700">Open</span>}
                </div>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

function Explainer({ icon, tint, title, text }: { icon: React.ReactNode; tint: string; title: string; text: string }) {
  return (
    <div className="flex gap-3 p-4 rounded-xl bg-white border border-slate-200 shadow-sm">
      <div className={`w-9 h-9 shrink-0 rounded-lg flex items-center justify-center ${tint}`}>{icon}</div>
      <div>
        <div className="text-[14px] font-semibold text-slate-900">{title}</div>
        <div className="text-[13px] text-slate-500 leading-snug mt-0.5">{text}</div>
      </div>
    </div>
  );
}

function Select({ value, onChange, label, options }: { value: string; onChange: (v: string) => void; label: string; options: [string, string][] }) {
  return (
    <label className="relative flex items-center gap-1 pl-3 pr-7 py-2 rounded-lg bg-white border border-slate-300 text-[13px] text-slate-700">
      {label}:
      <select value={value} onChange={e => onChange(e.target.value)}
        className="absolute inset-0 opacity-0 cursor-pointer" aria-label={label}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <span className="font-medium text-slate-900">{options.find(o => o[0] === value)?.[1] ?? 'Any'}</span>
      <ChevronDown size={14} className="absolute right-2 text-slate-500 pointer-events-none" />
    </label>
  );
}

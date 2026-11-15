/**
 * One module: its overview, the lessons in order (with the blueprint section
 * and objectives each one covers, and the learner's state in it), progress
 * and "Continue" to the next lesson to do.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowLeft, Check, ChevronDown, ChevronRight, Download, Layers, Loader2, Pencil, Play } from 'lucide-react';
import { api, downloadModule } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { ModuleDetail } from '../../types';
import Markdown from '../agent/Markdown';
import { DraftBadge, LevelBadge, TRACK_LABEL, TrackBadge } from '../lesson/meta';
import { hours } from './ModulesPage';

type Item = ModuleDetail['items'][number];

export default function ModulePage() {
  const { moduleId, setPage, editModule, openLesson } = useLabStore();
  const [m, setM] = useState<ModuleDetail | null>(null);
  const [error, setError] = useState('');
  const [openSec, setOpenSec] = useState<number | null>(0);

  useEffect(() => {
    if (!moduleId) return;
    setM(null);
    api.getModule(moduleId).then(setM).catch(e => setError((e as Error).message));
  }, [moduleId]);

  if (error) return <Shell><div className="text-red-600">{error}</div></Shell>;
  if (!m) return <Shell><div className="text-slate-400 flex items-center gap-2"><Loader2 size={15} className="animate-spin" /> Loading…</div></Shell>;

  const pct = m.lesson_count ? Math.round((100 * m.completed) / m.lesson_count) : 0;
  const next = m.items.find(i => i.lesson_id === m.next_lesson_id);
  const started = m.items.some(i => i.status.state !== 'not_started' && i.status.state !== 'not_installed');

  return (
    <Shell>
      <button onClick={() => setPage('modules')} className="flex items-center gap-1 text-[13px] text-blue-700 hover:underline mb-3">
        <ArrowLeft size={14} /> Modules
      </button>

      <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-6 mb-5">
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <TrackBadge track={m.track} /><LevelBadge level={m.level} />{m.status === 'draft' && <DraftBadge />}
          {m.blueprint && <span className="text-[12.5px] font-medium text-indigo-700">{m.blueprint}</span>}
        </div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-[700px]">
            <h1 className="text-[26px] leading-tight font-bold text-slate-900 flex items-center gap-2.5">
              <Layers size={24} className="text-indigo-600 shrink-0" />{m.title}
            </h1>
            {m.summary && <p className="mt-1.5 text-[14px] text-slate-600 leading-relaxed">{m.summary}</p>}
            <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-slate-500">
              <span>{m.lesson_count} lesson{m.lesson_count === 1 ? '' : 's'}</span>
              <span>{hours(m.minutes)}</span>
              {m.tags.length > 0 && <span>{m.tags.map(t => '#' + t).join(' ')}</span>}
            </div>
            <div className="mt-3 flex items-center gap-2.5">
              <div className="w-56 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
              </div>
              <span className="text-[12.5px] text-slate-600">
                {m.lesson_count && m.completed === m.lesson_count ? 'Module completed' : `${m.completed} of ${m.lesson_count} lessons completed`}
              </span>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => downloadModule(m.id).catch(e => toast.error((e as Error).message))}
              title="Export as a module pack (.zip), including its lessons"
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-white border border-slate-300 hover:bg-slate-50"><Download size={14} /> Export</button>
            {m.can_edit && (
              <button onClick={() => editModule(m.id)}
                className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-white border border-slate-300 hover:bg-slate-50"><Pencil size={14} /> Edit module</button>
            )}
            {next && (
              <button onClick={() => openLesson(next.lesson_id!)}
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-700 hover:bg-blue-600 text-white shadow-sm">
                <Play size={15} /> {started ? 'Continue' : 'Start module'}: {next.section || next.lesson_title}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_280px] gap-5 items-start">
        <div className="space-y-5 min-w-0">
          <section className="rounded-xl bg-white border border-slate-200 shadow-sm p-5">
            <h2 className="text-[16px] font-semibold text-slate-900 mb-3">Lessons</h2>
            <ol className="relative">
              {m.items.map((it, i) => <LessonRow key={it.lesson + i} it={it} last={i === m.items.length - 1}
                current={it.lesson_id != null && it.lesson_id === m.next_lesson_id} onOpen={openLesson} />)}
              {m.items.length === 0 && <li className="text-sm text-slate-500">No lessons in this module yet.</li>}
            </ol>
          </section>

          {m.sections.length > 0 && (
            <section className="rounded-xl bg-white border border-slate-200 shadow-sm p-5">
              <h2 className="text-[16px] font-semibold text-slate-900 mb-3">Overview</h2>
              <div className="space-y-2">
                {m.sections.map((s, i) => (
                  <div key={i} className="rounded-lg border border-slate-200 bg-slate-50/60">
                    <button onClick={() => setOpenSec(openSec === i ? null : i)}
                      className="w-full flex items-center justify-between px-3.5 py-2.5 text-left">
                      <span className="flex items-center gap-1.5 text-[13.5px] font-medium text-slate-800">
                        {openSec === i ? <ChevronDown size={15} /> : <ChevronRight size={15} />}{s.title}
                      </span>
                      <span className="text-[12px] text-slate-500">{s.minutes} min read</span>
                    </button>
                    {openSec === i && <div className="px-4 pb-3 bg-white rounded-b-lg border-t border-slate-200"><Markdown text={s.body} /></div>}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="space-y-4">
          {m.items.some(i => i.section) && (
            <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-4">
              <div className="text-[14px] font-semibold text-slate-900 mb-2">Blueprint coverage</div>
              {m.blueprint && <div className="text-[12px] text-indigo-700 font-medium mb-2">{m.blueprint}</div>}
              {m.items.filter(i => i.section).map(i => (
                <div key={i.lesson} className="flex items-start gap-2 mb-1.5 text-[12.5px]">
                  <span className={`mt-0.5 w-3.5 h-3.5 shrink-0 rounded-full flex items-center justify-center ${i.status.state === 'completed' ? 'bg-emerald-500 text-white' : 'border border-slate-300'}`}>
                    {i.status.state === 'completed' && <Check size={10} strokeWidth={3} />}
                  </span>
                  <span className="text-slate-700"><b className="font-semibold">{i.section}</b> {i.title}</span>
                </div>
              ))}
            </div>
          )}
          <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-4">
            <div className="text-[14px] font-semibold text-slate-900 mb-2.5">Module files</div>
            {[['module.yaml', 'Title, track, blueprint, the lessons in order'], ['overview.md', 'The module introduction'],
              ['lessons/', 'Every lesson pack, so the module imports anywhere']].map(([f, d]) => (
              <div key={f} className="mb-1.5"><div className="font-mono text-[12px] text-slate-800">{f}</div><div className="text-[11.5px] text-slate-500">{d}</div></div>
            ))}
          </div>
          <div className="px-1 text-[12px] text-slate-500">
            Version {m.version}{m.author ? ` · ${m.author}` : ''} · {TRACK_LABEL[m.track]}
          </div>
        </div>
      </div>
    </Shell>
  );
}

function LessonRow({ it, last, current, onOpen }: { it: Item; last: boolean; current: boolean; onOpen: (id: number) => void }) {
  const st = it.status;
  const done = st.state === 'completed';
  const missing = st.state === 'not_installed';
  return (
    <li className="relative flex gap-3.5 pb-5 last:pb-0">
      {!last && <span className={`absolute left-[15px] top-8 bottom-0 w-0.5 ${done ? 'bg-emerald-300' : 'bg-slate-200'}`} />}
      <span className={`relative z-[1] w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-[12px] font-bold
        ${done ? 'bg-emerald-500 text-white' : current ? 'bg-blue-700 text-white' : 'bg-white border-2 border-slate-300 text-slate-500'}`}>
        {done ? <Check size={15} strokeWidth={3} /> : (it.section || '•')}
      </span>
      <div className={`flex-1 min-w-0 rounded-lg border p-3.5 ${current ? 'border-blue-300 bg-blue-50/40' : 'border-slate-200'}`}>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            {it.title && <div className="text-[12px] font-semibold uppercase tracking-wide text-indigo-700">{it.section} {it.title}</div>}
            <div className="text-[15px] font-semibold text-slate-900 leading-snug">{missing ? it.lesson : it.lesson_title}</div>
          </div>
          <StateChip st={st} />
        </div>
        {it.lesson_summary && <p className="mt-1 text-[13px] text-slate-600 leading-relaxed line-clamp-2">{it.lesson_summary}</p>}
        {it.objectives.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {it.objectives.map(o => <span key={o} className="px-2 py-0.5 rounded-md bg-slate-100 text-[12px] text-slate-700">{o}</span>)}
          </div>
        )}
        <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-slate-500">
            {it.level && <LevelBadge level={it.level} />}
            {it.topology_summary && <span>{it.topology_summary}</span>}
            {!missing && <span>{it.task_count} tasks</span>}
            {it.minutes != null && <span>{it.minutes} min</span>}
          </div>
          {missing
            ? <span className="text-[12.5px] text-amber-700">This lesson isn't installed — import the module pack that includes it.</span>
            : <button onClick={() => onOpen(it.lesson_id!)}
                className={`px-3 py-1.5 text-[13px] font-semibold rounded-lg ${current ? 'bg-blue-700 hover:bg-blue-600 text-white' : 'bg-white border border-slate-300 hover:bg-slate-50 text-slate-800'}`}>
                {done ? 'Review' : st.state === 'in_progress' ? 'Resume' : 'Open lesson'}
              </button>}
        </div>
      </div>
    </li>
  );
}

function StateChip({ st }: { st: Item['status'] }) {
  const map = {
    completed: ['Completed', 'bg-emerald-50 text-emerald-700 border-emerald-200'],
    in_progress: [`In progress · ${st.passed ?? 0}/${st.total ?? 0}`, 'bg-blue-50 text-blue-700 border-blue-200'],
    not_started: ['Not started', 'bg-slate-50 text-slate-600 border-slate-200'],
    not_installed: ['Not installed', 'bg-amber-50 text-amber-700 border-amber-200'],
  } as const;
  const [text, cls] = map[st.state];
  return (
    <span className={`shrink-0 px-2 py-0.5 rounded-full border text-[11.5px] font-medium ${cls}`}>
      {text}{st.outdated ? ' · update available' : ''}
    </span>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800"><div className="max-w-[1160px] mx-auto px-8 py-7">{children}</div></div>;
}

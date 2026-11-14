/**
 * One lesson: what it covers, its starting topology, the tasks and their
 * checks (with the learner's progress), the written content, and Start /
 * Resume — which builds (or reopens) the learner's own lab for it.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowLeft, Bot, Check, ChevronDown, ChevronRight, Download, FileCode2, Loader2, Pencil, Play, X } from 'lucide-react';
import { api, downloadLesson } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { LessonDetail, PublicTask } from '../../types';
import Markdown from '../agent/Markdown';
import TopologyPreview from '../lesson/TopologyPreview';
import { AI_HELP, AI_LABEL, DraftBadge, LevelBadge, TRACK_LABEL, TrackBadge } from '../lesson/meta';

const HINTS_TEXT = { open: 'Hints are shown with every task.', on_request: 'Hints are revealed one at a time when you ask.', none: 'No hints or solutions.' } as const;

export default function LessonPage() {
  const { lessonId, setPage, editLesson, openTopology } = useLabStore();
  const [l, setL] = useState<LessonDetail | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [yaml, setYaml] = useState(false);
  const [openSec, setOpenSec] = useState<number | null>(null);

  useEffect(() => {
    if (!lessonId) return;
    setL(null);
    api.getLesson(lessonId).then(setL).catch(e => setError((e as Error).message));
  }, [lessonId]);

  if (error) return <Shell><div className="text-red-600">{error}</div></Shell>;
  if (!l) return <Shell><div className="text-slate-400 flex items-center gap-2"><Loader2 size={15} className="animate-spin" /> Loading…</div></Shell>;

  const a = l.attempt;
  // An attempt built from an older version of the lesson: rebuild its lab
  // from the current topology (wipes it) before opening it.
  const upgrade = async () => {
    const att = l.attempt;
    if (!att) return;
    if (!confirm(`Restart this lesson on v${l.version}? Your current lab (built from v${att.lesson_version}) is wiped and `
      + 'rebuilt from the new topology, and your progress is cleared.')) return;
    setStarting(true);
    try {
      const fresh = await api.resetLessonAttempt(att.id);
      toast.success('Your lab was rebuilt — deploy it to start');
      if (fresh.topology_id) openTopology(fresh.topology_id);
    } catch (e) { toast.error((e as Error).message); }
    finally { setStarting(false); }
  };
  const start = async () => {
    setStarting(true);
    try {
      const r = await api.startLesson(l.id);
      if (r.created) toast.success('Your lab is ready — deploy it to start');
      if (r.attempt.topology_id) openTopology(r.attempt.topology_id);
    } catch (e) { toast.error((e as Error).message); }
    finally { setStarting(false); }
  };
  const missing = l.requires.filter(r => !r.ready);
  const tasks = l.tasks as PublicTask[];

  return (
    <Shell>
      <button onClick={() => setPage('lessons')} className="flex items-center gap-1 text-[13px] text-blue-700 hover:underline mb-3">
        <ArrowLeft size={14} /> Lessons
      </button>

      {/* Header */}
      <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-6 mb-5">
        <div className="flex flex-wrap items-center gap-2 mb-1.5">
          <LevelBadge level={l.level} /><TrackBadge track={l.track} />
          {l.topic && <span className="text-[12.5px] text-slate-500">{l.topic}</span>}
          {l.status === 'draft' && <DraftBadge />}
        </div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-[680px]">
            <h1 className="text-[26px] leading-tight font-bold text-slate-900">{l.title}</h1>
            {l.summary && <p className="mt-1.5 text-[14px] text-slate-600 leading-relaxed">{l.summary}</p>}
            <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-slate-500">
              <span>{l.topology_summary || 'Blank canvas'}</span>
              <span>{l.task_count} tasks</span>
              <span>~{l.minutes} min</span>
              {l.tags.length > 0 && <span>{l.tags.map(t => '#' + t).join(' ')}</span>}
            </div>
            {a && (
              <div className="mt-3 flex items-center gap-2.5">
                <div className="w-48 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                  <div className="h-full bg-emerald-500" style={{ width: `${a.total ? (100 * a.passed) / a.total : 0}%` }} />
                </div>
                <span className="text-[12.5px] text-slate-600">{a.completed_at ? 'Completed' : `${a.passed} of ${a.total} tasks passed`}</span>
              </div>
            )}
            {a?.outdated && (
              <div className="mt-2 text-[12.5px] text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-2.5 py-1.5">
                This lesson was updated to v{l.version} after you started (v{a.lesson_version}). Your lab still has the
                old topology: <b>Restart on v{l.version}</b> rebuilds it, or <button onClick={start} className="underline">open
                the old lab</button> as it is.
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={() => downloadLesson(l.id).catch(e => toast.error((e as Error).message))} title="Export as a lesson pack (.zip)"
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-white border border-slate-300 hover:bg-slate-50"><Download size={14} /> Export</button>
            {l.can_edit && (
              <button onClick={() => editLesson(l.id)}
                className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-white border border-slate-300 hover:bg-slate-50"><Pencil size={14} /> Edit lesson</button>
            )}
            <button onClick={a?.outdated ? upgrade : start} disabled={starting}
              className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-700 hover:bg-blue-600 text-white shadow-sm disabled:opacity-50">
              {starting ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
              {a ? (a.outdated ? `Restart on v${l.version}` : a.completed_at ? 'Open my lab' : 'Resume lesson') : 'Start lesson'}
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_280px] gap-5 items-start">
        <div className="space-y-5 min-w-0">
          {/* 1 Topology */}
          <Card n={1} title="Topology" right={
            <button onClick={() => setYaml(v => !v)} className="flex items-center gap-1 text-[12.5px] px-2 py-1 rounded border border-slate-300 hover:bg-slate-50">
              <FileCode2 size={13} /> {yaml ? 'Diagram' : 'View YAML'}
            </button>}>
            {yaml
              ? <pre className="max-h-[420px] overflow-auto rounded-lg bg-[#1e1e1e] text-[#d4d4d4] text-[12px] p-3 font-mono">{l.topology_yaml}</pre>
              : <TopologyPreview topology={l.topology} />}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-3 text-[12.5px]">
              <div>
                <div className="font-semibold text-slate-500 uppercase tracking-wide text-[11px] mb-0.5">Starting state</div>
                <div className="text-slate-600">
                  {l.topology.nodes.length === 0 ? 'Empty canvas.'
                    : `${l.topology.links.length} cable${l.topology.links.length === 1 ? '' : 's'}; `
                      + `${l.topology.nodes.filter(n => !!n.startup_config).length} of ${l.topology.nodes.length} devices start with configuration`
                      + (l.topology.nodes.some(n => n.enabled === false) ? `; ${l.topology.nodes.filter(n => n.enabled === false).length} powered off.` : '.')}
                </div>
              </div>
              <div>
                <div className="font-semibold text-slate-500 uppercase tracking-wide text-[11px] mb-0.5">Images</div>
                {l.requires.map(r => (
                  <div key={r.kind + r.image} className="flex items-center gap-1.5 text-slate-600">
                    {r.ready ? <Check size={13} className="text-emerald-600" /> : <X size={13} className="text-red-500" />}
                    {r.label} ×{r.devices.length} <span className="font-mono text-[11px] text-slate-400 truncate">{r.image}</span>
                  </div>
                ))}
                {missing.length > 0 && <div className="text-amber-700 mt-1">Load or build the missing image{missing.length > 1 ? 's' : ''} on the Images page first.</div>}
              </div>
            </div>
          </Card>

          {/* 2 Tasks */}
          <Card n={2} title="Tasks & checks" right={a && <span className="text-[12.5px] text-slate-500">{a.passed} of {a.total} passing</span>}>
            <ol className="divide-y divide-slate-100">
              {tasks.map((t, i) => {
                const r = a?.results[t.id];
                return (
                  <li key={t.id} className="py-3 flex gap-3">
                    <TaskDot n={i + 1} state={r ? (r.passed ? 'pass' : 'fail') : 'todo'} />
                    <div className="min-w-0">
                      <div className="text-[14px] font-semibold text-slate-900">{t.title}</div>
                      {t.checks.map((c, j) => {
                        const cr = r?.checks[j];
                        return (
                          <div key={j} className="text-[12px] font-mono text-slate-500 flex items-center gap-1.5">
                            <span className={cr ? (cr.ok ? 'text-emerald-600' : 'text-red-500') : 'text-slate-400'}>{cr ? (cr.ok ? '✓' : '✗') : 'check'}</span>
                            {c.describe}
                          </div>
                        );
                      })}
                      {t.hint_count > 0 && l.hints_mode !== 'none' && <div className="text-[11.5px] text-slate-400 mt-0.5">{t.hint_count} hint{t.hint_count === 1 ? '' : 's'} available</div>}
                    </div>
                  </li>
                );
              })}
              {tasks.length === 0 && <li className="py-3 text-sm text-slate-500">No tasks yet.</li>}
            </ol>
          </Card>

          {/* 3 Content */}
          {l.sections.length > 0 && (
            <Card n={3} title="Lesson content">
              <div className="space-y-2">
                {l.sections.map((s, i) => (
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
            </Card>
          )}
        </div>

        {/* Side */}
        <div className="space-y-4">
          <Side title="AI assistance">
            <div className="flex p-1 rounded-lg bg-slate-100 mb-2">
              {(['teach', 'hints', 'off'] as const).map(p => (
                <div key={p} className={`flex-1 text-center text-[12.5px] py-1 rounded-md ${l.ai_policy === p ? 'bg-white shadow-sm font-semibold text-slate-900' : 'text-slate-500'}`}>{AI_LABEL[p]}</div>
              ))}
            </div>
            <div className="text-[12.5px] text-slate-500 flex gap-1.5"><Bot size={14} className="shrink-0 mt-0.5" />{AI_HELP[l.ai_policy]}</div>
            <div className="text-[12.5px] text-slate-500 mt-1.5">{HINTS_TEXT[l.hints_mode]}</div>
          </Side>
          <Side title="Lesson files">
            {[['lesson.yaml', 'Title, level, track, tags, AI policy'], ['topology.yaml', 'Devices, cables, starting config'],
              ['tasks.yaml', 'Steps, hints, checks, reference answers'], ['content.md', 'The written lesson']].map(([f, d]) => (
              <div key={f} className="mb-1.5"><div className="font-mono text-[12px] text-slate-800">{f}</div><div className="text-[11.5px] text-slate-500">{d}</div></div>
            ))}
          </Side>
          <div className="px-1 text-[12px] text-slate-500">
            Version {l.version}{l.author ? ` · ${l.author}` : ''} · {TRACK_LABEL[l.track]}
          </div>
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800"><div className="max-w-[1160px] mx-auto px-8 py-7">{children}</div></div>;
}

function Card({ n, title, right, children }: { n: number; title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-xl bg-white border border-slate-200 shadow-sm p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <span className="w-6 h-6 rounded-md bg-blue-50 text-blue-700 text-[12px] font-bold flex items-center justify-center">{n}</span>
          <h2 className="text-[16px] font-semibold text-slate-900">{title}</h2>
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function Side({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-4">
      <div className="text-[14px] font-semibold text-slate-900 mb-2.5">{title}</div>
      {children}
    </div>
  );
}

export function TaskDot({ n, state }: { n: number; state: 'pass' | 'fail' | 'todo' | 'current' }) {
  if (state === 'pass') return <span className="w-6 h-6 shrink-0 rounded-full bg-emerald-500 text-white flex items-center justify-center"><Check size={14} strokeWidth={3} /></span>;
  const ring = state === 'fail' ? 'border-red-400 text-red-500' : state === 'current' ? 'border-blue-600 text-blue-700' : 'border-slate-300 text-slate-500';
  return <span className={`w-6 h-6 shrink-0 rounded-full border-2 text-[11.5px] font-semibold flex items-center justify-center ${ring}`}>{n}</span>;
}


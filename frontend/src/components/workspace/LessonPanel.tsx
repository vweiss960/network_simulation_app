/**
 * Lesson panel, docked on the right of a learner's lesson lab: progress, the
 * current task (markdown), Check (runs the task's checks on the live
 * devices), hints, the reference solution, the lesson's readings and
 * Restart. Also the authoring bar shown on a lesson's authoring lab.
 */
import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import {
  ArrowLeft, BookOpen, Check, ChevronDown, ChevronRight, Eye, Lightbulb, ListChecks, Loader2, Play, RotateCcw, Save, Trophy, X,
} from 'lucide-react';
import { api, type LessonPanelData } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { LessonAttempt, TaskResult } from '../../types';
import Markdown from '../agent/Markdown';
import { LevelBadge } from '../lesson/meta';

export default function LessonPanel({ onClose }: { onClose: () => void }) {
  const { currentTopologyId, lab, deploy, busy, refreshTopology, pollLab } = useLabStore();
  const [data, setData] = useState<LessonPanelData | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<string | null>(null);        // expanded task
  const [checking, setChecking] = useState<string | null>(null); // task id or '*'
  const [solution, setSolution] = useState<Record<string, Record<string, string>>>({});
  const [reading, setReading] = useState<number | null>(null);
  const [view, setView] = useState<'tasks' | 'readings'>('tasks');
  const [resetting, setResetting] = useState(false);

  const load = useCallback(async () => {
    if (!currentTopologyId) return;
    try {
      const d = await api.lessonForTopology(currentTopologyId);
      setData(d);
      setOpen(o => o ?? firstOpen(d));
    } catch (e) { setError((e as Error).message); }
  }, [currentTopologyId]);
  useEffect(() => { load(); }, [load]);

  if (error) return <Frame onClose={onClose}><div className="p-4 text-sm text-red-300">{error}</div></Frame>;
  if (!data) return <Frame onClose={onClose}><div className="p-4 text-sm text-surface-400 flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Loading lesson…</div></Frame>;

  const { lesson, attempt } = data;
  const setAttempt = (a: LessonAttempt) => setData(d => (d ? { ...d, attempt: a } : d));
  const deployed = !!lab && !['idle', 'stopped'].includes(lab.status);
  const pct = attempt.total ? Math.round((100 * attempt.passed) / attempt.total) : 0;

  const check = async (task?: string) => {
    setChecking(task ?? '*');
    try {
      const r = await api.checkLessonTask(attempt.id, task);
      setAttempt(r.attempt);
      const ran = Object.entries(r.ran);
      if (task && ran[0]?.[1].passed) {
        toast.success('Task passed');
        const idx = lesson.tasks.findIndex(t => t.id === task);
        const next = lesson.tasks.slice(idx + 1).find(t => !r.attempt.results[t.id]?.passed);
        if (next) setOpen(next.id);
      }
      if (r.attempt.completed_at && !attempt.completed_at) toast.success('Lesson complete!', { icon: '🏆' });
    } catch (e) { toast.error((e as Error).message); }
    finally { setChecking(null); }
  };
  const hint = async (task: string) => {
    try { setAttempt(await api.revealHint(attempt.id, task)); } catch (e) { toast.error((e as Error).message); }
  };
  const showSolution = async (task: string) => {
    if (!confirm('Show the reference solution for this task?')) return;
    try {
      const r = await api.lessonSolution(attempt.id, task);
      setSolution(s => ({ ...s, [task]: r.solution }));
    } catch (e) { toast.error((e as Error).message); }
  };
  const restart = async () => {
    if (!confirm('Restart the lesson? The lab is wiped and rebuilt from the starting topology, and your progress is cleared.')) return;
    setResetting(true);
    try {
      setAttempt(await api.resetLessonAttempt(attempt.id));
      setSolution({});
      setOpen(lesson.tasks[0]?.id ?? null);
      await refreshTopology();
      pollLab();
      toast.success('Lesson restarted — deploy the lab to begin again');
    } catch (e) { toast.error((e as Error).message); }
    finally { setResetting(false); }
  };

  return (
    <Frame onClose={onClose}>
      {/* Header */}
      <div className="px-4 pt-3 pb-3 border-b border-surface-700">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-surface-400 mb-1">
          <BookOpen size={12} /> Lesson <LevelBadge level={lesson.level} />
        </div>
        <div className="text-[15px] font-semibold text-surface-100 leading-snug pr-6">{lesson.title}</div>
        <div className="mt-2 flex items-center gap-2">
          <div className="flex-1 h-1.5 rounded-full bg-surface-700 overflow-hidden">
            <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
          <span className="text-[11.5px] text-surface-300 tabular-nums">{attempt.passed}/{attempt.total}</span>
        </div>
        <div className="mt-2 flex gap-1">
          <Tab on={view === 'tasks'} onClick={() => setView('tasks')}><ListChecks size={13} /> Tasks</Tab>
          {lesson.sections.length > 0 && <Tab on={view === 'readings'} onClick={() => setView('readings')}><BookOpen size={13} /> Readings</Tab>}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {view === 'readings' ? (
          reading === null ? (
            <div className="p-3 space-y-1.5">
              {lesson.sections.map((s, i) => (
                <button key={i} onClick={() => setReading(i)}
                  className="w-full flex items-center justify-between px-3 py-2 rounded-lg bg-surface-900/50 hover:bg-surface-700 text-left">
                  <span className="text-[13px] text-surface-100">{s.title}</span>
                  <span className="text-[11px] text-surface-400">{s.minutes} min</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="p-4">
              <button onClick={() => setReading(null)} className="flex items-center gap-1 text-[12px] text-sky-300 hover:underline mb-2">
                <ArrowLeft size={13} /> All readings
              </button>
              <div className="text-[15px] font-semibold text-surface-100 mb-1">{lesson.sections[reading].title}</div>
              <Markdown text={lesson.sections[reading].body} dark />
            </div>
          )
        ) : (
          <>
            {!deployed && (
              <div className="m-3 p-3 rounded-lg bg-sky-900/30 border border-sky-800/60 text-[12.5px] text-sky-100">
                {lab?.status === 'stopped' ? 'The lab is stopped.' : 'Deploy the lab to start working on the tasks.'}
                <button onClick={() => deploy(true)} disabled={!!busy}
                  className="mt-2 flex items-center gap-1.5 px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-[12.5px] disabled:opacity-50">
                  <Play size={13} /> {lab?.status === 'stopped' ? 'Start the lab' : 'Deploy the lab'}
                </button>
              </div>
            )}
            {attempt.completed_at && (
              <div className="m-3 p-3 rounded-lg bg-emerald-900/30 border border-emerald-800/60 text-[12.5px] text-emerald-100 flex gap-2">
                <Trophy size={16} className="shrink-0 text-amber-300" />
                <div><div className="font-semibold">Lesson complete</div>Every task passed. Keep exploring the lab, or restart to practise again.</div>
              </div>
            )}
            <ol>
              {lesson.tasks.map((t, i) => {
                const r = attempt.results[t.id];
                const isOpen = open === t.id;
                const hints = attempt.hints[t.id] ?? [];
                return (
                  <li key={t.id} className="border-b border-surface-700/70">
                    <button onClick={() => setOpen(isOpen ? null : t.id)} className="w-full flex items-start gap-2.5 px-4 py-2.5 text-left hover:bg-surface-700/40">
                      <Dot n={i + 1} r={r} current={isOpen} />
                      <span className={`flex-1 text-[13px] leading-snug ${r?.passed ? 'text-surface-300' : 'text-surface-100'}`}>{t.title}</span>
                      {isOpen ? <ChevronDown size={14} className="mt-0.5 text-surface-400" /> : <ChevronRight size={14} className="mt-0.5 text-surface-400" />}
                    </button>
                    {isOpen && (
                      <div className="px-4 pb-3.5 pl-[46px]">
                        {t.body && <Markdown text={t.body} dark />}
                        <Checks task={t.checks} result={r} />
                        {hints.length > 0 && (
                          <div className="mt-2 space-y-1.5">
                            {hints.map((h, j) => (
                              <div key={j} className="flex gap-1.5 p-2 rounded bg-amber-900/20 border border-amber-800/40 text-[12.5px] text-amber-100">
                                <Lightbulb size={13} className="shrink-0 mt-0.5 text-amber-300" /><div className="min-w-0"><Markdown text={h} dark /></div>
                              </div>
                            ))}
                          </div>
                        )}
                        {solution[t.id] && (
                          <div className="mt-2">
                            {Object.keys(solution[t.id]).length === 0 && <div className="text-[12px] text-surface-400">No configuration needed for this task.</div>}
                            {Object.entries(solution[t.id]).map(([dev, cfg]) => (
                              <div key={dev}><div className="text-[12px] font-semibold text-surface-200 mt-1.5">{dev}</div>
                                <Markdown text={'```\n' + cfg.trimEnd() + '\n```'} dark /></div>
                            ))}
                          </div>
                        )}
                        <div className="mt-2.5 flex flex-wrap gap-1.5">
                          <button onClick={() => check(t.id)} disabled={!!checking || !deployed}
                            title={deployed ? 'Run this task\'s checks on the live devices' : 'Deploy the lab first'}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white text-[12.5px] font-medium disabled:opacity-40">
                            {checking === t.id ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Check
                          </button>
                          {lesson.hints_mode === 'on_request' && hints.length < t.hint_count && (
                            <button onClick={() => hint(t.id)}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-surface-700 hover:bg-surface-600 text-[12.5px] text-surface-100">
                              <Lightbulb size={13} /> Hint ({t.hint_count - hints.length} left)
                            </button>
                          )}
                          {lesson.hints_mode !== 'none' && t.has_solution && !solution[t.id] && (
                            <button onClick={() => showSolution(t.id)}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[12.5px] text-surface-300 hover:text-surface-100 hover:bg-surface-700">
                              <Eye size={13} /> Solution
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </div>

      <div className="px-3 py-2.5 border-t border-surface-700 flex items-center gap-2">
        <button onClick={() => check()} disabled={!!checking || !deployed}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-surface-700 hover:bg-surface-600 text-[12.5px] text-surface-100 disabled:opacity-40">
          {checking === '*' ? <Loader2 size={13} className="animate-spin" /> : <ListChecks size={13} />} Check all
        </button>
        <div className="flex-1" />
        <button onClick={restart} disabled={resetting}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[12.5px] text-surface-300 hover:text-red-200 hover:bg-red-900/30 disabled:opacity-40">
          {resetting ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />} Restart lesson
        </button>
      </div>
    </Frame>
  );
}

function firstOpen(d: LessonPanelData): string | null {
  return (d.lesson.tasks.find(t => !d.attempt.results[t.id]?.passed) ?? d.lesson.tasks[0])?.id ?? null;
}

function Frame({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="relative w-[360px] h-full flex flex-col bg-surface-800 text-surface-200">
      <button onClick={onClose} title="Hide the lesson panel"
        className="absolute top-2.5 right-2.5 z-10 p-1 rounded text-surface-400 hover:text-surface-100 hover:bg-surface-700"><X size={15} /></button>
      {children}
    </div>
  );
}

function Tab({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`flex items-center gap-1 px-2.5 py-1 rounded text-[12px] ${on ? 'bg-surface-600 text-white' : 'text-surface-400 hover:text-surface-100'}`}>
      {children}
    </button>
  );
}

function Dot({ n, r, current }: { n: number; r?: TaskResult; current: boolean }) {
  if (r?.passed) return <span className="w-5 h-5 shrink-0 rounded-full bg-emerald-500 text-white flex items-center justify-center"><Check size={12} strokeWidth={3} /></span>;
  const ring = r ? 'border-red-400 text-red-300' : current ? 'border-sky-400 text-sky-300' : 'border-surface-500 text-surface-400';
  return <span className={`w-5 h-5 shrink-0 rounded-full border-2 text-[10.5px] font-semibold flex items-center justify-center ${ring}`}>{n}</span>;
}

function Checks({ task, result }: { task: { type: string; describe: string }[]; result?: TaskResult }) {
  const [shown, setShown] = useState<number | null>(null);
  if (!task.length) return null;
  return (
    <div className="mt-2 rounded-lg bg-surface-900/50 border border-surface-700 px-2.5 py-1.5">
      <div className="text-[10.5px] uppercase tracking-wider text-surface-500 mb-0.5">Checks</div>
      {result?.changed_since && (
        <div className="text-[11.5px] text-sky-300/90 mb-1">Passed earlier — later tasks have changed what these checks look at, which is expected.</div>
      )}
      {task.map((c, i) => {
        const cr = result?.checks[i];
        return (
          <div key={i} className="py-0.5">
            <button onClick={() => cr && setShown(shown === i ? null : i)} className="w-full flex items-start gap-1.5 text-left">
              <span className={`text-[12px] mt-px ${cr ? (cr.ok ? 'text-emerald-400' : 'text-red-400') : 'text-surface-500'}`}>{cr ? (cr.ok ? '✓' : '✗') : '•'}</span>
              <span className="text-[12px] text-surface-300 leading-snug">{c.describe}
                {cr && !cr.ok && cr.detail && <span className="block text-red-300/90">{cr.detail}</span>}</span>
            </button>
            {shown === i && cr?.output && (
              <pre className="mt-1 max-h-48 overflow-auto rounded bg-black/40 p-2 text-[11px] text-surface-300 font-mono whitespace-pre-wrap">{cr.output}</pre>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Banner on a lesson's authoring lab: save the canvas back into the lesson,
 *  test the lesson's checks against this lab, go back to the editor. */
export function AuthoringBar() {
  const { topology, closeTopology, lab } = useLabStore();
  const [capture, setCapture] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [results, setResults] = useState<Record<string, TaskResult> | null>(null);
  const lessonId = topology?.lesson_id;
  if (!lessonId) return null;
  const deployed = !!lab && !['idle', 'stopped'].includes(lab.status);

  const save = async () => {
    setSaving(true);
    try {
      const r = await api.saveLessonAuthoring(lessonId, capture);
      toast.success(`Saved to the lesson (${r.topology_summary || 'empty topology'})`
        + (r.captured.length ? ` · starting configs from ${r.captured.join(', ')}` : ''));
      if (r.failed.length) toast.error(`Couldn't read: ${r.failed.map(f => f.device).join(', ')}`);
      if (r.warnings.length) toast(`Task problems: ${r.warnings.slice(0, 3).join('; ')}`, { icon: '⚠️', duration: 8000 });
    } catch (e) { toast.error((e as Error).message); }
    finally { setSaving(false); }
  };
  const test = async () => {
    setTesting(true);
    try { setResults(await api.testLessonChecks(lessonId)); } catch (e) { toast.error((e as Error).message); }
    finally { setTesting(false); }
  };

  return (
    <div className="absolute top-[72px] left-1/2 -translate-x-1/2 z-30 max-w-[calc(100%-32px)]">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 rounded-xl bg-white border border-violet-300 shadow-lg text-slate-800 text-[13px]">
        <span className="font-semibold text-violet-700">Editing the lesson topology</span>
        <label className="flex items-center gap-1.5 text-[12.5px] text-slate-600" title="Each running device's live configuration becomes its starting configuration in the lesson">
          <input type="checkbox" checked={capture} onChange={e => setCapture(e.target.checked)} disabled={!deployed} />
          use running configs as starting configs
        </label>
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-medium disabled:opacity-50">
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} Save to lesson
        </button>
        <button onClick={test} disabled={testing || !deployed} title={deployed ? 'Run every task\'s checks against this lab' : 'Deploy this lab first'}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50 disabled:opacity-40">
          {testing ? <Loader2 size={13} className="animate-spin" /> : <ListChecks size={13} />} Test checks
        </button>
        <button onClick={closeTopology} className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-slate-600 hover:bg-slate-100">
          <ArrowLeft size={13} /> Back to lesson
        </button>
      </div>
      {results && (
        <div className="mt-2 max-h-[50vh] overflow-y-auto rounded-xl bg-white border border-slate-200 shadow-lg p-3 text-[12.5px]">
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold">Check results on this lab</span>
            <button onClick={() => setResults(null)} className="p-0.5 rounded hover:bg-slate-100"><X size={14} /></button>
          </div>
          {Object.entries(results).map(([tid, r]) => (
            <div key={tid} className="py-1 border-b last:border-b-0 border-slate-100">
              <div className={`font-medium ${r.passed ? 'text-emerald-700' : 'text-red-600'}`}>{r.passed ? '✓' : '✗'} {tid}</div>
              {r.checks.filter(c => !c.ok).map((c, i) => <div key={i} className="pl-4 text-slate-500">✗ {c.describe} — {c.detail}</div>)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

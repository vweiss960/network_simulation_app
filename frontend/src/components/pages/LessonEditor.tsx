/**
 * Lesson editor (admins). Four tabs:
 *  - Details: title, summary, level, track, topic, tags, time, AI policy, hints.
 *  - Topology: preview + "Edit on canvas" (the lesson's authoring lab) + raw YAML.
 *  - Tasks: task list; per task its body, hints, reference solution per device
 *    and the checks that grade it.
 *  - Content: the written lesson in markdown, with a preview.
 */
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowDown, ArrowLeft, ArrowUp, Eye, Loader2, Network, Plus, Save, Trash2, X } from 'lucide-react';
import { api, type LessonPayload } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { AIPolicy, HintsMode, LessonCheck, LessonDetail, LessonLevel, LessonTask, LessonTrack } from '../../types';
import Markdown from '../agent/Markdown';
import TopologyPreview from '../lesson/TopologyPreview';
import { AI_HELP, AI_LABEL, HINTS_LABEL, LEVEL_LABEL, LEVELS, TRACK_LABEL, TRACKS } from '../lesson/meta';

type Meta = {
  slug: string; title: string; summary: string; level: LessonLevel; track: LessonTrack; topic: string; tags: string[];
  minutes: number; ai_policy: AIPolicy; hints_mode: HintsMode; status: 'draft' | 'published'; version: string; author: string;
};
const EMPTY_TOPO = 'name: lab\nnodes: []\nlinks: []\n';
const NEW_META: Meta = {
  slug: '', title: '', summary: '', level: 'beginner', track: 'general', topic: '', tags: [], minutes: 30,
  ai_policy: 'hints', hints_mode: 'open', status: 'draft', version: '1.0', author: '',
};
const DEFAULT_HINTS: Record<LessonLevel, HintsMode> = { beginner: 'open', intermediate: 'on_request', advanced: 'none' };
type Tab = 'details' | 'topology' | 'tasks' | 'content';

const field = 'w-full px-3 py-2 text-sm rounded-lg bg-white border border-slate-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none';
const label = 'block text-[12px] font-semibold text-slate-600 mb-1';

export default function LessonEditor() {
  const { lessonId, openLesson, setPage, editLesson, openTopology } = useLabStore();
  const [detail, setDetail] = useState<LessonDetail | null>(null);
  const [meta, setMeta] = useState<Meta>(NEW_META);
  const [topo, setTopo] = useState(EMPTY_TOPO);
  const [tasks, setTasks] = useState<LessonTask[]>([]);
  const [content, setContent] = useState('');
  const [tab, setTab] = useState<Tab>('details');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(!!lessonId);

  const load = async (id: number) => {
    const d = await api.getLesson(id, true);
    setDetail(d);
    setMeta({ slug: d.slug, title: d.title, summary: d.summary, level: d.level, track: d.track, topic: d.topic, tags: d.tags,
              minutes: d.minutes, ai_policy: d.ai_policy, hints_mode: d.hints_mode, status: d.status, version: d.version, author: d.author });
    setTopo(d.topology_yaml || EMPTY_TOPO);
    setTasks(d.tasks as LessonTask[]);
    setContent(d.content_md);
    setDirty(false);
  };
  useEffect(() => {
    if (!lessonId) { setDetail(null); setMeta(NEW_META); setTopo(EMPTY_TOPO); setTasks([]); setContent(''); setLoading(false); return; }
    setLoading(true);
    load(lessonId).catch(e => toast.error((e as Error).message)).finally(() => setLoading(false));
  }, [lessonId]);

  const devices = useMemo(() => (detail?.topology.nodes ?? []).filter(n => n.kind !== 'bridge').map(n => n.name), [detail]);
  const payload = (): LessonPayload => ({ meta, topology_yaml: topo, tasks, content_md: content });
  const change = <T,>(fn: (v: T) => void) => (v: T) => { fn(v); setDirty(true); };

  const save = async (): Promise<number | null> => {
    setSaving(true);
    try {
      const v = await api.validateLesson(payload());
      setErrors(v.errors);
      if (v.errors.length) { toast.error(`${v.errors.length} problem${v.errors.length === 1 ? '' : 's'} to fix`); return null; }
      const r = lessonId ? await api.updateLesson(lessonId, payload()) : await api.createLesson(payload());
      setDirty(false);
      toast.success('Lesson saved');
      if (!lessonId) editLesson(r.id); else await load(r.id);
      return r.id;
    } catch (e) { toast.error((e as Error).message); return null; }
    finally { setSaving(false); }
  };

  const editOnCanvas = async () => {
    const id = dirty || !lessonId ? await save() : lessonId;
    if (!id) return;
    try {
      const r = await api.openLessonAuthoring(id);
      openTopology(r.topology_id);
    } catch (e) { toast.error((e as Error).message); }
  };

  const remove = async () => {
    if (!lessonId || !confirm(`Delete the lesson "${meta.title}"? Learners' labs stay, but their progress is removed.`)) return;
    try { await api.deleteLesson(lessonId); toast.success('Lesson deleted'); setPage('lessons'); }
    catch (e) { toast.error((e as Error).message); }
  };

  if (loading) return <div className="h-full flex items-center justify-center text-slate-400 bg-[#f3f5f9]"><Loader2 className="animate-spin" /></div>;

  return (
    <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800">
      <div className="max-w-[1160px] mx-auto px-8 py-7">
        <button onClick={() => (lessonId ? openLesson(lessonId) : setPage('lessons'))}
          className="flex items-center gap-1 text-[13px] text-blue-700 hover:underline mb-3">
          <ArrowLeft size={14} /> {lessonId ? 'Back to the lesson' : 'Lessons'}
        </button>
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div>
            <div className="text-[12px] font-semibold uppercase tracking-wider text-blue-700">{lessonId ? 'Edit lesson' : 'New lesson'}</div>
            <h1 className="text-[24px] font-bold text-slate-900">{meta.title || 'Untitled lesson'}</h1>
          </div>
          <div className="flex items-center gap-2">
            {dirty && <span className="text-[12.5px] text-amber-700">Unsaved changes</span>}
            <select value={meta.status} onChange={e => change(setMeta)({ ...meta, status: e.target.value as Meta['status'] })}
              className="px-2 py-2 text-sm rounded-lg bg-white border border-slate-300">
              <option value="draft">Draft (admins only)</option>
              <option value="published">Published</option>
            </select>
            <button onClick={save} disabled={saving}
              className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-700 hover:bg-blue-600 text-white shadow-sm disabled:opacity-50">
              {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save
            </button>
          </div>
        </div>

        {errors.length > 0 && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-[13px] text-red-700">
            <div className="flex items-center justify-between font-semibold mb-1">Fix these before saving
              <button onClick={() => setErrors([])}><X size={14} /></button></div>
            <ul className="list-disc pl-5">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </div>
        )}

        <div className="flex gap-1 mb-4 border-b border-slate-300">
          {(['details', 'topology', 'tasks', 'content'] as Tab[]).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm -mb-px border-b-2 ${tab === t ? 'border-blue-600 text-blue-700 font-semibold' : 'border-transparent text-slate-600 hover:text-slate-900'}`}>
              {t === 'details' ? 'Details' : t === 'topology' ? 'Topology' : t === 'tasks' ? `Tasks (${tasks.length})` : 'Content'}
            </button>
          ))}
        </div>

        {tab === 'details' && <Details meta={meta} setMeta={change(setMeta)} onDelete={lessonId ? remove : undefined} />}
        {tab === 'topology' && (
          <Panel>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <div className="text-[13px] text-slate-600 max-w-[640px]">
                Build the starting topology on a real canvas: add devices, cable them and give them starting configs
                (a device's Startup config). Deploy it to configure devices live, then <b>Save to lesson</b> — optionally
                using their running configs as the starting state.
              </div>
              <button onClick={editOnCanvas}
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-violet-600 hover:bg-violet-500 text-white">
                <Network size={15} /> Edit on canvas
              </button>
            </div>
            {detail ? <TopologyPreview topology={detail.topology} height={360} />
              : <div className="p-6 text-sm text-slate-500 rounded-lg border border-dashed border-slate-300">Save the lesson to edit its topology.</div>}
            <details className="mt-3">
              <summary className="cursor-pointer text-[13px] text-slate-600">Topology YAML (advanced)</summary>
              <textarea value={topo} onChange={e => change(setTopo)(e.target.value)} spellCheck={false}
                className={`${field} mt-2 h-72 font-mono text-[12px]`} />
            </details>
          </Panel>
        )}
        {tab === 'tasks' && <TasksEditor tasks={tasks} setTasks={change(setTasks)} devices={devices} />}
        {tab === 'content' && (
          <Panel>
            <div className="text-[13px] text-slate-600 mb-2">Markdown. Each <code className="text-[12px] bg-slate-100 px-1 rounded">## heading</code> starts a reading the learner opens on its own.</div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <textarea value={content} onChange={e => change(setContent)(e.target.value)} spellCheck={false}
                className={`${field} h-[560px] font-mono text-[12.5px]`} placeholder={'## Why this matters\n\n…'} />
              <div className="h-[560px] overflow-y-auto rounded-lg border border-slate-200 bg-white px-4 py-2"><Markdown text={content || '_Nothing written yet._'} /></div>
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}

function Panel({ children }: { children: React.ReactNode }) {
  return <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-5">{children}</div>;
}

function Details({ meta, setMeta, onDelete }: { meta: Meta; setMeta: (m: Meta) => void; onDelete?: () => void }) {
  const set = <K extends keyof Meta>(k: K, v: Meta[K]) => setMeta({ ...meta, [k]: v });
  const [tagText, setTagText] = useState(meta.tags.join(', '));
  return (
    <Panel>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="md:col-span-2"><label className={label}>Title</label>
          <input className={field} value={meta.title} onChange={e => set('title', e.target.value)} placeholder="OSPF multi-area and area types" /></div>
        <div className="md:col-span-2"><label className={label}>Summary</label>
          <textarea className={`${field} h-20`} value={meta.summary} onChange={e => set('summary', e.target.value)} /></div>
        <div><label className={label}>Level</label>
          <select className={field} value={meta.level} onChange={e => {
            const level = e.target.value as LessonLevel;
            setMeta({ ...meta, level, hints_mode: DEFAULT_HINTS[level] });
          }}>{LEVELS.map(l => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}</select></div>
        <div><label className={label}>Track</label>
          <select className={field} value={meta.track} onChange={e => set('track', e.target.value as LessonTrack)}>
            {TRACKS.map(t => <option key={t} value={t}>{TRACK_LABEL[t]}</option>)}</select></div>
        <div><label className={label}>Topic</label>
          <input className={field} value={meta.topic} onChange={e => set('topic', e.target.value)} placeholder="Routing" /></div>
        <div><label className={label}>Tags (comma separated)</label>
          <input className={field} value={tagText} placeholder="ospf, iosxr"
            onChange={e => { setTagText(e.target.value); set('tags', e.target.value.split(',').map(t => t.trim().toLowerCase()).filter(Boolean)); }} /></div>
        <div><label className={label}>Estimated time (minutes)</label>
          <input type="number" min={5} className={field} value={meta.minutes} onChange={e => set('minutes', Number(e.target.value) || 30)} /></div>
        <div><label className={label}>Slug (pack id — blank: from the title)</label>
          <input className={`${field} font-mono`} value={meta.slug} onChange={e => set('slug', e.target.value)} placeholder="ospf-xr-multiarea" /></div>
        <div><label className={label}>AI assistance</label>
          <select className={field} value={meta.ai_policy} onChange={e => set('ai_policy', e.target.value as AIPolicy)}>
            {(['teach', 'hints', 'off'] as AIPolicy[]).map(p => <option key={p} value={p}>{AI_LABEL[p]}</option>)}</select>
          <div className="text-[12px] text-slate-500 mt-1">{AI_HELP[meta.ai_policy]}</div></div>
        <div><label className={label}>Hints &amp; solutions</label>
          <select className={field} value={meta.hints_mode} onChange={e => set('hints_mode', e.target.value as HintsMode)}>
            {(['open', 'on_request', 'none'] as HintsMode[]).map(h => <option key={h} value={h}>{HINTS_LABEL[h]}</option>)}</select>
          <div className="text-[12px] text-slate-500 mt-1">Defaults from the level; "None" also hides solutions.</div></div>
        <div><label className={label}>Version</label>
          <input className={field} value={meta.version} onChange={e => set('version', e.target.value)} /></div>
        <div><label className={label}>Author</label>
          <input className={field} value={meta.author} onChange={e => set('author', e.target.value)} /></div>
      </div>
      {onDelete && (
        <div className="mt-6 pt-4 border-t border-slate-200">
          <button onClick={onDelete} className="flex items-center gap-1.5 text-[13px] text-red-600 hover:text-red-500"><Trash2 size={14} /> Delete lesson</button>
        </div>
      )}
    </Panel>
  );
}

// --- tasks -------------------------------------------------------------------------

const CHECK_TYPES: LessonCheck['type'][] = ['route', 'cli', 'ping', 'config'];
const CHECK_HELP: Record<LessonCheck['type'], string> = {
  route: 'A route is (or isn\'t) in the device\'s routing table.',
  cli: 'Run a command and test its output.',
  ping: 'Ping from a device; pass at a minimum success rate.',
  config: 'Test the device\'s running configuration.',
};
const EXPECT_KINDS = ['regex', 'contains', 'not_regex', 'not_contains'] as const;

function newTask(n: number): LessonTask {
  return { id: `task-${n}`, title: '', body: '', hints: [], solution: {}, checks: [] };
}

function TasksEditor({ tasks, setTasks, devices }: { tasks: LessonTask[]; setTasks: (t: LessonTask[]) => void; devices: string[] }) {
  const [sel, setSel] = useState(0);
  const t = tasks[sel];
  const update = (patch: Partial<LessonTask>) => setTasks(tasks.map((x, i) => (i === sel ? { ...x, ...patch } : x)));
  const move = (d: -1 | 1) => {
    const j = sel + d;
    if (j < 0 || j >= tasks.length) return;
    const next = [...tasks];
    [next[sel], next[j]] = [next[j], next[sel]];
    setTasks(next);
    setSel(j);
  };
  const add = () => {
    let n = tasks.length + 1;
    while (tasks.some(x => x.id === `task-${n}`)) n++;
    setTasks([...tasks, newTask(n)]);
    setSel(tasks.length);
  };
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)] gap-4">
      <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-2 self-start">
        {tasks.map((x, i) => (
          <button key={i} onClick={() => setSel(i)}
            className={`w-full text-left px-3 py-2 rounded-lg text-[13px] ${i === sel ? 'bg-blue-50 text-blue-800 font-semibold' : 'hover:bg-slate-50 text-slate-700'}`}>
            <span className="text-slate-400 mr-1.5">{i + 1}.</span>{x.title || <em className="text-slate-400">Untitled</em>}
          </button>
        ))}
        <button onClick={add} className="w-full mt-1 flex items-center gap-1.5 px-3 py-2 rounded-lg text-[13px] text-blue-700 hover:bg-blue-50">
          <Plus size={14} /> Add task
        </button>
      </div>
      {!t ? <Panel><div className="text-sm text-slate-500">No tasks yet. Add the first one.</div></Panel> : (
        <Panel>
          <div className="flex items-center justify-between mb-3">
            <div className="text-[13px] font-semibold text-slate-500">Task {sel + 1}</div>
            <div className="flex gap-1">
              <IconBtn title="Move up" onClick={() => move(-1)}><ArrowUp size={14} /></IconBtn>
              <IconBtn title="Move down" onClick={() => move(1)}><ArrowDown size={14} /></IconBtn>
              <IconBtn title="Delete task" onClick={() => { if (confirm('Delete this task?')) { setTasks(tasks.filter((_, i) => i !== sel)); setSel(Math.max(0, sel - 1)); } }}>
                <Trash2 size={14} className="text-red-600" /></IconBtn>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-[1fr_200px] gap-3">
            <div><label className={label}>Title</label><input className={field} value={t.title} onChange={e => update({ title: e.target.value })} /></div>
            <div><label className={label}>Id</label><input className={`${field} font-mono`} value={t.id} onChange={e => update({ id: e.target.value })} /></div>
          </div>
          <BodyField value={t.body} onChange={body => update({ body })} />

          <Section title="Checks" help="Every check must pass for the task to pass. Devices are names from the topology.">
            {t.checks.map((c, i) => (
              <CheckEditor key={i} c={c} devices={devices}
                onChange={nc => update({ checks: t.checks.map((x, j) => (j === i ? nc : x)) })}
                onDelete={() => update({ checks: t.checks.filter((_, j) => j !== i) })} />
            ))}
            <button onClick={() => update({ checks: [...t.checks, { type: 'route', device: devices[0] ?? '', prefix: '', present: true }] })}
              className="flex items-center gap-1.5 text-[13px] text-blue-700 hover:underline"><Plus size={14} /> Add check</button>
          </Section>

          <Section title="Hints" help="Revealed one at a time (or all, for beginner lessons).">
            {t.hints.map((h, i) => (
              <div key={i} className="flex gap-2 mb-2">
                <textarea className={`${field} h-16`} value={h} onChange={e => update({ hints: t.hints.map((x, j) => (j === i ? e.target.value : x)) })} />
                <IconBtn title="Remove hint" onClick={() => update({ hints: t.hints.filter((_, j) => j !== i) })}><X size={14} /></IconBtn>
              </div>
            ))}
            <button onClick={() => update({ hints: [...t.hints, ''] })} className="flex items-center gap-1.5 text-[13px] text-blue-700 hover:underline"><Plus size={14} /> Add hint</button>
          </Section>

          <Section title="Reference solution" help="Config per device that solves the task: shown on request, and used by the solve test.">
            {Object.entries(t.solution).map(([dev, cfg]) => (
              <div key={dev} className="mb-2">
                <div className="flex items-center justify-between"><span className="text-[13px] font-semibold">{dev}</span>
                  <IconBtn title="Remove" onClick={() => { const s = { ...t.solution }; delete s[dev]; update({ solution: s }); }}><X size={14} /></IconBtn></div>
                <textarea className={`${field} h-32 font-mono text-[12px]`} value={cfg} spellCheck={false}
                  onChange={e => update({ solution: { ...t.solution, [dev]: e.target.value } })} />
              </div>
            ))}
            <select className="px-2 py-1.5 text-[13px] rounded-lg border border-slate-300" value=""
              onChange={e => e.target.value && update({ solution: { ...t.solution, [e.target.value]: '' } })}>
              <option value="">+ Add a device's solution…</option>
              {devices.filter(d => !(d in t.solution)).map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </Section>
        </Panel>
      )}
    </div>
  );
}

function BodyField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [preview, setPreview] = useState(false);
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between"><label className={label}>What the learner does (markdown)</label>
        <button onClick={() => setPreview(p => !p)} className="flex items-center gap-1 text-[12px] text-blue-700"><Eye size={13} /> {preview ? 'Edit' : 'Preview'}</button></div>
      {preview ? <div className="min-h-[140px] rounded-lg border border-slate-200 px-3 py-1"><Markdown text={value} /></div>
        : <textarea className={`${field} h-36 font-mono text-[12.5px]`} value={value} onChange={e => onChange(e.target.value)} />}
    </div>
  );
}

function Section({ title, help, children }: { title: string; help: string; children: React.ReactNode }) {
  return (
    <div className="mt-5">
      <div className="text-[14px] font-semibold text-slate-900">{title}</div>
      <div className="text-[12px] text-slate-500 mb-2">{help}</div>
      {children}
    </div>
  );
}

function IconBtn({ title, onClick, children }: { title: string; onClick: () => void; children: React.ReactNode }) {
  return <button title={title} onClick={onClick} className="p-1.5 rounded-md hover:bg-slate-100 text-slate-600">{children}</button>;
}

function CheckEditor({ c, devices, onChange, onDelete }:
  { c: LessonCheck; devices: string[]; onChange: (c: LessonCheck) => void; onDelete: () => void }) {
  const set = (patch: Partial<LessonCheck>) => onChange({ ...c, ...patch });
  const devField = (key: 'device' | 'from') => (
    <select className={field} value={(c[key] as string) ?? ''} onChange={e => set({ [key]: e.target.value })}>
      <option value="">Device…</option>
      {[...new Set([...devices, ...(c[key] ? [c[key] as string] : [])])].map(d => <option key={d} value={d}>{d}</option>)}
    </select>
  );
  const expect = c.expect ?? [];
  return (
    <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3">
      <div className="flex items-start gap-2">
        <select className="px-2 py-2 text-sm rounded-lg bg-white border border-slate-300" value={c.type}
          onChange={e => {
            const type = e.target.value as LessonCheck['type'];
            const dev = c.device ?? c.from ?? devices[0] ?? '';
            onChange(type === 'ping' ? { type, from: dev, to: '', count: 5, min_success: 80, describe: c.describe }
              : type === 'route' ? { type, device: dev, prefix: '', present: true, describe: c.describe }
              : type === 'cli' ? { type, device: dev, command: '', expect: [{ regex: '' }], describe: c.describe }
              : { type, device: dev, expect: [{ regex: '' }], describe: c.describe });
          }}>
          {CHECK_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <div className="flex-1 text-[12px] text-slate-500 pt-2.5">{CHECK_HELP[c.type]}</div>
        <IconBtn title="Delete check" onClick={onDelete}><Trash2 size={14} className="text-red-600" /></IconBtn>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
        {c.type === 'ping' ? <>
          <div><label className={label}>From</label>{devField('from')}</div>
          <div><label className={label}>To (IP)</label><input className={field} value={c.to ?? ''} onChange={e => set({ to: e.target.value })} /></div>
          <div><label className={label}>Source (optional)</label><input className={field} value={c.source ?? ''} onChange={e => set({ source: e.target.value || undefined })} /></div>
          <div><label className={label}>Min success %</label><input type="number" className={field} value={c.min_success ?? 80} onChange={e => set({ min_success: Number(e.target.value) })} /></div>
        </> : <div><label className={label}>Device</label>{devField('device')}</div>}
        {c.type === 'route' && <>
          <div><label className={label}>Prefix</label><input className={`${field} font-mono`} value={c.prefix ?? ''} placeholder="10.0.0.0/24" onChange={e => set({ prefix: e.target.value })} /></div>
          <div><label className={label}>Protocol (optional)</label><input className={`${field} font-mono`} value={c.protocol ?? ''} placeholder="O IA" onChange={e => set({ protocol: e.target.value || undefined })} /></div>
          <div><label className={label}>Expect</label>
            <select className={field} value={c.present === false ? 'absent' : 'present'} onChange={e => set({ present: e.target.value === 'present' })}>
              <option value="present">present</option><option value="absent">absent</option></select></div>
        </>}
        {c.type === 'cli' && <div className="col-span-2 md:col-span-3"><label className={label}>Command</label>
          <input className={`${field} font-mono`} value={c.command ?? ''} placeholder="show ospf neighbor" onChange={e => set({ command: e.target.value })} /></div>}
      </div>
      {(c.type === 'cli' || c.type === 'config') && (
        <div className="mt-2">
          <label className={label}>The output must…</label>
          {expect.map((ex, i) => {
            const kind = (EXPECT_KINDS.find(k => k in ex) ?? 'regex') as typeof EXPECT_KINDS[number];
            const val = String(ex[kind] ?? '');
            const put = (k: typeof EXPECT_KINDS[number], v: string) => set({ expect: expect.map((x, j) => (j === i ? { [k]: v } : x)) });
            return (
              <div key={i} className="flex gap-2 mb-1.5">
                <select className="px-2 py-1.5 text-[13px] rounded-lg bg-white border border-slate-300" value={kind} onChange={e => put(e.target.value as typeof kind, val)}>
                  <option value="regex">match regex</option><option value="contains">contain</option>
                  <option value="not_regex">not match regex</option><option value="not_contains">not contain</option>
                </select>
                <input className={`${field} font-mono text-[12.5px]`} value={val} onChange={e => put(kind, e.target.value)} />
                <IconBtn title="Remove" onClick={() => set({ expect: expect.filter((_, j) => j !== i) })}><X size={14} /></IconBtn>
              </div>
            );
          })}
          <button onClick={() => set({ expect: [...expect, { regex: '' }] })} className="text-[12.5px] text-blue-700 hover:underline">+ Add condition</button>
        </div>
      )}
      <div className="mt-2"><label className={label}>Shown to the learner (optional)</label>
        <input className={field} value={c.describe ?? ''} placeholder="e.g. R2: show ospf neighbor → R1 FULL" onChange={e => set({ describe: e.target.value || undefined })} /></div>
    </div>
  );
}

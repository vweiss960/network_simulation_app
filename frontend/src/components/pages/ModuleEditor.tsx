/**
 * Module editor (admins). Three tabs:
 *  - Details: title, summary, level, track, blueprint, tags, version.
 *  - Lessons: the ordered lessons, each with the blueprint section, title and
 *    objectives it covers.
 *  - Overview: the module introduction in markdown, with a preview.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowDown, ArrowLeft, ArrowUp, Eye, Loader2, Plus, Save, Siren, Trash2, X } from 'lucide-react';
import { api, type ModulePayload } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { LessonSummary, ModuleItem, ModuleMeta, ScenarioSummary } from '../../types';
import Markdown from '../agent/Markdown';
import { LEVEL_LABEL, LEVELS, TRACK_LABEL, TRACKS } from '../lesson/meta';

const NEW_META: ModuleMeta = {
  slug: '', title: '', summary: '', level: 'intermediate', track: 'general', blueprint: '', tags: [],
  status: 'draft', version: '1.0', author: '',
};
type Tab = 'details' | 'lessons' | 'overview';

const field = 'w-full px-3 py-2 text-sm rounded-lg bg-white border border-slate-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none';
const label = 'block text-[12px] font-semibold text-slate-600 mb-1';

export default function ModuleEditor() {
  const { moduleId, openModule, setPage, editModule } = useLabStore();
  const [meta, setMeta] = useState<ModuleMeta>(NEW_META);
  const [items, setItems] = useState<ModuleItem[]>([]);
  const [overview, setOverview] = useState('');
  const [lessons, setLessons] = useState<LessonSummary[]>([]);
  const [tab, setTab] = useState<Tab>('details');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [preview, setPreview] = useState(false);
  const [loading, setLoading] = useState(!!moduleId);

  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  useEffect(() => {
    api.listLessons().then(setLessons).catch(() => setLessons([]));
    api.listScenarios().then(setScenarios).catch(() => setScenarios([]));
  }, []);
  const load = async (id: number) => {
    const d = await api.getModule(id);
    setMeta({ slug: d.slug, title: d.title, summary: d.summary, level: d.level, track: d.track, blueprint: d.blueprint,
              tags: d.tags, status: d.status, version: d.version, author: d.author });
    setItems(d.items.map(({ kind, lesson, scenario, section, title, objectives }) => ({ kind, lesson, scenario, section, title, objectives })));
    setOverview(d.overview_md);
    setDirty(false);
  };
  useEffect(() => {
    if (!moduleId) { setMeta(NEW_META); setItems([]); setOverview(''); setLoading(false); return; }
    setLoading(true);
    load(moduleId).catch(e => toast.error((e as Error).message)).finally(() => setLoading(false));
  }, [moduleId]);

  const payload = (): ModulePayload => ({ meta, items, overview_md: overview });
  const setM = (patch: Partial<ModuleMeta>) => { setMeta(m => ({ ...m, ...patch })); setDirty(true); };
  const setItem = (i: number, patch: Partial<ModuleItem>) => { setItems(xs => xs.map((x, j) => (j === i ? { ...x, ...patch } : x))); setDirty(true); };
  const move = (i: number, d: -1 | 1) => {
    setItems(xs => { const c = [...xs]; [c[i], c[i + d]] = [c[i + d], c[i]]; return c; });
    setDirty(true);
  };
  const byslug = new Map(lessons.map(l => [l.slug, l]));
  const scBySlug = new Map(scenarios.map(x => [x.slug, x]));
  const isScenario = (i: ModuleItem) => i.kind === 'scenario';
  const unused = lessons.filter(l => !items.some(i => !isScenario(i) && i.lesson === l.slug));
  const unusedSc = scenarios.filter(x => !items.some(i => isScenario(i) && i.scenario === x.slug));
  const add = (value: string) => {
    if (!value) return;
    const [kind, slug] = value.split(':') as ['lesson' | 'scenario', string];
    setItems(xs => [...xs, kind === 'scenario'
      ? { kind, lesson: '', scenario: slug, section: '', title: '', objectives: [] }
      : { kind, lesson: slug, section: '', title: '', objectives: [] }]);
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const v = await api.validateModule(payload());
      setErrors(v.errors);
      if (v.errors.length) { toast.error(`${v.errors.length} problem${v.errors.length === 1 ? '' : 's'} to fix`); return; }
      const r = moduleId ? await api.updateModule(moduleId, payload()) : await api.createModule(payload());
      setDirty(false);
      toast.success('Module saved');
      if (!moduleId) editModule(r.id); else await load(r.id);
    } catch (e) { toast.error((e as Error).message); }
    finally { setSaving(false); }
  };

  const remove = async () => {
    if (!moduleId || !confirm(`Delete the module "${meta.title}"? Its lessons are kept.`)) return;
    try { await api.deleteModule(moduleId); toast.success('Module deleted'); setPage('modules'); }
    catch (e) { toast.error((e as Error).message); }
  };

  if (loading) return <div className="h-full flex items-center justify-center text-slate-400 bg-[#f3f5f9]"><Loader2 className="animate-spin" /></div>;

  return (
    <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800">
      <div className="max-w-[1000px] mx-auto px-8 py-7">
        <button onClick={() => (moduleId ? openModule(moduleId) : setPage('modules'))}
          className="flex items-center gap-1 text-[13px] text-blue-700 hover:underline mb-3">
          <ArrowLeft size={14} /> {moduleId ? 'Back to the module' : 'Modules'}
        </button>
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div>
            <div className="text-[12px] font-semibold uppercase tracking-wider text-blue-700">{moduleId ? 'Edit module' : 'New module'}</div>
            <h1 className="text-[24px] font-bold text-slate-900">{meta.title || 'Untitled module'}</h1>
          </div>
          <div className="flex items-center gap-2">
            {dirty && <span className="text-[12.5px] text-amber-700">Unsaved changes</span>}
            <select value={meta.status} onChange={e => setM({ status: e.target.value as ModuleMeta['status'] })}
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
          {(['details', 'lessons', 'overview'] as Tab[]).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm -mb-px border-b-2 ${tab === t ? 'border-blue-600 text-blue-700 font-semibold' : 'border-transparent text-slate-600 hover:text-slate-900'}`}>
              {t === 'details' ? 'Details' : t === 'lessons' ? `Lessons (${items.length})` : 'Overview'}
            </button>
          ))}
        </div>

        {tab === 'details' && (
          <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-5 grid grid-cols-2 gap-4">
            <div className="col-span-2"><label className={label}>Title</label>
              <input className={field} value={meta.title} onChange={e => setM({ title: e.target.value })} placeholder="Service Provider Networking: IS-IS and OSPF" /></div>
            <div><label className={label}>Slug</label>
              <input className={`${field} font-mono`} value={meta.slug} onChange={e => setM({ slug: e.target.value })} placeholder="generated from the title" /></div>
            <div><label className={label}>Blueprint</label>
              <input className={field} value={meta.blueprint} onChange={e => setM({ blueprint: e.target.value })} placeholder="CCNP SPCOR 350-501 · 2.0 Networking" /></div>
            <div className="col-span-2"><label className={label}>Summary</label>
              <textarea className={field} rows={3} value={meta.summary} onChange={e => setM({ summary: e.target.value })} /></div>
            <div><label className={label}>Level</label>
              <select className={field} value={meta.level} onChange={e => setM({ level: e.target.value as ModuleMeta['level'] })}>
                {LEVELS.map(l => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
              </select></div>
            <div><label className={label}>Track</label>
              <select className={field} value={meta.track} onChange={e => setM({ track: e.target.value as ModuleMeta['track'] })}>
                {TRACKS.map(t => <option key={t} value={t}>{TRACK_LABEL[t]}</option>)}
              </select></div>
            <div><label className={label}>Tags (comma separated)</label>
              <input className={field} value={meta.tags.join(', ')}
                onChange={e => setM({ tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean) })} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className={label}>Version</label><input className={field} value={meta.version} onChange={e => setM({ version: e.target.value })} /></div>
              <div><label className={label}>Author</label><input className={field} value={meta.author} onChange={e => setM({ author: e.target.value })} /></div>
            </div>
            {moduleId && (
              <div className="col-span-2 pt-2 border-t border-slate-100">
                <button onClick={remove} className="flex items-center gap-1.5 text-[13px] text-red-600 hover:underline"><Trash2 size={14} /> Delete module</button>
              </div>
            )}
          </div>
        )}

        {tab === 'lessons' && (
          <div className="space-y-3">
            {items.map((it, i) => {
              const sc = isScenario(it);
              const slug = (sc ? it.scenario : it.lesson) || '';
              const l = sc ? scBySlug.get(slug) : byslug.get(slug);
              return (
                <div key={slug + i} className="rounded-xl bg-white border border-slate-200 shadow-sm p-4">
                  <div className="flex items-start gap-3">
                    <span className="w-7 h-7 shrink-0 rounded-full bg-blue-50 text-blue-700 text-[12px] font-bold flex items-center justify-center">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-[14px] font-semibold text-slate-900 flex items-center gap-1.5">
                        {sc && <Siren size={14} className="text-rose-600" />}{l?.title ?? slug}
                      </div>
                      <div className="text-[12px] text-slate-500 font-mono">{sc ? 'scenario · ' : ''}{slug}{!l && ' — not installed here'}</div>
                      <div className="grid grid-cols-[100px_minmax(0,1fr)] gap-3 mt-3">
                        <div><label className={label}>Section</label>
                          <input className={field} value={it.section} onChange={e => setItem(i, { section: e.target.value })} placeholder="2.1" /></div>
                        <div><label className={label}>Blueprint title</label>
                          <input className={field} value={it.title} onChange={e => setItem(i, { title: e.target.value })} placeholder="Implement IS-IS (IPv4 and IPv6)" /></div>
                        <div className="col-span-2"><label className={label}>Objectives (one per line)</label>
                          <textarea className={`${field} font-mono text-[12.5px]`} rows={Math.max(2, it.objectives.length)}
                            value={it.objectives.join('\n')}
                            onChange={e => setItem(i, { objectives: e.target.value.split('\n') })}
                            onBlur={() => setItem(i, { objectives: it.objectives.map(o => o.trim()).filter(Boolean) })} /></div>
                      </div>
                    </div>
                    <div className="flex flex-col gap-1">
                      <button disabled={i === 0} onClick={() => move(i, -1)} className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-30" title="Move up"><ArrowUp size={15} /></button>
                      <button disabled={i === items.length - 1} onClick={() => move(i, 1)} className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-30" title="Move down"><ArrowDown size={15} /></button>
                      <button onClick={() => { setItems(xs => xs.filter((_, j) => j !== i)); setDirty(true); }}
                        className="p-1.5 rounded hover:bg-red-50 text-red-600" title="Remove from the module"><Trash2 size={15} /></button>
                    </div>
                  </div>
                </div>
              );
            })}
            <div className="rounded-xl border-2 border-dashed border-slate-300 p-4 flex items-center gap-3">
              <Plus size={16} className="text-slate-500" />
              <select className={`${field} max-w-[520px]`} value="" disabled={!unused.length && !unusedSc.length}
                onChange={e => add(e.target.value)}>
                <option value="">{unused.length || unusedSc.length ? 'Add a lesson or troubleshooting scenario…' : 'Everything is already in the module'}</option>
                {unused.length > 0 && (
                  <optgroup label="Lessons">
                    {unused.map(l => <option key={l.slug} value={`lesson:${l.slug}`}>{l.title} ({TRACK_LABEL[l.track]}, {LEVEL_LABEL[l.level]})</option>)}
                  </optgroup>
                )}
                {unusedSc.length > 0 && (
                  <optgroup label="Troubleshooting scenarios">
                    {unusedSc.map(x => <option key={x.slug} value={`scenario:${x.slug}`}>{x.title} ({TRACK_LABEL[x.track]})</option>)}
                  </optgroup>
                )}
              </select>
            </div>
          </div>
        )}

        {tab === 'overview' && (
          <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-5">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[12.5px] text-slate-500">Markdown. Each <code>## </code> heading becomes a collapsible section on the module page.</span>
              <button onClick={() => setPreview(p => !p)} className="flex items-center gap-1 text-[12.5px] px-2 py-1 rounded border border-slate-300 hover:bg-slate-50">
                <Eye size={13} /> {preview ? 'Edit' : 'Preview'}
              </button>
            </div>
            {preview
              ? <div className="min-h-[300px]"><Markdown text={overview} /></div>
              : <textarea className={`${field} font-mono text-[12.5px] min-h-[420px]`} value={overview}
                  onChange={e => { setOverview(e.target.value); setDirty(true); }} placeholder={'## About this module\n\n…'} />}
          </div>
        )}
      </div>
    </div>
  );
}

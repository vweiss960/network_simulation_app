/**
 * ServiceDesignView — full-viewport Service authoring + deploy.
 *
 * Mirrors ConfigDesignView: left = service picker + Templates/Variables tabs,
 * middle = the same CodeMirror Jinja editor (with the backend syntax linter),
 * right = the Deploy panel. An AI drawer (streaming) helps author templates.
 */
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import {
  Plus, Save, Trash2, FileCode, Variable, Sparkles, ChevronUp, ChevronDown,
  Download, Upload, AlertCircle, CheckCircle2,
} from 'lucide-react';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLineGutter, highlightActiveLine } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { bracketMatching, indentOnInput, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language';
import { jinja } from '@codemirror/lang-jinja';
import { linter, lintGutter, type Diagnostic } from '@codemirror/lint';
import { useStore } from '../stores/useStore';
import { api } from '../utils/api';
import ServiceDeployPanel from './ServiceDeployPanel';
import ServiceDesignAIDrawer from './ServiceDesignAIDrawer';

type Template = { id: number; name: string; body: string; position: number };
type Variable = { id: number; name: string; required: boolean; default: any; description: string };
type Service = { id: number; name: string; description: string; target_mode: string; templates: Template[]; variables: Variable[] };

export default function ServiceDesignView() {
  const currentTopologyId = useStore(s => s.currentTopologyId);
  const [services, setServices] = useState<Service[]>([]);
  const [selectedServiceId, setSelectedServiceId] = useState<number | null>(null);
  const [leftTab, setLeftTab] = useState<'templates' | 'variables'>('templates');
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);
  const [draft, setDraft] = useState<{ id: number; name: string; body: string } | null>(null);
  const [validation, setValidation] = useState<{ ok: boolean; error: string; line: number | null }>({ ok: true, error: '', line: null });
  const [aiOpen, setAiOpen] = useState(false);
  const [aiMessages, setAiMessages] = useState<any[]>([]);
  const [newVar, setNewVar] = useState({ name: '', required: false, default: '', description: '' });
  const importRef = useRef<HTMLInputElement | null>(null);

  const editorParentRef = useRef<HTMLDivElement | null>(null);
  const editorViewRef = useRef<EditorView | null>(null);

  const service = services.find(s => s.id === selectedServiceId) || null;

  const reload = async () => {
    const list = await api.listServices();
    setServices(list);
    return list as Service[];
  };

  useEffect(() => {
    reload().then(list => { if (list.length && selectedServiceId == null) setSelectedServiceId(list[0].id); })
      .catch(() => toast.error('Failed to load services'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { setSelectedTemplateId(null); setDraft(null); }, [selectedServiceId]);

  const selectTemplate = (t: Template) => {
    setSelectedTemplateId(t.id);
    setDraft({ id: t.id, name: t.name, body: t.body });
    setLeftTab('templates');
  };

  const applyAgentBody = (body: string) => {
    const view = editorViewRef.current;
    if (view) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: body } });
    setDraft(d => d ? { ...d, body } : d);
  };

  // Build the CodeMirror editor per template (same stack as ConfigDesignView).
  useEffect(() => {
    if (!editorParentRef.current || !draft) return;
    if (editorViewRef.current) return;

    const jinjaLinter = linter(async (view): Promise<Diagnostic[]> => {
      if (selectedServiceId == null) return [];
      const body = view.state.doc.toString();
      try {
        const res = await api.validateServiceTemplate(selectedServiceId, body);
        setValidation(res);
        if (res.ok) return [];
        const line = res.line && res.line > 0 ? res.line : 1;
        const linePos = view.state.doc.line(Math.min(line, view.state.doc.lines));
        return [{ from: linePos.from, to: linePos.to, severity: 'error', message: res.error }];
      } catch { return []; }
    });

    const state = EditorState.create({
      doc: draft.body,
      extensions: [
        lineNumbers(), highlightActiveLineGutter(), highlightActiveLine(),
        history(), indentOnInput(), bracketMatching(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        jinja(), lintGutter(), jinjaLinter,
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        EditorView.theme({
          '&': { height: '100%', fontSize: '13px' },
          '.cm-scroller': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' },
          '&.cm-focused': { outline: 'none' },
          '.cm-gutters': { backgroundColor: '#0f172a', color: '#64748b', border: 'none' },
          '.cm-activeLineGutter': { backgroundColor: '#1e293b' },
          '.cm-activeLine': { backgroundColor: '#1e293b80' },
        }, { dark: true }),
        EditorView.updateListener.of(u => {
          if (u.docChanged) setDraft(d => d ? { ...d, body: u.state.doc.toString() } : d);
        }),
      ],
    });
    editorViewRef.current = new EditorView({ state, parent: editorParentRef.current });
    return () => { editorViewRef.current?.destroy(); editorViewRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.id]);

  // Reconcile the doc when the draft body changes programmatically (AI apply).
  useEffect(() => {
    const view = editorViewRef.current;
    if (!view || !draft) return;
    const current = view.state.doc.toString();
    if (current !== draft.body) view.dispatch({ changes: { from: 0, to: current.length, insert: draft.body } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.body]);

  // ---- Service actions ----
  const onNewService = async () => {
    const name = prompt('New service name:')?.trim();
    if (!name) return;
    try {
      const s = await api.createService({ name });
      await reload();
      setSelectedServiceId(s.id);
    } catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Create failed'); }
  };
  const onDeleteService = async () => {
    if (!service) return;
    if (!confirm(`Delete service "${service.name}" and all its templates/variables?`)) return;
    await api.deleteService(service.id);
    const list = await reload();
    setSelectedServiceId(list[0]?.id ?? null);
  };

  // ---- Template actions ----
  const onNewTemplate = async () => {
    if (!service) return;
    const name = prompt('New template name:')?.trim();
    if (!name) return;
    try {
      await api.createServiceTemplate(service.id, { name, body: '' });
      const list = await reload();
      const t = list.find(s => s.id === service.id)?.templates.find(t => t.name === name);
      if (t) selectTemplate(t);
    } catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Create failed'); }
  };
  const onSaveTemplate = async () => {
    if (!service || !draft) return;
    try {
      await api.updateServiceTemplate(service.id, draft.id, { name: draft.name, body: draft.body });
      await reload();
      toast.success('Template saved');
    } catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Save failed'); }
  };
  const onDeleteTemplate = async () => {
    if (!service || !draft) return;
    if (!confirm(`Delete template "${draft.name}"?`)) return;
    await api.deleteServiceTemplate(service.id, draft.id);
    setSelectedTemplateId(null); setDraft(null);
    await reload();
  };
  const moveTemplate = async (t: Template, dir: -1 | 1) => {
    if (!service) return;
    const ordered = [...service.templates].sort((a, b) => a.position - b.position);
    const i = ordered.findIndex(x => x.id === t.id);
    const j = i + dir;
    if (j < 0 || j >= ordered.length) return;
    [ordered[i], ordered[j]] = [ordered[j], ordered[i]];
    await api.reorderServiceTemplates(service.id, ordered.map(x => x.id));
    await reload();
  };

  // ---- Variable actions ----
  const onAddVariable = async () => {
    if (!service) return;
    const name = newVar.name.trim();
    if (!name) { toast.error('Variable name required'); return; }
    let def: any = undefined;
    if (newVar.default.trim() !== '') {
      try { def = JSON.parse(newVar.default); } catch { def = newVar.default; }
    }
    try {
      await api.createServiceVariable(service.id, { name, required: newVar.required, default: def, description: newVar.description });
      setNewVar({ name: '', required: false, default: '', description: '' });
      await reload();
    } catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Add failed'); }
  };
  const onUpdateVariable = async (v: Variable, patch: any) => {
    if (!service) return;
    await api.updateServiceVariable(service.id, v.id, patch);
    await reload();
  };
  const onDeleteVariable = async (v: Variable) => {
    if (!service) return;
    await api.deleteServiceVariable(service.id, v.id);
    await reload();
  };

  // ---- Export / Import ----
  const onExport = async () => {
    if (!service) return;
    const data = await api.exportService(service.id);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${service.name}.service.json`; a.click();
    URL.revokeObjectURL(url);
  };
  const onImportFile = async (file: File) => {
    try {
      const payload = JSON.parse(await file.text());
      const s = await api.importService(payload);
      await reload();
      setSelectedServiceId(s.id);
      toast.success(`Imported "${s.name}"`);
    } catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Import failed'); }
  };

  const orderedTemplates = service ? [...service.templates].sort((a, b) => a.position - b.position) : [];

  return (
    <div className="h-full flex bg-surface-950 text-sm relative">
      {/* Left column */}
      <div className="w-64 shrink-0 border-r border-surface-700 flex flex-col">
        {/* Service selector */}
        <div className="p-2 border-b border-surface-700 space-y-1.5">
          <div className="flex items-center gap-1">
            <select className="input input-sm text-xs flex-1"
              value={selectedServiceId ?? ''} onChange={e => setSelectedServiceId(Number(e.target.value))}>
              {services.length === 0 && <option value="">No services</option>}
              {services.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <button className="btn btn-ghost btn-sm p-1" title="New service" onClick={onNewService}><Plus size={14} /></button>
            {service && <button className="btn btn-ghost btn-sm p-1 text-red-400" title="Delete service" onClick={onDeleteService}><Trash2 size={13} /></button>}
          </div>
          {service && <div className="text-[10px] text-surface-500 truncate">{service.description || 'no description'}</div>}
        </div>

        {/* Tabs */}
        <div className="flex border-b border-surface-700 text-xs">
          {(['templates', 'variables'] as const).map(t => (
            <button key={t} className={`flex-1 px-2 py-1.5 flex items-center justify-center gap-1 ${leftTab === t ? 'bg-surface-800 text-sky-200' : 'text-surface-400'}`}
              onClick={() => setLeftTab(t)}>
              {t === 'templates' ? <FileCode size={12} /> : <Variable size={12} />}
              {t === 'templates' ? 'Templates' : 'Variables'}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {!service && <div className="text-[11px] text-surface-500">Create a service to begin.</div>}

          {service && leftTab === 'templates' && (
            <>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-surface-500">{orderedTemplates.length} template(s)</span>
                <button className="btn btn-ghost btn-sm text-[10px]" onClick={onNewTemplate}><Plus size={11} /> New</button>
              </div>
              {orderedTemplates.map((t, i) => (
                <div key={t.id} className={`group flex items-center gap-1 rounded ${selectedTemplateId === t.id ? 'bg-sky-700/30 border border-sky-600/40' : 'border border-transparent hover:bg-surface-800'}`}>
                  <button className="flex-1 text-left px-2 py-1.5 text-xs truncate" onClick={() => selectTemplate(t)}>{t.name}</button>
                  <div className="flex opacity-0 group-hover:opacity-100 pr-1">
                    <button className="p-0.5 text-surface-400 hover:text-surface-200 disabled:opacity-20" disabled={i === 0} onClick={() => moveTemplate(t, -1)}><ChevronUp size={12} /></button>
                    <button className="p-0.5 text-surface-400 hover:text-surface-200 disabled:opacity-20" disabled={i === orderedTemplates.length - 1} onClick={() => moveTemplate(t, 1)}><ChevronDown size={12} /></button>
                  </div>
                </div>
              ))}
            </>
          )}

          {service && leftTab === 'variables' && (
            <>
              {service.variables.map(v => (
                <div key={v.id} className="border border-surface-700 rounded p-1.5 mb-1 space-y-1">
                  <div className="flex items-center gap-1">
                    <span className="text-xs font-mono flex-1 truncate">{v.name}</span>
                    <label className="text-[9px] text-surface-400 flex items-center gap-0.5">
                      <input type="checkbox" checked={v.required} onChange={e => onUpdateVariable(v, { required: e.target.checked })} /> req
                    </label>
                    <button className="p-0.5 text-red-400" onClick={() => onDeleteVariable(v)}><Trash2 size={12} /></button>
                  </div>
                  <input className="input input-sm w-full text-[10px]" placeholder="default (JSON)"
                    defaultValue={v.default != null ? JSON.stringify(v.default) : ''}
                    onBlur={e => {
                      const raw = e.target.value.trim();
                      let def: any = null;
                      if (raw !== '') { try { def = JSON.parse(raw); } catch { def = raw; } }
                      onUpdateVariable(v, { default: def });
                    }} />
                  <input className="input input-sm w-full text-[10px]" placeholder="description"
                    defaultValue={v.description} onBlur={e => onUpdateVariable(v, { description: e.target.value })} />
                </div>
              ))}
              {/* Add variable */}
              <div className="border border-dashed border-surface-600 rounded p-1.5 space-y-1">
                <input className="input input-sm w-full text-[10px]" placeholder="new variable name"
                  value={newVar.name} onChange={e => setNewVar(v => ({ ...v, name: e.target.value }))} />
                <div className="flex gap-1">
                  <input className="input input-sm flex-1 text-[10px]" placeholder="default (JSON, optional)"
                    value={newVar.default} onChange={e => setNewVar(v => ({ ...v, default: e.target.value }))} />
                  <label className="text-[9px] text-surface-400 flex items-center gap-0.5">
                    <input type="checkbox" checked={newVar.required} onChange={e => setNewVar(v => ({ ...v, required: e.target.checked }))} /> req
                  </label>
                </div>
                <button className="btn btn-ghost btn-sm w-full text-[10px]" onClick={onAddVariable}><Plus size={11} /> Add variable</button>
              </div>
            </>
          )}
        </div>

        {/* Export / Import */}
        {service && (
          <div className="p-2 border-t border-surface-700 flex gap-1.5">
            <button className="btn btn-ghost btn-sm text-[10px] flex-1" onClick={onExport}><Download size={11} /> Export</button>
            <button className="btn btn-ghost btn-sm text-[10px] flex-1" onClick={() => importRef.current?.click()}><Upload size={11} /> Import</button>
            <input ref={importRef} type="file" accept="application/json,.json" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) onImportFile(f); e.target.value = ''; }} />
          </div>
        )}
        {!service && (
          <div className="p-2 border-t border-surface-700">
            <button className="btn btn-ghost btn-sm text-[10px] w-full" onClick={() => importRef.current?.click()}><Upload size={11} /> Import service</button>
            <input ref={importRef} type="file" accept="application/json,.json" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) onImportFile(f); e.target.value = ''; }} />
          </div>
        )}
      </div>

      {/* Middle column — editor */}
      <div className="flex-1 flex flex-col min-w-0">
        {draft ? (
          <>
            <div className="p-2 border-b border-surface-700 flex items-center gap-2">
              <input className="input input-sm text-xs flex-1" value={draft.name}
                onChange={e => setDraft(d => d ? { ...d, name: e.target.value } : d)} />
              <div className="text-[10px] flex items-center gap-1 shrink-0">
                {validation.ok
                  ? <span className="text-green-400 flex items-center gap-1"><CheckCircle2 size={12} /> valid</span>
                  : <span className="text-red-400 flex items-center gap-1"><AlertCircle size={12} /> {validation.line ? `line ${validation.line}` : 'error'}</span>}
              </div>
              <button className={`btn btn-sm text-[11px] ${aiOpen ? 'bg-sky-600 text-white' : 'btn-ghost'}`} onClick={() => setAiOpen(v => !v)}>
                <Sparkles size={12} /> AI
              </button>
            </div>
            <div ref={editorParentRef} className="flex-1 min-h-0 bg-surface-950" />
            {!validation.ok && <div className="px-3 py-1 text-[10px] text-red-300 bg-red-900/20 border-t border-red-700/40">{validation.error}</div>}
            <div className="p-2 border-t border-surface-700 flex gap-2">
              <button className="btn btn-primary btn-sm text-xs flex-1" onClick={onSaveTemplate}><Save size={12} /> Save</button>
              <button className="btn btn-ghost btn-sm text-xs text-red-400" onClick={onDeleteTemplate}><Trash2 size={12} /> Delete</button>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-surface-600 text-xs">
            {service ? 'Select or create a template to edit.' : 'Create a service to begin.'}
          </div>
        )}
      </div>

      {/* Right column — deploy */}
      {service && (
        <ServiceDeployPanel
          serviceId={service.id}
          defaultTargetMode={service.target_mode}
          variables={service.variables}
          topologyId={currentTopologyId}
        />
      )}

      {/* AI drawer overlay */}
      <ServiceDesignAIDrawer
        isOpen={aiOpen}
        onClose={() => setAiOpen(false)}
        serviceId={selectedServiceId}
        templateId={draft?.id ?? null}
        templateName={draft?.name}
        currentBody={draft?.body ?? ''}
        setBody={applyAgentBody}
        messages={aiMessages}
        setMessages={setAiMessages}
      />
    </div>
  );
}

/**
 * ConfigDesignView — full-viewport version of the Config Design feature.
 *
 * Replaces the topology canvas when the user is in Config Design mode. The
 * canvas isn't relevant while editing Jinja templates, so this view takes the
 * whole main area: template list on the left, editor in the middle, preview
 * on the right. The Toolbar still sits on its own column on the far left so
 * the user can pop open auxiliary side panels (Nodes, Templates, etc.) if
 * they need to cross-reference device attributes.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import {
  Plus, Save, Trash2, Play, AlertCircle, CheckCircle2, FileCode, Download, RefreshCw, Sparkles, Variable, Eye, FolderTree, ChevronDown, ChevronRight,
} from 'lucide-react';
import ConfigDesignAIDrawer from './ConfigDesignAIDrawer';
import ConfigDesignGroupsSidebar, { ruleBadge } from './ConfigDesignGroupsSidebar';

// Schema mirrors what config_renderer.build_context() produces. Kept on the
// frontend so the variable browser renders instantly without an extra
// roundtrip — the per-device live values still come from /context/{id}.
// `loopOf` marks a list-shaped variable. The Variables panel renders an
// expander chevron that reveals the per-element schema, and clicking the
// variable inserts a for/endfor scaffold rather than a bare `{{ … }}` —
// list values can't be rendered directly, so the scaffold is the only
// useful insertion. `loopVar` names the loop variable (defaults to "item").
type VarDef = { path: string; type: string; desc: string; loopOf?: VarDef[]; loopVar?: string };

// Per-element schema for the four RR peer-style lists. Mirrors the keys
// _rr_dict produces in config_renderer.build_context; custom device
// attributes are appended at render time so peer.<custom_key> shows up too.
const RR_PEER_ELEMENT_FIELDS: VarDef[] = [
  { path: 'peer.id', type: 'int', desc: 'Peer node id' },
  { path: 'peer.name', type: 'str', desc: 'Peer hostname' },
  { path: 'peer.platform', type: 'str', desc: 'Peer platform string (iosxr, junos, …)' },
  { path: 'peer.vendor', type: 'str', desc: 'Peer vendor' },
  { path: 'peer.mgmt_ip', type: 'str', desc: 'Peer management IP' },
  { path: 'peer.is_route_reflector', type: 'bool', desc: 'Peer is an RR' },
  { path: 'peer.is_super_route_reflector', type: 'bool', desc: 'Peer is a super RR' },
  { path: 'peer.location', type: 'str', desc: 'Peer location label' },
];
const DEVICE_VARS: VarDef[] = [
  { path: 'device.name', type: 'str', desc: 'Hostname (unique within topology)' },
  { path: 'device.vendor', type: 'str', desc: 'Vendor (Cisco, Juniper, FRR, ...)' },
  { path: 'device.model', type: 'str', desc: 'Model string' },
  { path: 'device.platform', type: 'str', desc: 'Platform key (iosxr, junos, eos, linux, ...)' },
  { path: 'device.role', type: 'str', desc: "'router' or 'customer'" },
  { path: 'device.mgmt_ip', type: 'str', desc: 'Management interface IP' },
  { path: 'device.location', type: 'str', desc: 'Site / location label' },
  { path: 'device.capacity_gbps', type: 'float', desc: 'Switching capacity' },
  { path: 'device.prometheus_id', type: 'str', desc: 'Prometheus instance label' },
  { path: 'device.is_planned', type: 'bool', desc: 'True for not-yet-built nodes' },
  { path: 'device.color', type: 'str', desc: 'Custom color (hex) or empty' },
  { path: 'device.mtbf_hours', type: 'float?', desc: 'Mean time between failures' },
  { path: 'device.mttr_hours', type: 'float?', desc: 'Mean time to repair' },
  { path: 'device.latitude', type: 'float?', desc: 'Geographic latitude' },
  { path: 'device.longitude', type: 'float?', desc: 'Geographic longitude' },
  { path: 'device.is_route_reflector', type: 'bool', desc: 'True if this device is a BGP route reflector' },
  { path: 'device.is_super_route_reflector', type: 'bool', desc: 'True if this device is a top-tier "super" RR — its clients are non-super RRs' },
  { path: 'device.rr_peers', type: 'list', desc: 'RRs this device explicitly peers with. For a non-RR client: its direct RRs. For a non-super RR: its super-RR parents. Empty for super RRs (they auto-mesh).', loopOf: RR_PEER_ELEMENT_FIELDS, loopVar: 'peer' },
  { path: 'device.rr_clients', type: 'list', desc: 'Devices pointing at THIS device as their RR. Includes is_route_reflector + is_super_route_reflector on each item so you can split mid-tier RR clients from regular clients.', loopOf: RR_PEER_ELEMENT_FIELDS, loopVar: 'client' },
  { path: 'device.all_route_reflectors', type: 'list', desc: 'Every RR in the topology (super + non-super), excluding self. Use for flat full-mesh RR config.', loopOf: RR_PEER_ELEMENT_FIELDS, loopVar: 'peer' },
  { path: 'device.super_route_reflectors', type: 'list', desc: 'Every super RR in the topology, excluding self. Use for super-RR mesh in hierarchical mode.', loopOf: RR_PEER_ELEMENT_FIELDS, loopVar: 'peer' },
];
const IFACE_VARS: VarDef[] = [
  { path: 'iface.name', type: 'str', desc: 'Interface name (Gi0/0/0, eth0, et-0/0/0, ...)' },
  { path: 'iface.speed_gbps', type: 'float', desc: 'Interface speed' },
  { path: 'iface.description', type: 'str', desc: 'Free-form description from the device' },
  { path: 'iface.is_connected', type: 'bool', desc: 'True if wired to a link' },
  { path: 'iface.ip', type: 'str', desc: 'Local IPv4 on this interface (link IP)' },
  { path: 'iface.ipv6', type: 'str', desc: 'Local IPv6 on this interface (link IPv6)' },
  { path: 'iface.neighbor_device', type: 'str', desc: 'Hostname of the device on the other end' },
  { path: 'iface.neighbor_interface', type: 'str', desc: 'Far-end interface name' },
  { path: 'iface.neighbor_ip', type: 'str', desc: 'Far-end IPv4' },
  { path: 'iface.neighbor_ipv6', type: 'str', desc: 'Far-end IPv6' },
  { path: 'iface.circuit_id', type: 'str', desc: 'Carrier circuit ID' },
  { path: 'iface.circuit_cost', type: 'float', desc: 'Monthly circuit cost' },
  { path: 'iface.link_id', type: 'str', desc: 'Custom link identifier' },
  { path: 'iface.bandwidth_gbps', type: 'float', desc: 'Link bandwidth' },
  { path: 'iface.latency_ms', type: 'float', desc: 'Link latency' },
  { path: 'iface.carrier', type: 'str', desc: 'Carrier name' },
  { path: 'iface.color', type: 'str', desc: 'Custom link color (hex) or empty' },
];

// Common Jinja boilerplate the user can insert in one click.
type Snippet = { label: string; body: string; cursorBackOffset?: number };
const SNIPPETS: Snippet[] = [
  { label: 'Loop all interfaces', body: '{% for iface in interfaces %}\n  \n{% endfor %}\n', cursorBackOffset: 12 },
  { label: 'Loop connected interfaces only', body: '{% for iface in interfaces if iface.is_connected %}\n  \n{% endfor %}\n', cursorBackOffset: 12 },
  { label: 'If/else', body: '{% if  %}\n\n{% else %}\n\n{% endif %}\n', cursorBackOffset: 26 },
  { label: 'Platform switch', body: '{% if device.platform == "iosxr" %}\n\n{% elif device.platform == "junos" %}\n\n{% endif %}\n', cursorBackOffset: 47 },
];

import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLineGutter, highlightActiveLine } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { bracketMatching, indentOnInput, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language';
import { jinja } from '@codemirror/lang-jinja';
import { linter, lintGutter, type Diagnostic } from '@codemirror/lint';

import { useStore } from '../stores/useStore';
import { api } from '../utils/api';

type Template = {
  id: number;
  name: string;
  description: string;
  body: string;
  // Default rule: platform_default is the VALUE (historical name);
  // match_attr picks which device attribute it compares against.
  platform_default: string;
  match_attr: string;
};

// Builtin match attributes, in the same specificity order the backend
// resolves with (narrow -> broad); custom device attributes rank above all.
export const BUILTIN_MATCH_ATTRS: Array<{ key: string; label: string }> = [
  { key: 'location', label: 'Location' },
  { key: 'device_template', label: 'Device template' },
  { key: 'model', label: 'Model' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'role', label: 'Role' },
  { key: 'platform', label: 'Platform' },
];

export default function ConfigDesignView() {
  const { nodes, currentTopologyId, nodeTemplates } = useStore();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Template | null>(null);
  const [validation, setValidation] = useState<{ ok: boolean; error: string; line: number | null }>({ ok: true, error: '', line: null });
  const [previewNodeId, setPreviewNodeId] = useState<number | null>(null);
  const [previewOutput, setPreviewOutput] = useState<{ ok: boolean; rendered: string; error: string; line: number | null } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [creating, setCreating] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  // AI chat lives at this level so it persists across template switches.
  // Wiped only when the user navigates away from Config Design (component
  // unmount) — switching templates carries the conversation forward.
  const [aiMessages, setAiMessages] = useState<Array<{
    role: 'user' | 'assistant';
    content: string;
    toolEvents?: any[];
  }>>([]);
  const [rightTab, setRightTab] = useState<'preview' | 'variables'>('preview');
  // Left sidebar tab: flat template list vs. grouped (ordered collections).
  const [leftTab, setLeftTab] = useState<'templates' | 'groups'>('templates');
  // Lifted from the Groups sidebar so the Preview pane can offer a "Render
  // group" action — composite render of every member in order.
  const [selectedGroup, setSelectedGroup] = useState<{ id: number; name: string } | null>(null);
  const [previewIsGroup, setPreviewIsGroup] = useState(false);  // flips when the user previews the group output
  // Live variable context for the currently-selected preview device — used
  // by the Variables tab to show actual values alongside each name.
  const [liveContext, setLiveContext] = useState<{ device?: any; interfaces?: any[]; interface?: any } | null>(null);
  // Custom attribute definitions — fetched once so the Variables panel can
  // show them dynamically alongside the built-in fields.
  const [customDevDefs, setCustomDevDefs] = useState<Array<{ key: string; label: string; type: string }>>([]);
  const [customIfaceDefs, setCustomIfaceDefs] = useState<Array<{ key: string; label: string }>>([]);
  useEffect(() => {
    api.listDeviceAttrDefs().then((rows: any[]) => setCustomDevDefs(rows.map(r => ({ key: r.key, label: r.label, type: r.type })))).catch(() => {});
    api.listIfaceAttrDefs().then((rows: any[]) => setCustomIfaceDefs(rows.map(r => ({ key: r.key, label: r.label })))).catch(() => {});
  }, []);

  const editorParentRef = useRef<HTMLDivElement | null>(null);
  const editorViewRef = useRef<EditorView | null>(null);

  // Setter the AI drawer calls to apply an agent edit. Writes the new body
  // into both the CodeMirror doc and the draft state so they stay in sync.
  const applyAgentBody = (body: string) => {
    const view = editorViewRef.current;
    if (view) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: body } });
    }
    setDraft(d => d ? { ...d, body } : d);
  };

  // Insert text at the editor's current cursor (or selection). Used by the
  // Variables tab — clicking a variable inserts `{{ device.foo }}` at the
  // caret. cursorBackOffset places the caret N chars from the end of the
  // inserted string (useful for snippets where the user fills in a value).
  const insertAtCursor = (text: string, cursorBackOffset = 0) => {
    const view = editorViewRef.current;
    if (!view) return;
    const { from, to } = view.state.selection.main;
    view.dispatch({
      changes: { from, to, insert: text },
      selection: { anchor: from + text.length - cursorBackOffset },
    });
    view.focus();
  };

  const reload = async () => {
    const list = await api.listConfigTemplates();
    setTemplates(list);
    return list;
  };

  useEffect(() => { reload().catch(() => toast.error('Failed to load templates')); }, []);

  useEffect(() => {
    if (selectedId == null) { setDraft(null); return; }
    const t = templates.find(x => x.id === selectedId);
    if (t) setDraft({ ...t });
  }, [selectedId, templates]);

  useEffect(() => {
    if (previewNodeId == null && nodes.length > 0) {
      const r = nodes.find(n => (n as any).role !== 'customer') || nodes[0];
      setPreviewNodeId(r.id);
    }
  }, [nodes, previewNodeId]);

  // Refetch the variable context whenever the user picks a different preview
  // device so the Variables tab can show that device's actual values.
  useEffect(() => {
    if (previewNodeId == null) { setLiveContext(null); return; }
    api.getConfigContext(previewNodeId).then(setLiveContext).catch(() => setLiveContext(null));
  }, [previewNodeId]);

  // ---- CodeMirror lifecycle ----
  useEffect(() => {
    if (!editorParentRef.current || !draft) return;
    if (editorViewRef.current) return;

    const jinjaLinter = linter(async (view): Promise<Diagnostic[]> => {
      const body = view.state.doc.toString();
      try {
        const res = await api.validateConfigTemplate(body);
        setValidation(res);
        if (res.ok) return [];
        const line = res.line && res.line > 0 ? res.line : 1;
        const linePos = view.state.doc.line(Math.min(line, view.state.doc.lines));
        return [{ from: linePos.from, to: linePos.to, severity: 'error', message: res.error }];
      } catch {
        return [];
      }
    });

    const state = EditorState.create({
      doc: draft.body,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        history(),
        indentOnInput(),
        bracketMatching(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        jinja(),
        lintGutter(),
        jinjaLinter,
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
          if (u.docChanged) {
            const next = u.state.doc.toString();
            setDraft(d => d ? { ...d, body: next } : d);
          }
        }),
      ],
    });
    editorViewRef.current = new EditorView({ state, parent: editorParentRef.current });

    return () => {
      editorViewRef.current?.destroy();
      editorViewRef.current = null;
    };
  }, [draft?.id]);

  useEffect(() => {
    const view = editorViewRef.current;
    if (!view || !draft) return;
    const current = view.state.doc.toString();
    if (current !== draft.body) {
      view.dispatch({ changes: { from: 0, to: current.length, insert: draft.body } });
    }
  }, [draft?.id]);

  // ---- Actions ----
  const onCreate = async () => {
    setCreating(true);
    try {
      const name = `Template ${templates.length + 1}`;
      const t = await api.createConfigTemplate({ name, description: '', body: '! ' + name + '\n', platform_default: '', match_attr: 'platform' });
      await reload();
      setSelectedId(t.id);
      toast.success(`Created ${name}`);
    } catch (e: any) {
      toast.error(`Create failed: ${e.message || e}`);
    } finally {
      setCreating(false);
    }
  };

  const onSave = async () => {
    if (!draft) return;
    try {
      await api.updateConfigTemplate(draft.id, {
        name: draft.name, description: draft.description, body: draft.body, platform_default: draft.platform_default,
        match_attr: draft.match_attr || 'platform',
      });
      await reload();
      toast.success('Template saved');
    } catch (e: any) {
      toast.error(`Save failed: ${e.message || e}`);
    }
  };

  const onDelete = async () => {
    if (!draft) return;
    if (!confirm(`Delete template "${draft.name}"? Any per-device assignments to it will revert to the platform default.`)) return;
    try {
      await api.deleteConfigTemplate(draft.id);
      await reload();
      setSelectedId(null);
      toast.success('Template deleted');
    } catch (e: any) {
      toast.error(`Delete failed: ${e.message || e}`);
    }
  };

  const onPreview = async () => {
    if (!draft || previewNodeId == null) return;
    setPreviewing(true);
    setPreviewIsGroup(false);
    try {
      const res = await api.renderConfigTemplate(draft.body, previewNodeId);
      setPreviewOutput(res);
    } catch (e: any) {
      setPreviewOutput({ ok: false, rendered: '', error: e.message || String(e), line: null });
    } finally {
      setPreviewing(false);
    }
  };

  const onPreviewGroup = async () => {
    if (!selectedGroup || previewNodeId == null) return;
    setPreviewing(true);
    setPreviewIsGroup(true);
    try {
      const res = await api.renderConfigTemplateGroup(selectedGroup.id, previewNodeId);
      setPreviewOutput({ ok: res.ok, rendered: res.rendered, error: res.error, line: res.line });
    } catch (e: any) {
      setPreviewOutput({ ok: false, rendered: '', error: e.message || String(e), line: null });
    } finally {
      setPreviewing(false);
    }
  };

  const onRenderAll = async () => {
    if (!currentTopologyId) return;
    setBulkRunning(true);
    try {
      const res = await api.renderAllConfigs(currentTopologyId);
      const ok = res.results.filter((r: any) => r.status === 'rendered').length;
      const errors = res.results.filter((r: any) => r.status === 'error').length;
      const noTpl = res.results.filter((r: any) => r.status === 'no_template').length;
      toast.success(`Rendered ${ok} device(s)${errors ? `, ${errors} error(s)` : ''}${noTpl ? `, ${noTpl} without template` : ''}`);
    } catch (e: any) {
      toast.error(`Bulk render failed: ${e.message || e}`);
    } finally {
      setBulkRunning(false);
    }
  };

  const onDownloadPreview = () => {
    if (!previewOutput?.rendered) return;
    const node = nodes.find(n => n.id === previewNodeId);
    const name = node ? node.name : `node-${previewNodeId}`;
    const blob = new Blob([previewOutput.rendered], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name}.cfg`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Distinct value suggestions per match attribute, drawn from the live
  // topology (builtin fields, device-template names, custom attr values).
  const suggestionsByAttr = useMemo(() => {
    const map: Record<string, Set<string>> = {};
    const add = (attr: string, v: any) => {
      if (v === undefined || v === null || v === '') return;
      (map[attr] = map[attr] || new Set()).add(String(v));
    };
    const tmplName = new Map(nodeTemplates.map(t => [t.id, t.name]));
    nodes.forEach(n => {
      add('platform', n.platform); add('vendor', n.vendor); add('model', n.model);
      add('role', n.role); add('location', n.location);
      if (n.template_id) add('device_template', tmplName.get(n.template_id));
      const ca = (n as any).custom_attrs || {};
      Object.keys(ca).forEach(k => add(k, ca[k]));
    });
    const out: Record<string, string[]> = {};
    Object.keys(map).forEach(k => { out[k] = Array.from(map[k]).sort(); });
    return out;
  }, [nodes, nodeTemplates]);
  const matchAttrOptions = useMemo(() => ([
    ...BUILTIN_MATCH_ATTRS,
    ...customDevDefs.map(d => ({ key: d.key, label: `Custom: ${d.label || d.key}` })),
  ]), [customDevDefs]);

  return (
    <div className="h-full flex bg-surface-950 text-sm relative">
      {/* ---- Left column: tabbed Templates / Groups list ---- */}
      <div className="w-64 border-r border-surface-700 bg-surface-900 flex flex-col shrink-0">
        <div className="flex items-stretch border-b border-surface-700">
          {(['templates', 'groups'] as const).map(tab => (
            <button
              key={tab}
              className={`flex-1 px-2 py-2 text-[11px] font-medium flex items-center justify-center gap-1.5 border-b-2 transition-colors ${
                leftTab === tab
                  ? 'border-amber-500 text-surface-100'
                  : 'border-transparent text-surface-400 hover:text-surface-200'
              }`}
              onClick={() => setLeftTab(tab)}
            >
              {tab === 'templates'
                ? <><FileCode size={11} /> Templates</>
                : <><FolderTree size={11} /> Groups</>}
            </button>
          ))}
        </div>

        {leftTab === 'templates' ? (
          <>
            <div className="h-10 px-3 flex items-center justify-between border-b border-surface-700">
              <span className="text-[10px] uppercase tracking-wide text-surface-500">
                {templates.length} template{templates.length === 1 ? '' : 's'}
              </span>
              <button
                className="btn btn-ghost btn-sm text-[10px]"
                onClick={onCreate}
                disabled={creating}
                title="Create a new template"
              >
                <Plus size={11} /> New
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
              {templates.length === 0 && (
                <div className="text-[11px] text-surface-500 px-2 py-3">
                  No templates yet — click <span className="font-mono">New</span> to create one.
                </div>
              )}
              {templates.map(t => (
                <button
                  key={t.id}
                  className={`w-full text-left px-2 py-1.5 rounded text-xs flex items-center justify-between gap-2 ${
                    selectedId === t.id ? 'bg-amber-700/30 border border-amber-600/40' : 'hover:bg-surface-800 border border-transparent'
                  }`}
                  onClick={() => setSelectedId(t.id)}
                >
                  <span className="truncate">{t.name}</span>
                  {t.platform_default && (
                    <span className="text-[9px] font-mono bg-surface-700 px-1.5 py-0.5 rounded shrink-0">
                      {ruleBadge(t.match_attr, t.platform_default)}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </>
        ) : (
          <ConfigDesignGroupsSidebar
            templates={templates}
            selectedTemplateId={selectedId}
            selectTemplate={id => setSelectedId(id)}
            suggestionsByAttr={suggestionsByAttr}
            matchAttrOptions={matchAttrOptions}
            onSelectedGroupChange={(gid, gname) => setSelectedGroup(gid ? { id: gid, name: gname || '' } : null)}
          />
        )}
        {/* Bulk render lives at the bottom of the sidebar — affects all templates */}
        <div className="p-2 border-t border-surface-700">
          <button
            className="btn btn-secondary btn-sm text-[11px] w-full"
            onClick={onRenderAll}
            disabled={bulkRunning || !currentTopologyId}
            title="Render every device in this topology and cache the result on each device"
          >
            {bulkRunning ? <><RefreshCw size={11} className="animate-spin" /> Rendering…</> : <><Play size={11} /> Render all devices</>}
          </button>
        </div>
      </div>

      {!draft && (
        <div className="flex-1 flex items-center justify-center text-surface-500 text-sm">
          {templates.length === 0
            ? <span>Create a template to get started.</span>
            : <span>Select a template on the left to edit it.</span>}
        </div>
      )}

      {draft && (
        <>
          {/* ---- Middle column: editor + metadata ---- */}
          <div className="flex-1 flex flex-col min-w-0 border-r border-surface-700">
            <div className="px-4 py-3 border-b border-surface-700 space-y-2">
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-surface-400 w-24 shrink-0">Name</label>
                <input
                  className="input input-sm flex-1 text-xs"
                  value={draft.name}
                  onChange={e => setDraft(d => d ? { ...d, name: e.target.value } : d)}
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-surface-400 w-24 shrink-0">Description</label>
                <input
                  className="input input-sm flex-1 text-xs"
                  value={draft.description}
                  onChange={e => setDraft(d => d ? { ...d, description: e.target.value } : d)}
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-surface-400 w-24 shrink-0">Default for</label>
                <select
                  className="input input-sm text-xs w-40 shrink-0"
                  value={draft.match_attr || 'platform'}
                  onChange={e => setDraft(d => d ? { ...d, match_attr: e.target.value } : d)}
                  title="Which device attribute this default rule matches on"
                >
                  {matchAttrOptions.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
                <span className="text-surface-500 text-xs">=</span>
                <input
                  className="input input-sm flex-1 text-xs font-mono"
                  placeholder="(none — set a value to make this the default for matching devices)"
                  list="config-attr-suggestions"
                  value={draft.platform_default}
                  onChange={e => setDraft(d => d ? { ...d, platform_default: e.target.value } : d)}
                />
                <datalist id="config-attr-suggestions">
                  {(suggestionsByAttr[draft.match_attr || 'platform'] || []).map(p => <option key={p} value={p} />)}
                </datalist>
              </div>
              <div className="text-[10px] text-surface-500">
                Devices whose <span className="font-mono">{draft.match_attr || 'platform'}</span> equals the value get this
                template by default. A per-device override always wins; if several defaults match, the more specific
                attribute wins (custom attr &gt; location &gt; device template &gt; model &gt; vendor &gt; role &gt; platform).
              </div>
            </div>

            <div className="flex items-center justify-between px-4 py-1.5 bg-surface-900 border-b border-surface-700 text-[10px]">
              <span className="text-surface-400">Jinja template body</span>
              <div className="flex items-center gap-3">
                <span className={`flex items-center gap-1 ${validation.ok ? 'text-green-400' : 'text-red-400'}`}>
                  {validation.ok
                    ? <><CheckCircle2 size={10} /> valid</>
                    : <><AlertCircle size={10} /> line {validation.line}: {validation.error}</>}
                </span>
                <button
                  className={`flex items-center gap-1 px-2 py-0.5 rounded transition-colors ${
                    aiOpen
                      ? 'bg-violet-700 text-white'
                      : 'border border-violet-600/50 text-violet-300 hover:bg-violet-600/20'
                  }`}
                  onClick={() => setAiOpen(v => !v)}
                  title={aiOpen ? 'Close AI assistant' : 'Open AI assistant'}
                >
                  <Sparkles size={10} /> AI
                </button>
              </div>
            </div>
            <div ref={editorParentRef} className="flex-1 min-h-0 bg-surface-950" />
            <div className="flex gap-2 px-4 py-2 border-t border-surface-700">
              <button className="btn btn-primary btn-sm text-[11px]" onClick={onSave}>
                <Save size={11} /> Save
              </button>
              <button className="btn btn-ghost btn-sm text-[11px] text-red-400 hover:bg-red-900/30" onClick={onDelete}>
                <Trash2 size={11} /> Delete
              </button>
            </div>
          </div>

          {/* ---- Right column: tabbed Preview / Variables ---- */}
          <div className="w-[400px] flex flex-col shrink-0 bg-surface-900">
            <div className="h-10 flex items-stretch border-b border-surface-700">
              {(['preview', 'variables'] as const).map(tab => (
                <button
                  key={tab}
                  className={`px-3 text-xs font-medium flex items-center gap-1.5 border-b-2 transition-colors ${
                    rightTab === tab
                      ? 'border-amber-500 text-surface-100'
                      : 'border-transparent text-surface-400 hover:text-surface-200'
                  }`}
                  onClick={() => setRightTab(tab)}
                >
                  {tab === 'preview' ? <><Eye size={12} /> Preview</> : <><Variable size={12} /> Variables</>}
                </button>
              ))}
            </div>

            {/* Device picker is shared between both tabs — it drives Preview's
                render target and Variables' live values. */}
            <div className="p-3 border-b border-surface-700 space-y-2">
              <select
                className="input input-sm w-full text-xs"
                value={previewNodeId ?? ''}
                onChange={e => setPreviewNodeId(parseInt(e.target.value) || null)}
              >
                <option value="">— pick a device —</option>
                {nodes.map(n => (
                  <option key={n.id} value={n.id}>{n.name} ({n.platform || 'no platform'})</option>
                ))}
              </select>
              {rightTab === 'preview' && (
                <div className="space-y-1.5">
                  <button
                    className="btn btn-secondary btn-sm text-[11px] w-full"
                    onClick={onPreview}
                    disabled={previewing || previewNodeId == null || !draft}
                    title="Render only the template currently open in the editor"
                  >
                    {previewing && !previewIsGroup
                      ? <><RefreshCw size={11} className="animate-spin" /> Rendering…</>
                      : <><Play size={11} /> Render template</>}
                  </button>
                  {selectedGroup && (
                    <button
                      className="btn btn-primary btn-sm text-[11px] w-full"
                      onClick={onPreviewGroup}
                      disabled={previewing || previewNodeId == null}
                      title={`Render every member of "${selectedGroup.name}" in order and concatenate`}
                    >
                      {previewing && previewIsGroup
                        ? <><RefreshCw size={11} className="animate-spin" /> Rendering group…</>
                        : <><Play size={11} /> Render group "{selectedGroup.name}"</>}
                    </button>
                  )}
                </div>
              )}
            </div>

            {rightTab === 'preview' && (
              <>
                <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2">
                  {!previewOutput && (
                    <div className="text-[11px] text-surface-500 text-center py-6">
                      Pick a device and click <span className="font-mono">Render</span> to preview the output.
                    </div>
                  )}
                  {previewOutput?.ok && (
                    <>
                      <div className="text-[10px] text-surface-500">
                        Source: {previewIsGroup
                          ? <>composite render of group <span className="font-mono text-amber-300">{selectedGroup?.name}</span></>
                          : <>single template <span className="font-mono text-amber-300">{draft?.name}</span></>}
                      </div>
                      <pre className="text-[11px] font-mono bg-surface-950 border border-surface-700 rounded p-3 whitespace-pre-wrap break-all">
                        {previewOutput.rendered || <span className="text-surface-600 italic">(empty output)</span>}
                      </pre>
                    </>
                  )}
                  {previewOutput && !previewOutput.ok && (
                    <div className="text-[11px] text-red-300 bg-red-900/20 border border-red-700/40 rounded p-3">
                      {previewOutput.error}
                    </div>
                  )}
                </div>
                {previewOutput?.ok && previewOutput.rendered && (
                  <div className="p-2 border-t border-surface-700">
                    <button className="btn btn-ghost btn-sm text-[10px] w-full" onClick={onDownloadPreview}>
                      <Download size={10} /> Download as .cfg
                    </button>
                  </div>
                )}
              </>
            )}

            {rightTab === 'variables' && (
              <div className="flex-1 min-h-0 overflow-y-auto">
                {/* Snippets — common Jinja boilerplate, one-click insert */}
                <div className="p-2 border-b border-surface-700 space-y-1">
                  <div className="text-[10px] uppercase tracking-wide text-surface-500 px-1">Snippets</div>
                  {SNIPPETS.map(s => (
                    <button
                      key={s.label}
                      className="w-full text-left text-[11px] px-2 py-1 rounded hover:bg-surface-800 text-surface-300"
                      onClick={() => insertAtCursor(s.body, s.cursorBackOffset ?? 0)}
                      title={`Insert: ${s.body.replace(/\n/g, ' ↵ ').slice(0, 60)}...`}
                    >
                      <span className="font-mono text-[10px] text-amber-300">+</span>{' '}{s.label}
                    </button>
                  ))}
                </div>

                {/* device.* — list-typed vars get a for/endfor scaffold
                    instead of a bare {{ … }} (lists can't be rendered
                    directly in Jinja with StrictUndefined); the chevron
                    next to a list reveals the per-item attribute schema
                    you can use inside the loop body. */}
                <VariableSection
                  title="device.*"
                  vars={DEVICE_VARS}
                  values={liveContext?.device || null}
                  onInsert={(def) => {
                    if (def.loopOf) {
                      const loopVar = def.loopVar || 'item';
                      // Scaffold: blank line of body indented by 2 spaces;
                      // cursor lands right after the indent so the user can
                      // start typing the inner statement immediately.
                      insertAtCursor(
                        `{% for ${loopVar} in ${def.path} %}\n  \n{% endfor %}\n`,
                        14,
                      );
                    } else {
                      insertAtCursor(`{{ ${def.path} }}`);
                    }
                  }}
                  // Make user-defined custom dev attrs reachable inside each
                  // RR list's per-item expansion (e.g. peer.v4_index).
                  extraListElementFields={customDevDefs.map(d => ({
                    key: d.key, type: d.type, desc: d.label,
                  }))}
                />

                {/* Custom device attributes — defined in Settings */}
                {customDevDefs.length > 0 && (
                  <VariableSection
                    title="device.* (custom)"
                    vars={customDevDefs.map(d => ({ path: `device.${d.key}`, type: d.type, desc: d.label }))}
                    values={liveContext?.device || null}
                    onInsert={(def) => insertAtCursor(`{{ ${def.path} }}`)}
                  />
                )}

                {/* Custom interface refs — `interface.<key>` resolves to the
                    iface dict on this device (device.<key> is a legacy alias).
                    Same fields as interfaces[*]. */}
                {customIfaceDefs.length > 0 && (
                  <VariableSection
                    title="interface.* (interface refs)"
                    vars={customIfaceDefs.map(d => ({ path: `interface.${d.key}`, type: 'iface', desc: `${d.label} — full interface dict` }))}
                    values={liveContext?.interface || null}
                    onInsert={(def) => insertAtCursor(`{{ ${def.path}.name }}`)}
                  />
                )}

                {/* interfaces[*].* */}
                <div className="p-2 border-t border-surface-700">
                  <div className="text-[10px] uppercase tracking-wide text-surface-500 px-1 mb-1">
                    interfaces[*].*
                    {liveContext?.interfaces && (
                      <span className="ml-1 text-surface-600 normal-case">({liveContext.interfaces.length} on this device)</span>
                    )}
                  </div>
                  <div className="text-[10px] text-surface-500 px-1 mb-1">
                    Wrap in a <span className="font-mono text-amber-300">{'{% for iface in interfaces %}'}</span> loop.
                    Values shown are from the first interface on the selected device.
                  </div>
                  {IFACE_VARS.map(v => {
                    const sample = liveContext?.interfaces?.[0]?.[v.path.split('.')[1]];
                    return <VariableRow key={v.path} def={v} value={sample} onInsert={() => insertAtCursor(`{{ ${v.path} }}`)} />;
                  })}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      <ConfigDesignAIDrawer
        isOpen={aiOpen}
        onClose={() => setAiOpen(false)}
        topologyId={currentTopologyId}
        templateId={draft?.id ?? null}
        templateName={draft?.name}
        templateDescription={draft?.description}
        templatePlatformDefault={draft?.platform_default}
        templateMatchAttr={draft?.match_attr}
        currentBody={draft?.body ?? ''}
        setBody={applyAgentBody}
        messages={aiMessages}
        setMessages={setAiMessages}
      />
    </div>
  );
}

function VariableSection({ title, vars, values, onInsert, extraListElementFields }: {
  title: string;
  vars: VarDef[];
  values: any | null;
  onInsert: (def: VarDef) => void;
  // Tacked onto every list-typed var's element schema — used to surface
  // user-defined custom device attributes inside `peer.<key>` expansions.
  extraListElementFields?: Array<{ key: string; type: string; desc: string }>;
}) {
  return (
    <div className="p-2 border-t border-surface-700">
      <div className="text-[10px] uppercase tracking-wide text-surface-500 px-1 mb-1">{title}</div>
      {vars.map(v => {
        const key = v.path.split('.').slice(1).join('.');  // e.g. "device.name" -> "name"
        const sample = values ? values[key] : undefined;
        return (
          <VariableRow
            key={v.path}
            def={v}
            value={sample}
            onInsert={onInsert}
            extraListElementFields={extraListElementFields}
          />
        );
      })}
    </div>
  );
}

function VariableRow({ def, value, onInsert, extraListElementFields }: {
  def: VarDef;
  value: any;
  onInsert: (def: VarDef) => void;
  extraListElementFields?: Array<{ key: string; type: string; desc: string }>;
}) {
  const [expanded, setExpanded] = useState(false);

  const display = value === undefined || value === null || value === ''
    ? <span className="text-surface-600 italic">—</span>
    : Array.isArray(value)
      ? <span className="text-surface-300">{value.length} item{value.length === 1 ? '' : 's'}</span>
      : <span className="text-surface-300">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>;

  const isList = !!def.loopOf;
  const loopVar = def.loopVar || 'item';

  // Build the merged element schema for list-typed vars: built-in fields
  // (re-prefixed with the parent's loopVar) + any custom dev attrs the user
  // has defined globally. Computed lazily; the chevron has to be clicked
  // to bring it into view.
  const elementFields = !isList ? [] : (() => {
    const builtIn = (def.loopOf || []).map(f => {
      const fromPrefix = f.path.split('.')[0];
      return { ...f, path: f.path.replace(`${fromPrefix}.`, `${loopVar}.`) };
    });
    const custom = (extraListElementFields || []).map(c => ({
      path: `${loopVar}.${c.key}`,
      type: c.type,
      desc: `${c.desc} (custom attribute)`,
    }));
    return [...builtIn, ...custom];
  })();

  // Sample element for the live-values column inside the expanded view.
  const firstSample = Array.isArray(value) ? value[0] : null;

  return (
    <div>
      <div className="w-full flex items-center group hover:bg-surface-800 rounded">
        {isList && (
          <button
            className="px-0.5 py-1 text-surface-500 hover:text-surface-200"
            onClick={() => setExpanded(v => !v)}
            title={expanded ? 'Hide per-item attributes' : 'Show per-item attributes you can use inside the loop'}
            type="button"
          >
            {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          </button>
        )}
        <button
          className="flex-1 text-left px-1 py-1 rounded"
          onClick={() => onInsert(def)}
          title={
            isList
              ? `${def.desc}\n\nClick to insert a {% for ${loopVar} in ${def.path} %} … {% endfor %} loop.\nExpand the chevron to see the per-item attributes available inside the loop.`
              : `${def.desc}\n\nClick to insert: {{ ${def.path} }}`
          }
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[11px] font-mono text-amber-300 truncate">{def.path}</span>
            <span className="text-[9px] text-surface-500 shrink-0">{def.type}</span>
          </div>
          <div className="text-[10px] text-surface-400 truncate">{display}</div>
        </button>
      </div>
      {isList && expanded && (
        <div className="ml-3 border-l border-surface-700 pl-2 mt-0.5 mb-1">
          <div className="text-[9px] text-surface-500 px-1 pb-1">
            Inside <span className="font-mono text-amber-300">{`{% for ${loopVar} in ${def.path} %}`}</span> you can use:
          </div>
          {elementFields.map(f => {
            const fkey = f.path.split('.').slice(1).join('.');
            const fsample = firstSample ? firstSample[fkey] : undefined;
            const fdisp = fsample === undefined || fsample === null || fsample === ''
              ? <span className="text-surface-600 italic">—</span>
              : <span className="text-surface-300">{typeof fsample === 'object' ? JSON.stringify(fsample) : String(fsample)}</span>;
            return (
              <button
                key={f.path}
                className="w-full text-left px-1 py-0.5 rounded hover:bg-surface-800/70"
                onClick={() => onInsert(f)}
                title={`${f.desc}\n\nClick to insert: {{ ${f.path} }}\n(use inside a {% for ${loopVar} in ${def.path} %} loop)`}
                type="button"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[10px] font-mono text-amber-300/90 truncate">{f.path}</span>
                  <span className="text-[9px] text-surface-500 shrink-0">{f.type}</span>
                </div>
                <div className="text-[9px] text-surface-500 truncate">{fdisp}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

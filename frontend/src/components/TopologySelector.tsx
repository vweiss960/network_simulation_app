import { useState, useCallback, useEffect } from 'react';
import { useStore } from '../stores/useStore';
import { useAuthStore } from '../stores/useAuthStore';
import { api } from '../utils/api';
import { editSessionErrorMessage } from '../utils/editSession';
import { TopologyVersion } from '../types';
import toast from 'react-hot-toast';
import { ChevronDown, Download, Home, History, Save, RotateCcw, Trash2, Lock, Unlock, Check, X, GitCompare } from 'lucide-react';

export default function TopologySelector() {
  const {
    topologies, currentTopologyId, selectTopology,
  } = useStore();
  const editSession = useStore(s => s.editSession);
  const beginEditSession = useStore(s => s.beginEditSession);
  const commitEditSession = useStore(s => s.commitEditSession);
  const discardEditSession = useStore(s => s.discardEditSession);
  const heartbeatEditSession = useStore(s => s.heartbeatEditSession);
  const isAdmin = useAuthStore(s => !!s.user?.is_admin);
  const canEdit = useStore(s => (s as any).canEditCurrentTopology()) as boolean;
  const savedViews = useStore(s => (s as any).savedViews) as Array<{ id: number; name: string }>;
  const customers = useStore(s => s.customers) as Array<{ id: number; name: string }>;
  const targetViewId = useStore(s => (s as any).targetViewId) as number | 'default';
  const setTargetViewId = useStore(s => (s as any).setTargetViewId) as (id: number | 'default') => void;
  const saveTopologyView = useStore(s => (s as any).saveTopologyView) as (tid: number, name: string, nodeIds: number[], description?: string) => Promise<{ id: number }>;
  const [showDropdown, setShowDropdown] = useState(false);
  const [showExportPicker, setShowExportPicker] = useState(false);
  const [showHtmlOptions, setShowHtmlOptions] = useState(false);
  const [htmlViewIds, setHtmlViewIds] = useState<number[]>([]);
  const [htmlIncludeDefault, setHtmlIncludeDefault] = useState(true);
  const [htmlPlanned, setHtmlPlanned] = useState(true);
  const [htmlPlannedParent, setHtmlPlannedParent] = useState(true);
  const [htmlCustomerNodes, setHtmlCustomerNodes] = useState(false);
  const [htmlCui, setHtmlCui] = useState(false);
  const [htmlStripDevice, setHtmlStripDevice] = useState(false);
  const [htmlStripIface, setHtmlStripIface] = useState(false);
  const [htmlPaths, setHtmlPaths] = useState(true);
  const [htmlReports, setHtmlReports] = useState(true);
  const [htmlSpof, setHtmlSpof] = useState(false);
  // Starting theme for the offline viewer (the exported page has its own toggle).
  const [htmlTheme, setHtmlTheme] = useState<'dark' | 'light'>('dark');
  const [htmlTitle, setHtmlTitle] = useState('');
  // Per-customer demand-path filter. All customers + unassigned pre-selected so
  // the export is unchanged until the user narrows it.
  const [htmlCustomerIds, setHtmlCustomerIds] = useState<number[]>([]);
  const [htmlIncludeUnassigned, setHtmlIncludeUnassigned] = useState(true);
  const [showVersions, setShowVersions] = useState(false);
  const [versions, setVersions] = useState<TopologyVersion[]>([]);
  const [versionName, setVersionName] = useState('');
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [busy, setBusy] = useState(false);
  // Version diff state.
  const [showDiff, setShowDiff] = useState(false);
  const [diffFrom, setDiffFrom] = useState('');
  const [diffTo, setDiffTo] = useState('current');
  const [diffResult, setDiffResult] = useState<{ diff: string; identical: boolean } | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  // Keep the edit session alive while held (server auto-discards on timeout).
  useEffect(() => {
    if (!editSession?.heldByMe) return;
    const iv = setInterval(() => { heartbeatEditSession(); }, 60_000);
    return () => clearInterval(iv);
  }, [editSession?.heldByMe, heartbeatEditSession]);

  const loadVersions = useCallback(async () => {
    if (!currentTopologyId) return;
    setLoadingVersions(true);
    try {
      const data = await api.listVersions(currentTopologyId);
      setVersions(data);
    } catch { setVersions([]); }
    setLoadingVersions(false);
  }, [currentTopologyId]);

  const handleOpenVersions = () => {
    setShowVersions(!showVersions);
    if (!showVersions) loadVersions();
  };

  const handleSaveVersion = async () => {
    if (!currentTopologyId || !versionName.trim()) return;
    try {
      await api.saveVersion(currentTopologyId, { name: versionName.trim() });
      setVersionName('');
      toast.success('Version saved');
      loadVersions();
    } catch { toast.error('Failed to save version'); }
  };

  const handleRestore = async (v: TopologyVersion) => {
    if (!currentTopologyId) return;
    if (!confirm(`Restore "${v.name}"? Current state will be auto-backed up.`)) return;
    try {
      await api.restoreVersion(currentTopologyId, v.id);
      toast.success(`Restored "${v.name}"`);
      selectTopology(currentTopologyId);
      loadVersions();
    } catch { toast.error('Failed to restore'); }
  };

  const handleDeleteVersion = async (v: TopologyVersion) => {
    if (!currentTopologyId) return;
    if (!confirm(`Delete version "${v.name}"?`)) return;
    try {
      await api.deleteVersion(currentTopologyId, v.id);
      toast.success('Version deleted');
      loadVersions();
    } catch { toast.error('Failed to delete'); }
  };

  const current = topologies.find(t => t.id === currentTopologyId);

  const doBegin = async () => {
    setBusy(true);
    try {
      await beginEditSession();
      toast.success('Edit mode on');
    } catch (e: any) {
      toast.error(editSessionErrorMessage(e));
    }
    setBusy(false);
  };

  // Quick-create a view from the View dropdown, then target it so new nodes
  // land in it. The view starts empty.
  const doCreateView = async () => {
    if (!currentTopologyId) return;
    const name = window.prompt('New view name:')?.trim();
    if (!name) return;
    setBusy(true);
    try {
      const created = await saveTopologyView(currentTopologyId, name, [], '');
      if (created?.id != null) setTargetViewId(created.id);
      toast.success(`View "${name}" created`);
    } catch (e: any) {
      toast.error(`Could not create view: ${e?.message || e}`);
    }
    setBusy(false);
  };

  const doCommit = async () => {
    const message = window.prompt('Commit message (optional) — saved as a new version:') ?? '';
    setBusy(true);
    try { await commitEditSession(message); toast.success('Committed as a new version'); }
    catch { toast.error('Commit failed'); }
    setBusy(false);
  };

  const doDiscard = async () => {
    if (!confirm('Discard all changes since you entered edit mode?')) return;
    setBusy(true);
    try { await discardEditSession(); toast.success('Changes discarded'); }
    catch { toast.error('Discard failed'); }
    setBusy(false);
  };

  const openDiff = (from?: string) => {
    if (!currentTopologyId) return;
    setShowVersions(false);
    setDiffFrom(from || diffFrom || (versions[0] ? String(versions[0].id) : ''));
    setDiffTo('current');
    setDiffResult(null);
    setShowDiff(true);
  };

  // Recompute the diff whenever the modal is open and a from/to is chosen.
  useEffect(() => {
    if (!showDiff || !currentTopologyId || !diffFrom || !diffTo) return;
    let cancelled = false;
    setDiffLoading(true);
    api.diffVersions(currentTopologyId, diffFrom, diffTo)
      .then(r => { if (!cancelled) setDiffResult({ diff: r.diff, identical: r.identical }); })
      .catch(() => { if (!cancelled) { setDiffResult(null); toast.error('Failed to compute diff'); } })
      .finally(() => { if (!cancelled) setDiffLoading(false); });
    return () => { cancelled = true; };
  }, [showDiff, diffFrom, diffTo, currentTopologyId]);

  const handleExport = async (
    topoId: number,
    format: 'json' | 'yaml' | 'html',
    opts?: {
      viewIds: number[]; planned: boolean; plannedParent: boolean; paths: boolean; reports: boolean; spof: boolean;
      includeDefault: boolean; customerIds: number[]; includeUnassigned: boolean;
      theme: 'dark' | 'light'; title: string;
      customerNodes: boolean; cui: boolean; stripDevice: boolean; stripIface: boolean;
    },
  ) => {
    setShowExportPicker(false);
    setShowHtmlOptions(false);
    try {
      const token = localStorage.getItem('auth_token');
      let exportUrl = `/api/topologies/${topoId}/export?format=${format}`;
      if (format === 'html' && opts) {
        const qs = new URLSearchParams();
        if (opts.viewIds.length) qs.set('view_ids', opts.viewIds.join(','));
        qs.set('include_default', opts.includeDefault ? '1' : '0');
        qs.set('include_planned', opts.planned ? '1' : '0');
        qs.set('include_planned_parent', opts.plannedParent ? '1' : '0');
        qs.set('show_paths', opts.paths ? '1' : '0');
        qs.set('include_reports', opts.reports ? '1' : '0');
        qs.set('include_spof', opts.spof ? '1' : '0');
        // Always send the explicit customer selection so the backend filters
        // visible demand paths (all-selected => unchanged; empty => none).
        qs.set('customer_ids', opts.customerIds.join(','));
        qs.set('include_unassigned', opts.includeUnassigned ? '1' : '0');
        qs.set('theme', opts.theme);
        if (opts.title.trim()) qs.set('title', opts.title.trim());
        qs.set('include_customer_nodes', opts.customerNodes ? '1' : '0');
        qs.set('cui', opts.cui ? '1' : '0');
        // The redaction options only apply to CUI-marked exports.
        qs.set('strip_device_info', opts.cui && opts.stripDevice ? '1' : '0');
        qs.set('strip_interface_info', opts.cui && opts.stripIface ? '1' : '0');
        exportUrl += '&' + qs.toString();
      }
      const res = await fetch(exportUrl, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const disposition = res.headers.get('Content-Disposition') || '';
      const filenameMatch = disposition.match(/filename="(.+)"/);
      const filename = filenameMatch ? filenameMatch[1] : `topology.${format}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Exported as ${format.toUpperCase()}`);
    } catch {
      toast.error('Export failed');
    }
  };

  return (
    <div className="flex items-center gap-2 w-full">
      {/* Back to landing */}
      <button
        className="btn btn-ghost btn-sm text-surface-400 hover:text-surface-200"
        onClick={() => useStore.setState({ currentTopologyId: null, sidePanel: 'none' })}
        title="Back to topologies"
      >
        <Home size={15} />
      </button>

      {/* Topology dropdown */}
      <div className="relative">
        <button
          className="btn btn-secondary text-sm flex items-center gap-2 min-w-[200px]"
          onClick={() => setShowDropdown(!showDropdown)}
        >
          <span className="truncate">{current ? current.name : 'Select topology...'}</span>
          <ChevronDown size={14} />
        </button>

        {showDropdown && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setShowDropdown(false)} />
            <div className="absolute top-full left-0 mt-1 z-50 bg-surface-800 border border-surface-600 rounded-lg shadow-xl min-w-[280px] fade-in">
              <div className="max-h-[300px] overflow-y-auto">
                {topologies.map(t => {
                  const parent = t.parent_topology_id ? topologies.find(p => p.id === t.parent_topology_id) : null;
                  return (
                    <div
                      key={t.id}
                      className={`px-3 py-2 hover:bg-surface-700 cursor-pointer text-sm truncate ${
                        t.id === currentTopologyId ? 'bg-surface-700 text-primary-400' : ''
                      }`}
                      onClick={() => { selectTopology(t.id); setShowDropdown(false); }}
                    >
                      <div className="truncate">{t.name}</div>
                      {parent && (
                        <div className="text-[10px] text-cyan-400/80 truncate">
                          ↳ inherits from {parent.name}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>

      {/* View selector. Switches the displayed view, and (when editing) new
          nodes are added to the chosen view + excluded from Default. While
          editing it also offers a quick "New view" action. */}
      {(canEdit || savedViews.length > 0) && (
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-surface-500">View</span>
          <select
            className="text-xs"
            value={savedViews.some(v => v.id === targetViewId) ? String(targetViewId) : 'default'}
            onChange={e => {
              const val = e.target.value;
              if (val === '__new__') doCreateView();
              else setTargetViewId(val === 'default' ? 'default' : Number(val));
            }}
            title="New nodes are added to this view (and excluded from the Default view)"
          >
            <option value="default">Default view</option>
            {savedViews.map(v => <option key={v.id} value={String(v.id)}>{v.name}</option>)}
            {canEdit && <option value="__new__">＋ New view…</option>}
          </select>
        </div>
      )}

      {/* Protected-topology edit controls. Protection itself is toggled by an
          admin from the home screen; editing a protected topology is admin-only. */}
      {current?.is_protected && (() => {
        const editing = !!editSession?.heldByMe;
        const lockedByOther = !editing && !!editSession?.active;
        const editTitle = lockedByOther
          ? `Locked — ${editSession?.username || 'another user'} is editing`
          : !isAdmin ? 'Protected — only an administrator can edit' : 'Enter edit mode';
        const seg = (active: boolean) => `px-3 py-1 rounded-md text-[11px] font-medium transition-colors disabled:cursor-not-allowed ${
          active ? 'bg-primary-600/25 text-white' : 'text-surface-400 hover:text-surface-200 disabled:hover:text-surface-400 disabled:opacity-50'
        }`;
        return (
          <div className="flex items-center gap-1.5">
            {/* Protected indicator: amber lock while viewing, grey open lock
                while you hold the edit session. */}
            {editing
              ? <span title="Protected topology — you are editing"><Unlock size={14} className="text-surface-400" /></span>
              : <span title="Protected topology — read-only until you switch to Edit"><Lock size={14} className="text-amber-400" /></span>}
            {/* View | Edit switch. Leaving edit mode goes through Commit or
                Discard, so View is disabled while the session is held. */}
            <div className="flex bg-surface-900 border border-surface-700 rounded-lg p-0.5">
              <button
                className={seg(!editing)}
                disabled={editing}
                title={editing ? 'Commit or discard your changes first' : 'Viewing (read-only)'}
              >
                View
              </button>
              <button
                className={seg(editing)}
                onClick={() => { if (!editing) doBegin(); }}
                disabled={!editing && (busy || lockedByOther || !isAdmin)}
                title={editing ? 'Editing' : editTitle}
              >
                Edit
              </button>
            </div>
            {editing && (
              <>
                <button className="btn btn-sm text-[11px] bg-emerald-600/90 text-white" onClick={doCommit} disabled={busy} title="Save changes as a new version">
                  <Check size={12} /> Commit
                </button>
                <button className="btn btn-sm text-[11px] bg-surface-700 text-surface-200" onClick={doDiscard} disabled={busy} title="Discard all changes since you entered edit mode">
                  <X size={12} /> Discard
                </button>
              </>
            )}
            {lockedByOther && (
              <span className="text-[11px] font-medium text-red-300 flex items-center gap-1 px-1" title="Another user is editing this protected topology">
                <Lock size={12} /> Locked by {editSession?.username || 'another user'}
              </span>
            )}
          </div>
        );
      })()}

      {/* Version diff modal */}
      {showDiff && current && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50" onClick={() => setShowDiff(false)}>
          <div className="bg-surface-800 border border-surface-600 rounded-lg shadow-xl w-[80vw] max-w-[900px] h-[80vh] flex flex-col fade-in" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 p-3 border-b border-surface-600">
              <GitCompare size={15} className="text-surface-300" />
              <span className="text-sm font-medium">Compare versions</span>
              <div className="flex-1" />
              <button className="btn btn-ghost btn-sm p-1" onClick={() => setShowDiff(false)}><X size={15} /></button>
            </div>
            <div className="flex items-center gap-2 p-3 border-b border-surface-600 text-xs">
              <span className="text-surface-400">From</span>
              <select className="text-xs flex-1 min-w-0" value={diffFrom} onChange={e => setDiffFrom(e.target.value)}>
                <option value="current">Current state</option>
                {versions.map(v => <option key={v.id} value={String(v.id)}>{v.name} — {new Date(v.created_at).toLocaleString()}</option>)}
              </select>
              <span className="text-surface-500">→</span>
              <span className="text-surface-400">To</span>
              <select className="text-xs flex-1 min-w-0" value={diffTo} onChange={e => setDiffTo(e.target.value)}>
                <option value="current">Current state</option>
                {versions.map(v => <option key={v.id} value={String(v.id)}>{v.name} — {new Date(v.created_at).toLocaleString()}</option>)}
              </select>
            </div>
            <div className="flex-1 overflow-auto p-3 font-mono text-[11px] leading-relaxed bg-surface-900/50">
              {diffLoading ? (
                <div className="text-center text-surface-500 py-6">Computing diff…</div>
              ) : !diffResult ? (
                <div className="text-center text-surface-500 py-6">Select two versions to compare.</div>
              ) : diffResult.identical ? (
                <div className="text-center text-emerald-400 py-6">No differences — these snapshots are identical.</div>
              ) : (
                <pre className="whitespace-pre-wrap break-words">
                  {diffResult.diff.split('\n').map((line, i) => {
                    let cls = 'text-surface-400';
                    if (line.startsWith('+++') || line.startsWith('---')) cls = 'text-surface-300 font-semibold';
                    else if (line.startsWith('@@')) cls = 'text-cyan-400';
                    else if (line.startsWith('+')) cls = 'text-emerald-400';
                    else if (line.startsWith('-')) cls = 'text-red-400';
                    return <div key={i} className={cls}>{line || ' '}</div>;
                  })}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Version history button */}
      {current && (
        <div className="relative">
          <button
            className="btn btn-ghost btn-sm text-surface-400 hover:text-surface-200"
            onClick={handleOpenVersions}
            title="Version history"
          >
            <History size={15} />
          </button>
          {showVersions && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowVersions(false)} />
              <div className="absolute right-0 top-full mt-1 bg-surface-800 border border-surface-600 rounded-lg shadow-xl z-50 min-w-[320px] fade-in">
                {/* Save new version */}
                <div className="p-3 border-b border-surface-600">
                  <div className="text-[10px] text-surface-400 font-medium mb-1.5">Save Current State</div>
                  <div className="flex gap-1.5">
                    <input
                      className="flex-1 text-xs"
                      placeholder="Version name (e.g. Before upgrade)"
                      value={versionName}
                      onChange={e => setVersionName(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') handleSaveVersion(); }}
                    />
                    <button
                      className="btn btn-primary btn-sm text-[10px] px-2"
                      onClick={handleSaveVersion}
                      disabled={!versionName.trim()}
                    >
                      <Save size={11} /> Save
                    </button>
                  </div>
                  <button
                    className="btn btn-ghost btn-sm text-[10px] mt-1.5 text-surface-400 hover:text-surface-200"
                    onClick={() => openDiff()}
                  >
                    <GitCompare size={11} /> Compare versions…
                  </button>
                </div>

                {/* Version list */}
                <div className="max-h-[300px] overflow-y-auto">
                  {loadingVersions ? (
                    <div className="text-center py-4 text-surface-500 text-xs">Loading...</div>
                  ) : versions.length === 0 ? (
                    <div className="text-center py-4 text-surface-500 text-xs">No saved versions yet</div>
                  ) : (
                    versions.map(v => (
                      <div key={v.id} className="px-3 py-2 border-b border-surface-700/50 hover:bg-surface-700/30">
                        <div className="flex items-center justify-between">
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium truncate">{v.name}</div>
                            {v.description && (
                              <div className="text-[10px] text-surface-300 italic truncate" title={v.description}>“{v.description}”</div>
                            )}
                            <div className="text-[10px] text-surface-500">
                              {v.node_count} nodes, {v.link_count} links, {v.flow_count} flows
                            </div>
                            <div className="text-[9px] text-surface-600">
                              {new Date(v.created_at).toLocaleString()}{v.created_by ? ` · ${v.created_by}` : ''}
                            </div>
                          </div>
                          <div className="flex items-center gap-1 ml-2 flex-shrink-0">
                            <button
                              className="btn btn-ghost btn-sm p-1 text-surface-400 hover:text-surface-200"
                              title="Compare this version to the current state"
                              onClick={() => openDiff(String(v.id))}
                            >
                              <GitCompare size={12} />
                            </button>
                            <button
                              className="btn btn-ghost btn-sm p-1 text-primary-400"
                              title="Restore this version"
                              onClick={() => handleRestore(v)}
                            >
                              <RotateCcw size={12} />
                            </button>
                            <button
                              className="btn btn-ghost btn-sm p-1 text-red-400"
                              title="Delete this version"
                              onClick={() => handleDeleteVersion(v)}
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* Export button */}
      {current && (
        <div className="relative">
          <button
            className="btn btn-ghost btn-sm text-surface-400 hover:text-surface-200"
            onClick={() => setShowExportPicker(!showExportPicker)}
            title="Export topology"
          >
            <Download size={15} />
          </button>
          {showExportPicker && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowExportPicker(false)} />
              <div className="absolute right-0 top-full mt-1 bg-surface-800 border border-surface-600 rounded-lg shadow-lg z-50 py-1 min-w-[100px] fade-in">
                <button
                  className="w-full text-left px-3 py-1.5 text-xs hover:bg-surface-700 transition-colors"
                  onClick={() => handleExport(current.id, 'json')}
                >
                  JSON
                </button>
                <button
                  className="w-full text-left px-3 py-1.5 text-xs hover:bg-surface-700 transition-colors"
                  onClick={() => handleExport(current.id, 'yaml')}
                >
                  YAML
                </button>
                <div className="border-t border-surface-600 my-1" />
                <button
                  className="w-full text-left px-3 py-1.5 text-xs hover:bg-surface-700 transition-colors text-amber-400"
                  onClick={() => {
                    setShowExportPicker(false); setHtmlViewIds([]); setHtmlIncludeDefault(true);
                    setHtmlCustomerIds(customers.map(c => c.id)); setHtmlIncludeUnassigned(true);
                    setHtmlTitle(current ? current.name.replace(/ /g, '_') : '');
                    // Reset the options that could otherwise silently carry over from a
                    // previous export (the parent-planned checkbox is hidden on
                    // non-child topologies but its value would still be sent).
                    setHtmlPlannedParent(true); setHtmlCui(false); setHtmlStripDevice(false); setHtmlStripIface(false); setHtmlCustomerNodes(false);
                    setShowHtmlOptions(true);
                  }}
                >
                  Offline Viewer (HTML)…
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Offline HTML export options */}
      {showHtmlOptions && current && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50" onClick={() => setShowHtmlOptions(false)}>
          <div className="bg-surface-800 border border-surface-600 rounded-lg shadow-xl p-4 w-[360px] fade-in" onClick={e => e.stopPropagation()}>
            <div className="text-sm font-medium mb-1 flex items-center gap-1.5"><Download size={14} /> Offline HTML export</div>
            <div className="text-[11px] text-surface-400 mb-2">
              Pick which views to embed (planned items are always included). Uncheck the Default view to export only the selected saved view(s):
            </div>
            <div className="max-h-[150px] overflow-y-auto border border-surface-700 rounded p-2 mb-3 space-y-1">
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input type="checkbox" checked={htmlIncludeDefault} onChange={e => setHtmlIncludeDefault(e.target.checked)} />
                <span className="font-medium">Default view</span>
              </label>
              {savedViews.map(v => (
                <label key={v.id} className="flex items-center gap-2 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={htmlViewIds.includes(v.id)}
                    onChange={e => setHtmlViewIds(ids => e.target.checked ? [...ids, v.id] : ids.filter(x => x !== v.id))}
                  />
                  {v.name}
                </label>
              ))}
              {savedViews.length === 0 && <div className="text-[11px] text-surface-500">No saved views.</div>}
            </div>
            <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
              <input type="checkbox" checked={htmlPlanned} onChange={e => setHtmlPlanned(e.target.checked)} /> Include planned nodes/links{current.parent_topology_id ? ' (this topology)' : ''}
            </label>
            {current.parent_topology_id != null && (
              <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
                <input type="checkbox" checked={htmlPlannedParent} onChange={e => setHtmlPlannedParent(e.target.checked)} /> Include planned nodes/links inherited from parent
              </label>
            )}
            <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
              <input type="checkbox" checked={htmlCustomerNodes} onChange={e => setHtmlCustomerNodes(e.target.checked)} /> Include customer nodes
            </label>
            <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
              <input type="checkbox" checked={htmlPaths} onChange={e => setHtmlPaths(e.target.checked)} /> Include demand paths
            </label>
            {/* Customer flows terminate on customer nodes, so without them every
                customer's flows are dropped and this filter would be a no-op. */}
            {htmlPaths && htmlCustomerNodes && customers.length > 0 && (
              <div className="ml-5 mb-2">
                <div className="text-[10px] text-surface-500 mb-1">
                  Visible demand paths by customer (utilization always covers all flows):
                </div>
                <div className="max-h-[110px] overflow-y-auto border border-surface-700 rounded p-2 space-y-1">
                  <label className="flex items-center gap-2 text-xs cursor-pointer">
                    <input type="checkbox" checked={htmlIncludeUnassigned} onChange={e => setHtmlIncludeUnassigned(e.target.checked)} />
                    <span className="italic text-surface-400">(Unassigned)</span>
                  </label>
                  {customers.map(c => (
                    <label key={c.id} className="flex items-center gap-2 text-xs cursor-pointer">
                      <input
                        type="checkbox"
                        checked={htmlCustomerIds.includes(c.id)}
                        onChange={e => setHtmlCustomerIds(ids => e.target.checked ? [...ids, c.id] : ids.filter(x => x !== c.id))}
                      />
                      {c.name}
                    </label>
                  ))}
                </div>
              </div>
            )}
            <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
              <input type="checkbox" checked={htmlReports} onChange={e => setHtmlReports(e.target.checked)} /> Include Monte Carlo reports
            </label>
            <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
              <input type="checkbox" checked={htmlSpof} onChange={e => setHtmlSpof(e.target.checked)} /> Include SPOF analysis <span className="text-surface-500">(slower)</span>
            </label>
            <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
              <input type="checkbox" checked={htmlCui} onChange={e => setHtmlCui(e.target.checked)} /> Mark as CUI <span className="text-surface-500">(banner at the top of the page)</span>
            </label>
            {htmlCui && (
              <div className="ml-5 mb-1">
                <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
                  <input type="checkbox" checked={htmlStripDevice} onChange={e => setHtmlStripDevice(e.target.checked)} /> Remove device information <span className="text-surface-500">(vendor, model, platform, mgmt IP, custom attrs)</span>
                </label>
                <label className="flex items-center gap-2 text-xs mb-1 cursor-pointer">
                  <input type="checkbox" checked={htmlStripIface} onChange={e => setHtmlStripIface(e.target.checked)} /> Remove interface information <span className="text-surface-500">(interface names & IP addressing)</span>
                </label>
              </div>
            )}
            <label className="flex items-center gap-2 text-xs mb-2">
              <span className="text-surface-400">Title</span>
              <input
                type="text"
                className="input input-sm text-xs flex-1 bg-surface-900 border border-surface-600 rounded px-2 py-1"
                value={htmlTitle}
                maxLength={120}
                onChange={e => setHtmlTitle(e.target.value)}
                title="Shown in the top-left corner of the exported page (and used as the filename)"
              />
            </label>
            <div className="flex items-center gap-3 text-xs mb-3">
              <span className="text-surface-400">Theme</span>
              {(['dark', 'light'] as const).map(t => (
                <label key={t} className="flex items-center gap-1 cursor-pointer">
                  <input type="radio" name="html-theme" checked={htmlTheme === t} onChange={() => setHtmlTheme(t)} />
                  {t === 'dark' ? 'Dark' : 'Light'}
                </label>
              ))}
              <span className="text-[10px] text-surface-500">starting theme — the page has a toggle</span>
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn btn-ghost btn-sm text-xs" onClick={() => setShowHtmlOptions(false)}>Cancel</button>
              <button
                className="btn btn-primary btn-sm text-xs"
                disabled={!htmlIncludeDefault && htmlViewIds.length === 0}
                title={!htmlIncludeDefault && htmlViewIds.length === 0 ? 'Select the Default view or at least one saved view' : ''}
                onClick={() => handleExport(current.id, 'html', {
                  viewIds: htmlViewIds, planned: htmlPlanned, plannedParent: htmlPlannedParent, paths: htmlPaths, reports: htmlReports, spof: htmlSpof,
                  includeDefault: htmlIncludeDefault, customerIds: htmlCustomerIds, includeUnassigned: htmlIncludeUnassigned,
                  theme: htmlTheme, title: htmlTitle,
                  customerNodes: htmlCustomerNodes, cui: htmlCui, stripDevice: htmlStripDevice, stripIface: htmlStripIface,
                })}
              >
                Export
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

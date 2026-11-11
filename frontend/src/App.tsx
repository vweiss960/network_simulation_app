import { useEffect, useMemo, useRef, useState } from 'react';
import { Toaster } from 'react-hot-toast';
import toast from 'react-hot-toast';
import { useStore } from './stores/useStore';
import { useAuthStore } from './stores/useAuthStore';
import LoginPage from './components/LoginPage';
import TopologySelector from './components/TopologySelector';
import TopologyEditor from './components/TopologyEditor';
import ConfigDesignView from './components/ConfigDesignView';
import ServiceDesignView from './components/ServiceDesignView';
import Toolbar from './components/Toolbar';
import SidePanel from './components/SidePanel';
import { Plus, Upload, Download, Copy, Trash2, Cpu, ChevronDown, ChevronRight, LogOut, Users, Settings, Wifi, WifiOff, Loader2, CheckCircle2, XCircle, X, Edit3, Check, BookOpen, Layers, Container, FileCode, Lock, Unlock, Send, MoreHorizontal } from 'lucide-react';
import { api } from './utils/api';
import type { Topology } from './types';

function AdminSettingsModal({ onClose }: { onClose: () => void }) {
  const [ldapStatus, setLdapStatus] = useState<{ enabled: boolean; server: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    api.ldapStatus().then(setLdapStatus).catch(() => {});
  }, []);

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const res = await api.ldapTest();
      setResult(res);
      if (res.status === 'ok') toast.success('LDAP connection successful');
      else toast.error(res.message || 'LDAP connection failed');
    } catch (e: any) {
      setResult({ status: 'error', message: e.message });
      toast.error('LDAP test failed');
    } finally {
      setTesting(false);
    }
  };

  const StatusIcon = ({ status }: { status: string }) => {
    if (status === 'ok') return <CheckCircle2 size={12} className="text-green-400 shrink-0" />;
    if (status === 'fail') return <XCircle size={12} className="text-red-400 shrink-0" />;
    return <XCircle size={12} className="text-amber-400 shrink-0" />;
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={onClose}>
      <div className="bg-surface-800 border border-surface-600 rounded-lg w-[420px] fade-in" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Settings size={14} /> Admin Settings
          </h3>
          <button className="btn btn-ghost btn-sm p-1" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="p-4 space-y-4">
          {/* LDAP Section */}
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-xs font-medium">
              {ldapStatus?.enabled ? <Wifi size={13} className="text-primary-400" /> : <WifiOff size={13} className="text-surface-500" />}
              LDAP / Active Directory
            </div>
            <div className="text-[10px] text-surface-500">
              {ldapStatus ? (
                ldapStatus.enabled
                  ? <>Enabled &middot; <span className="font-mono">{ldapStatus.server}</span></>
                  : 'Disabled in ldap_config.yaml'
              ) : 'Loading...'}
            </div>
            <button
              className="btn btn-primary btn-sm w-full"
              onClick={runTest}
              disabled={testing || !ldapStatus?.enabled}
            >
              {testing ? <><Loader2 size={13} className="animate-spin" /> Testing...</> : 'Test Connection'}
            </button>
            {!ldapStatus?.enabled && ldapStatus !== null && (
              <div className="text-[10px] text-surface-500">
                Enable LDAP in ldap_config.yaml to test connectivity.
              </div>
            )}
            {result && (
              <div className="bg-surface-900 rounded-lg p-3 space-y-1.5">
                <div className={`text-xs font-medium ${result.status === 'ok' ? 'text-green-400' : 'text-red-400'}`}>
                  {result.status === 'ok' ? 'All checks passed' : result.message}
                </div>
                {result.dns && (
                  <div className="flex items-center gap-1.5 text-[10px]">
                    <StatusIcon status={result.dns.status} />
                    <span>DNS: {result.dns.status === 'ok' ? `resolved to ${result.dns.resolved_ip}` : result.dns.error}</span>
                  </div>
                )}
                {result.tcp && (
                  <div className="flex items-center gap-1.5 text-[10px]">
                    <StatusIcon status={result.tcp.status} />
                    <span>TCP port {result.port}: {result.tcp.status === 'ok' ? 'reachable' : result.tcp.error}</span>
                  </div>
                )}
                {result.tls && (
                  <div className="flex items-center gap-1.5 text-[10px]">
                    <StatusIcon status={result.tls.status} />
                    <span>TLS: {result.tls.status === 'ok' ? result.tls.mode : result.tls.error}</span>
                  </div>
                )}
                {result.ldap_bind && (
                  <div className="flex items-center gap-1.5 text-[10px]">
                    <StatusIcon status={result.ldap_bind.status} />
                    <span>LDAP bind: {result.ldap_bind.status === 'ok' ? 'authenticated' : result.ldap_bind.error}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const LANDING_SORTS = {
  'id-asc': { label: 'Oldest first', cmp: (a: Topology, b: Topology) => a.id - b.id },
  'id-desc': { label: 'Newest first', cmp: (a: Topology, b: Topology) => b.id - a.id },
  'name-asc': { label: 'Name A→Z', cmp: (a: Topology, b: Topology) => a.name.localeCompare(b.name) },
  'name-desc': { label: 'Name Z→A', cmp: (a: Topology, b: Topology) => b.name.localeCompare(a.name) },
};
type LandingSort = keyof typeof LANDING_SORTS;

function TopologyLanding() {
  const {
    topologies, selectTopology, createTopology, deleteTopology, duplicateTopology, renameTopology, loadTopologies,
    nodeTemplates, loadNodeTemplates, deleteNodeTemplate,
  } = useStore();
  const isAdmin = useAuthStore(s => !!s.user?.is_admin);

  const setProtection = async (id: number, next: boolean) => {
    try {
      await api.setTopologyProtection(id, next);
      await loadTopologies();
      toast.success(next ? 'Topology protected' : 'Protection removed');
    } catch {
      toast.error('Failed to change protection');
    }
  };
  const [showCreate, setShowCreate] = useState(false);
  // Per-topology count of currently-running clab containers. Polled every
  // 10s so the green badge reflects live state without spamming docker ps.
  // Gated by lab feature: a 404 from the endpoint (lab disabled) just
  // leaves the map empty and no tiles get badged.
  const [labRunning, setLabRunning] = useState<Record<string, number>>({});
  useEffect(() => {
    let cancelled = false;
    const fetchSummary = async () => {
      try {
        const r = await api.getLabRunningSummary();
        if (!cancelled) setLabRunning(r || {});
      } catch {
        if (!cancelled) setLabRunning({});
      }
    };
    fetchSummary();
    const id = setInterval(fetchSummary, 10000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);
  const [newName, setNewName] = useState('');
  const [newParentId, setNewParentId] = useState<number | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  // "Set parent…" dialog state. `parentDialogId` is the topology being
  // edited; `parentDialogValue` is the selected parent id ('' = detach).
  const [parentDialogId, setParentDialogId] = useState<number | null>(null);
  const [parentDialogValue, setParentDialogValue] = useState<string>('');
  const [renameValue, setRenameValue] = useState('');
  const importRef = useRef<HTMLInputElement>(null);
  const templateImportRef = useRef<HTMLInputElement>(null);
  // Topology whose ⋯ actions menu is open.
  const [menuOpenId, setMenuOpenId] = useState<number | null>(null);
  // Landing tile order (applies to roots and to each layer's siblings).
  const [sortBy, setSortBy] = useState<LandingSort>(() => {
    try {
      const v = localStorage.getItem('phanero.landingSort');
      if (v && Object.prototype.hasOwnProperty.call(LANDING_SORTS, v)) return v as LandingSort;
    } catch { /* storage unavailable */ }
    return 'id-asc';
  });
  const changeSort = (v: LandingSort) => {
    setSortBy(v);
    try { localStorage.setItem('phanero.landingSort', v); } catch { /* ignore */ }
  };

  // Group planning layers under their root topology. A topology whose parent
  // isn't in the list is treated as a root so it never disappears.
  const { roots, descendants } = useMemo(() => {
    const ids = new Set(topologies.map(t => t.id));
    const childrenOf = new Map<number, Topology[]>();
    const roots: Topology[] = [];
    for (const t of topologies) {
      if (t.parent_topology_id != null && ids.has(t.parent_topology_id)) {
        const list = childrenOf.get(t.parent_topology_id) || [];
        list.push(t);
        childrenOf.set(t.parent_topology_id, list);
      } else {
        roots.push(t);
      }
    }
    const cmp = LANDING_SORTS[sortBy].cmp;
    roots.sort(cmp);
    childrenOf.forEach(list => list.sort(cmp));
    // Depth-first descendants of every topology with their nesting depth
    // (1 = direct child). The seen-set guards against a (server-rejected)
    // parent cycle.
    const descendants = new Map<number, Array<{ t: Topology; depth: number }>>();
    for (const t of topologies) {
      const out: Array<{ t: Topology; depth: number }> = [];
      const seen = new Set<number>([t.id]);
      const walk = (id: number, depth: number) => {
        for (const c of childrenOf.get(id) || []) {
          if (seen.has(c.id)) continue;
          seen.add(c.id);
          out.push({ t: c, depth });
          walk(c.id, depth + 1);
        }
      };
      walk(t.id, 1);
      descendants.set(t.id, out);
    }
    return { roots, descendants };
  }, [topologies, sortBy]);
  const descendantsOf = (id: number) => descendants.get(id) || [];

  // Device counts come from the list endpoint, so refresh it whenever the
  // landing page mounts (e.g. returning from an edited topology).
  useEffect(() => { loadTopologies().catch(() => { /* keep the cached list */ }); }, [loadTopologies]);

  // Layers count only their local devices, i.e. what they add to the parent.
  const devicesText = (t: Topology, isLayer: boolean) => {
    const n = t.device_count ?? 0;
    return `${isLayer ? '+' : ''}${n} device${n === 1 ? '' : 's'}`;
  };

  const openAddLayer = (parentId: number) => {
    setNewName('');
    setNewParentId(parentId);
    setShowCreate(true);
  };

  const commitRename = (id: number) => {
    if (renameValue.trim()) { renameTopology(id, renameValue.trim()); toast.success('Renamed'); }
    setRenamingId(null);
  };

  const renderRenameInput = (t: Topology) => (
    <div className="flex gap-1 min-w-0" onClick={e => e.stopPropagation()}>
      <input
        className="flex-1 text-sm font-semibold min-w-0"
        value={renameValue}
        onChange={e => setRenameValue(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') commitRename(t.id);
          else if (e.key === 'Escape') setRenamingId(null);
        }}
        autoFocus
      />
      <button className="btn btn-primary btn-sm p-1" onClick={() => commitRename(t.id)}>
        <Check size={14} />
      </button>
      <button className="btn btn-ghost btn-sm p-1" onClick={() => setRenamingId(null)}>
        <X size={14} />
      </button>
    </div>
  );

  // ⋯ actions menu shared by the root pane and the planning-layer rows.
  const renderActionsMenu = (t: Topology) => {
    const item = (icon: React.ReactNode, label: string, onClick: () => void, cls = 'text-surface-200') => (
      <button
        className={`w-full text-left px-3 py-1.5 text-xs hover:bg-surface-700 transition-colors flex items-center gap-2 ${cls}`}
        onClick={() => { setMenuOpenId(null); onClick(); }}
      >
        {icon} {label}
      </button>
    );
    const layerCount = descendantsOf(t.id).length;
    return (
      <div className="relative shrink-0" onClick={e => e.stopPropagation()}>
        <button
          className="btn btn-ghost btn-sm p-1 text-surface-400 hover:text-surface-200"
          onClick={() => setMenuOpenId(menuOpenId === t.id ? null : t.id)}
          title="Actions"
        >
          <MoreHorizontal size={16} />
        </button>
        {menuOpenId === t.id && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setMenuOpenId(null)} />
            <div className="absolute right-0 top-full mt-1 z-50 bg-surface-800 border border-surface-600 rounded-lg shadow-xl py-1 min-w-[190px] fade-in">
              {item(<Edit3 size={13} />, 'Rename', () => { setRenamingId(t.id); setRenameValue(t.name); })}
              {item(<Copy size={13} />, 'Duplicate', () => {
                duplicateTopology(t.id)
                  .then(() => { loadTopologies(); toast.success('Duplicated'); })
                  .catch(() => toast.error('Duplicate failed'));
              })}
              {item(<Plus size={13} />, 'Add planning layer', () => openAddLayer(t.id))}
              {item(<Layers size={13} />, 'Set parent…', () => {
                setParentDialogId(t.id);
                setParentDialogValue(t.parent_topology_id ? String(t.parent_topology_id) : '');
              })}
              {isAdmin && item(
                t.is_protected ? <Unlock size={13} /> : <Lock size={13} />,
                t.is_protected ? 'Unprotect' : 'Protect',
                () => setProtection(t.id, !t.is_protected),
                'text-amber-300',
              )}
              {/* Protected topologies can't be deleted, so the option is hidden. */}
              {!t.is_protected && <div className="border-t border-surface-600 my-1" />}
              {!t.is_protected && item(<Trash2 size={13} />, 'Delete', () => {
                // The backend refuses to delete a topology that others inherit from.
                if (layerCount) {
                  toast.error(`"${t.name}" has ${layerCount} planning layer${layerCount > 1 ? 's' : ''} — delete or re-parent ${layerCount > 1 ? 'them' : 'it'} first`);
                  return;
                }
                if (confirm(`Delete "${t.name}"?`)) {
                  deleteTopology(t.id)
                    .then(() => toast.success('Deleted'))
                    .catch(() => toast.error('Delete failed'));
                }
              }, 'text-red-400')}
            </div>
          </>
        )}
      </div>
    );
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      const topo = await createTopology(newName.trim(), '', newParentId);
      await selectTopology(topo.id);
      setNewName('');
      setNewParentId(null);
      setShowCreate(false);
      toast.success(newParentId ? 'Planning topology created' : 'Topology created');
    } catch {
      toast.error('Failed to create topology');
    }
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const formData = new FormData();
      formData.append('file', file);
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/topologies/import', {
        method: 'POST',
        body: formData,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }
      const topo = await res.json();
      await loadTopologies();
      await selectTopology(topo.id);
      toast.success(`Imported "${topo.name}"`);
    } catch (err: any) {
      toast.error(`Import failed: ${err?.message || 'unknown error'}`);
    }
    e.target.value = '';
  };

  const handleTemplateExport = async (format: 'json' | 'yaml') => {
    try {
      const token = localStorage.getItem('auth_token');
      const res = await fetch(`/api/node-templates/export?format=${format}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `device_templates.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Templates exported as ${format.toUpperCase()}`);
    } catch {
      toast.error('Export failed');
    }
  };

  const handleTemplateImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const formData = new FormData();
      formData.append('file', file);
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/node-templates/import', {
        method: 'POST',
        body: formData,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }
      const result = await res.json();
      await loadNodeTemplates();
      toast.success(`Imported ${result.imported} template(s), ${result.skipped} skipped (duplicates)`);
    } catch (err: any) {
      toast.error(`Import failed: ${err?.message || 'unknown error'}`);
    }
    e.target.value = '';
  };

  return (
    <div className="flex-1 overflow-y-auto landing-page">
      <div className="max-w-7xl mx-auto px-6 py-10 fade-in">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h2 className="text-2xl font-semibold uppercase tracking-widest landing-text">Topologies</h2>
            <p className="text-sm landing-muted mt-1 tracking-wide">Select a topology to open, or create a new one</p>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs landing-muted mr-1">
              Sort
              <select
                className="text-xs rounded px-2 py-1"
                value={sortBy}
                onChange={e => changeSort(e.target.value as LandingSort)}
              >
                {(Object.keys(LANDING_SORTS) as LandingSort[]).map(k => (
                  <option key={k} value={k}>{LANDING_SORTS[k].label}</option>
                ))}
              </select>
            </label>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => importRef.current?.click()}
            >
              <Upload size={14} /> Import
            </button>
            <input
              ref={importRef}
              type="file"
              accept=".json,.yaml,.yml"
              className="hidden"
              onChange={handleImport}
            />
            <button
              className="btn btn-primary btn-sm"
              onClick={() => { setNewParentId(null); setShowCreate(true); }}
            >
              <Plus size={14} /> New Topology
            </button>
          </div>
        </div>

        {/* Topology tiles. A root with planning layers takes a double-width
            tile (parent left, layer list right); others are single tiles.
            row-dense lets single tiles backfill gaps beside double ones. */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 grid-flow-row-dense">
          {roots.map(t => {
            const layers = descendantsOf(t.id);
            const runningCount = labRunning[String(t.id)] || 0;
            const hasLayers = layers.length > 0;
            return (
            <div
              key={t.id}
              className={`landing-tile rounded-xl transition-colors ${runningCount > 0 ? 'is-running' : ''} ${hasLayers ? 'sm:col-span-2 sm:grid sm:grid-cols-2' : ''}`}
            >
              {/* Parent pane */}
              <div
                className={`p-4 flex flex-col min-h-[150px] cursor-pointer group landing-pane transition-colors ${
                  hasLayers ? 'rounded-t-xl sm:rounded-tr-none sm:rounded-l-xl' : 'rounded-xl h-full'
                }`}
                onClick={() => selectTopology(t.id)}
              >
                <div className="flex items-start gap-3 mb-3">
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center relative shrink-0 bg-primary-600/20">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" className="text-primary-400">
                      <path d="M3.5 5.5 Q6 11, 8.5 16 Q10 19.5, 12 21" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" />
                      <path d="M20.5 5.5 Q18 11, 15.5 16 Q14 19.5, 12 21" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" />
                      <path d="M3.5 5.5 Q12 3.5, 20.5 5.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" />
                      <circle cx="3.5" cy="5.5" r="2.5" stroke="currentColor" strokeWidth="1.4" fill="none" />
                      <circle cx="20.5" cy="5.5" r="2.5" stroke="currentColor" strokeWidth="1.4" fill="none" />
                      <circle cx="12" cy="21" r="2.2" stroke="currentColor" strokeWidth="1.4" fill="none" />
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0 pt-1.5">
                    {renamingId === t.id ? renderRenameInput(t) : (
                      <h3 className="text-base font-semibold tracking-wide landing-text truncate" title={t.name}>{t.name}</h3>
                    )}
                  </div>
                  {renderActionsMenu(t)}
                </div>
                {(t.is_protected || runningCount > 0) && (
                  <div className="flex flex-wrap items-center gap-1.5 mb-2">
                    {t.is_protected && (
                      <span className="text-[11px] bg-amber-600/15 text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded-md flex items-center gap-1">
                        <Lock size={10} /> Protected
                      </span>
                    )}
                    {/* Only running is flagged; stopped is the common case. */}
                    {runningCount > 0 && (
                      <span
                        className="text-[11px] bg-green-600/15 text-green-300 border border-green-500/30 px-2 py-0.5 rounded-md flex items-center gap-1.5"
                        title={`${runningCount} container${runningCount > 1 ? 's' : ''} running`}
                      >
                        <span className="relative inline-flex h-1.5 w-1.5">
                          <span className="absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75 animate-ping" />
                          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-green-400" />
                        </span>
                        Running
                      </span>
                    )}
                  </div>
                )}
                {t.description && (
                  <p className="text-[11px] landing-muted mb-2 line-clamp-2">{t.description}</p>
                )}
                <div className="mt-auto pt-2 flex items-center gap-3 text-[11px] landing-muted">
                  <span className="font-mono">ID {t.id}</span>
                  <span>{devicesText(t, false)}</span>
                  {!hasLayers && (
                    <button
                      className="ml-auto text-primary-400 hover:text-primary-300 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={e => { e.stopPropagation(); openAddLayer(t.id); }}
                      title={`Create a planning layer that inherits from ${t.name}`}
                    >
                      <Plus size={12} /> Add layer
                    </button>
                  )}
                </div>
              </div>

              {/* Planning layers pane */}
              {hasLayers && (
              <div className="p-4 border-t sm:border-t-0 sm:border-l landing-divider min-w-0">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-semibold uppercase tracking-widest landing-muted">
                    Planning layers · {layers.length}
                  </span>
                  <button
                    className="text-[11px] text-primary-400 hover:text-primary-300 flex items-center gap-1"
                    onClick={() => openAddLayer(t.id)}
                    title={`Create a planning layer that inherits from ${t.name}`}
                  >
                    <Plus size={12} /> Add layer
                  </button>
                </div>
                <div className="space-y-1.5">
                  {layers.map(({ t: c, depth }) => {
                    const layerRunning = labRunning[String(c.id)] || 0;
                    return (
                      <div
                        key={c.id}
                        style={{ marginLeft: (depth - 1) * 14 }}
                        className={`flex items-center gap-2.5 px-3 py-2 rounded-lg landing-row cursor-pointer transition-colors ${
                          depth > 1 ? 'border-l-2 border-l-cyan-700/60' : ''
                        }`}
                        onClick={() => selectTopology(c.id)}
                      >
                        <Layers size={15} className="text-cyan-400 shrink-0" />
                        <div className="flex-1 min-w-0">
                          {renamingId === c.id ? renderRenameInput(c) : (
                            <div className="text-sm font-medium landing-text flex items-center gap-1.5 min-w-0">
                              <span className="truncate" title={c.name}>{c.name}</span>
                              {c.is_protected && <Lock size={11} className="text-amber-400 shrink-0" />}
                              {layerRunning > 0 && (
                                <span
                                  className="inline-flex h-2 w-2 rounded-full bg-green-500 shrink-0"
                                  title={`${layerRunning} container${layerRunning > 1 ? 's' : ''} running`}
                                />
                              )}
                            </div>
                          )}
                          <div className="text-[10px] landing-muted">
                            <span className="font-mono">ID {c.id}</span> · {devicesText(c, true)}
                          </div>
                        </div>
                        {renderActionsMenu(c)}
                        <ChevronRight size={14} className="landing-muted shrink-0" />
                      </div>
                    );
                  })}
                </div>
              </div>
              )}
            </div>
            );
          })}

          {/* Empty state / create card */}
          {topologies.length === 0 && (
            <div
              className="bg-[#0f1626]/60 border-2 border-dashed border-[#1c2638] rounded-xl p-8 text-center cursor-pointer hover:border-primary-600 transition-colors col-span-full"
              onClick={() => { setNewParentId(null); setShowCreate(true); }}
            >
              <Plus size={32} className="mx-auto mb-3 text-surface-600" />
              <p className="text-sm text-surface-400">Create your first topology</p>
              <p className="text-[11px] text-surface-600 mt-1">or import one from a JSON/YAML file</p>
            </div>
          )}
        </div>

        {/* Device Templates Section */}
        <div className="mt-12">
          <div className="flex items-center justify-between mb-4">
            <button
              className="flex items-center gap-2 text-lg font-bold landing-text hover:text-primary-400 transition-colors"
              onClick={() => setShowTemplates(!showTemplates)}
            >
              {showTemplates ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
              <span className="uppercase tracking-widest">Device Templates</span>
              <span className="text-sm font-normal text-surface-500">({nodeTemplates.length})</span>
            </button>
            {showTemplates && (
              <div className="flex gap-2">
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => templateImportRef.current?.click()}
                >
                  <Upload size={14} /> Import
                </button>
                <input
                  ref={templateImportRef}
                  type="file"
                  accept=".json,.yaml,.yml"
                  className="hidden"
                  onChange={handleTemplateImport}
                />
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => handleTemplateExport('json')}
                >
                  <Download size={14} /> JSON
                </button>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => handleTemplateExport('yaml')}
                >
                  <Download size={14} /> YAML
                </button>
              </div>
            )}
          </div>
          {showTemplates && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {nodeTemplates.map(t => (
                <div key={t.id} className="landing-tile rounded-lg p-3 group">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2 mb-2">
                      <Cpu size={16} className="text-primary-400 shrink-0" />
                      <div>
                        <div className="text-sm font-medium text-surface-200 truncate">{t.name}</div>
                        <div className="text-[10px] text-surface-500">{t.vendor} {t.model}</div>
                      </div>
                    </div>
                    <button
                      className="btn btn-ghost btn-sm p-1 text-red-400/60 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={() => {
                        if (confirm(`Delete template "${t.name}"?`)) {
                          deleteNodeTemplate(t.id);
                          toast.success('Deleted');
                        }
                      }}
                      title="Delete template"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 text-[10px] text-surface-500">
                    {t.platform && <><span>Platform:</span><span>{t.platform}</span></>}
                    {t.capacity_gbps && <><span>Capacity:</span><span>{t.capacity_gbps}G</span></>}
                    <span>Interfaces:</span><span>{(t.default_interfaces || []).length}</span>
                    {t.default_mtbf_hours && <><span>MTBF:</span><span>{t.default_mtbf_hours}h</span></>}
                    {t.default_mttr_hours && <><span>MTTR:</span><span>{t.default_mttr_hours}h</span></>}
                  </div>
                </div>
              ))}
              {nodeTemplates.length === 0 && (
                <div className="col-span-full text-center py-6 text-surface-500 text-sm">
                  No device templates yet. Import templates or create them within a topology.
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => { setShowCreate(false); setNewParentId(null); }}>
          <div className="bg-surface-800 border border-surface-600 rounded-lg p-4 w-[400px] fade-in" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-medium mb-3">
              {newParentId
                ? <>New planning layer of <span className="text-cyan-300">{topologies.find(p => p.id === newParentId)?.name}</span></>
                : 'New Topology'}
            </h3>
            <input
              className="w-full mb-3"
              placeholder={newParentId ? 'Layer name...' : 'Topology name...'}
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              autoFocus
            />
            <label className="text-[10px] text-surface-400">Inherit from (optional — creates a planning layer)</label>
            <select
              className="w-full mb-1 bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
              value={newParentId ?? ''}
              onChange={e => setNewParentId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">— None (standalone topology) —</option>
              {topologies.map(t => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            {newParentId && (
              <div className="text-[10px] text-cyan-400 mb-3">
                Planning layer: nodes and links from the parent will appear as read-only inherited items. You can only add new items here.
              </div>
            )}
            {!newParentId && <div className="mb-3" />}
            <div className="flex justify-end gap-2">
              <button className="btn btn-secondary btn-sm" onClick={() => { setShowCreate(false); setNewParentId(null); }}>Cancel</button>
              <button className="btn btn-primary btn-sm" onClick={handleCreate}>Create</button>
            </div>
          </div>
        </div>
      )}

      {/* Set-parent modal — converts a standalone topology into a planning
          child of another, or clears the parent link. Backend rejects cycles
          (no x → x → x reachability through parent_topology_id). */}
      {parentDialogId !== null && (() => {
        const target = topologies.find(t => t.id === parentDialogId);
        // Hide self from the picker. Cycles are also rejected server-side,
        // so we don't need to walk the descendant tree on the client.
        const candidates = topologies.filter(t => t.id !== parentDialogId);
        const saveParent = async () => {
          try {
            const newParent = parentDialogValue ? Number(parentDialogValue) : null;
            await api.updateTopology(parentDialogId!, { name: target?.name || '', description: target?.description || '', parent_topology_id: newParent });
            await loadTopologies();
            setParentDialogId(null);
            toast.success(newParent ? 'Parent set' : 'Parent cleared');
          } catch (e: any) {
            toast.error(e?.message?.includes('cycle')
              ? 'Refused: that would create a cycle in the parent chain'
              : `Failed: ${e?.message || e}`);
          }
        };
        return (
          <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setParentDialogId(null)}>
            <div className="bg-surface-800 border border-surface-600 rounded-lg p-4 w-[440px] fade-in" onClick={e => e.stopPropagation()}>
              <h3 className="text-sm font-medium mb-3">
                Set parent for <span className="text-cyan-300">{target?.name}</span>
              </h3>
              <label className="text-[10px] text-surface-400">Inherit from</label>
              <select
                className="w-full mb-2 bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
                value={parentDialogValue}
                onChange={e => setParentDialogValue(e.target.value)}
                autoFocus
              >
                <option value="">— None (standalone topology) —</option>
                {candidates.map(t => (
                  <option key={t.id} value={t.id}>{t.name}{t.parent_topology_id ? ' (child)' : ''}</option>
                ))}
              </select>
              <div className="text-[10px] text-amber-300/90 bg-amber-900/15 border border-amber-800/40 rounded p-2 mb-3">
                Heads up: items in this topology whose name matches one in the
                parent become read-only — the parent's version wins. Your local
                edits on same-named items will be hidden. Renamed or newly-added
                items remain as local additions.
              </div>
              <div className="flex justify-end gap-2">
                <button className="btn btn-secondary btn-sm" onClick={() => setParentDialogId(null)}>Cancel</button>
                <button className="btn btn-primary btn-sm" onClick={saveParent}>Save</button>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

function ViewModeToggle() {
  const { viewMode, setViewMode } = useStore() as any;
  const geo = viewMode === 'geographic';
  const seg = (active: boolean) => `px-3 py-1 rounded-md text-[11px] font-medium flex items-center gap-1.5 transition-colors ${
    active ? 'bg-primary-600/25 text-white' : 'text-surface-400 hover:text-surface-200'
  }`;
  return (
    <div className="flex bg-surface-900 border border-surface-700 rounded-lg p-0.5">
      <button
        className={seg(!geo)}
        onClick={() => setViewMode('logical')}
        title="Switch to the logical topology view"
      >
        Logical
      </button>
      <button
        className={seg(geo)}
        onClick={() => setViewMode('geographic')}
        title="Switch to the geographic map view (place nodes by real-world location)"
      >
        Geographic
      </button>
    </div>
  );
}

function LabModeToggle() {
  const { labMode, setLabMode, setSidePanel } = useStore() as any;
  return (
    <button
      className={`btn btn-sm flex items-center gap-1.5 transition-colors ${
        labMode
          ? 'bg-emerald-600 hover:bg-emerald-500 text-white'
          : 'border border-emerald-600/50 text-emerald-300 hover:bg-emerald-600/20'
      }`}
      onClick={() => {
        const next = !labMode;
        setLabMode(next);
        if (next) setSidePanel('lab');
      }}
      title={labMode ? 'Exit lab mode and return to design' : 'Open lab view (templates + interfaces translated for deploy)'}
    >
      <span className="text-[11px] font-medium">{labMode ? 'Exit Lab' : 'Lab'}</span>
    </button>
  );
}

function ConfigModeToggle() {
  const { configMode, setConfigMode } = useStore() as any;
  return (
    <button
      className={`btn btn-sm flex items-center gap-1.5 transition-colors ${
        configMode
          ? 'bg-amber-600 hover:bg-amber-500 text-white'
          : 'border border-amber-600/50 text-amber-300 hover:bg-amber-600/20'
      }`}
      onClick={() => setConfigMode(!configMode)}
      title={configMode ? 'Exit Config Design and return to design view' : 'Open Config Design — Jinja templates and rendered configs'}
    >
      <span className="text-[11px] font-medium">{configMode ? 'Exit Config' : 'Config'}</span>
    </button>
  );
}

function ConfigModeBanner() {
  const { configMode, setConfigMode } = useStore() as any;
  if (!configMode) return null;
  return (
    <div className="bg-amber-900/30 border-b border-amber-700/40 px-4 py-1.5 flex items-center gap-2 text-xs">
      <FileCode size={13} className="text-amber-400 shrink-0" />
      <span className="text-amber-200">
        Config Design — Jinja templates per platform render to each device's <code className="text-amber-100">generated_config</code>.
      </span>
      <span className="text-amber-500/60 ml-auto text-[10px]">Design topology is untouched.</span>
      <button
        className="text-amber-200 hover:text-white text-[10px] underline ml-2"
        onClick={() => setConfigMode(false)}
      >
        Exit
      </button>
    </div>
  );
}

function ServiceModeToggle() {
  const { serviceMode, setServiceMode } = useStore() as any;
  return (
    <button
      className={`btn btn-sm flex items-center gap-1.5 transition-colors ${
        serviceMode
          ? 'bg-sky-600 hover:bg-sky-500 text-white'
          : 'border border-sky-600/50 text-sky-300 hover:bg-sky-600/20'
      }`}
      onClick={() => setServiceMode(!serviceMode)}
      title={serviceMode ? 'Exit Service and return to design view' : 'Open Service — render config from data and push to devices'}
    >
      <span className="text-[11px] font-medium">{serviceMode ? 'Exit Service' : 'Service'}</span>
    </button>
  );
}

function ServiceModeBanner() {
  const { serviceMode, setServiceMode } = useStore() as any;
  if (!serviceMode) return null;
  return (
    <div className="bg-sky-900/30 border-b border-sky-700/40 px-4 py-1.5 flex items-center gap-2 text-xs">
      <Send size={13} className="text-sky-400 shrink-0" />
      <span className="text-sky-200">
        Service — render config from supplied data and push it live to lab nodes or device management IPs.
      </span>
      <span className="text-sky-500/60 ml-auto text-[10px]">Deploys change real devices.</span>
      <button
        className="text-sky-200 hover:text-white text-[10px] underline ml-2"
        onClick={() => setServiceMode(false)}
      >
        Exit
      </button>
    </div>
  );
}

function LabModeBanner() {
  const { labMode, labView, setLabMode } = useStore() as any;
  if (!labMode) return null;
  const nodeCount = labView?.nodes?.length || 0;
  const skipped = labView?.skipped_nodes?.length || 0;
  return (
    <div className="bg-emerald-900/30 border-b border-emerald-700/40 px-4 py-1.5 flex items-center gap-2 text-xs">
      <Container size={13} className="text-emerald-400 shrink-0" />
      <span className="text-emerald-200">
        Lab mode — templates and interface names translated for containerlab deployment.
        {nodeCount > 0 && ` Showing ${nodeCount} deployable node(s)${skipped > 0 ? `, ${skipped} skipped` : ''}.`}
      </span>
      <span className="text-emerald-500/60 ml-auto text-[10px]">Design topology is untouched; exit lab mode to return.</span>
      <button
        className="text-emerald-200 hover:text-white text-[10px] underline ml-2"
        onClick={() => setLabMode(false)}
      >
        Exit
      </button>
    </div>
  );
}

function ChildTopologyBanner() {
  const { topologies, currentTopologyId, selectTopology } = useStore();
  const current = topologies.find(t => t.id === currentTopologyId);
  const parent = current?.parent_topology_id ? topologies.find(p => p.id === current.parent_topology_id) : null;
  if (!current || !parent) return null;
  return (
    <div className="bg-cyan-900/30 border-b border-cyan-700/40 px-4 py-1.5 flex items-center gap-2 text-xs">
      <Layers size={13} className="text-cyan-400 shrink-0" />
      <span className="text-cyan-200">
        Planning layer — inherits nodes, links, and flows from
      </span>
      <button
        className="text-cyan-300 hover:text-cyan-100 underline underline-offset-2 font-medium"
        onClick={() => selectTopology(parent.id)}
        title="Open parent topology"
      >
        {parent.name}
      </button>
      <span className="text-cyan-500/60 ml-auto text-[10px]">
        Inherited items are read-only here. Edit them in the parent.
      </span>
    </div>
  );
}

export default function App() {
  const { currentTopologyId, loadTopologies, loadNodeTemplates, loadSettings } = useStore();
  const features = useStore(s => s.features);
  const loadFeatures = useStore(s => s.loadFeatures);
  const configMode = useStore(s => (s as any).configMode) as boolean;
  const serviceMode = useStore(s => (s as any).serviceMode) as boolean;
  const { isAuthenticated, isLoading, user, logout, checkAuth } = useAuthStore();
  const [showAdminSettings, setShowAdminSettings] = useState(false);
  // Server-driven feature flags live in the store (see loadFeatures) so the
  // Toolbar and other components share one source of truth. `null` while
  // loading so gated toggles/menus don't flash before we know their state.

  useEffect(() => {
    checkAuth();
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      loadTopologies();
      loadNodeTemplates();
      loadSettings();
      loadFeatures();
    }
  }, [isAuthenticated]);

  if (isLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-surface-950">
        <div className="text-surface-500 text-sm">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <div className="h-screen flex flex-col bg-surface-950">
      <Toaster
        position="top-right"
        toastOptions={{
          style: { background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155' },
        }}
      />
      <header className="h-12 flex items-center px-4 border-b border-surface-700 bg-surface-900 shrink-0">
        <div className="flex items-center gap-3">
          <svg width="24" height="14" viewBox="0 0 24 14" fill="none" className="text-primary-400">
            {/* Left side rays (2) */}
            <path d="M12 13 Q10.2 12.2, 2.2 11.3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M12 13 Q9.2 8.5, 5.6 5.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            {/* Center ray */}
            <path d="M12 13 Q11.7 7, 11.5 1.2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            {/* Right side rays (4) */}
            <path d="M12 13 Q13.2 6.8, 14.8 2.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            <path d="M12 13 Q15 8.8, 19 5.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M12 13 Q15.2 10, 20.5 7.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M12 13 Q15.5 12.4, 22 11.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <h1 className="text-sm font-semibold tracking-widest uppercase text-surface-100" style={{ fontSize: '0.7rem', letterSpacing: '0.2em' }}>Phanero</h1>
        </div>
        {currentTopologyId && (
          <div className="ml-6 flex-1">
            <TopologySelector />
          </div>
        )}
        <div className="ml-auto flex items-center gap-3">
          {currentTopologyId && features?.geo_enabled && <ViewModeToggle />}
          {/* Module buttons, set apart from the view switch. */}
          {currentTopologyId && features?.geo_enabled
            && (features?.config_design_enabled || features?.service_enabled || features?.lab_enabled)
            && <div className="w-px h-5 bg-surface-700" />}
          {currentTopologyId && features?.config_design_enabled && <ConfigModeToggle />}
          {currentTopologyId && features?.service_enabled && <ServiceModeToggle />}
          {currentTopologyId && features?.lab_enabled && <LabModeToggle />}
          {/* App-level links (docs, admin, users, account) belong on the home
              screen, not in the topology view. Sign-out stays everywhere. */}
          {!currentTopologyId && (
          <>
          <a
            href="/api/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="text-surface-400 hover:text-primary-400 transition-colors"
            title="API Documentation (Swagger)"
          >
            <BookOpen size={14} />
          </a>
          {user?.is_admin && (
            <>
              <button
                className="btn btn-ghost btn-sm p-1 text-surface-400 hover:text-surface-200"
                onClick={() => setShowAdminSettings(true)}
                title="Admin Settings"
              >
                <Settings size={14} />
              </button>
              <button
                className="btn btn-ghost btn-sm p-1 text-surface-400 hover:text-amber-300"
                onClick={() => {
                  const { sidePanel, setSidePanel } = useStore.getState();
                  setSidePanel(sidePanel === 'users' ? 'none' : 'users');
                }}
                title="User Management"
              >
                <Users size={14} />
              </button>
            </>
          )}
          <span className="text-[11px] text-surface-500">{user?.display_name || user?.username}</span>
          </>
          )}
          <button
            className="btn btn-ghost btn-sm p-1 text-surface-400 hover:text-surface-200"
            onClick={logout}
            title="Sign out"
          >
            <LogOut size={14} />
          </button>
        </div>
      </header>

      {currentTopologyId && <ChildTopologyBanner />}
      {currentTopologyId && features?.lab_enabled && <LabModeBanner />}
      {currentTopologyId && features?.config_design_enabled && <ConfigModeBanner />}
      {currentTopologyId && features?.service_enabled && <ServiceModeBanner />}
      <div className="flex-1 flex overflow-hidden">
        {currentTopologyId ? (
          <>
            <Toolbar />
            <div className="flex-1 relative min-w-0">
              {serviceMode ? <ServiceDesignView /> : configMode ? <ConfigDesignView /> : <TopologyEditor />}
            </div>
          </>
        ) : (
          <TopologyLanding />
        )}
        <SidePanel />
      </div>
      {showAdminSettings && <AdminSettingsModal onClose={() => setShowAdminSettings(false)} />}
    </div>
  );
}

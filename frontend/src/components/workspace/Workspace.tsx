import { useEffect, useState } from 'react';
import { ReactFlowProvider } from 'reactflow';
import { Info, Loader2, Play, RefreshCcw, Square, X } from 'lucide-react';
import { PhaneroLogo } from '../LoginPage';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import Canvas from '../canvas/Canvas';
import Inspector from './Inspector';
import LabSummary from './LabSummary';
import LabEnterTransition from './LabEnterTransition';
import LabConsoleDrawer from '../LabConsoleDrawer';
import type { LabStatus } from '../../types';

const STATUS_STYLE: Record<LabStatus, string> = {
  idle: 'bg-surface-700 text-surface-300',
  deploying: 'bg-sky-900/60 text-sky-200',
  syncing: 'bg-sky-900/60 text-sky-200',
  running: 'bg-emerald-900/60 text-emerald-200',
  destroying: 'bg-amber-900/60 text-amber-200',
  failed: 'bg-red-900/60 text-red-200',
};
const STATUS_LABEL: Record<LabStatus, string> = {
  idle: 'Not deployed', deploying: 'Deploying', syncing: 'Applying changes', running: 'Running',
  destroying: 'Destroying', failed: 'Error',
};

export default function Workspace() {
  const {
    topology, lab, busy, nodes, closeTopology, pollLab, deploy, destroy, refreshTopology,
    consoleTabs, activeConsoleId, setActiveConsole, closeConsole, currentTopologyId, selection,
  } = useLabStore();
  const [showInfo, setShowInfo] = useState(false);
  // While the entry animation runs, the area around the growing canvas
  // keeps the Labs page's light background.
  const entering = useLabStore(st => !!st.enterFrom);

  const inspecting = selection?.type === 'node' || selection?.type === 'link';
  const offCount = nodes.filter(n => n.kind !== 'bridge' && n.enabled === false).length;
  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState('');

  // Poll runtime state: fast while something is changing, slow otherwise.
  const status = lab?.status ?? 'idle';
  const transient = busy || ['deploying', 'syncing', 'destroying'].includes(status) || lab?.pending_sync
    || Object.values(lab?.nodes ?? {}).some(n => n.ready_state === 'booting' || n.ready_state === 'starting');
  useEffect(() => {
    const t = setInterval(pollLab, transient ? 2000 : 8000);
    return () => clearInterval(t);
  }, [pollLab, transient]);

  const deployed = status !== 'idle';
  const up = Object.values(lab?.nodes ?? {}).filter(n => n.ready_state === 'up').length;

  const saveName = async () => {
    setEditingName(false);
    if (!currentTopologyId || !name.trim() || name === topology?.name) return;
    try { await api.updateTopology(currentTopologyId, { name }); await refreshTopology(); }
    catch (e) { alert((e as Error).message); }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Same bar as the rest of the app; the logo goes back to the Labs page. */}
      <header className="h-[60px] shrink-0 flex items-center gap-3 px-5 border-b border-surface-700 bg-surface-900">
        <button onClick={closeTopology} title="Back to all labs"
          className="flex items-center justify-center w-10 h-10 -ml-2 rounded-lg hover:bg-white/10">
          <PhaneroLogo />
        </button>
        <div className="w-px h-6 bg-surface-700" />
        {editingName
          ? <input autoFocus value={name} onChange={e => setName(e.target.value)} onBlur={saveName}
              onKeyDown={e => { if (e.key === 'Enter') saveName(); if (e.key === 'Escape') setEditingName(false); }}
              className="px-2 py-1 text-sm rounded bg-surface-900 border border-primary-500 outline-none" />
          : <button className="text-sm font-semibold hover:underline decoration-dotted" title={deployed ? 'Destroy the lab to rename it' : 'Rename'}
              onClick={() => { if (!deployed) { setName(topology?.name ?? ''); setEditingName(true); } }}>
              {topology?.name ?? '…'}
            </button>}
        <span className={`px-2 py-0.5 rounded-full text-[11px] ${STATUS_STYLE[status]}`}>
          {busy ?? STATUS_LABEL[status]}{status === 'running' && nodes.length ? ` · ${up}/${nodes.length} up` : ''}
        </span>
        {lab?.pending_sync && <span className="text-[11px] text-sky-300">changes queued…</span>}
        <div className="flex-1" />
        {busy && <Loader2 size={16} className="animate-spin text-surface-400" />}
        {deployed && status !== 'failed' && offCount > 0 && (
          <button onClick={() => deploy(true)} disabled={!!busy} title="Start every device that is off"
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40">
            <Play size={14} /> Start all ({offCount} off)
          </button>
        )}
        {!deployed || status === 'failed' ? (
          <button onClick={() => deploy(true)} disabled={!!busy || nodes.length === 0}
            title="Start every device (or right-click a device → Start just this device)"
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40">
            <Play size={14} /> Deploy
          </button>
        ) : (
          <button onClick={() => deploy(false)} disabled={!!busy} title="Re-apply the topology to the running lab (reconcile)"
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-surface-700 hover:bg-surface-600 disabled:opacity-40">
            <RefreshCcw size={14} /> Re-apply
          </button>
        )}
        {deployed && (
          <button onClick={() => { if (confirm('Destroy the lab? All containers are removed and running config not saved in startup configs is lost.')) destroy(); }}
            disabled={!!busy}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-red-700/80 hover:bg-red-600 text-white disabled:opacity-40">
            <Square size={14} /> Destroy
          </button>
        )}
        <button onClick={() => setShowInfo(v => !v)} title="This lab: resources, host checks, YAML, tips"
          className={`p-1.5 rounded ${showInfo ? 'bg-surface-600 text-white' : 'hover:bg-surface-700 text-surface-300'}`}>
          <Info size={18} />
        </button>
      </header>
      <div className={`flex-1 flex min-h-0 ${entering ? 'bg-[#f3f5f9] plabs-entering' : ''}`}>
        <main className="flex-1 relative min-w-0">
          <LabEnterTransition />
          <ReactFlowProvider><Canvas /></ReactFlowProvider>
          {showInfo && (
            <div className="absolute top-0 right-0 bottom-0 z-30 w-80 border-l border-surface-700 bg-surface-800 shadow-2xl flex flex-col">
              <div className="h-10 shrink-0 flex items-center justify-between px-3 border-b border-surface-700">
                <span className="text-sm font-semibold">Lab info</span>
                <button onClick={() => setShowInfo(false)} className="p-1 rounded hover:bg-surface-700"><X size={15} /></button>
              </div>
              <div className="flex-1 overflow-y-auto"><LabSummary /></div>
            </div>
          )}
          <LabConsoleDrawer tabs={consoleTabs} activeId={activeConsoleId} onSetActive={setActiveConsole} onClose={closeConsole} />
        </main>
        {inspecting && <aside className="w-80 shrink-0 border-l border-surface-700 bg-surface-800/40"><Inspector /></aside>}
      </div>
    </div>
  );
}

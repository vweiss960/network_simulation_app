import { useEffect, useState } from 'react';
import { ReactFlowProvider } from 'reactflow';
import { ArrowLeft, Loader2, Play, RefreshCcw, Square } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import Canvas from '../canvas/Canvas';
import Palette from './Palette';
import Inspector from './Inspector';
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
    consoleTabs, activeConsoleId, setActiveConsole, closeConsole, currentTopologyId,
  } = useLabStore();
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
      <header className="h-12 shrink-0 flex items-center gap-3 px-3 border-b border-surface-700 bg-surface-800/60">
        <button onClick={closeTopology} className="p-1 rounded hover:bg-surface-700" title="All labs"><ArrowLeft size={18} /></button>
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
        {!deployed || status === 'failed' ? (
          <button onClick={deploy} disabled={!!busy || nodes.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40">
            <Play size={14} /> Deploy
          </button>
        ) : (
          <button onClick={deploy} disabled={!!busy} title="Re-apply the whole topology (reconcile)"
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
      </header>
      <div className="flex-1 flex min-h-0">
        <aside className="w-56 shrink-0 border-r border-surface-700 bg-surface-800/40"><Palette /></aside>
        <main className="flex-1 relative min-w-0">
          <ReactFlowProvider><Canvas /></ReactFlowProvider>
          <LabConsoleDrawer tabs={consoleTabs} activeId={activeConsoleId} onSetActive={setActiveConsole} onClose={closeConsole} />
        </main>
        <aside className="w-80 shrink-0 border-l border-surface-700 bg-surface-800/40"><Inspector /></aside>
      </div>
    </div>
  );
}

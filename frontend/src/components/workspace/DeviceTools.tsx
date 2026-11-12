import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Camera, FileCode2, History, Play, RotateCcw, Trash2, X } from 'lucide-react';
import { api } from '../../utils/api';
import type { Device, Snapshot } from '../../types';

const field = 'w-full px-2 py-1.5 text-xs font-mono rounded bg-surface-900 border border-surface-700 focus:border-primary-500 outline-none';

/** Run commands, view running config, snapshot / restore — for one running device. */
export default function DeviceTools({ topologyId, node, running, family }: {
  topologyId: number; node: Device; running: boolean; family: string;
}) {
  const [cmd, setCmd] = useState('');
  const [busy, setBusy] = useState(false);
  const [output, setOutput] = useState<{ title: string; text: string } | null>(null);
  const [snaps, setSnaps] = useState<Snapshot[]>([]);
  const canConfig = family !== 'linux' || node.image_ref.includes('frr');
  const canLive = family === 'iosxr' || node.image_ref.includes('frr');

  const loadSnaps = () => api.listSnapshots(topologyId, node.id).then(setSnaps).catch(() => {});
  useEffect(() => { loadSnaps(); }, [node.id]);

  const run = async () => {
    const commands = cmd.split('\n').map(c => c.trim()).filter(Boolean);
    if (!commands.length) return;
    setBusy(true);
    try {
      const r = await api.runCli(topologyId, node.id, commands);
      setOutput({ title: `${node.name}`, text: r.results.map(x => `${node.name}# ${x.command}\n${x.output}`).join('\n\n') });
    } catch (e) { toast.error((e as Error).message, { duration: 8000 }); }
    finally { setBusy(false); }
  };

  const showRunning = async () => {
    setBusy(true);
    try { const r = await api.runningConfig(topologyId, node.id); setOutput({ title: `${node.name} running-config`, text: r.config }); }
    catch (e) { toast.error((e as Error).message, { duration: 8000 }); }
    finally { setBusy(false); }
  };

  const snapshot = async () => {
    const label = prompt('Snapshot label (optional)') ?? null;
    if (label === null) return;
    setBusy(true);
    try { await api.snapshotNode(topologyId, node.id, label); toast.success('Snapshot saved'); loadSnaps(); }
    catch (e) { toast.error((e as Error).message, { duration: 8000 }); }
    finally { setBusy(false); }
  };

  const restore = async (s: Snapshot, mode: 'live' | 'reboot') => {
    const msg = mode === 'live'
      ? `Replace ${node.name}'s running config with "${s.label}" now?`
      : `Make "${s.label}" ${node.name}'s startup config and recreate the device? (It reboots.)`;
    if (!confirm(msg)) return;
    setBusy(true);
    try {
      const r = await api.restoreSnapshot(topologyId, s.id, mode);
      if (mode === 'live' && r.ok === false) toast.error(`Restore failed: ${(r.errors || []).join('; ')}`, { duration: 10000 });
      else toast.success(mode === 'live' ? 'Configuration replaced' : r.note);
    } catch (e) { toast.error((e as Error).message, { duration: 8000 }); }
    finally { setBusy(false); }
  };

  const view = async (s: Snapshot) => {
    const full = await api.getSnapshot(topologyId, s.id);
    setOutput({ title: `${node.name} · ${s.label}`, text: full.config });
  };

  return (
    <div className="p-3 border-b border-surface-700">
      <div className="text-xs font-semibold text-surface-300 mb-2">Device tools</div>
      {!running && <div className="text-[11px] text-surface-500 mb-2">Available while the device is running.</div>}
      <textarea className={`${field} h-16`} placeholder={family === 'linux' ? 'ip -br addr' : family === 'iosxr' ? 'show ipv4 interface brief' : 'show ip interface brief'}
        value={cmd} onChange={e => setCmd(e.target.value)} disabled={!running}
        onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) run(); }} />
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <Btn icon={<Play size={12} />} onClick={run} disabled={!running || busy || !cmd.trim()}>Run</Btn>
        {canConfig && <Btn icon={<FileCode2 size={12} />} onClick={showRunning} disabled={!running || busy}>Running config</Btn>}
        {canConfig && <Btn icon={<Camera size={12} />} onClick={snapshot} disabled={!running || busy}>Snapshot</Btn>}
      </div>
      {snaps.length > 0 && (
        <div className="mt-3">
          <div className="flex items-center gap-1 text-[11px] uppercase tracking-wider text-surface-500 mb-1"><History size={11} /> Snapshots</div>
          {snaps.map(s => (
            <div key={s.id} className="flex items-center gap-1.5 py-0.5 text-xs">
              <button className="flex-1 text-left truncate text-primary-300 hover:underline" onClick={() => view(s)} title={s.created_at ?? ''}>{s.label}</button>
              {canLive && <button title="Restore now (replace running config)" disabled={!running || busy} onClick={() => restore(s, 'live')}
                className="p-0.5 text-surface-400 hover:text-surface-100 disabled:opacity-30"><RotateCcw size={12} /></button>}
              <button title="Restore by reboot (becomes startup config)" disabled={busy} onClick={() => restore(s, 'reboot')}
                className="px-1 text-[10px] rounded border border-surface-600 text-surface-400 hover:text-surface-100">boot</button>
              <button title="Delete" onClick={async () => { await api.deleteSnapshot(topologyId, s.id); loadSnaps(); }}
                className="p-0.5 text-surface-500 hover:text-red-300"><Trash2 size={12} /></button>
            </div>
          ))}
        </div>
      )}
      {output && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={() => setOutput(null)}>
          <div className="w-full max-w-4xl max-h-full flex flex-col bg-surface-800 rounded-lg border border-surface-600" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-2 border-b border-surface-700">
              <span className="text-sm font-semibold">{output.title}</span>
              <button onClick={() => setOutput(null)}><X size={16} /></button>
            </div>
            <pre className="p-4 overflow-auto text-xs font-mono text-surface-200 select-text">{output.text}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

function Btn({ icon, children, onClick, disabled }: { icon: React.ReactNode; children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled}
      className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-surface-700 hover:bg-surface-600 text-surface-100 disabled:opacity-40 disabled:cursor-not-allowed">
      {icon}{children}
    </button>
  );
}

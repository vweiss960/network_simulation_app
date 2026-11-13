/**
 * Dialogs for the lab's power controls:
 *  - WipeLabDialog: Wipe removes every device. Made hard to do by accident:
 *    type the lab name, and a snapshot of every running device is taken
 *    first unless unticked.
 *  - StopFailedDialog: Stop saves each device's config on the device first;
 *    if some couldn't, say which and offer to stop anyway.
 */
import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Camera, Loader2, Square, Trash2, X } from 'lucide-react';
import { api, type StopResult } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';

function Modal({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4" onMouseDown={onClose}>
      <div className="w-full max-w-[480px] rounded-xl bg-surface-800 border border-surface-600 shadow-2xl text-surface-100"
        onMouseDown={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

export function WipeLabDialog({ onClose }: { onClose: () => void }) {
  const { topology, lab, destroy, currentTopologyId } = useLabStore();
  const name = topology?.name ?? '';
  const running = Object.values(lab?.nodes ?? {}).filter(n => n.container && n.state === 'running').length;
  const [typed, setTyped] = useState('');
  const [snapshot, setSnapshot] = useState(running > 0);
  const [step, setStep] = useState('');
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  // Dash style, case and spacing don't matter ("—" is hard to type).
  const norm = (v: string) => v.toLowerCase().replace(/[\u2010-\u2015\u2212-]/g, '-').replace(/\s+/g, ' ').trim();
  const ok = !!name && norm(typed) === norm(name);

  const wipe = async () => {
    if (!ok || step || !currentTopologyId) return;
    setError('');
    if (snapshot && running > 0) {
      setStep('Taking a snapshot of every running device…');
      try {
        const label = `Before wipe ${new Date().toLocaleString()}`;
        const r = await api.snapshotLab(currentTopologyId, label);
        if (r.skipped.some(s => s.reason !== 'not running')) {
          setStep('');
          setError(`Couldn't snapshot ${r.skipped.filter(s => s.reason !== 'not running').map(s => s.node).join(', ')}. ` +
                   'Untick the snapshot to wipe anyway.');
          return;
        }
      } catch (e) {
        setStep(''); setError(`Snapshot failed: ${(e as Error).message}`); return;
      }
    }
    setStep('Wiping…');
    await destroy();
    onClose();
  };

  return (
    <Modal onClose={() => !step && onClose()}>
      <div className="flex items-start gap-3 p-5 pb-3">
        <div className="w-9 h-9 shrink-0 rounded-full bg-red-900/50 flex items-center justify-center"><Trash2 size={17} className="text-red-300" /></div>
        <div className="flex-1">
          <div className="text-base font-semibold">Wipe this lab?</div>
          <div className="text-sm text-surface-300 mt-1">
            Every device is deleted, along with the configuration saved <em>on</em> the devices.
            Next time the lab starts, each device boots from the startup config set in its panel.
          </div>
        </div>
        <button onClick={onClose} disabled={!!step} className="p-1 rounded hover:bg-surface-700 text-surface-400"><X size={16} /></button>
      </div>
      <div className="px-5 space-y-3 text-sm">
        <div className="rounded-lg bg-surface-900/60 border border-surface-700 px-3 py-2 text-[13px] text-surface-300">
          To keep the devices and their config, use <b className="text-surface-100">Stop</b> instead.
        </div>
        <label className={`flex items-start gap-2 ${running ? 'cursor-pointer' : 'opacity-50'}`}>
          <input type="checkbox" className="mt-0.5" checked={snapshot && running > 0} disabled={!running || !!step}
            onChange={e => setSnapshot(e.target.checked)} />
          <span>
            <span className="flex items-center gap-1.5"><Camera size={13} /> Snapshot every running device first</span>
            <span className="block text-[12px] text-surface-400">
              {running ? 'Restore it later from a device\'s Snapshots — live, or as its startup config.' : 'No devices are running.'}
            </span>
          </span>
        </label>
        <div>
          <div className="text-[12px] text-surface-400 mb-1">Type <b className="text-surface-100 select-all">{name}</b> to confirm</div>
          <input ref={input} value={typed} onChange={e => setTyped(e.target.value)} disabled={!!step}
            onKeyDown={e => { if (e.key === 'Enter') wipe(); }} spellCheck={false}
            className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-600 focus:border-red-500 outline-none" />
        </div>
        {error && <div className="text-[12px] text-red-300 flex gap-1.5"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{error}</div>}
      </div>
      <div className="flex items-center justify-end gap-2 p-5 pt-4">
        {step && <span className="mr-auto flex items-center gap-1.5 text-[12px] text-surface-300"><Loader2 size={13} className="animate-spin" />{step}</span>}
        <button onClick={onClose} disabled={!!step} className="px-3 py-1.5 text-sm rounded bg-surface-700 hover:bg-surface-600 disabled:opacity-40">Cancel</button>
        <button onClick={wipe} disabled={!ok || !!step}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-red-700 hover:bg-red-600 text-white disabled:opacity-40 disabled:cursor-not-allowed">
          <Trash2 size={14} /> Wipe lab
        </button>
      </div>
    </Modal>
  );
}

export function StopFailedDialog({ result, onClose }: { result: StopResult; onClose: () => void }) {
  const stopLab = useLabStore(s => s.stopLab);
  const [busy, setBusy] = useState(false);
  return (
    <Modal onClose={() => !busy && onClose()}>
      <div className="flex items-start gap-3 p-5 pb-3">
        <div className="w-9 h-9 shrink-0 rounded-full bg-amber-900/50 flex items-center justify-center"><AlertTriangle size={17} className="text-amber-300" /></div>
        <div className="flex-1">
          <div className="text-base font-semibold">Some devices couldn't save their config</div>
          <div className="text-sm text-surface-300 mt-1">Nothing was stopped. If you stop anyway, changes on these devices that weren't saved are lost when they stop.</div>
        </div>
      </div>
      <div className="px-5 space-y-1.5 text-[13px]">
        {result.failed.map(f => (
          <div key={f.device} className="rounded bg-surface-900/60 border border-surface-700 px-3 py-1.5">
            <b>{f.device}</b> <span className="text-surface-400">— {f.error}</span>
          </div>
        ))}
        {result.saved.length > 0 && <div className="text-[12px] text-surface-400 pt-1">Saved: {result.saved.map(s => s.device).join(', ')}</div>}
      </div>
      <div className="flex justify-end gap-2 p-5 pt-4">
        <button onClick={onClose} disabled={busy} className="px-3 py-1.5 text-sm rounded bg-surface-700 hover:bg-surface-600">Keep running</button>
        <button disabled={busy} onClick={async () => { setBusy(true); await stopLab(true); onClose(); }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-amber-700 hover:bg-amber-600 text-white disabled:opacity-40">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Square size={13} />} Stop anyway
        </button>
      </div>
    </Modal>
  );
}

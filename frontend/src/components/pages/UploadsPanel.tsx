import { useEffect, useState } from 'react';
import { RotateCcw, X } from 'lucide-react';
import { fmtBytes, fmtEta, useUploadStore } from '../../stores/useUploadStore';

interface Unfinished { id: string; filename: string; size: number; received: number; purpose: string; platform: string }

/** Active uploads (live progress) + unfinished ones left on the server. */
export default function UploadsPanel() {
  const { uploads, cancel, dismiss } = useUploadStore();
  const [unfinished, setUnfinished] = useState<Unfinished[]>([]);

  const loadUnfinished = () => {
    const token = localStorage.getItem('auth_token');
    fetch('/api/lab/uploads/', { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.ok ? r.json() : { uploads: [] }).then(r => setUnfinished(r.uploads)).catch(() => {});
  };
  useEffect(() => { loadUnfinished(); }, [Object.keys(uploads).length]);

  const active = Object.values(uploads);
  const orphans = unfinished.filter(u => !uploads[u.id] && u.received < u.size);
  if (!active.length && !orphans.length) return null;

  return (
    <section className="rounded-lg border border-primary-800/60 bg-primary-950/20 p-3 space-y-3">
      <h2 className="font-semibold text-sm">Uploads</h2>
      {active.map(u => {
        const pct = u.size ? (100 * u.received) / u.size : 0;
        const live = ['uploading', 'retrying', 'finishing'].includes(u.state);
        const eta = u.bytesPerSec > 0 ? (u.size - u.received) / u.bytesPerSec : NaN;
        return (
          <div key={u.id}>
            <div className="flex items-center gap-2 text-xs">
              <span className="font-mono truncate flex-1">{u.filename}</span>
              <span className="text-surface-400">{fmtBytes(u.received)} / {fmtBytes(u.size)}</span>
              {u.state === 'uploading' && u.bytesPerSec > 0 &&
                <span className="text-surface-300">{fmtBytes(u.bytesPerSec)}/s · {fmtEta(eta)} left</span>}
              {live
                ? <button title="Cancel and discard" onClick={() => { if (confirm(`Cancel ${u.filename}? Uploaded data is discarded.`)) cancel(u.id); }}
                    className="text-surface-400 hover:text-red-300"><X size={14} /></button>
                : <button title="Dismiss" onClick={() => dismiss(u.id)} className="text-surface-400 hover:text-surface-100"><X size={14} /></button>}
            </div>
            <div className="mt-1 h-1.5 rounded bg-surface-700 overflow-hidden">
              <div className={`h-full transition-all ${u.state === 'failed' ? 'bg-red-500' : u.state === 'retrying' ? 'bg-amber-400' : 'bg-primary-500'}`}
                style={{ width: `${pct.toFixed(1)}%` }} />
            </div>
            <div className={`text-[11px] mt-0.5 ${u.state === 'failed' ? 'text-red-300' : u.state === 'retrying' ? 'text-amber-300' : 'text-surface-400'}`}>
              {u.state === 'finishing' ? 'Finishing…' : u.message || (u.state === 'uploading' ? `${pct.toFixed(1)}%` : '')}
            </div>
          </div>
        );
      })}
      {orphans.map(u => (
        <div key={u.id} className="flex items-center gap-2 text-xs">
          <RotateCcw size={13} className="text-amber-300" />
          <span className="font-mono truncate flex-1">{u.filename}</span>
          <span className="text-surface-400">{Math.round((100 * u.received) / u.size)}% · {fmtBytes(u.received)} of {fmtBytes(u.size)}</span>
          <span className="text-amber-300">pick the same file again to resume</span>
          <button title="Discard" onClick={async () => { await useUploadStore.getState().cancel(u.id); loadUnfinished(); }}
            className="text-surface-400 hover:text-red-300"><X size={14} /></button>
        </div>
      ))}
    </section>
  );
}

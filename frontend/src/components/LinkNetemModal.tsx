import { useEffect, useState } from 'react';
import { api } from '../utils/api';
import toast from 'react-hot-toast';
import { X, Zap, RotateCcw } from 'lucide-react';

type Impairment = { delay: string; jitter: string; loss: number; rate: number; corruption: number } | null;
type Endpoint = { container: string; interface: string; impairment: Impairment };

const EMPTY = { delay: '', jitter: '', loss: 0, rate: 0, corruption: 0 };

/** Query / modify containerlab netem link impairments (symmetric — both ends).
 *  Lab-mode only; requires a running lab and the sch_netem host kernel module. */
export default function LinkNetemModal({ topologyId, linkId, label, onClose }: {
  topologyId: number; linkId: number; label: string; onClose: () => void;
}) {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const showResult = (r: any) => {
    const eps: Endpoint[] = r?.endpoints || [];
    setEndpoints(eps);
    const imp = eps[0]?.impairment;
    setForm(imp ? { delay: imp.delay || '', jitter: imp.jitter || '', loss: imp.loss || 0, rate: imp.rate || 0, corruption: imp.corruption || 0 } : { ...EMPTY });
  };

  useEffect(() => {
    (async () => {
      setLoading(true);
      try { showResult(await api.getLinkDegradation(topologyId, linkId)); }
      catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Failed to query'); }
      setLoading(false);
    })();
  }, [topologyId, linkId]);

  const apply = async () => {
    setBusy(true);
    try { showResult(await api.setLinkDegradation(topologyId, linkId, form)); toast.success('Impairment applied to both ends'); }
    catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Apply failed'); }
    setBusy(false);
  };
  const reset = async () => {
    setBusy(true);
    try { showResult(await api.resetLinkDegradation(topologyId, linkId)); toast.success('Impairment cleared'); }
    catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Reset failed'); }
    setBusy(false);
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-surface-800 border border-surface-600 rounded-lg shadow-xl p-4 w-[380px] fade-in" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-1">
          <div className="text-sm font-medium flex items-center gap-1.5"><Zap size={14} /> Link degradation</div>
          <button className="text-surface-400 hover:text-surface-200" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="text-[11px] text-surface-400 mb-2 truncate" title={label}>{label}</div>

        {loading ? (
          <div className="text-xs text-surface-500 py-3 text-center">Querying…</div>
        ) : (
          <>
            <div className="text-[10px] text-surface-400 mb-2">
              Current (per endpoint):
              {endpoints.map((e, i) => (
                <div key={i} className="text-surface-300">
                  {e.interface}: {e.impairment
                    ? `${e.impairment.delay || '0'}${e.impairment.jitter ? ` ±${e.impairment.jitter}` : ''}, loss ${e.impairment.loss}%, rate ${e.impairment.rate || '∞'}kbit, corrupt ${e.impairment.corruption}%`
                    : 'none'}
                </div>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-2 mb-3">
              <label className="text-[10px] text-surface-400">Delay
                <input className="w-full text-xs mt-0.5" placeholder="50ms" value={form.delay} onChange={e => setForm({ ...form, delay: e.target.value })} />
              </label>
              <label className="text-[10px] text-surface-400">Jitter
                <input className="w-full text-xs mt-0.5" placeholder="5ms" value={form.jitter} onChange={e => setForm({ ...form, jitter: e.target.value })} />
              </label>
              <label className="text-[10px] text-surface-400">Loss %
                <input type="number" step="0.1" min="0" className="w-full text-xs mt-0.5" value={form.loss} onChange={e => setForm({ ...form, loss: parseFloat(e.target.value) || 0 })} />
              </label>
              <label className="text-[10px] text-surface-400">Rate (Kbit, 0=∞)
                <input type="number" min="0" className="w-full text-xs mt-0.5" value={form.rate} onChange={e => setForm({ ...form, rate: parseInt(e.target.value) || 0 })} />
              </label>
              <label className="text-[10px] text-surface-400">Corruption %
                <input type="number" step="0.1" min="0" className="w-full text-xs mt-0.5" value={form.corruption} onChange={e => setForm({ ...form, corruption: parseFloat(e.target.value) || 0 })} />
              </label>
            </div>
            <div className="text-[9px] text-surface-500 mb-2">Applied symmetrically to both endpoints. Cleared on lab redeploy.</div>

            <div className="flex justify-between gap-2">
              <button className="btn btn-ghost btn-sm text-xs" onClick={reset} disabled={busy}><RotateCcw size={12} /> Reset</button>
              <div className="flex gap-2">
                <button className="btn btn-ghost btn-sm text-xs" onClick={onClose}>Close</button>
                <button className="btn btn-primary btn-sm text-xs" onClick={apply} disabled={busy}><Zap size={12} /> Apply</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

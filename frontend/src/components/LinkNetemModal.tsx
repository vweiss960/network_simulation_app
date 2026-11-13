/**
 * Link conditions (netem): delay / jitter / loss / corruption / rate on a
 * running cable, for both directions or one. Impairment set on a device's
 * end shapes traffic leaving that device, so "R1 → R2" is R1's end.
 * Requires the sch_netem kernel module on the host; cleared on redeploy.
 */
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Activity, ArrowLeftRight, RotateCcw, X, Zap } from 'lucide-react';
import { api } from '../utils/api';
import { useLabStore } from '../stores/useLabStore';

type Imp = { delay: string; jitter: string; loss: number; rate: number; corruption: number } | null;
type Endpoint = { side: 'a' | 'b'; node: string; container: string; interface: string; impairment: Imp };
type Dir = 'both' | 'a' | 'b';
type Form = { delay: number; jitter: number; loss: number; corruption: number; unlimited: boolean; mbps: number };

const CLEAN: Form = { delay: 0, jitter: 0, loss: 0, corruption: 0, unlimited: true, mbps: 0 };
const PRESETS: { name: string; f: Form }[] = [
  { name: 'Clean', f: CLEAN },
  { name: 'Long-haul WAN', f: { delay: 40, jitter: 5, loss: 0.5, corruption: 0, unlimited: true, mbps: 0 } },
  { name: 'Satellite', f: { delay: 300, jitter: 20, loss: 1, corruption: 0, unlimited: false, mbps: 20 } },
  { name: 'Lossy wireless', f: { delay: 8, jitter: 12, loss: 5, corruption: 0.5, unlimited: true, mbps: 0 } },
  { name: 'Congested 2 Mbps', f: { delay: 25, jitter: 15, loss: 2, corruption: 0, unlimited: false, mbps: 2 } },
];

/** "40ms" / "1.5s" / "250us" → milliseconds. */
function toMs(v: string): number {
  const m = String(v || '').trim().match(/^([\d.]+)\s*(us|µs|ms|s)?$/i);
  if (!m) return 0;
  const n = parseFloat(m[1]);
  const u = (m[2] || 'ms').toLowerCase();
  return u === 's' ? n * 1000 : u === 'us' || u === 'µs' ? n / 1000 : n;
}
const fmt = (n: number) => (Math.round(n * 10) / 10).toString();
const same = (a: Form, b: Form) => JSON.stringify(a) === JSON.stringify(b);

export default function LinkNetemModal({ topologyId, linkId, onClose }: {
  topologyId: number; linkId: number; label?: string; onClose: () => void;
}) {
  const { links, nodes } = useLabStore();
  const link = links.find(l => l.id === linkId);
  const nameOf = (id?: number) => nodes.find(n => n.id === id)?.name ?? '?';
  const A = { name: nameOf(link?.source_node_id), iface: link?.source_interface ?? '' };
  const B = { name: nameOf(link?.target_node_id), iface: link?.target_interface ?? '' };

  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [form, setForm] = useState<Form>(CLEAN);
  const [dir, setDir] = useState<Dir>('both');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = (r: { endpoints?: Endpoint[] }) => {
    const eps = r?.endpoints ?? [];
    setEndpoints(eps);
    const ia = eps.find(e => e.side === 'a')?.impairment, ib = eps.find(e => e.side === 'b')?.impairment;
    const imp = ia || ib;
    setDir(ia && ib ? 'both' : ia ? 'a' : ib ? 'b' : 'both');
    setForm(imp ? {
      delay: toMs(imp.delay), jitter: toMs(imp.jitter), loss: Number(imp.loss) || 0, corruption: Number(imp.corruption) || 0,
      unlimited: !imp.rate, mbps: imp.rate ? Number(imp.rate) / 1000 : 0,
    } : CLEAN);
  };

  useEffect(() => {
    (async () => {
      setLoading(true);
      try { load(await api.getLinkDegradation(topologyId, linkId)); }
      catch (e) { toast.error((e as Error).message.replace(/^API error \d+: /, '') || 'Failed to query'); }
      setLoading(false);
    })();
  }, [topologyId, linkId]);

  const degraded = endpoints.some(e => e.impairment);
  const deviceSides = endpoints.map(e => e.side);
  const oneEnd = deviceSides.length === 1;      // cabled to a Network node: only the device end can be shaped
  const preset = PRESETS.find(p => same(p.f, form))?.name;
  const set = (patch: Partial<Form>) => setForm(f => ({ ...f, ...patch }));
  const jitterNoDelay = form.jitter > 0 && form.delay <= 0;

  // What a ping from A to B would see.
  const summary = useMemo(() => {
    const fwd = oneEnd ? deviceSides[0] === 'a' : dir !== 'b';   // A → B leg impaired
    const ret = oneEnd ? deviceSides[0] === 'b' : dir !== 'a';   // B → A leg impaired
    const legs = Number(fwd) + Number(ret);
    const rtt = form.delay * legs, jit = form.jitter * legs;
    const p = Math.min(1, (form.loss + form.corruption) / 100);
    const lost = 1 - Math.pow(1 - p, legs);
    return { rtt, jit, lost: lost * 100, legs };
  }, [form, dir, oneEnd, deviceSides]);

  const apply = async () => {
    setBusy(true);
    try {
      load(await api.setLinkDegradation(topologyId, linkId, {
        delay: form.delay > 0 ? `${fmt(form.delay)}ms` : '',
        jitter: form.jitter > 0 ? `${fmt(form.jitter)}ms` : '',
        loss: form.loss, corruption: form.corruption,
        rate: form.unlimited ? 0 : Math.round(form.mbps * 1000),
        direction: oneEnd ? deviceSides[0] : dir,
      }));
      toast.success(same(form, CLEAN) ? 'Link is clean' : 'Link conditions applied');
      onClose();
    } catch (e) { toast.error((e as Error).message.replace(/^API error \d+: /, '') || 'Apply failed'); }
    setBusy(false);
  };
  const clearAll = async () => {
    setBusy(true);
    try { load(await api.resetLinkDegradation(topologyId, linkId)); toast.success('Link conditions cleared'); }
    catch (e) { toast.error((e as Error).message.replace(/^API error \d+: /, '') || 'Clear failed'); }
    setBusy(false);
  };

  const section = 'px-6 text-[11px] font-bold tracking-wider uppercase text-slate-400';
  const numBox = 'flex items-center rounded-lg bg-[#0d1117] border border-[#30363d] focus-within:border-[#2f81f7] focus-within:ring-2 focus-within:ring-[#2f81f7]/25';
  const numInput = 'w-full bg-transparent px-3 py-2.5 text-[15px] font-mono text-slate-100 outline-none';

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/55" onMouseDown={onClose}>
      <div className="w-[580px] max-w-[calc(100vw-24px)] max-h-[calc(100vh-24px)] overflow-y-auto rounded-2xl bg-[#161b22] border border-[#30363d] shadow-2xl text-slate-200"
        onMouseDown={e => e.stopPropagation()}>
        {/* Header */}
        <div className="px-6 pt-5 pb-4 flex items-start gap-3.5">
          <div className="w-11 h-11 shrink-0 rounded-xl bg-amber-500/15 border border-amber-500/30 flex items-center justify-center text-amber-400">
            <Zap size={20} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-lg font-semibold text-white">Link conditions</div>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <EndChip name={A.name} iface={A.iface} />
              <ArrowLeftRight size={16} className="text-slate-400" />
              <EndChip name={B.name} iface={B.iface} />
              {!loading && (
                <span className={`ml-1 flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs
                  ${degraded ? 'bg-amber-500/15 text-amber-300' : 'bg-emerald-500/15 text-emerald-300'}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${degraded ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                  {degraded ? 'Currently degraded' : 'Currently clean'}
                </span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-white/10"><X size={18} /></button>
        </div>

        {loading ? <div className="px-6 pb-8 text-sm text-slate-400">Reading the link's current conditions…</div> : <>
          {/* Presets */}
          <div className={section}>Start from a preset</div>
          <div className="px-6 pt-2 pb-5 flex flex-wrap gap-2 border-b border-[#30363d]">
            {PRESETS.map(p => (
              <button key={p.name} onClick={() => setForm(p.f)}
                className={`px-3.5 py-1.5 rounded-full text-sm border transition-colors
                  ${preset === p.name ? 'border-[#2f81f7] bg-[#2f81f7]/15 text-white font-medium' : 'border-[#30363d] hover:border-slate-500 text-slate-200'}`}>
                {p.name}
              </button>
            ))}
          </div>

          {/* Direction */}
          <div className="px-6 py-4 flex items-center justify-between gap-4">
            <span className="text-sm text-slate-300">Apply to</span>
            {oneEnd
              ? <span className="text-xs text-slate-400">Traffic leaving {deviceSides[0] === 'a' ? A.name : B.name} (the other end is a network)</span>
              : (
                <div className="flex p-1 rounded-lg bg-[#0d1117] border border-[#30363d]">
                  {([['both', 'Both directions'], ['a', `${A.name} → ${B.name}`], ['b', `${B.name} → ${A.name}`]] as [Dir, string][]).map(([d, l]) => (
                    <button key={d} onClick={() => setDir(d)}
                      className={`px-3.5 py-1.5 rounded-md text-sm ${dir === d ? 'bg-[#30363d] text-white font-semibold' : 'text-slate-400 hover:text-slate-200'}`}>
                      {l}
                    </button>
                  ))}
                </div>
              )}
          </div>

          {/* Timing */}
          <div className={section}>Timing</div>
          <div className="px-6 pt-2 pb-4 grid grid-cols-2 gap-3">
            <label className="text-sm text-slate-300">Delay
              <div className={`${numBox} mt-1.5`}>
                <input type="number" min={0} step={1} className={numInput} value={form.delay || ''} placeholder="0"
                  onChange={e => set({ delay: Math.max(0, Number(e.target.value) || 0) })} />
                <span className="pr-3 text-xs text-slate-400">ms</span>
              </div>
            </label>
            <label className="text-sm text-slate-300">Jitter
              <div className={`${numBox} mt-1.5`}>
                <input type="number" min={0} step={1} className={numInput} value={form.jitter || ''} placeholder="0"
                  onChange={e => set({ jitter: Math.max(0, Number(e.target.value) || 0) })} />
                <span className="pr-3 text-xs text-slate-400 whitespace-nowrap">± ms</span>
              </div>
            </label>
            {jitterNoDelay && <div className="col-span-2 text-xs text-amber-300">Jitter needs a delay: it varies the delay by ± that amount.</div>}
          </div>

          {/* Reliability */}
          <div className={section}>Reliability</div>
          <div className="px-6 pt-1 pb-4 space-y-2">
            <SliderRow label="Packet loss" value={form.loss} max={20} onChange={v => set({ loss: v })} />
            <SliderRow label="Corruption" value={form.corruption} max={10} onChange={v => set({ corruption: v })} />
          </div>

          {/* Bandwidth */}
          <div className={section}>Bandwidth</div>
          <div className="px-6 pt-2 pb-4 flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-slate-200 cursor-pointer shrink-0">
              <input type="checkbox" className="w-4 h-4 accent-[#2f81f7]" checked={form.unlimited}
                onChange={e => set({ unlimited: e.target.checked, mbps: e.target.checked ? 0 : form.mbps || 10 })} />
              Unlimited
            </label>
            <div className={`${numBox} flex-1 ${form.unlimited ? 'opacity-50' : ''}`}>
              <input type="number" min={0} step={0.5} disabled={form.unlimited} className={numInput}
                value={form.unlimited ? '' : form.mbps || ''} placeholder="Rate limit"
                onChange={e => set({ mbps: Math.max(0, Number(e.target.value) || 0) })} />
              <span className="pr-3 text-xs text-slate-400">Mbps</span>
            </div>
          </div>

          {/* What it means */}
          <div className="mx-6 mb-5 px-4 py-3 rounded-lg bg-[#0d1117] border border-[#30363d] flex items-start gap-2.5 text-sm text-slate-300">
            <Activity size={16} className="mt-0.5 shrink-0 text-slate-400" />
            <span>
              {same(form, CLEAN)
                ? <>The link runs clean: no added delay, loss or rate limit.</>
                : <>Ping {A.name} → {B.name} will take about{' '}
                    <b className="text-white">{fmt(summary.rtt)} ms{summary.jit ? ` ± ${fmt(summary.jit)}` : ''}</b>
                    {summary.lost > 0 && <> with <b className="text-white">~{summary.lost < 1 ? fmt(summary.lost) : Math.round(summary.lost)}%</b> of round trips lost</>}
                    {!form.unlimited && form.mbps > 0 && <>; throughput capped at <b className="text-white">{fmt(form.mbps)} Mbps</b>{summary.legs === 1 ? ' one way' : ''}</>}
                    .</>}
            </span>
          </div>
        </>}

        {/* Footer */}
        <div className="px-6 py-4 border-t border-[#30363d] flex items-center gap-3">
          <button onClick={clearAll} disabled={busy || loading || !degraded}
            className="flex items-center gap-1.5 text-sm text-slate-300 hover:text-white disabled:opacity-40">
            <RotateCcw size={15} /> Clear all
          </button>
          <span className="text-xs text-slate-500">Cleared when the lab is redeployed</span>
          <div className="flex-1" />
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-[#30363d] text-sm hover:bg-white/5">Cancel</button>
          <button onClick={apply} disabled={busy || loading || jitterNoDelay}
            className="px-4 py-2 rounded-lg bg-[#2f81f7] hover:bg-[#4493f8] text-sm font-semibold text-white disabled:opacity-40">
            {busy ? 'Applying…' : 'Apply to link'}
          </button>
        </div>
      </div>
    </div>
  );
}

function EndChip({ name, iface }: { name: string; iface: string }) {
  return (
    <span className="px-2 py-0.5 rounded-md bg-[#0d1117] border border-[#30363d] text-sm">
      <b className="text-white">{name}</b> <span className="font-mono text-xs text-slate-400">{iface}</span>
    </span>
  );
}

function SliderRow({ label, value, max, onChange }: { label: string; value: number; max: number; onChange: (v: number) => void }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="flex items-center gap-4">
      <span className="w-28 text-sm text-slate-300 shrink-0">{label}</span>
      <input type="range" min={0} max={max} step={0.1} value={Math.min(value, max)} onChange={e => onChange(Number(e.target.value))}
        className="plabs-range flex-1" style={{ ['--pct' as string]: `${pct}%` }} />
      <div className="flex items-center w-24 rounded-lg bg-[#0d1117] border border-[#30363d] focus-within:border-[#2f81f7]">
        <input type="number" min={0} max={100} step={0.1} value={value}
          onChange={e => onChange(Math.min(100, Math.max(0, Number(e.target.value) || 0)))}
          className="w-full bg-transparent px-2.5 py-2 text-[15px] font-mono text-slate-100 outline-none" />
        <span className="pr-2.5 text-xs text-slate-400">%</span>
      </div>
    </div>
  );
}

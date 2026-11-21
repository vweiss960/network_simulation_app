/**
 * A traffic generator node's panel (console-drawer tab): its interface, the
 * UDP streams it sends (a DSCP per stream, unicast or multicast), the groups
 * it joins / ports it listens on, its reflector, and live stats — rate, loss,
 * jitter and the DSCP every flow arrives with. Edits are applied live by the
 * node (backend services/traffic_gen.py); nothing restarts.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Play, Plus, RotateCcw, Square, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../utils/api';
import { useLabStore } from '../stores/useLabStore';
import type { DscpCounts, TrafficConfig, TrafficFlowStats, TrafficReceiverCfg, TrafficState, TrafficStreamCfg } from '../types';

const K = {
  bg: '#1e1e1e', bar: '#252526', card: '#252526', border: '#3c3c3c', text: '#cccccc', dim: '#8b949e',
  input: '#3c3c3c', accent: '#0e639c', good: '#89d185', bad: '#f14c4c', warn: '#cca700',
};
const DSCP_OPTIONS: [string, number][] = [
  ['default', 0], ['cs1', 8], ['af11', 10], ['af12', 12], ['af13', 14], ['cs2', 16], ['af21', 18], ['af22', 20],
  ['af23', 22], ['cs3', 24], ['af31', 26], ['af32', 28], ['af33', 30], ['cs4', 32], ['af41', 34], ['af42', 36],
  ['af43', 38], ['cs5', 40], ['ef', 46], ['cs6', 48], ['cs7', 56],
];
const dscpName = (n: number | string) => DSCP_OPTIONS.find(([, v]) => v === Number(n))?.[0] ?? String(n);
// A stable colour per DSCP for the counts chips.
const DSCP_COLOR: Record<number, string> = { 0: '#6e7681', 46: '#f14c4c', 10: '#3794ff', 12: '#b180d7', 14: '#c586c0',
  34: '#d7ba7d', 26: '#4ec9b0', 18: '#89d185', 48: '#cca700', 8: '#9cdcfe' };

const isMcast = (a: string) => /^(22[4-9]|23\d)\./.test(a) || /^ff/i.test(a);
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

function Chips({ counts }: { counts: DscpCounts }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return <span style={{ color: K.dim }}>—</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([d, n]) => (
        <span key={d} className="px-1.5 rounded text-[11px] font-medium" title={`DSCP ${d}: ${n} packets in the last 10 s`}
          style={{ background: (DSCP_COLOR[Number(d)] ?? '#569cd6') + '33', color: DSCP_COLOR[Number(d)] ?? '#9cdcfe' }}>
          {dscpName(d)} {Math.round(100 * n / total)}%
        </span>
      ))}
    </span>
  );
}

function Field({ label, value, onChange, placeholder, w = 'w-40', mono = true, title }: {
  label?: string; value: string; onChange: (v: string) => void; placeholder?: string; w?: string; mono?: boolean; title?: string;
}) {
  return (
    <label className="flex flex-col gap-0.5" title={title}>
      {label && <span className="text-[10px] uppercase tracking-wide" style={{ color: K.dim }}>{label}</span>}
      <input className={`${w} h-6 px-1.5 rounded outline-none text-[12px] ${mono ? 'font-mono' : ''}`}
        style={{ background: K.input, color: K.text, border: `1px solid ${K.border}` }}
        value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} />
    </label>
  );
}

function DscpSelect({ value, onChange, keep }: { value: number | 'keep'; onChange: (v: number | 'keep') => void; keep?: boolean }) {
  return (
    <select className="h-6 px-1 rounded outline-none text-[12px]" style={{ background: K.input, color: K.text, border: `1px solid ${K.border}` }}
      value={String(value)} onChange={e => onChange(e.target.value === 'keep' ? 'keep' : Number(e.target.value))}>
      {keep && <option value="keep">keep</option>}
      {DSCP_OPTIONS.map(([n, v]) => <option key={n} value={v}>{n} ({v})</option>)}
    </select>
  );
}

function Card({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded mb-2" style={{ background: K.card, border: `1px solid ${K.border}` }}>
      <div className="flex items-center gap-2 px-2 h-7 text-[11px] uppercase tracking-wide font-semibold"
        style={{ color: K.dim, borderBottom: `1px solid ${K.border}` }}>
        {title}<span className="ml-auto normal-case tracking-normal font-normal">{right}</span>
      </div>
      <div className="p-2">{children}</div>
    </section>
  );
}

const btn = 'inline-flex items-center gap-1 h-6 px-2 rounded text-[12px] disabled:opacity-40';

export default function TrafficView({ nodeId }: { nodeId: number }) {
  const tid = useLabStore(s => s.currentTopologyId)!;
  const [state, setState] = useState<TrafficState | null>(null);
  const [draft, setDraft] = useState<TrafficConfig | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;

  const load = useCallback(async () => {
    try {
      const s = await api.getTraffic(tid, nodeId);
      setState(s);
      setErr('');
      if (!dirtyRef.current) setDraft(clone(s.config));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [tid, nodeId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [load]);

  const edit = (f: (c: TrafficConfig) => void) => {
    setDraft(d => { if (!d) return d; const c = clone(d); f(c); return c; });
    setDirty(true);
  };

  const apply = async (cfg = draft) => {
    if (!cfg) return;
    setSaving(true);
    try {
      const s = await api.putTraffic(tid, nodeId, cfg);
      setState(s);
      setDraft(clone(s.config));
      setDirty(false);
    } catch (e) { toast.error(e instanceof Error ? e.message : String(e), { duration: 6000 }); }
    finally { setSaving(false); }
  };

  // Start / stop one stream right away (keeps any other pending edits).
  const toggle = (i: number) => {
    if (!draft) return;
    const c = clone(draft);
    c.streams[i].enabled = !c.streams[i].enabled;
    setDraft(c);
    apply(c);
  };

  if (!draft || !state) {
    return <div className="w-full h-full flex items-center justify-center" style={{ background: K.bg, color: K.dim }}>
      {err ? <span style={{ color: K.bad }}>{err}</span> : <Loader2 className="animate-spin" size={18} />}
    </div>;
  }

  const st = state.stats;
  const streamStats = (name: string) => st?.streams.find(s => s.name === name);
  const status = state.running
    ? <span className="flex items-center gap-1.5" style={{ color: K.good }}><span className="w-2 h-2 rounded-full" style={{ background: K.good }} />Running</span>
    : state.container === 'running'
      ? <span className="flex items-center gap-1.5" style={{ color: K.warn }}><Loader2 size={12} className="animate-spin" />Starting…</span>
      : <span className="flex items-center gap-1.5" style={{ color: K.dim }}><span className="w-2 h-2 rounded-full bg-[#6e7681]" />Not running — changes apply when it starts</span>;
  const problems = [st?.config_error, st?.interface?.error,
    ...(st?.receivers ?? []).filter(r => r.error).map(r => `${r.group || 'listener'}:${r.port}: ${r.error}`),
    ...(st?.streams ?? []).filter(s => s.error).map(s => `${s.name}: ${s.error}`)].filter(Boolean) as string[];

  const flows: TrafficFlowStats[] = st?.flows ?? [];

  return (
    <div className="w-full h-full flex flex-col text-[12px]" style={{ background: K.bg, color: K.text }}>
      <div className="h-9 shrink-0 flex items-center gap-3 px-2" style={{ background: K.bar, borderBottom: `1px solid ${K.border}` }}>
        <span className="font-semibold">{state.name}</span>
        {status}
        <span className="ml-auto flex items-center gap-2">
          {dirty && <span style={{ color: K.warn }}>Unapplied changes</span>}
          <button className={btn} style={{ border: `1px solid ${K.border}` }} disabled={!dirty || saving}
            onClick={() => { setDraft(clone(state.config)); setDirty(false); }}><RotateCcw size={12} />Revert</button>
          <button className={`${btn} text-white`} style={{ background: K.accent }} disabled={!dirty || saving} onClick={() => apply()}>
            {saving ? <Loader2 size={12} className="animate-spin" /> : null}Apply</button>
        </span>
      </div>
      {problems.length > 0 && (
        <div className="px-3 py-1 text-[11px] shrink-0" style={{ background: '#3a1d1d', color: '#f48771', borderBottom: `1px solid ${K.border}` }}>
          {problems.join(' · ')}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto p-2 grid gap-2" style={{ gridTemplateColumns: 'minmax(520px, 1.15fr) minmax(380px, 1fr)' }}>
        {/* --- configuration --- */}
        <div>
          <Card title="Interface" right={<span style={{ color: K.dim }}>{draft.interface.name}</span>}>
            <div className="flex flex-wrap gap-2">
              <Field label="IPv4 address / prefix" value={draft.interface.ipv4} placeholder="10.1.3.10/24"
                onChange={v => edit(c => { c.interface.ipv4 = v; })} />
              <Field label="IPv4 gateway" value={draft.interface.gateway4} placeholder="10.1.3.1" w="w-32"
                onChange={v => edit(c => { c.interface.gateway4 = v; })} />
              <Field label="IPv6 address / prefix" value={draft.interface.ipv6} placeholder="2001:db8:1:3::10/64" w="w-48"
                onChange={v => edit(c => { c.interface.ipv6 = v; })} />
              <Field label="IPv6 gateway" value={draft.interface.gateway6} placeholder="2001:db8:1:3::1" w="w-40"
                onChange={v => edit(c => { c.interface.gateway6 = v; })} />
            </div>
          </Card>

          <Card title="Send — streams" right={
            <button className={btn} style={{ border: `1px solid ${K.border}` }}
              onClick={() => edit(c => {
                let i = c.streams.length + 1;
                while (c.streams.some(s => s.name === `S${i}`)) i++;
                c.streams.push({ name: `S${i}`, dst: '', port: 5001, rate_pps: 10, size: 100, dscp: 0, ttl: null, enabled: true });
              })}><Plus size={12} />Stream</button>}>
            {draft.streams.length === 0
              ? <div style={{ color: K.dim }}>No streams. Add one to send UDP traffic to a host, a multicast group or a reflector.</div>
              : <table className="w-full border-separate" style={{ borderSpacing: '0 3px' }}>
                  <thead><tr className="text-left text-[10px] uppercase tracking-wide" style={{ color: K.dim }}>
                    <th className="font-normal"></th><th className="font-normal">Name</th><th className="font-normal">Destination</th>
                    <th className="font-normal">Port</th><th className="font-normal" title="Packets per second">pps</th>
                    <th className="font-normal" title="UDP payload bytes">Bytes</th><th className="font-normal">DSCP</th>
                    <th className="font-normal" title="TTL / hop limit (default 64, multicast 16)">TTL</th><th></th>
                  </tr></thead>
                  <tbody>
                    {draft.streams.map((s: TrafficStreamCfg, i) => {
                      const live = streamStats(s.name);
                      return (
                        <tr key={i}>
                          <td className="pr-1">
                            <button className={btn + ' px-1'} title={s.enabled ? 'Stop this stream' : 'Start this stream'}
                              style={{ color: s.enabled ? K.bad : K.good }} onClick={() => toggle(i)} disabled={saving}>
                              {s.enabled ? <Square size={12} /> : <Play size={12} />}
                            </button>
                          </td>
                          <td><Field w="w-20" value={s.name} onChange={v => edit(c => { c.streams[i].name = v; })} /></td>
                          <td><Field w="w-36" value={s.dst} placeholder="10.1.4.10 / 239.1.1.1"
                            onChange={v => edit(c => { c.streams[i].dst = v.trim(); })} /></td>
                          <td><Field w="w-14" value={String(s.port)} onChange={v => edit(c => { c.streams[i].port = Number(v) || 0; })} /></td>
                          <td><Field w="w-14" value={String(s.rate_pps)} onChange={v => edit(c => { c.streams[i].rate_pps = Number(v) || 0; })} /></td>
                          <td><Field w="w-14" value={String(s.size)} onChange={v => edit(c => { c.streams[i].size = Number(v) || 0; })} /></td>
                          <td><DscpSelect value={s.dscp} onChange={v => edit(c => { c.streams[i].dscp = v as number; })} /></td>
                          <td><Field w="w-10" value={s.ttl == null ? '' : String(s.ttl)} placeholder={isMcast(s.dst) ? '16' : '64'}
                            onChange={v => edit(c => { c.streams[i].ttl = v.trim() ? Number(v) : null; })} /></td>
                          <td className="pl-1 whitespace-nowrap">
                            <span className="tabular-nums mr-1" style={{ color: live ? K.good : K.dim }}
                              title="Packets sent per second (last 5 s)">{live ? `${live.pps} pps` : s.enabled ? '' : 'stopped'}</span>
                            <button className={btn + ' px-1'} style={{ color: K.dim }} title="Remove"
                              onClick={() => edit(c => { c.streams.splice(i, 1); })}><Trash2 size={12} /></button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>}
          </Card>

          <Card title="Receive — groups and listeners" right={
            <span className="flex gap-1">
              <button className={btn} style={{ border: `1px solid ${K.border}` }}
                onClick={() => edit(c => { c.receivers.push({ group: '239.1.1.1', source: '', port: 5001 }); })}><Plus size={12} />Group</button>
              <button className={btn} style={{ border: `1px solid ${K.border}` }}
                onClick={() => edit(c => { c.receivers.push({ group: '', source: '', port: 5001 }); })}><Plus size={12} />Listener</button>
            </span>}>
            {draft.receivers.length === 0
              ? <div style={{ color: K.dim }}>Nothing joined. Add a multicast group (any-source, or with a source for SSM) or a unicast UDP listener.</div>
              : draft.receivers.map((r: TrafficReceiverCfg, i) => {
                  const joined = st?.receivers.find(x => x.group === r.group && x.port === r.port && (x.source || '') === (r.source || ''));
                  return (
                    <div key={i} className="flex items-end gap-2 mb-1">
                      <Field label={i === 0 ? 'Group (empty = unicast)' : undefined} w="w-36" value={r.group} placeholder="unicast listener"
                        onChange={v => edit(c => { c.receivers[i].group = v.trim(); })} />
                      <Field label={i === 0 ? 'Source (SSM)' : undefined} w="w-40" value={r.source} placeholder={r.group ? 'any source' : '—'}
                        title="A source makes it a source-specific join (IGMPv3 / MLDv2 INCLUDE)"
                        onChange={v => edit(c => { c.receivers[i].source = v.trim(); })} />
                      <Field label={i === 0 ? 'Port' : undefined} w="w-14" value={String(r.port)}
                        onChange={v => edit(c => { c.receivers[i].port = Number(v) || 0; })} />
                      {!r.group && (
                        <label className="flex items-center gap-1 h-6" style={{ color: K.dim }}>
                          <input type="checkbox" checked={!!r.ipv6} onChange={e => edit(c => { c.receivers[i].ipv6 = e.target.checked; })} />IPv6
                        </label>
                      )}
                      <span className="h-6 flex items-center text-[11px]" style={{ color: joined ? (joined.error ? K.bad : K.good) : K.dim }}>
                        {joined ? (joined.error ? 'error' : r.group ? (r.source ? `joined (${r.source}, ${r.group})` : 'joined') : 'listening') : ''}
                      </span>
                      <button className={btn + ' px-1 ml-auto'} style={{ color: K.dim }} title="Remove"
                        onClick={() => edit(c => { c.receivers.splice(i, 1); })}><Trash2 size={12} /></button>
                    </div>
                  );
                })}
          </Card>

          <Card title="Reflector" right={st?.reflector?.enabled ? <span style={{ color: K.good }}>{st.reflector.reflected} reflected</span> : null}>
            <div className="flex items-end gap-3">
              <label className="flex items-center gap-1.5 h-6">
                <input type="checkbox" checked={draft.reflector.enabled} onChange={e => edit(c => { c.reflector.enabled = e.target.checked; })} />
                Send stream packets back to their sender
              </label>
              <Field label="Port" w="w-14" value={String(draft.reflector.port)} onChange={v => edit(c => { c.reflector.port = Number(v) || 0; })} />
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] uppercase tracking-wide" style={{ color: K.dim }}>Mark replies</span>
                <DscpSelect keep value={draft.reflector.dscp} onChange={v => edit(c => { c.reflector.dscp = v; })} />
              </label>
            </div>
          </Card>
        </div>

        {/* --- live stats --- */}
        <div>
          <Card title="Sending" right={st ? <span style={{ color: K.dim }}>live</span> : null}>
            {!st?.streams.length
              ? <div style={{ color: K.dim }}>{state.running ? 'No streams running.' : '—'}</div>
              : <table className="w-full text-left">
                  <thead><tr className="text-[10px] uppercase tracking-wide" style={{ color: K.dim }}>
                    <th className="font-normal">Stream</th><th className="font-normal">To</th><th className="font-normal">pps</th>
                    <th className="font-normal">Sent</th><th className="font-normal" title="Copies sent back by a reflector">Reflected</th>
                  </tr></thead>
                  <tbody>
                    {st.streams.map(s => (
                      <tr key={s.name} className="align-top">
                        <td className="py-0.5 font-medium">{s.name} <span className="text-[11px]" style={{ color: K.dim }}>{dscpName(s.dscp)}</span></td>
                        <td className="font-mono">{s.dst}:{s.port}</td>
                        <td className="tabular-nums">{s.pps}</td>
                        <td className="tabular-nums">{s.sent}</td>
                        <td className="text-[11px]">
                          {s.reflected && s.reflected.last_seen_s < 5 ? (
                            <div className="flex flex-col gap-0.5">
                              <span className="tabular-nums">{s.reflected.pps} pps · loss {s.reflected.loss_pct ?? '—'}% · RTT {s.reflected.rtt_ms ?? '—'} ms</span>
                              <span>at reflector: <Chips counts={s.reflected.dscp_at_reflector} /></span>
                              <span>back: <Chips counts={s.reflected.dscp_back} /></span>
                            </div>
                          ) : <span style={{ color: K.dim }}>—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
          </Card>

          <Card title="Receiving" right={<span style={{ color: K.dim }}>rates: last 5 s · loss, DSCP: last 10 s</span>}>
            {!flows.length
              ? <div style={{ color: K.dim }}>{state.running ? 'Nothing arriving.' : '—'}</div>
              : <table className="w-full text-left">
                  <thead><tr className="text-[10px] uppercase tracking-wide" style={{ color: K.dim }}>
                    <th className="font-normal">From</th><th className="font-normal">To</th><th className="font-normal">Stream</th>
                    <th className="font-normal">pps</th><th className="font-normal">Loss</th><th className="font-normal">Jitter</th>
                    <th className="font-normal">Arrives as</th>
                  </tr></thead>
                  <tbody>
                    {flows.map(f => {
                      const stale = f.last_seen_s > 5;
                      return (
                        <tr key={`${f.src}|${f.dst}|${f.port}|${f.stream}`} style={{ opacity: stale ? 0.45 : 1 }}
                          title={stale ? `Nothing for ${f.last_seen_s} s` : `${f.packets} packets, ${f.lost} lost, ${f.reordered} reordered`}>
                          <td className="py-0.5 font-mono">{f.src}</td>
                          <td className="font-mono">{f.dst}:{f.port}{f.multicast && <span className="ml-1 text-[10px]" style={{ color: '#4ec9b0' }}>mcast</span>}</td>
                          <td>{f.stream || <span style={{ color: K.dim }}>other</span>}</td>
                          <td className="tabular-nums">{stale ? 0 : f.pps}</td>
                          <td className="tabular-nums" style={{ color: (f.loss_pct ?? 0) > 1 ? K.bad : undefined }}>
                            {f.loss_pct == null ? '—' : `${f.loss_pct}%`}</td>
                          <td className="tabular-nums">{f.stream ? `${f.jitter_ms} ms` : '—'}</td>
                          <td><Chips counts={f.dscp} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>}
          </Card>
        </div>
      </div>
    </div>
  );
}

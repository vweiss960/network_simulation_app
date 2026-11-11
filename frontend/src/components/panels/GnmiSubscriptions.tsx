import { useEffect, useState } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import toast from 'react-hot-toast';
import { ChevronDown, ChevronRight, Radio, Trash2, Plus, RefreshCw, Play, X } from 'lucide-react';

type Sub = { id: number; name: string; path: string; mode: string; sample_interval: string; enabled: boolean };
type TestResult = { ok: boolean; target: string; address: string; path: string; output: string; error: string };

const EMPTY = { name: '', path: '', mode: 'sample', sample_interval: '30s' };

/** Pretty-print gnmic output if it's a single JSON document; otherwise return
 *  it verbatim (gnmic may emit multiple concatenated notifications). */
function prettyJson(s: string): string {
  const t = (s || '').trim();
  if (!t) return '';
  try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return t; }
}

/** Lab telemetry add-on: manage custom gNMI subscriptions that get injected into
 *  the gnmic collector config (→ Prometheus). Self-contained card for LabPanel. */
export default function GnmiSubscriptions() {
  const currentTopologyId = useStore(s => s.currentTopologyId);
  const telemetryOn = useStore(s => (s.features as any)?.lab_telemetry_enabled);
  const [show, setShow] = useState(false);
  const [subs, setSubs] = useState<Sub[]>([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [testing, setTesting] = useState<string | null>(null);   // path currently being tested
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const load = async () => {
    if (!currentTopologyId) return;
    try { const r = await api.getLabSubscriptions(currentTopologyId); setSubs(r.subscriptions || []); }
    catch { /* add-on off or transient */ }
  };
  useEffect(() => { if (telemetryOn) load(); }, [currentTopologyId, telemetryOn]);

  if (!telemetryOn) return null;

  const add = async () => {
    if (!currentTopologyId) return;
    if (!form.name.trim() || !form.path.trim()) { toast.error('Name and path are required'); return; }
    setBusy(true);
    try {
      await api.createLabSubscription(currentTopologyId, form);
      setForm({ ...EMPTY });
      await load();
      toast.success('Subscription added — click Apply to push to a running collector');
    } catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Add failed'); }
    setBusy(false);
  };
  const del = async (id: number) => {
    if (!currentTopologyId) return;
    setBusy(true);
    try { await api.deleteLabSubscription(currentTopologyId, id); await load(); }
    catch (e: any) { toast.error(e?.message || 'Delete failed'); }
    setBusy(false);
  };
  const test = async (path: string) => {
    if (!currentTopologyId) return;
    const p = (path || '').trim();
    if (!p) { toast.error('Enter a gNMI path to test'); return; }
    setTesting(p);
    setTestResult(null);
    try {
      const r = await api.testLabSubscription(currentTopologyId, p);
      setTestResult(r);
      if (!r.ok) toast.error(`${r.target}: query failed`);
    } catch (e: any) {
      toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Test failed');
    }
    setTesting(null);
  };
  const apply = async () => {
    if (!currentTopologyId) return;
    setBusy(true);
    try {
      const r = await api.applyLabSubscriptions(currentTopologyId);
      toast.success(`Applied ${r.applied_subscriptions} subscription(s) — collector restarted`);
    } catch (e: any) { toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Apply failed'); }
    setBusy(false);
  };

  return (
    <div className="border border-surface-700 rounded">
      <button className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium" onClick={() => setShow(!show)}>
        <span className="flex items-center gap-1.5"><Radio size={12} /> gNMI Subscriptions ({subs.length})</span>
        {show ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {show && (
        <div className="px-3 pb-3 space-y-2">
          <div className="text-[10px] text-surface-400">
            Extra gNMI paths the lab collector streams into Prometheus. Add/remove here, then <b>Apply</b> to
            recreate a running collector.
          </div>
          {subs.map(s => (
            <div key={s.id} className="flex items-center gap-2 text-[11px] bg-surface-800 rounded px-2 py-1">
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">
                  {s.name} <span className="text-surface-500">· {s.mode}{s.mode === 'sample' ? ` ${s.sample_interval}` : ''}</span>
                </div>
                <div className="text-surface-500 truncate" title={s.path}>{s.path}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => test(s.path)} disabled={testing !== null} title="Test — query this path on a running node">
                <Play size={12} />
              </button>
              <button className="btn btn-ghost btn-sm text-red-300" onClick={() => del(s.id)} disabled={busy} title="Delete">
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {subs.length === 0 && <div className="text-[10px] text-surface-500">No custom subscriptions.</div>}
          <div className="space-y-1 border-t border-surface-700 pt-2">
            <input className="w-full text-xs" placeholder="name (e.g. bgp-neighbors)"
                   value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
            <input className="w-full text-xs" placeholder="gNMI path (e.g. openconfig-…:/…)"
                   value={form.path} onChange={e => setForm({ ...form, path: e.target.value })} />
            <div className="flex gap-1">
              <select className="text-xs flex-1" value={form.mode} onChange={e => setForm({ ...form, mode: e.target.value })}>
                <option value="sample">sample</option>
                <option value="on-change">on-change</option>
              </select>
              <input className="text-xs w-16" placeholder="30s" value={form.sample_interval}
                     onChange={e => setForm({ ...form, sample_interval: e.target.value })}
                     disabled={form.mode !== 'sample'} title="sample interval" />
              <button className="btn btn-ghost btn-sm text-[11px]" onClick={() => test(form.path)}
                      disabled={testing !== null || !form.path.trim()} title="Test — query this path on a running node">
                <Play size={12} /> Test
              </button>
              <button className="btn btn-primary btn-sm text-[11px]" onClick={add} disabled={busy}><Plus size={12} /> Add</button>
            </div>
          </div>

          {(testing || testResult) && (
            <div className="border border-surface-700 rounded bg-surface-900 text-[10px]">
              <div className="flex items-center justify-between px-2 py-1 border-b border-surface-700">
                <span className="text-surface-400 truncate">
                  {testing
                    ? <>Querying <span className="text-surface-300">{testing}</span>…</>
                    : <>Result · <span className="text-surface-300">{testResult!.target}</span> <span className="text-surface-600">{testResult!.address}</span></>}
                </span>
                {testResult && <button className="text-surface-500 hover:text-surface-300" onClick={() => setTestResult(null)}><X size={11} /></button>}
              </div>
              {testResult && (
                testResult.error
                  ? <pre className="text-red-300 whitespace-pre-wrap px-2 py-1.5 max-h-52 overflow-auto">{testResult.error}</pre>
                  : <pre className="text-surface-300 px-2 py-1.5 max-h-52 overflow-auto">{prettyJson(testResult.output) || '(connected, but the device returned no data for this path)'}</pre>
              )}
            </div>
          )}
          <button className="btn btn-secondary btn-sm w-full text-[11px]" onClick={apply} disabled={busy}>
            <RefreshCw size={12} /> Apply to running collector
          </button>
        </div>
      )}
    </div>
  );
}

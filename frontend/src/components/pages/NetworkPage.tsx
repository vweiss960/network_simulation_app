import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { useAuthStore } from '../../stores/useAuthStore';
import { api } from '../../utils/api';
import type { LabNetworkInfo, MgmtSettings } from '../../types';

const field = 'w-full px-2 py-1.5 text-sm rounded bg-surface-900 border border-surface-700 focus:border-primary-500 outline-none disabled:opacity-60';
const label = 'block text-[11px] uppercase tracking-wider text-surface-500 mb-1 mt-3';

/** Host NICs and the lab management network (internal vs bridged onto a real network). */
export default function NetworkPage() {
  const isAdmin = !!useAuthStore(s => s.user?.is_admin);
  const [info, setInfo] = useState<LabNetworkInfo | null>(null);
  const [s, setS] = useState<MgmtSettings | null>(null);
  const [saving, setSaving] = useState(false);

  const load = () => api.getLabNetwork().then(r => { setInfo(r); setS(r.settings); });
  useEffect(() => { load(); }, []);
  if (!info || !s) return <div className="p-6 text-surface-400">Loading…</div>;

  const set = (p: Partial<MgmtSettings>) => setS({ ...s, ...p });
  const save = async () => {
    setSaving(true);
    try { await api.putLabNetwork(s); toast.success('Management network saved'); await load(); }
    catch (e) { toast.error((e as Error).message, { duration: 8000 }); }
    finally { setSaving(false); }
  };
  const bridged = s.mode === 'bridged';
  const dis = !isAdmin;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto p-6 space-y-8">
        <div>
          <h1 className="text-2xl font-semibold">Network</h1>
          <p className="text-sm text-surface-400">
            Every device's management interface sits on the management network. Bridge it onto a real network and each
            device is reachable at its own IP from anywhere that network routes. Data-plane connections to physical
            networks are made per lab with <b>Network</b> nodes on the canvas.
          </p>
        </div>

        <section className="rounded-lg border border-surface-700 p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Management network</h2>
            <span className={`flex items-center gap-1.5 text-xs ${info.status.ok ? 'text-emerald-300' : 'text-amber-300'}`}>
              {info.status.ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />} {info.status.detail}
            </span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {(['internal', 'bridged'] as const).map(m => (
              <label key={m} className={`p-3 rounded border cursor-pointer ${s.mode === m ? 'border-primary-500 bg-primary-900/20' : 'border-surface-700'}`}>
                <input type="radio" disabled={dis} checked={s.mode === m} onChange={() => set({ mode: m, ...(m === 'internal' ? { subnet: '172.30.0.0/22' } : {}) })} />{' '}
                <span className="font-medium">{m === 'internal' ? 'Internal' : 'Bridged to a NIC'}</span>
                <div className="text-xs text-surface-400 mt-1">
                  {m === 'internal' ? 'Private Docker network on this server. Reach devices from the server (console, SSH from the host).'
                    : 'Devices get static IPs from a pool on a real network — reachable directly from your laptop.'}
                </div>
              </label>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-x-4">
            {bridged && (
              <>
                <div><label className={label}>NIC</label>
                  <select disabled={dis} className={field} value={s.nic} onChange={e => set({ nic: e.target.value })}>
                    <option value="">— choose —</option>
                    {info.nics.map(n => <option key={n.name} value={n.name}>{n.name} · {n.carrier ? 'link up' : 'no link'}{n.protected ? ' · server uplink' : ''}</option>)}
                  </select></div>
                <div><label className={label}>VLAN (0 = untagged)</label>
                  <input disabled={dis} type="number" min={0} max={4094} className={field} value={s.vlan} onChange={e => set({ vlan: Number(e.target.value) })} /></div>
                <div />
              </>
            )}
            <div><label className={label}>Subnet</label>
              <input disabled={dis} className={field} value={s.subnet} onChange={e => set({ subnet: e.target.value })} placeholder="10.50.0.0/24" /></div>
            {bridged && (
              <>
                <div><label className={label}>Gateway (upstream router)</label>
                  <input disabled={dis} className={field} value={s.gateway} onChange={e => set({ gateway: e.target.value })} placeholder="10.50.0.1" /></div>
                <div><label className={label}>This server's address (optional)</label>
                  <input disabled={dis} className={field} value={s.host_ip} onChange={e => set({ host_ip: e.target.value })} placeholder="10.50.0.10" /></div>
                <div><label className={label}>Device pool start</label>
                  <input disabled={dis} className={field} value={s.pool_start} onChange={e => set({ pool_start: e.target.value })} placeholder="10.50.0.100" /></div>
                <div><label className={label}>Device pool end</label>
                  <input disabled={dis} className={field} value={s.pool_end} onChange={e => set({ pool_end: e.target.value })} placeholder="10.50.0.199" /></div>
                <div><label className={label}>MTU (0 = default)</label>
                  <input disabled={dis} type="number" className={field} value={s.mtu} onChange={e => set({ mtu: Number(e.target.value) })} /></div>
              </>
            )}
          </div>
          {bridged && (
            <p className="text-xs text-surface-400 mt-3">
              Use a pool range no DHCP server hands out. The switch port must allow multiple MAC addresses
              {s.vlan ? ` and carry VLAN ${s.vlan} tagged` : ''}. Give this server an address on the segment if you want to
              reach devices from it over the bridge.
            </p>
          )}
          {isAdmin ? (
            <div className="mt-4 flex items-center gap-3">
              <button onClick={save} disabled={saving} className="px-4 py-1.5 text-sm rounded bg-primary-600 hover:bg-primary-500 text-white disabled:opacity-50">
                {saving ? 'Saving…' : 'Save'}
              </button>
              <span className="text-xs text-surface-500">Changing this requires all labs to be destroyed first.</span>
            </div>
          ) : <p className="text-xs text-surface-500 mt-3">Only administrators can change the management network.</p>}
        </section>

        <section>
          <h2 className="font-semibold mb-2">Host NICs</h2>
          <div className="rounded border border-surface-700 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-surface-800 text-[11px] uppercase tracking-wider text-surface-400">
                <tr><th className="text-left p-2">NIC</th><th className="text-left p-2">Link</th><th className="text-left p-2">Addresses</th>
                  <th className="text-left p-2">Attached to</th><th className="text-left p-2">Used by labs</th></tr>
              </thead>
              <tbody className="divide-y divide-surface-700">
                {info.nics.map(n => {
                  const used = info.usage.filter(u => u.nic === n.name);
                  return (
                    <tr key={n.name}>
                      <td className="p-2 font-mono">{n.name}{n.protected && <span className="ml-2 text-[10px] text-amber-300">server uplink</span>}
                        {!n.protected && n.host_managed && !n.master && <span className="ml-2 text-[10px] text-sky-300">used by host</span>}</td>
                      <td className="p-2">{n.carrier ? <span className="text-emerald-300">up{n.speed_mbps ? ` · ${n.speed_mbps / 1000}G` : ''}</span> : <span className="text-surface-500">no link</span>}</td>
                      <td className="p-2 font-mono text-xs">{n.addresses.join(', ') || '—'}</td>
                      <td className="p-2 font-mono text-xs">{n.master || '—'}</td>
                      <td className="p-2 text-xs">{used.map(u => `${u.topology}/${u.node}${u.vlan ? ` (VLAN ${u.vlan})` : ''}`).join(', ') || '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

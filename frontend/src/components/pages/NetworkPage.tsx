import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
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

  const save = async () => {
    setSaving(true);
    try { await api.putLabNetwork(s); toast.success('Saved'); await load(); }
    catch (e) { toast.error((e as Error).message, { duration: 8000 }); }
    finally { setSaving(false); }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto p-6 space-y-8">
        <div>
          <h1 className="text-2xl font-semibold">Network</h1>
          <p className="text-sm text-surface-400">
            Labs reach real networks through <b>Network</b> nodes on the canvas: pick a NIC (untagged) or a VLAN on a trunk
            NIC, then cable device ports to it. Mark one Network node in a lab as its <b>management network</b> and every
            device's management interface joins it with its own address from that network's DHCP server. Labs without one
            use the internal management network below, reachable from this server only.
          </p>
        </div>

        <section className="rounded-lg border border-surface-700 p-4">
          <h2 className="font-semibold">Bridged management networks in use</h2>
          {info.status.bridged.length === 0
            ? <p className="text-sm text-surface-400 mt-2">None — no deployed lab has a management Network node.</p>
            : (
              <table className="w-full text-sm mt-2">
                <thead className="text-[11px] uppercase tracking-wider text-surface-400">
                  <tr><th className="text-left py-1">Bridge</th><th className="text-left">Subnet (DHCP)</th><th className="text-left">Gateway</th>
                    <th className="text-left">DNS</th><th className="text-left">This server</th><th className="text-left">Device leases</th></tr>
                </thead>
                <tbody>
                  {info.status.bridged.map(b => (
                    <tr key={b.bridge} className="border-t border-surface-700">
                      <td className="py-1 font-mono">{b.bridge}</td>
                      <td className="font-mono">{b.learned?.subnet ?? '—'}</td>
                      <td className="font-mono">{b.learned?.gateway ?? '—'}</td>
                      <td className="font-mono text-xs">{b.learned?.dns.join(', ') || '—'}</td>
                      <td className="font-mono">{b.learned?.server_ip ?? '—'}</td>
                      <td>{b.leases}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </section>

        <section className="rounded-lg border border-surface-700 p-4">
          <h2 className="font-semibold">Internal management network</h2>
          <p className="text-xs text-surface-400 mt-1">Used by labs without a management Network node. Docker assigns addresses.</p>
          <div className="grid grid-cols-3 gap-x-4">
            <div><label className={label}>Subnet</label>
              <input disabled={!isAdmin} className={field} value={s.subnet} onChange={e => setS({ ...s, subnet: e.target.value })} /></div>
            <div><label className={label}>Bridge MTU (0 = default)</label>
              <input disabled={!isAdmin} type="number" className={field} value={s.mtu} onChange={e => setS({ ...s, mtu: Number(e.target.value) })} /></div>
            <div className="flex items-end">
              {isAdmin && <button onClick={save} disabled={saving} className="px-4 py-1.5 text-sm rounded bg-primary-600 hover:bg-primary-500 text-white disabled:opacity-50">
                {saving ? 'Saving…' : 'Save'}</button>}
            </div>
          </div>
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
                      <td className="p-2 text-xs">{used.map(u => `${u.topology}/${u.node}${u.vlan ? ` (VLAN ${u.vlan})` : ''}${u.mgmt ? ' · mgmt' : ''}`).join(', ') || '—'}</td>
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

import { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import type { Device, HostNic, NetworkSpec } from '../../types';

const field = 'w-full px-2 py-1.5 text-sm rounded bg-surface-900 border border-surface-700 focus:border-primary-500 outline-none';
const label = 'block text-[11px] uppercase tracking-wider text-surface-500 mb-1 mt-3';

const MODES: { value: NetworkSpec['mode']; title: string; help: string }[] = [
  { value: 'internal', title: 'Lab switch', help: 'An L2 segment between devices in this lab only.' },
  { value: 'access', title: 'Physical port (untagged)', help: 'Bridges devices onto everything plugged into that NIC.' },
  { value: 'trunk', title: 'Trunk port + VLAN', help: 'Bridges devices onto one VLAN carried by a trunk NIC.' },
];

/** Inspector for Network nodes: which host NIC / VLAN the segment rides on. */
export default function NetworkInspector({ node }: { node: Device }) {
  const { lab, links, nodes, updateNode, deleteNode, select } = useLabStore();
  const [nics, setNics] = useState<HostNic[]>([]);
  const [spec, setSpec] = useState<NetworkSpec>(node.network ?? { mode: 'internal', nic: '', vlan: 0 });
  const rt = lab?.nodes?.[String(node.id)];
  const deployed = !!lab && lab.status !== 'idle';

  useEffect(() => { api.getLabNetwork().then(r => setNics(r.nics)).catch(() => {}); }, []);

  const dirty = JSON.stringify(spec) !== JSON.stringify(node.network);
  const nic = nics.find(n => n.name === spec.nic);
  const attached = links.flatMap(l => {
    const name = (id: number) => nodes.find(n => n.id === id)?.name ?? '?';
    if (l.source_node_id === node.id) return [{ id: l.id, dev: name(l.target_node_id), iface: l.target_interface }];
    if (l.target_node_id === node.id) return [{ id: l.id, dev: name(l.source_node_id), iface: l.source_interface }];
    return [];
  });

  return (
    <div className="h-full overflow-y-auto text-sm">
      <div className="p-3 border-b border-surface-700">
        <div className="text-base font-semibold text-surface-100">{node.name}</div>
        <div className="text-xs text-surface-400">{node.network_label}</div>
        {deployed && rt && (
          <div className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
            <span className="text-surface-500">Bridge</span><span className="font-mono">{rt.bridge ?? '—'}</span>
            <span className="text-surface-500">State</span>
            <span className={rt.ready_state === 'up' ? 'text-emerald-300' : rt.ready_state === 'down' ? 'text-red-300' : ''}>
              {rt.ready_state === 'down' ? 'no link on NIC' : rt.ready_state}
            </span>
          </div>
        )}
      </div>

      <div className="p-3 border-b border-surface-700">
        <div className="text-xs font-semibold text-surface-300">Connects to</div>
        <div className="mt-2 space-y-1.5">
          {MODES.map(m => (
            <label key={m.value} className={`flex gap-2 p-2 rounded border cursor-pointer ${spec.mode === m.value ? 'border-primary-500 bg-primary-900/20' : 'border-surface-700 hover:border-surface-500'}`}>
              <input type="radio" checked={spec.mode === m.value} onChange={() => setSpec({ ...spec, mode: m.value })} className="mt-0.5" />
              <span><span className="text-surface-100">{m.title}</span><br /><span className="text-[11px] text-surface-400">{m.help}</span></span>
            </label>
          ))}
        </div>

        {spec.mode !== 'internal' && (
          <>
            <label className={label}>Host NIC</label>
            <select className={field} value={spec.nic} onChange={e => setSpec({ ...spec, nic: e.target.value })}>
              <option value="">— choose —</option>
              {nics.map(n => (
                <option key={n.name} value={n.name} disabled={spec.mode === 'access' && n.protected}>
                  {n.name} · {n.carrier ? `link up${n.speed_mbps ? ` ${n.speed_mbps / 1000}G` : ''}` : 'no link'}
                  {n.protected ? ' · server uplink' : ''}
                </option>
              ))}
            </select>
            {nic && !nic.carrier && <div className="text-[11px] text-amber-400 mt-1">{nic.name} has no link — check the cable / switch port.</div>}
            {nic && spec.mode === 'access' && nic.host_managed && !nic.master.startsWith('pl-') && (
              <div className="text-[11px] text-amber-400 mt-1">
                This server is using {nic.name}{nic.addresses.length ? ` (${nic.addresses.join(', ')})` : ''}. Applying hands the port to
                the lab: the host stops managing it and that address is removed.
              </div>
            )}
            {spec.mode === 'access' && <div className="text-[11px] text-surface-500 mt-1">The server's own uplink can only be used in trunk mode.</div>}
            {spec.mode === 'trunk' && (
              <>
                <label className={label}>VLAN ID</label>
                <input type="number" min={1} max={4094} className={field} value={spec.vlan || ''}
                  onChange={e => setSpec({ ...spec, vlan: Number(e.target.value) })} />
                <div className="text-[11px] text-surface-500 mt-1">The switch port must be a trunk that allows this VLAN.</div>
              </>
            )}
          </>
        )}
        {dirty && (
          <div className="mt-3 flex items-center justify-between gap-2">
            <span className="text-[11px] text-surface-500">{deployed ? 'Applies live: cables move to the new segment.' : ''}</span>
            <button onClick={() => updateNode(node.id, { network: spec })}
              className="px-3 py-1 text-xs rounded bg-primary-600 hover:bg-primary-500 text-white">Apply</button>
          </div>
        )}
      </div>

      <div className="p-3 border-b border-surface-700">
        <div className="text-xs font-semibold text-surface-300 mb-2">Attached devices ({attached.length})</div>
        {attached.length === 0 && <div className="text-xs text-surface-500">Drag from a device's blue dot onto this network.</div>}
        {attached.map(a => (
          <button key={a.id} onClick={() => select({ type: 'link', id: a.id })}
            className="block w-full text-left text-xs py-0.5 text-primary-300 hover:underline">{a.dev} {a.iface}</button>
        ))}
      </div>

      <div className="p-3">
        <button onClick={() => { if (!deployed || confirm(`Delete ${node.name}? Devices cabled to it lose that connection.`)) deleteNode(node.id); }}
          className="flex items-center gap-1.5 text-xs text-red-300 hover:text-red-200">
          <Trash2 size={13} /> Delete network
        </button>
      </div>
    </div>
  );
}

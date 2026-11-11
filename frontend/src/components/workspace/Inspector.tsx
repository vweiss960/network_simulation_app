import { useEffect, useState } from 'react';
import { Play, RotateCw, Square, Terminal, Trash2, FileText, Zap } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import type { Device, Link } from '../../types';
import LinkNetemModal from '../LinkNetemModal';
import LabSummary from './LabSummary';

const field = 'w-full px-2 py-1.5 text-sm rounded bg-surface-900 border border-surface-700 focus:border-primary-500 outline-none';
const label = 'block text-[11px] uppercase tracking-wider text-surface-500 mb-1';

export default function Inspector() {
  const { selection, nodes, links } = useLabStore();
  if (selection?.type === 'node') {
    const n = nodes.find(x => x.id === selection.id);
    if (n) return <NodeInspector key={n.id} node={n} />;
  }
  if (selection?.type === 'link') {
    const l = links.find(x => x.id === selection.id);
    if (l) return <LinkInspector key={l.id} link={l} />;
  }
  return <LabSummary />;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-3 border-b border-surface-700">
      <div className="text-xs font-semibold text-surface-300 mb-2">{title}</div>
      {children}
    </div>
  );
}

function NodeInspector({ node }: { node: Device }) {
  const { kinds, lab, links, nodes, updateNode, deleteNode, openConsole, nodeAction, select } = useLabStore();
  const k = kinds[node.kind];
  const rt = lab?.nodes?.[String(node.id)];
  const deployed = !!lab && lab.status !== 'idle';
  const running = rt?.state === 'running';
  const [name, setName] = useState(node.name);
  const [image, setImage] = useState(node.image_ref);
  const [ifCount, setIfCount] = useState(node.interfaces.length);
  const [username, setUsername] = useState(node.username);
  const [password, setPassword] = useState('');
  const [config, setConfig] = useState(node.startup_config);
  const [images, setImages] = useState<string[]>([]);

  useEffect(() => { api.listDockerImages().then(r => setImages(r.images)).catch(() => {}); }, []);

  const cabled = new Map<string, { peer: string; peerIf: string; linkId: number }>();
  for (const l of links) {
    const peerName = (id: number) => nodes.find(x => x.id === id)?.name ?? '?';
    if (l.source_node_id === node.id) cabled.set(l.source_interface, { peer: peerName(l.target_node_id), peerIf: l.target_interface, linkId: l.id });
    if (l.target_node_id === node.id) cabled.set(l.target_interface, { peer: peerName(l.source_node_id), peerIf: l.source_interface, linkId: l.id });
  }
  const recreateWarn = deployed ? ' Applying recreates this device in the running lab.' : '';
  const mgmt = rt?.mgmt_ip || node.mgmt_ipv4;

  return (
    <div className="h-full overflow-y-auto text-sm">
      <div className="p-3 border-b border-surface-700">
        <div className="text-base font-semibold text-surface-100">{node.name}</div>
        <div className="text-xs text-surface-400">{k?.label ?? node.kind} · {k?.is_vm ? 'VM' : 'container'}</div>
        {deployed && (
          <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <span className="text-surface-500">State</span><span className="text-surface-200">{rt?.ready_state ?? 'absent'}</span>
            <span className="text-surface-500">Mgmt IP</span><span className="font-mono text-surface-200">{mgmt || '—'}</span>
            {k?.default_user && <><span className="text-surface-500">User</span>
              <span className="font-mono text-surface-200">{node.username || k.default_user}</span></>}
            {mgmt && k?.ssh && <><span className="text-surface-500">SSH</span>
              <span className="font-mono text-surface-200 select-all">ssh {node.username || k.default_user}@{mgmt}</span></>}
          </div>
        )}
        <div className="mt-3 flex flex-wrap gap-1.5">
          <Btn icon={<Terminal size={13} />} onClick={() => openConsole(node.id)} disabled={!running}>Console</Btn>
          <Btn icon={<FileText size={13} />} onClick={() => openConsole(node.id, 'logs')} disabled={!rt || rt.state === 'absent'}>Logs</Btn>
          {running
            ? <Btn icon={<Square size={13} />} onClick={() => nodeAction(node.id, 'stop')}>Stop</Btn>
            : <Btn icon={<Play size={13} />} onClick={() => nodeAction(node.id, 'start')} disabled={!deployed}>Start</Btn>}
          <Btn icon={<RotateCw size={13} />} onClick={() => nodeAction(node.id, 'restart')} disabled={!rt || rt.state === 'absent'}>Restart</Btn>
        </div>
      </div>

      <Section title="Device">
        <label className={label}>Name</label>
        <div className="flex gap-1 mb-2">
          <input className={field} value={name} onChange={e => setName(e.target.value)} />
          {name !== node.name && <Apply onClick={() => updateNode(node.id, { name })} />}
        </div>
        <label className={label}>Image</label>
        <div className="flex gap-1 mb-1">
          <input className={`${field} font-mono text-xs`} value={image} list="plabs-docker-images" onChange={e => setImage(e.target.value)} placeholder="registry/image:tag" />
          {image !== node.image_ref && <Apply onClick={() => updateNode(node.id, { image_ref: image })} />}
        </div>
        <datalist id="plabs-docker-images">{images.map(i => <option key={i} value={i} />)}</datalist>
        {image && images.length > 0 && !images.includes(image) && !images.includes(`${image}:latest`) &&
          <div className="text-[11px] text-amber-400 mb-1">This image isn't in the local Docker daemon yet.</div>}
        <label className={`${label} mt-2`}>Data interfaces</label>
        <div className="flex gap-1">
          <input type="number" min={0} max={k?.max_interfaces ?? 32} className={field} value={ifCount}
            onChange={e => setIfCount(Number(e.target.value))} />
          {ifCount !== node.interfaces.length && <Apply onClick={() => updateNode(node.id, { interface_count: ifCount })} />}
        </div>
        {deployed && image !== node.image_ref && <div className="text-[11px] text-surface-500 mt-1">{recreateWarn.trim()}</div>}
      </Section>

      {k?.default_user && (
        <Section title="Credentials">
          <div className="grid grid-cols-2 gap-1">
            <input className={field} value={username} placeholder={k.default_user} onChange={e => setUsername(e.target.value)} />
            <input className={field} type="password" value={password} placeholder="(default)" onChange={e => setPassword(e.target.value)} />
          </div>
          {(username !== node.username || password) && (
            <div className="mt-1 flex justify-end">
              <Apply onClick={async () => { if (await updateNode(node.id, { username, password })) setPassword(''); }} />
            </div>
          )}
          <div className="text-[11px] text-surface-500 mt-1">Used by the console and (later) the lab agent. Blank = kind default.</div>
        </Section>
      )}

      <Section title={`Interfaces (${node.interfaces.length})`}>
        <table className="w-full text-xs">
          <tbody>
            {node.interfaces.map(i => {
              const c = cabled.get(i.name);
              return (
                <tr key={i.name} className="border-b border-surface-800 last:border-0">
                  <td className="py-1 font-mono text-surface-200">{i.name}</td>
                  <td className="py-1 text-right">
                    {c ? <button className="text-primary-300 hover:underline" onClick={() => select({ type: 'link', id: c.linkId })}>
                      {c.peer} {c.peerIf}</button> : <span className="text-surface-600">free</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Section>

      <Section title="Startup config">
        <textarea className={`${field} font-mono text-xs h-48`} spellCheck={false} value={config}
          onChange={e => setConfig(e.target.value)}
          placeholder={node.kind === 'cisco_xrd'
            ? '! Merged after the containerlab defaults (user clab/clab@123, SSH, NETCONF, gRPC)\ninterface Loopback0\n ipv4 address 10.0.0.1 255.255.255.255'
            : 'Day-0 configuration applied when the device boots'} />
        {config !== node.startup_config && (
          <div className="mt-1 flex items-center justify-between">
            <span className="text-[11px] text-surface-500">{recreateWarn}</span>
            <Apply onClick={() => updateNode(node.id, { startup_config: config })} />
          </div>
        )}
      </Section>

      <div className="p-3">
        <button onClick={() => { if (!deployed || confirm(`Delete ${node.name}? Its container will be removed from the running lab.`)) deleteNode(node.id); }}
          className="flex items-center gap-1.5 text-xs text-red-300 hover:text-red-200">
          <Trash2 size={13} /> Delete device
        </button>
      </div>
    </div>
  );
}

function LinkInspector({ link }: { link: Link }) {
  const { nodes, lab, updateLink, deleteLink, currentTopologyId } = useLabStore();
  const a = nodes.find(n => n.id === link.source_node_id);
  const b = nodes.find(n => n.id === link.target_node_id);
  const [aFree, setAFree] = useState<{ name: string; used: boolean }[]>([]);
  const [bFree, setBFree] = useState<{ name: string; used: boolean }[]>([]);
  const [netem, setNetem] = useState(false);
  const deployed = !!lab && lab.status !== 'idle';

  useEffect(() => {
    if (!currentTopologyId || !a || !b) return;
    api.freeInterfaces(currentTopologyId, a.id).then(r => setAFree(r.interfaces));
    api.freeInterfaces(currentTopologyId, b.id).then(r => setBFree(r.interfaces));
  }, [currentTopologyId, a?.id, b?.id, link.source_interface, link.target_interface]);

  if (!a || !b) return null;
  const pick = (opts: { name: string; used: boolean }[], current: string, onPick: (v: string) => void) => (
    <select className={`${field} font-mono text-xs`} value={current} onChange={e => onPick(e.target.value)}>
      {opts.map(o => <option key={o.name} value={o.name} disabled={o.used && o.name !== current}>{o.name}{o.used && o.name !== current ? ' (used)' : ''}</option>)}
    </select>
  );
  return (
    <div className="h-full overflow-y-auto text-sm">
      <div className="p-3 border-b border-surface-700">
        <div className="text-base font-semibold text-surface-100">{a.name} ↔ {b.name}</div>
        <div className="text-xs text-surface-400">Point-to-point cable (veth pair)</div>
      </div>
      <Section title="Endpoints">
        <label className={label}>{a.name}</label>
        {pick(aFree, link.source_interface, v => updateLink(link.id, { source_interface: v }))}
        <label className={`${label} mt-2`}>{b.name}</label>
        {pick(bFree, link.target_interface, v => updateLink(link.id, { target_interface: v }))}
        {deployed && <div className="text-[11px] text-surface-500 mt-2">Re-pinning a running cable re-creates it on the new interfaces.</div>}
      </Section>
      <Section title="Impairments">
        <Btn icon={<Zap size={13} />} onClick={() => setNetem(true)} disabled={!deployed}>Delay / loss / rate…</Btn>
        {!deployed && <div className="text-[11px] text-surface-500 mt-1">Available once the lab is deployed.</div>}
      </Section>
      <div className="p-3">
        <button onClick={() => deleteLink(link.id)} className="flex items-center gap-1.5 text-xs text-red-300 hover:text-red-200">
          <Trash2 size={13} /> Delete link
        </button>
      </div>
      {netem && currentTopologyId && (
        <LinkNetemModal topologyId={currentTopologyId} linkId={link.id}
          label={`${a.name} ${link.source_interface} ↔ ${b.name} ${link.target_interface}`} onClose={() => setNetem(false)} />
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

function Apply({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className="px-2 py-1 text-xs rounded bg-primary-600 hover:bg-primary-500 text-white shrink-0">Apply</button>
  );
}

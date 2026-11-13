import { useEffect, useState } from 'react';
import { Play, RotateCw, Square, Terminal, Trash2, FileText, Zap } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import type { Device, Link } from '../../types';
import LinkNetemModal from '../LinkNetemModal';
import NetworkInspector from './NetworkInspector';
import DeviceTools from './DeviceTools';

const field = 'w-full px-2 py-1.5 text-sm rounded bg-surface-900 border border-surface-700 focus:border-primary-500 outline-none';
const label = 'block text-[11px] uppercase tracking-wider text-surface-500 mb-1';

export default function Inspector() {
  const { selection, nodes, links } = useLabStore();
  if (selection?.type === 'node') {
    const n = nodes.find(x => x.id === selection.id);
    if (n) return n.kind === 'bridge' ? <NetworkInspector key={n.id} node={n} /> : <NodeInspector key={n.id} node={n} />;
  }
  if (selection?.type === 'link') {
    const l = links.find(x => x.id === selection.id);
    if (l) return <LinkInspector key={l.id} link={l} />;
  }
  return null;
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
  const { kinds, lab, links, nodes, updateNode, deleteNode, openConsole, nodeAction, select, currentTopologyId,
          discardPending } = useLabStore();
  const k = kinds[node.kind];
  const rt = lab?.nodes?.[String(node.id)];
  const deployed = !!lab && lab.status !== 'idle';
  const running = rt?.state === 'running';
  const [name, setName] = useState(node.name);
  // Values shown are what the device will have after its next restart.
  const pending = node.pending ?? {};
  const hasPending = Object.keys(pending).length > 0;
  const nextImage = pending.image_ref ?? node.image_ref;
  const nextIfCount = pending.interface_count ?? node.interfaces.length;
  const nextConfig = pending.startup_config ?? node.startup_config;
  const [image, setImage] = useState(nextImage);
  const [ifCount, setIfCount] = useState(nextIfCount);
  const [username, setUsername] = useState(node.username);
  const [password, setPassword] = useState('');
  const [config, setConfig] = useState(nextConfig);
  const [images, setImages] = useState<string[]>([]);

  useEffect(() => { api.listDockerImages().then(r => setImages(r.images)).catch(() => {}); }, []);

  const cabled = new Map<string, { peer: string; peerIf: string; linkId: number }>();
  for (const l of links) {
    const peerName = (id: number) => nodes.find(x => x.id === id)?.name ?? '?';
    if (l.source_node_id === node.id) cabled.set(l.source_interface, { peer: peerName(l.target_node_id), peerIf: l.target_interface, linkId: l.id });
    if (l.target_node_id === node.id) cabled.set(l.target_interface, { peer: peerName(l.source_node_id), peerIf: l.source_interface, linkId: l.id });
  }
  const restartNote = (extra = '') => deployed
    ? <div className="text-[11px] text-surface-500 mt-1">
        <b className="text-surface-400">Apply now</b> restarts {node.name} with it{extra}.{' '}
        <b className="text-surface-400">Next restart</b> saves it until the device is restarted (its Restart button, or a lab Deploy).
      </div>
    : null;
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
            <span className="text-surface-500">Mgmt network</span>
            <span className="text-surface-200">{lab?.mgmt?.bridged ? (nodes.find(x => x.id === lab.mgmt.node_id)?.name ?? 'bridged') + ' (DHCP)' : 'internal'}</span>
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
            : <Btn icon={<Play size={13} />} onClick={() => nodeAction(node.id, 'start')}
                title={deployed ? 'Start this device' : 'Deploy only this device; start the others when you need them'}>
                {deployed ? 'Start' : 'Start just this'}</Btn>}
          <Btn icon={<RotateCw size={13} />} onClick={() => nodeAction(node.id, 'restart')} disabled={!rt || rt.state === 'absent'}>Restart</Btn>
        </div>
      </div>

      {hasPending && (
        <div className="m-3 p-2 rounded border border-amber-700/60 bg-amber-900/20 text-xs">
          <div className="font-semibold text-amber-300 mb-1">Saved for the next restart</div>
          <ul className="text-amber-100/90 space-y-0.5 mb-2">
            {pending.image_ref !== undefined && <li>Image → <span className="font-mono">{pending.image_ref}</span></li>}
            {pending.interface_count !== undefined && <li>Data interfaces → {pending.interface_count}</li>}
            {pending.startup_config !== undefined && <li>New startup config (replaces the live config)</li>}
          </ul>
          <div className="flex gap-1.5">
            <Btn icon={<RotateCw size={13} />} onClick={() => nodeAction(node.id, 'restart')} disabled={!deployed}>Restart now</Btn>
            <Btn icon={<Trash2 size={13} />} onClick={async () => {
              await discardPending(node.id);
              setImage(node.image_ref); setIfCount(node.interfaces.length); setConfig(node.startup_config);
            }}>Discard</Btn>
          </div>
        </div>
      )}

      {deployed && currentTopologyId && (
        <DeviceTools topologyId={currentTopologyId} node={node} running={running} family={k?.family ?? 'linux'} />
      )}

      <Section title="Device">
        <label className={label}>Name</label>
        <div className="flex gap-1 mb-2">
          <input className={field} value={name} onChange={e => setName(e.target.value)} />
          {name !== node.name && <Apply onClick={() => updateNode(node.id, { name })} />}
        </div>
        <label className={label}>Image</label>
        <div className="flex gap-1 mb-1">
          <input className={`${field} font-mono text-xs`} value={image} list="plabs-docker-images" onChange={e => setImage(e.target.value)} placeholder="registry/image:tag" />
          {image !== nextImage && <ApplyChoice deployed={deployed} onApply={defer => updateNode(node.id, { image_ref: image, defer })} />}
        </div>
        <datalist id="plabs-docker-images">{images.map(i => <option key={i} value={i} />)}</datalist>
        {image && images.length > 0 && !images.includes(image) && !images.includes(`${image}:latest`) &&
          <div className="text-[11px] text-amber-400 mb-1">This image isn't in the local Docker daemon yet.</div>}
        <label className={`${label} mt-2`}>Data interfaces</label>
        <div className="flex gap-1">
          <input type="number" min={0} max={k?.max_interfaces ?? 32} className={field} value={ifCount}
            onChange={e => setIfCount(Number(e.target.value))} />
          {ifCount !== nextIfCount && <ApplyChoice deployed={deployed} onApply={defer => updateNode(node.id, { interface_count: ifCount, defer })} />}
        </div>
        {(image !== nextImage || ifCount !== nextIfCount) && restartNote(
          image !== nextImage ? '; its live config is kept' : '; cables stay connected and its live config is kept')}
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
        {config !== nextConfig && (
          <>
            <div className="mt-1 flex justify-end">
              <ApplyChoice deployed={deployed} onApply={defer => updateNode(node.id, { startup_config: config, defer })} />
            </div>
            {restartNote(', and its live config is replaced by this one (snapshot first to keep it)')}
          </>
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
        {deployed && <div className="text-[11px] text-surface-500 mt-2">Changing the ports of a running cable re-patches it live — no device restarts.</div>}
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

function Btn({ icon, children, onClick, disabled, title }: { icon: React.ReactNode; children: React.ReactNode; onClick: () => void; disabled?: boolean; title?: string }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-surface-700 hover:bg-surface-600 text-surface-100 disabled:opacity-40 disabled:cursor-not-allowed">
      {icon}{children}
    </button>
  );
}

/** Apply a change that restarts the device: now, or saved for its next restart. */
function ApplyChoice({ deployed, onApply }: { deployed: boolean; onApply: (defer: boolean) => void }) {
  if (!deployed) return <Apply onClick={() => onApply(false)} />;
  return (
    <div className="flex gap-1 shrink-0">
      <button onClick={() => onApply(false)} title="Restart the device now with this change"
        className="px-2 py-1 text-xs rounded bg-primary-600 hover:bg-primary-500 text-white">Apply now</button>
      <button onClick={() => onApply(true)} title="Keep the device running; apply when it is next restarted"
        className="px-2 py-1 text-xs rounded bg-surface-700 hover:bg-surface-600 text-surface-100">Next restart</button>
    </div>
  );
}

function Apply({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className="px-2 py-1 text-xs rounded bg-primary-600 hover:bg-primary-500 text-white shrink-0">Apply</button>
  );
}

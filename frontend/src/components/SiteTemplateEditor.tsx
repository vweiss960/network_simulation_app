/**
 * SiteTemplateEditor — build a reusable site: 1-4 devices (from device
 * templates), the links between their ports, and the site's patch panels
 * with port documentation and jumpers already in place. Text fields may use
 * {{variable}} placeholders, filled in when the site is added to a topology
 * (canvas right-click → Add Site Template).
 *
 * The preview canvas is only for layout: drag devices to set where they land
 * relative to the drop point. All validation is server-side
 * (services/site_templates.py); its message is shown in the error banner.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactFlow, {
  Background, BackgroundVariant, Controls, ReactFlowProvider, applyNodeChanges, useReactFlow,
  type Edge, type Node, type NodeChange,
} from 'reactflow';
import 'reactflow/dist/style.css';
import RouterNode from './RouterNode';
import CustomerNode from './CustomerNode';
import CustomEdge from './CustomEdge';
import ContextMenu from './ContextMenu';
import toast from 'react-hot-toast';
import { Lock, Plus, Trash2, X } from 'lucide-react';
import { api } from '../utils/api';
import { useStore } from '../stores/useStore';
import { apiErrorMessage, usedVariables } from '../utils/siteTemplates';
import type {
  PatchPortStatus, SiteTemplate, SiteTemplateDefinition, SiteTemplateLink, SiteTemplateNode,
  SiteTemplatePanel, SiteTemplatePort,
} from '../types';

const MAX_NODES = 4;

const STATUS_LABEL: Record<PatchPortStatus, string> = {
  unused: 'Unused', cabled_free: 'Cabled / free', cabled_reserved: 'Cabled / reserved', cabled_used: 'Cabled / used',
};

export const EMPTY_DEFINITION: SiteTemplateDefinition = {
  variables: [
    { name: 'site', label: 'Site code', default: '', required: true },
    { name: 'location', label: 'Site location', default: '', required: true },
  ],
  nodes: [], links: [], patch_panels: [],
};

const nextKey = (prefix: string, used: string[]) => {
  let i = 1;
  while (used.includes(`${prefix}${i}`)) i++;
  return `${prefix}${i}`;
};

const inputCls = 'w-full text-xs';
const Field = ({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) => (
  <label className={`text-[10px] text-surface-400 block ${className}`}>{label}<div className="mt-0.5">{children}</div></label>
);
const Section = ({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) => (
  <div className="mb-4">
    <div className="flex items-center justify-between mb-1.5">
      <div className="text-xs font-semibold text-surface-200 uppercase tracking-wide">{title}</div>
      {action}
    </div>
    {children}
  </div>
);

interface Props {
  /** Template to edit, or a starting point for a new one (id 0). */
  template: SiteTemplate;
  onClose: () => void;
}

export default function SiteTemplateEditor({ template, onClose }: Props) {
  const nodeTemplates = useStore(s => s.nodeTemplates);
  const loadSiteTemplates = useStore(s => s.loadSiteTemplates);
  const panelsEnabled = !!useStore(s => s.features)?.patch_panels_enabled;
  const [name, setName] = useState(template.name);
  const [description, setDescription] = useState(template.description);
  const [def, setDef] = useState<SiteTemplateDefinition>(template.definition);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Card briefly highlighted (and scrolled to) after a preview action.
  const [focus, setFocus] = useState<string | null>(null);
  const focusCard = (kind: 'node' | 'link', key: string) => setFocus(`${kind}:${key}`);
  useEffect(() => {
    if (!focus) return;
    const el = document.getElementById(`st-${focus.replace(':', '-')}`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const t = setTimeout(() => setFocus(null), 1600);
    return () => clearTimeout(t);
  }, [focus]);
  const ring = (kind: string, key: string) => focus === `${kind}:${key}` ? 'ring-2 ring-primary-500' : '';

  const deviceTemplates = nodeTemplates;
  const ifacesOf = (nodeKey: string | null) => {
    const n = def.nodes.find(x => x.key === nodeKey);
    return deviceTemplates.find(t => t.id === n?.template_id)?.default_interfaces ?? [];
  };
  const reservedOf = (nodeKey: string) => new Set(def.nodes.find(n => n.key === nodeKey)?.reserved_interfaces ?? []);
  // Interfaces on `nodeKey` cabled by a template link (optionally ignoring one link).
  const linkedIfaces = (nodeKey: string, exceptLink?: string) => new Set(def.links.filter(l => l.key !== exceptLink).flatMap(l =>
    [l.source === nodeKey ? l.source_interface : '', l.target === nodeKey ? l.target_interface : '']).filter(Boolean));
  const nodeLabel = (key: string | null) => def.nodes.find(n => n.key === key)?.name || key || '';

  const declared = new Set(def.variables.map(v => v.name));
  const missingVars = [...usedVariables(def)].filter(v => !declared.has(v));

  // ---- mutations ---------------------------------------------------------
  const patch = (fn: (d: SiteTemplateDefinition) => SiteTemplateDefinition) => { setDef(fn); setError(null); };
  const setNode = (key: string, upd: Partial<SiteTemplateNode>) =>
    patch(d => ({ ...d, nodes: d.nodes.map(n => n.key === key ? { ...n, ...upd } : n) }));
  // Switching a device's template keeps each port reference (links, panel
  // ports, reservations) the new template still has at the same speed, and
  // clears the rest so the definition doesn't point at ports that are gone.
  const changeTemplate = (key: string, templateId: number | null) => {
    const oldSpeed = new Map(ifacesOf(key).map(i => [i.name, i.speed_gbps]));
    const newSpeed = new Map((deviceTemplates.find(t => t.id === templateId)?.default_interfaces ?? []).map(i => [i.name, i.speed_gbps]));
    const keep = (iface: string) => !!iface && newSpeed.has(iface) && newSpeed.get(iface) === oldSpeed.get(iface);
    let cleared = 0;
    const fix = (iface: string) => {
      if (!iface || keep(iface)) return iface;
      cleared++;
      return '';
    };
    const links = def.links.map(l => ({
      ...l,
      source_interface: l.source === key ? fix(l.source_interface) : l.source_interface,
      target_interface: l.target === key ? fix(l.target_interface) : l.target_interface,
    }));
    const next: SiteTemplateDefinition = {
      ...def,
      nodes: def.nodes.map(n => n.key !== key ? n
        : { ...n, template_id: templateId, reserved_interfaces: (n.reserved_interfaces ?? []).filter(keep) }),
      links,
      patch_panels: def.patch_panels.map(p => ({
        ...p, ports: Object.fromEntries(Object.entries(p.ports).map(([k, v]) => {
          if (v.node !== key) return [k, v];
          const l = v.link ? links.find(x => x.key === v.link) : undefined;
          // A port on a link follows the link's (possibly cleared) interface.
          return [k, { ...v, interface: l ? (l.source === key ? l.source_interface : l.target_interface) : fix(v.interface) }];
        })),
      })),
    };
    patch(() => next);
    if (cleared) toast(`${cleared} port${cleared > 1 ? 's' : ''} not on the new device were cleared — pick them again`, { icon: '⚠️' });
  };
  const setLink = (key: string, upd: Partial<SiteTemplateLink>) =>
    patch(d => ({ ...d, links: d.links.map(l => l.key === key ? { ...l, ...upd } : l) }));
  const setPanel = (key: string, upd: Partial<SiteTemplatePanel>) =>
    patch(d => ({ ...d, patch_panels: d.patch_panels.map(p => p.key === key ? { ...p, ...upd } : p) }));
  const setPort = (panelKey: string, port: string, upd: Partial<SiteTemplatePort>) =>
    patch(d => ({
      ...d, patch_panels: d.patch_panels.map(p => p.key !== panelKey ? p
        : { ...p, ports: { ...p.ports, [port]: { ...p.ports[port], ...upd } } }),
    }));

  // Add a device (from the form, or the preview's right-click menu at a
  // canvas point). Names default to {{site}}-r1, -r2… (-cust1… for customer
  // endpoints), skipping ones already used.
  const addNode = (templateId?: number, pos?: { x: number; y: number }) => {
    if (def.nodes.length >= MAX_NODES) return;
    const t = deviceTemplates.find(x => x.id === templateId)
      ?? deviceTemplates.find(x => (x.role || 'router') !== 'customer') ?? deviceTemplates[0];
    const key = nextKey('n', def.nodes.map(n => n.key));
    const i = def.nodes.length;
    const stem = (t?.role || 'router') === 'customer' ? '{{site}}-cust' : '{{site}}-r';
    let k = 1;
    while (def.nodes.some(n => n.name === `${stem}${k}`)) k++;
    patch(d => ({
      ...d, nodes: [...d.nodes, {
        key, template_id: t?.id ?? null, name: `${stem}${k}`, location: '{{location}}', mgmt_ip: '', is_planned: false, reserved_interfaces: [],
        dx: pos ? Math.round(pos.x) : (i % 2) * 180, dy: pos ? Math.round(pos.y) : Math.floor(i / 2) * 140,
      }],
    }));
    focusCard('node', key);
  };
  const toggleReserved = (key: string, iface: string) => patch(d => ({
    ...d, nodes: d.nodes.map(n => {
      if (n.key !== key) return n;
      const cur = n.reserved_interfaces ?? [];
      return { ...n, reserved_interfaces: cur.includes(iface) ? cur.filter(i => i !== iface) : [...cur, iface] };
    }),
  }));
  // Removing a device drops its links and clears it from panel ports.
  const removeNode = (key: string) => patch(d => {
    const goneLinks = new Set(d.links.filter(l => l.source === key || l.target === key).map(l => l.key));
    return {
      ...d,
      nodes: d.nodes.filter(n => n.key !== key),
      links: d.links.filter(l => !goneLinks.has(l.key)),
      patch_panels: d.patch_panels.map(p => ({
        ...p, ports: Object.fromEntries(Object.entries(p.ports).map(([k, v]) => [k,
          v.node === key ? { ...v, node: null, interface: '', link: null }
            : v.link && goneLinks.has(v.link) ? { ...v, link: null } : v])),
      })),
    };
  });
  // Add a link (from the form, or drawn between two devices in the
  // preview). Ports default to the first free pair with matching speeds; the
  // link's card is highlighted so they can be changed.
  const addLink = (a = def.nodes[0]?.key ?? '', b = def.nodes[1]?.key ?? '') => {
    const used = (nk: string) => new Set([...reservedOf(nk), ...linkedIfaces(nk)]);
    const ua = used(a), ub = used(b);
    let si = '', ti = '';
    for (const x of ifacesOf(a).filter(i => !ua.has(i.name))) {
      const y = ifacesOf(b).find(i => !ub.has(i.name) && i.speed_gbps === x.speed_gbps);
      if (y) { si = x.name; ti = y.name; break; }
    }
    const key = nextKey('l', def.links.map(l => l.key));
    patch(d => ({
      ...d, links: [...d.links, {
        key, source: a, source_interface: si, target: b, target_interface: ti, latency_ms: 1, carrier: '',
        circuit_id: '', link_id_label: '', is_planned: false,
      }],
    }));
    if (a && b && !si) toast('No free ports with matching speeds — pick the interfaces on the left', { icon: '⚠️' });
    focusCard('link', key);
  };
  const removeLink = (key: string) => patch(d => ({
    ...d, links: d.links.filter(l => l.key !== key),
    patch_panels: d.patch_panels.map(p => ({
      ...p, ports: Object.fromEntries(Object.entries(p.ports).map(([k, v]) => [k, v.link === key ? { ...v, link: null } : v])),
    })),
  }));
  const addPanel = () => patch(d => ({
    ...d, patch_panels: [...d.patch_panels, {
      key: nextKey('p', d.patch_panels.map(p => p.key)), name: `PP-{{site}}-${d.patch_panels.length + 1}`,
      location: '{{location}}', rack: '', start_u: null, height_u: 1, rows: 1, ports_per_row: 24,
      notes: '', in_front_of: null, ports: {},
    }],
  }));
  const removePanel = (key: string) => patch(d => ({
    ...d, patch_panels: d.patch_panels.filter(p => p.key !== key).map(p => p.in_front_of !== key ? p : {
      ...p, in_front_of: null,
      ports: Object.fromEntries(Object.entries(p.ports).map(([k, v]) => [k, { ...v, rear_port: null }])),
    }),
  }));
  const addPort = (p: SiteTemplatePanel) => {
    const count = p.rows * p.ports_per_row;
    let n = 1;
    while (p.ports[String(n)] && n <= count) n++;
    if (n > count) { toast.error(`${p.name || 'Panel'} has no free ports`); return; }
    setPort(p.key, String(n), { status: 'cabled_free', label: '', node: null, interface: '', link: null, notes: '', rear_port: null });
  };
  const removePort = (panelKey: string, port: string) => patch(d => ({
    ...d, patch_panels: d.patch_panels.map(p => {
      if (p.key !== panelKey) return p;
      const { [port]: _, ...rest } = p.ports;
      return { ...p, ports: rest };
    }),
  }));
  const renumberPort = (p: SiteTemplatePanel, from: string, to: number) => {
    if (!to || String(to) === from || p.ports[String(to)]) return;
    patch(d => ({
      ...d, patch_panels: d.patch_panels.map(x => {
        if (x.key !== p.key) return x;
        const { [from]: v, ...rest } = x.ports;
        return { ...x, ports: { ...rest, [String(to)]: v } };
      }),
    }));
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const body = { name, description, definition: def };
      if (template.id) await api.updateSiteTemplate(template.id, body);
      else await api.createSiteTemplate(body);
      await loadSiteTemplates();
      toast.success(`Saved site template "${name}"`);
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  // ---- render ------------------------------------------------------------
  const wanFacing = new Set(def.patch_panels.map(p => p.in_front_of).filter(Boolean) as string[]);

  return createPortal(
    <div data-canvas-keys-off className="fixed inset-0 z-[90] bg-black/60 flex items-center justify-center p-4">
      <div className="bg-surface-900 border border-surface-600 rounded-lg shadow-2xl w-[1500px] max-w-full h-[94vh] flex flex-col fade-in">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-700">
          <div className="text-sm font-medium shrink-0">{template.id ? 'Edit site template' : 'New site template'}</div>
          <input className="text-sm flex-1 min-w-0" placeholder="Template name (e.g. Small PoP)" value={name}
            onChange={e => { setName(e.target.value); setError(null); }} autoFocus={!template.id} />
          <button className="text-surface-400 hover:text-surface-200" onClick={onClose} title="Close"><X size={16} /></button>
        </div>

        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          <div className="md:w-[600px] md:shrink-0 min-w-0 flex-1 md:flex-none overflow-y-auto p-4">
            <Field label="Description" className="mb-4">
              <input className={inputCls} value={description} onChange={e => setDescription(e.target.value)}
                placeholder="What this site is for" />
            </Field>

            <Section title="Variables" action={
              <button className="btn btn-ghost btn-sm text-xs" onClick={() => patch(d => ({
                ...d, variables: [...d.variables, { name: nextKey('var', d.variables.map(v => v.name)), label: '', default: '', required: true }],
              }))}><Plus size={12} /> Variable</button>
            }>
              <div className="text-[10px] text-surface-500 mb-1.5">
                Use <code className="text-primary-300">{'{{name}}'}</code> in any text field below. Each variable is asked for when the site is added.
              </div>
              {def.variables.map((v, i) => (
                <div key={i} className="grid grid-cols-[1fr_1.4fr_1fr_auto_auto] gap-1.5 items-center mb-1">
                  <input className={`${inputCls} font-mono`} placeholder="name" value={v.name}
                    onChange={e => patch(d => ({ ...d, variables: d.variables.map((x, j) => j === i ? { ...x, name: e.target.value.replace(/[^A-Za-z0-9_]/g, '') } : x) }))} />
                  <input className={inputCls} placeholder="Prompt (e.g. Site code)" value={v.label}
                    onChange={e => patch(d => ({ ...d, variables: d.variables.map((x, j) => j === i ? { ...x, label: e.target.value } : x) }))} />
                  <input className={inputCls} placeholder="Default" value={v.default}
                    onChange={e => patch(d => ({ ...d, variables: d.variables.map((x, j) => j === i ? { ...x, default: e.target.value } : x) }))} />
                  <label className="text-[10px] text-surface-400 flex items-center gap-1" title="Must be filled in when adding the site">
                    <input type="checkbox" checked={v.required}
                      onChange={e => patch(d => ({ ...d, variables: d.variables.map((x, j) => j === i ? { ...x, required: e.target.checked } : x) }))} />
                    Required
                  </label>
                  <button className="text-surface-500 hover:text-red-400" title="Remove variable"
                    onClick={() => patch(d => ({ ...d, variables: d.variables.filter((_, j) => j !== i) }))}><Trash2 size={12} /></button>
                </div>
              ))}
              {missingVars.length > 0 && (
                <div className="text-[11px] text-amber-400 mt-1 flex items-center gap-2">
                  Used but not defined: {missingVars.map(v => `{{${v}}}`).join(', ')}
                  <button className="btn btn-secondary btn-sm text-[10px] py-0.5" onClick={() => patch(d => ({
                    ...d, variables: [...d.variables, ...missingVars.map(name => ({ name, label: '', default: '', required: true }))],
                  }))}>Add</button>
                </div>
              )}
            </Section>

            <Section title={`Devices (${def.nodes.length}/${MAX_NODES})`} action={
              <button className="btn btn-ghost btn-sm text-xs" onClick={() => addNode()} disabled={def.nodes.length >= MAX_NODES || !deviceTemplates.length}>
                <Plus size={12} /> Device
              </button>
            }>
              {!deviceTemplates.length && <div className="text-[11px] text-amber-400">No device templates exist yet — create one first.</div>}
              {def.nodes.map(n => (
                <div key={n.key} id={`st-node-${n.key}`} className={`bg-surface-800 border border-surface-700 rounded p-2 mb-1.5 transition-shadow ${ring('node', n.key)}`}>
                  <div className="grid grid-cols-2 gap-1.5">
                    <Field label="Device template">
                      <select className={inputCls} value={n.template_id ?? ''} onChange={e => changeTemplate(n.key, Number(e.target.value) || null)}>
                        <option value="">— pick —</option>
                        {deviceTemplates.map(t => <option key={t.id} value={t.id}>{t.name} ({t.vendor} {t.model})</option>)}
                      </select>
                    </Field>
                    <Field label="Name">
                      <input className={`${inputCls} font-mono`} value={n.name} onChange={e => setNode(n.key, { name: e.target.value })} />
                    </Field>
                    <Field label="Location">
                      <input className={`${inputCls} font-mono`} value={n.location} onChange={e => setNode(n.key, { location: e.target.value })} />
                    </Field>
                    <Field label="Mgmt IP">
                      <input className={`${inputCls} font-mono`} value={n.mgmt_ip} placeholder="optional" onChange={e => setNode(n.key, { mgmt_ip: e.target.value })} />
                    </Field>
                  </div>
                  {(() => {
                    const ifaces = ifacesOf(n.key);
                    const reserved = reservedOf(n.key);
                    // Reserved names the device template no longer has: shown so they can be cleared.
                    const stale = [...reserved].filter(r => !ifaces.some(i => i.name === r));
                    if (ifaces.length === 0 && stale.length === 0) return null;
                    const linked = linkedIfaces(n.key);
                    return (
                      <div className="mt-1.5">
                        <div className="text-[10px] text-surface-400 mb-0.5">
                          Reserved ports <span className="text-surface-500">— click to reserve; reserved ports can't be used for links or flows</span>
                        </div>
                        <div className="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
                          {stale.map(r => (
                            <button key={r} type="button" onClick={() => toggleReserved(n.key, r)}
                              title="Not on this device template — click to remove"
                              className="text-[10px] font-mono px-1.5 py-0.5 rounded border flex items-center gap-1 border-red-500/60 bg-red-500/15 text-red-300 line-through">
                              <Lock size={9} />{r}
                            </button>
                          ))}
                          {ifaces.map(i => {
                            const isRes = reserved.has(i.name);
                            const isLinked = linked.has(i.name);
                            return (
                              <button key={i.name} type="button" disabled={isLinked && !isRes}
                                onClick={() => toggleReserved(n.key, i.name)}
                                title={isLinked ? 'Used by a link in this template' : isRes ? 'Reserved — click to free' : 'Free — click to reserve'}
                                className={`text-[10px] font-mono px-1.5 py-0.5 rounded border flex items-center gap-1 ${
                                  isRes ? 'border-amber-500/60 bg-amber-500/15 text-amber-300'
                                    : isLinked ? 'border-emerald-600/40 text-emerald-400/70 cursor-not-allowed'
                                      : 'border-surface-600 text-surface-300 hover:border-surface-400'}`}>
                                {isRes && <Lock size={9} />}{i.name}<span className="text-surface-500">{i.speed_gbps}G</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })()}
                  <div className="flex items-center justify-between mt-1.5">
                    <label className="text-[10px] text-surface-400 flex items-center gap-1">
                      <input type="checkbox" checked={n.is_planned} onChange={e => setNode(n.key, { is_planned: e.target.checked })} /> Planned
                    </label>
                    <button className="text-[10px] text-surface-500 hover:text-red-400 flex items-center gap-1" onClick={() => removeNode(n.key)}>
                      <Trash2 size={11} /> Remove device
                    </button>
                  </div>
                </div>
              ))}
            </Section>

            <Section title="Links" action={
              <button className="btn btn-ghost btn-sm text-xs" onClick={() => addLink()} disabled={def.nodes.length < 2}><Plus size={12} /> Link</button>
            }>
              {def.nodes.length < 2 && <div className="text-[10px] text-surface-500">Add two devices to link them.</div>}
              {def.links.map(l => {
                const end = (side: 'source' | 'target') => {
                  const nk = l[side];
                  const used = linkedIfaces(nk, l.key);
                  const reserved = reservedOf(nk);
                  const ifKey = side === 'source' ? 'source_interface' : 'target_interface';
                  return (
                    <div className="grid grid-cols-2 gap-1">
                      <select className={inputCls} value={nk} onChange={e => setLink(l.key, { [side]: e.target.value, [ifKey]: '' } as any)}>
                        <option value="">— device —</option>
                        {def.nodes.map(n => <option key={n.key} value={n.key}>{n.name || n.key}</option>)}
                      </select>
                      <select className={`${inputCls} font-mono`} value={l[ifKey]} onChange={e => setLink(l.key, { [ifKey]: e.target.value } as any)}>
                        <option value="">— port —</option>
                        {ifacesOf(nk).map(i => (
                          <option key={i.name} value={i.name} disabled={used.has(i.name) || reserved.has(i.name)}>
                            {i.name} ({i.speed_gbps}G){used.has(i.name) ? ' – used' : reserved.has(i.name) ? ' – reserved' : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                  );
                };
                return (
                  <div key={l.key} id={`st-link-${l.key}`} className={`bg-surface-800 border border-surface-700 rounded p-2 mb-1.5 transition-shadow ${ring('link', l.key)}`}>
                    <div className="grid grid-cols-[1fr_auto_1fr] gap-1.5 items-center">
                      {end('source')}<span className="text-surface-500 text-xs">↔</span>{end('target')}
                    </div>
                    <div className="grid grid-cols-4 gap-1.5 mt-1.5">
                      <Field label="Circuit ID"><input className={`${inputCls} font-mono`} value={l.circuit_id} onChange={e => setLink(l.key, { circuit_id: e.target.value })} /></Field>
                      <Field label="Link ID"><input className={`${inputCls} font-mono`} value={l.link_id_label} onChange={e => setLink(l.key, { link_id_label: e.target.value })} /></Field>
                      <Field label="Carrier"><input className={inputCls} value={l.carrier} onChange={e => setLink(l.key, { carrier: e.target.value })} /></Field>
                      <Field label="Latency (ms)"><input type="number" min={0} step={0.1} className={inputCls} value={l.latency_ms} onChange={e => setLink(l.key, { latency_ms: parseFloat(e.target.value) || 0 })} /></Field>
                    </div>
                    <div className="flex items-center justify-between mt-1.5">
                      <label className="text-[10px] text-surface-400 flex items-center gap-1">
                        <input type="checkbox" checked={l.is_planned} onChange={e => setLink(l.key, { is_planned: e.target.checked })} /> Planned
                      </label>
                      <button className="text-[10px] text-surface-500 hover:text-red-400 flex items-center gap-1" onClick={() => removeLink(l.key)}>
                        <Trash2 size={11} /> Remove link
                      </button>
                    </div>
                  </div>
                );
              })}
            </Section>

            {(panelsEnabled || def.patch_panels.length > 0) && (
              <Section title="Patch panels" action={
                <button className="btn btn-ghost btn-sm text-xs" onClick={addPanel}><Plus size={12} /> Panel</button>
              }>
                {!panelsEnabled && <div className="text-[11px] text-amber-400 mb-1">The patch panel module is off — these panels are skipped when the site is added.</div>}
                <div className="text-[10px] text-surface-500 mb-1.5">
                  Document which device port each panel port is cabled to. A panel placed <em>in front of</em> another is
                  customer-facing; its ports can be jumpered to the wan-facing panel behind it via “Rear port”.
                </div>
                {def.patch_panels.map(p => {
                  const isWan = wanFacing.has(p.key);
                  const behind = def.patch_panels.find(x => x.key === p.in_front_of);
                  const ports = Object.entries(p.ports).sort((a, b) => Number(a[0]) - Number(b[0]));
                  return (
                    <div key={p.key} className="bg-surface-800 border border-surface-700 rounded p-2 mb-2">
                      <div className="grid grid-cols-4 gap-1.5">
                        <Field label="Name" className="col-span-2"><input className={`${inputCls} font-mono`} value={p.name} onChange={e => setPanel(p.key, { name: e.target.value })} /></Field>
                        <Field label="Location" className="col-span-2"><input className={`${inputCls} font-mono`} value={p.location} onChange={e => setPanel(p.key, { location: e.target.value })} /></Field>
                        <Field label="Rack"><input className={`${inputCls} font-mono`} value={p.rack} onChange={e => setPanel(p.key, { rack: e.target.value })} /></Field>
                        <Field label="Start U"><input type="number" min={1} className={inputCls} value={p.start_u ?? ''} onChange={e => setPanel(p.key, { start_u: e.target.value ? Number(e.target.value) : null })} /></Field>
                        <Field label="Rows × ports">
                          <div className="flex gap-1 items-center">
                            <input type="number" min={1} className={inputCls} value={p.rows} onChange={e => setPanel(p.key, { rows: Math.max(1, Number(e.target.value) || 1) })} />
                            <span className="text-surface-500">×</span>
                            <input type="number" min={1} className={inputCls} value={p.ports_per_row} onChange={e => setPanel(p.key, { ports_per_row: Math.max(1, Number(e.target.value) || 1) })} />
                          </div>
                        </Field>
                        <Field label="In front of">
                          <select className={inputCls} value={p.in_front_of ?? ''}
                            onChange={e => setPanel(p.key, {
                              in_front_of: e.target.value || null,
                              // A different panel behind invalidates the rear jumpers.
                              ports: Object.fromEntries(Object.entries(p.ports).map(([k, v]) => [k, { ...v, rear_port: null }])),
                            })}>
                            <option value="">— none —</option>
                            {def.patch_panels.filter(x => x.key !== p.key).map(x => <option key={x.key} value={x.key}>{x.name || x.key}</option>)}
                          </select>
                        </Field>
                      </div>
                      {isWan && <div className="text-[10px] text-sky-400 mt-1.5">Wan-facing: carries the WAN link only — devices are documented on the panel in front.</div>}

                      <div className="mt-2">
                        {ports.length > 0 && (
                          <div className="grid grid-cols-[52px_1.1fr_1fr_1fr_1fr_60px_1fr_auto] gap-1 text-[9px] uppercase text-surface-500 mb-0.5">
                            <span>Port</span><span>Status</span><span>Device</span><span>Interface</span><span>Link</span><span title={behind ? `Port on ${behind.name}` : ''}>Rear</span><span>Label</span><span />
                          </div>
                        )}
                        {ports.map(([k, v]) => {
                          const devLinks = def.links.filter(l => v.node && (l.source === v.node || l.target === v.node));
                          return (
                            <div key={k} className="grid grid-cols-[52px_1.1fr_1fr_1fr_1fr_60px_1fr_auto] gap-1 items-center mb-0.5">
                              <input type="number" min={1} max={p.rows * p.ports_per_row} className={inputCls} defaultValue={k}
                                onBlur={e => renumberPort(p, k, Number(e.target.value))} title="Port number" />
                              <select className={inputCls} value={v.status} onChange={e => setPort(p.key, k, { status: e.target.value as PatchPortStatus })}>
                                {(Object.keys(STATUS_LABEL) as PatchPortStatus[]).map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                              </select>
                              <select className={inputCls} value={v.node ?? ''} disabled={isWan}
                                onChange={e => setPort(p.key, k, { node: e.target.value || null, interface: '', link: null })}>
                                <option value="">—</option>
                                {def.nodes.map(n => <option key={n.key} value={n.key}>{n.name || n.key}</option>)}
                              </select>
                              <select className={`${inputCls} font-mono`} value={v.interface} disabled={isWan || !v.node || !!v.link}
                                onChange={e => setPort(p.key, k, { interface: e.target.value })}>
                                <option value="">—</option>
                                {ifacesOf(v.node).map(i => <option key={i.name} value={i.name}>{i.name}</option>)}
                              </select>
                              <select className={inputCls} value={v.link ?? ''} disabled={isWan || !devLinks.length}
                                onChange={e => {
                                  const l = def.links.find(x => x.key === e.target.value);
                                  setPort(p.key, k, l
                                    ? { link: l.key, interface: l.source === v.node ? l.source_interface : l.target_interface }
                                    : { link: null });
                                }}>
                                <option value="">—</option>
                                {devLinks.map(l => {
                                  const far = l.source === v.node ? l.target : l.source;
                                  return <option key={l.key} value={l.key}>to {nodeLabel(far)}</option>;
                                })}
                              </select>
                              <input type="number" min={1} className={inputCls} disabled={!behind} value={v.rear_port ?? ''}
                                title={behind ? `Jumper to this port on ${behind.name}` : 'Place this panel in front of another to jumper ports'}
                                onChange={e => setPort(p.key, k, { rear_port: e.target.value ? Number(e.target.value) : null })} />
                              <input className={inputCls} value={v.label} placeholder="label" onChange={e => setPort(p.key, k, { label: e.target.value })} />
                              <button className="text-surface-500 hover:text-red-400" title="Clear port" onClick={() => removePort(p.key, k)}><X size={12} /></button>
                            </div>
                          );
                        })}
                        <div className="flex items-center justify-between mt-1">
                          <button className="btn btn-ghost btn-sm text-[11px]" onClick={() => addPort(p)}><Plus size={11} /> Port</button>
                          <button className="text-[10px] text-surface-500 hover:text-red-400 flex items-center gap-1" onClick={() => removePanel(p.key)}>
                            <Trash2 size={11} /> Remove panel
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </Section>
            )}
          </div>

          <div className="flex-1 min-w-0 h-80 md:h-auto border-t md:border-t-0 md:border-l border-surface-700 flex flex-col">
            <div className="px-3 py-2 text-[10px] text-surface-400 border-b border-surface-700">
              Right-click the canvas to add a device; right-click a device to link it to another. Drag to arrange — the site lands in the topology in exactly this arrangement, with the canvas point you right-click as the origin (+).
            </div>
            <div className="flex-1 min-h-0">
              <ReactFlowProvider>
                <LayoutPreview
                  def={def}
                  onMove={(key, dx, dy) => setNode(key, { dx, dy })}
                  onAddNode={(tid, pos) => addNode(tid, pos)}
                  onRemoveNode={removeNode}
                  onAddLink={(a, b) => addLink(a, b)}
                  onRemoveLink={removeLink}
                  onFocus={focusCard}
                />
              </ReactFlowProvider>
            </div>
            {def.patch_panels.length > 0 && (
              <div className="px-3 py-2 text-[10px] text-surface-400 border-t border-surface-700">
                + {def.patch_panels.length} patch panel{def.patch_panels.length > 1 ? 's' : ''}: {def.patch_panels.map(p => p.name).join(', ')}
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-surface-700 px-4 py-2.5 flex items-center gap-3">
          <div className="flex-1 min-w-0 text-xs text-red-400 truncate" title={error ?? ''}>{error}</div>
          <button className="btn btn-ghost btn-sm text-xs" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary btn-sm text-xs" onClick={save} disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : 'Save template'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// The real canvas node/edge renderers, so the preview looks like the topology.
const previewNodeTypes = { router: RouterNode, customer: CustomerNode };
const previewEdgeTypes = { custom: CustomEdge };

interface PreviewProps {
  def: SiteTemplateDefinition;
  onMove: (key: string, dx: number, dy: number) => void;
  onAddNode: (templateId: number, pos: { x: number; y: number }) => void;
  onRemoveNode: (key: string) => void;
  onAddLink: (source: string, target: string) => void;
  onRemoveLink: (key: string) => void;
  onFocus: (kind: 'node' | 'link', key: string) => void;
}
type Menu = { x: number; y: number; type: 'pane' | 'node' | 'edge'; id: string; flow: { x: number; y: number } };

/** Positions are the real canvas offsets (dx, dy) from the drop point: the
 *  main canvas places node i at (click.x + dx, click.y + dy) using the same
 *  node components, so this arrangement is what appears in the topology.
 *  Editing mirrors the main canvas: right-click the pane → Add Node ▸ type;
 *  right-click a device → Add Link from here, then click the target. */
function LayoutPreview({ def, onMove, onAddNode, onRemoveNode, onAddLink, onRemoveLink, onFocus }: PreviewProps) {
  const nodeTemplates = useStore(s => s.nodeTemplates);
  const rf = useReactFlow();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [linkFrom, setLinkFrom] = useState<string | null>(null);
  const [mouse, setMouse] = useState<{ x: number; y: number } | null>(null);
  const label = (key: string) => def.nodes.find(n => n.key === key)?.name || key;

  const build = (): Node[] => [
    // Drop-point marker (the right-clicked canvas point).
    {
      id: '__origin', position: { x: -6, y: -6 }, draggable: false, selectable: false,
      data: { label: '' }, type: 'default',
      style: { width: 12, height: 12, padding: 0, background: 'transparent', border: 'none', boxShadow: 'none' },
      className: 'site-origin',
    },
    ...def.nodes.map((n, i) => {
      const t = nodeTemplates.find(x => x.id === n.template_id);
      const customer = (t?.role || 'router') === 'customer';
      return {
        id: n.key, type: customer ? 'customer' : 'router', position: { x: n.dx, y: n.dy },
        draggable: !linkFrom,
        // Negative nodeId: never matches a real node's selection/highlight state.
        data: { label: n.name || n.key, vendor: t?.vendor ?? '', model: t?.model ?? '', nodeId: -1 - i, isPlanned: n.is_planned },
      };
    }),
  ];
  const [rfNodes, setRfNodes] = useState<Node[]>(build);
  // Rebuild when the devices change (positions included — they come from def).
  const sig = JSON.stringify(def.nodes.map(n => [n.key, n.name, n.template_id, n.dx, n.dy, n.is_planned]));
  useEffect(() => { setRfNodes(build()); }, [sig, nodeTemplates, linkFrom]); // eslint-disable-line react-hooks/exhaustive-deps
  const edges: Edge[] = useMemo(() => {
    const valid = def.links.filter(l => l.source && l.target);
    return valid.map(l => {
      // Parallel links between the same pair fan out like on the canvas.
      const pair = valid.filter(o => [o.source, o.target].sort().join() === [l.source, l.target].sort().join());
      const idx = pair.indexOf(l);
      const speed = ifaceSpeed(def, nodeTemplates, l.source, l.source_interface);
      return {
        id: l.key, source: l.source, target: l.target, type: 'custom',
        data: {
          width: 2, curveOffset: pair.length > 1 ? (idx - (pair.length - 1) / 2) * 50 : 0,
          isPlanned: l.is_planned, linkLabelFields: ['interface'],
          linkData: { source_interface: l.source_interface, target_interface: l.target_interface, bandwidth_gbps: speed },
        },
      };
    });
  }, [def, nodeTemplates]);

  // Link drawing: follow the cursor; Esc cancels.
  const cancelLink = () => { setLinkFrom(null); setMouse(null); };
  useEffect(() => {
    if (!linkFrom) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); cancelLink(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [linkFrom]);

  const openMenu = (e: React.MouseEvent, type: Menu['type'], id = '') => {
    e.preventDefault();
    setMenu({ x: e.clientX, y: e.clientY, type, id, flow: rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }) });
  };
  const clickNode = (key: string) => {
    if (!linkFrom) { onFocus('node', key); return; }
    if (key !== linkFrom) onAddLink(linkFrom, key);
    cancelLink();
  };

  const full = def.nodes.length >= MAX_NODES;
  const menuItems = (() => {
    if (!menu) return [];
    if (menu.type === 'pane') {
      const routers = nodeTemplates.filter(t => (t.role || 'router') !== 'customer');
      const customer = nodeTemplates.find(t => t.role === 'customer');
      // Same placement as the main canvas: the node's position is the click point.
      const at = { x: menu.flow.x, y: menu.flow.y };
      const items: { label: string; onClick: () => void; disabled?: boolean; children?: any[] }[] = [{
        label: full ? `Add Node (max ${MAX_NODES})` : 'Add Node', disabled: full, onClick: () => {},
        children: routers.length
          ? routers.map(t => ({ label: `${t.vendor} ${t.model}`, onClick: () => onAddNode(t.id, at) }))
          : [{ label: 'No device templates', onClick: () => {}, disabled: true }],
      }];
      if (customer) items.push({ label: 'Add Customer Endpoint', disabled: full, onClick: () => onAddNode(customer.id, at) });
      if (linkFrom) items.push({ label: 'Cancel link', onClick: cancelLink });
      return items;
    }
    if (menu.type === 'node') {
      return [
        linkFrom
          ? linkFrom === menu.id
            ? { label: 'Cancel link', onClick: cancelLink }
            : { label: `Link ${label(linkFrom)} → here`, onClick: () => clickNode(menu.id) }
          : { label: 'Add Link from here', onClick: () => { setLinkFrom(menu.id); toast('Click the target device to connect', { icon: '🔗' }); } },
        { label: `Edit "${label(menu.id)}"`, onClick: () => onFocus('node', menu.id) },
        { label: 'Delete device', danger: true, onClick: () => { if (linkFrom === menu.id) cancelLink(); onRemoveNode(menu.id); } },
      ];
    }
    return [
      { label: 'Edit link', onClick: () => onFocus('link', menu.id) },
      { label: 'Delete link', danger: true, onClick: () => onRemoveLink(menu.id) },
    ];
  })();

  // Rubber-band line from the source device to the cursor while linking.
  let rubber: { sx: number; sy: number; tx: number; ty: number } | null = null;
  if (linkFrom && mouse && wrapRef.current) {
    const n = rf.getNode(linkFrom);
    if (n) {
      const b = wrapRef.current.getBoundingClientRect();
      // Same anchor the canvas edges use: icon centre (handle at top: 22px).
      const p = rf.flowToScreenPosition({ x: n.position.x + (n.width ?? 44) / 2, y: n.position.y + 22 });
      rubber = { sx: p.x - b.left, sy: p.y - b.top, tx: mouse.x - b.left, ty: mouse.y - b.top };
    }
  }

  return (
    <div ref={wrapRef} className={`relative w-full h-full ${linkFrom ? 'cursor-crosshair' : ''}`}
      onMouseMove={e => linkFrom && setMouse({ x: e.clientX, y: e.clientY })}>
      <ReactFlow
        nodes={rfNodes} edges={edges} nodeTypes={previewNodeTypes} edgeTypes={previewEdgeTypes}
        fitView fitViewOptions={{ padding: 0.35, maxZoom: 1 }} minZoom={0.3} maxZoom={2}
        onNodesChange={(c: NodeChange[]) => setRfNodes(ns => applyNodeChanges(c, ns))}
        onNodeDragStop={(_, n) => n.id !== '__origin' && onMove(n.id, Math.round(n.position.x), Math.round(n.position.y))}
        onNodeClick={(_, n) => n.id !== '__origin' && clickNode(n.id)}
        onPaneClick={() => { if (linkFrom) cancelLink(); }}
        onPaneContextMenu={e => openMenu(e as React.MouseEvent, 'pane')}
        onNodeContextMenu={(e, n) => n.id === '__origin' ? openMenu(e, 'pane') : openMenu(e, 'node', n.id)}
        onEdgeContextMenu={(e, ed) => openMenu(e, 'edge', ed.id)}
        onEdgeClick={(_, ed) => onFocus('link', ed.id)}
        nodesConnectable={false} deleteKeyCode={null} proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#1e293b" />
        <Controls showInteractive={false} />
      </ReactFlow>
      {rubber && (
        <svg className="absolute inset-0 w-full h-full pointer-events-none z-10">
          <path d={`M ${rubber.sx} ${rubber.sy} L ${rubber.tx} ${rubber.ty}`} stroke="#3b82f6" strokeWidth={6} opacity={0.15} strokeLinecap="round" />
          <path d={`M ${rubber.sx} ${rubber.sy} L ${rubber.tx} ${rubber.ty}`} stroke="#3b82f6" strokeWidth={2} strokeDasharray="8 6" strokeLinecap="round" />
        </svg>
      )}
      {linkFrom && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-primary-600/90 text-white text-xs px-4 py-1.5 rounded-full shadow-lg flex items-center gap-2 z-20 whitespace-nowrap">
          <span className="inline-block w-2 h-2 rounded-full bg-white animate-pulse" />
          Linking from {label(linkFrom)} — click the target device or press Esc to cancel
        </div>
      )}
      {def.nodes.length === 0 && !menu && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none text-xs text-surface-500">
          Right-click to add a device
        </div>
      )}
      {menu && menuItems.length > 0 && createPortal(
        <ContextMenu key={`${menu.type}-${menu.id}-${menu.x}-${menu.y}`} x={menu.x} y={menu.y} items={menuItems} onClose={() => setMenu(null)} />,
        document.body,
      )}
    </div>
  );
}

function ifaceSpeed(def: SiteTemplateDefinition, nodeTemplates: { id: number; default_interfaces: { name: string; speed_gbps: number }[] }[],
  nodeKey: string, iface: string) {
  const n = def.nodes.find(x => x.key === nodeKey);
  return nodeTemplates.find(t => t.id === n?.template_id)?.default_interfaces.find(i => i.name === iface)?.speed_gbps;
}

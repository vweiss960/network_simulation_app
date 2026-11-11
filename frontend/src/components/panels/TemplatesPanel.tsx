import { useEffect, useState } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import toast from 'react-hot-toast';
import { Plus, Trash2, GripVertical, X, Container, Save } from 'lucide-react';
import { InterfaceSpec } from '../../types';

const CLAB_KIND_OPTIONS = [
  { value: '', label: '— Not deployable —' },
  { value: 'linux', label: 'linux (generic / FRR / Quagga)' },
  { value: 'cisco_xrd', label: 'cisco_xrd (XRd vRouter)' },
  { value: 'cisco_xrv9k', label: 'cisco_xrv9k' },
  { value: 'arista_ceos', label: 'arista_ceos' },
  { value: 'nokia_srlinux', label: 'nokia_srlinux' },
  { value: 'juniper_crpd', label: 'juniper_crpd' },
];

type KindDefaults = Record<string, {
  min_memory_mb: number;
  min_cpu_cores: number;
  requires_hugepages: boolean;
  description: string;
}>;

export default function TemplatesPanel() {
  const { nodeTemplates, createNodeTemplate, deleteNodeTemplate, loadNodeTemplates } = useStore();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    name: '', vendor: 'Cisco', model: '', platform: 'iosxr',
    capacity_gbps: '100', default_mtbf_hours: '50000', default_mttr_hours: '4',
  });
  const [interfaces, setInterfaces] = useState<InterfaceSpec[]>([]);
  // Lab config inline editing: which template's lab section is expanded, plus a draft form per template
  const [labExpandedId, setLabExpandedId] = useState<number | null>(null);
  const [labForm, setLabForm] = useState<Record<number, any>>({});
  const [labSaving, setLabSaving] = useState(false);
  const [kindDefaults, setKindDefaults] = useState<KindDefaults>({});
  // Built QCOW VM images (from vrnetlab) available as image_ref picks. Loaded
  // lazily — only the lab-config-editing flow uses them. Items with status !=
  // 'built' are excluded since their tag isn't real yet.
  const [builtVmImages, setBuiltVmImages] = useState<Array<{
    id: number; filename: string; clab_kind: string; built_image_tag: string; platform: string;
  }>>([]);
  const [pickerOpenId, setPickerOpenId] = useState<number | null>(null);

  useEffect(() => {
    api.getLabKindDefaults()
      .then(r => setKindDefaults(r.kinds || {}))
      .catch(() => { /* non-fatal */ });
    api.listQcowImages()
      .then(r => setBuiltVmImages(
        (r.images || []).filter(i => i.status === 'built' && i.built_image_tag)
          .map(i => ({ id: i.id, filename: i.filename, clab_kind: i.clab_kind, built_image_tag: i.built_image_tag, platform: i.platform })),
      ))
      .catch(() => { /* Lab Mode off, or no built images yet — leave empty */ });
  }, []);

  // When the user picks a clab_kind, auto-populate the resource fields with
  // the kind's recommended defaults — but only if the operator hasn't already
  // typed a non-zero value in any of them. Their edits stick.
  const onKindChange = (id: number, newKind: string) => {
    setLabForm(prev => {
      const cur = prev[id] || {};
      const d = kindDefaults[newKind];
      const userHasEdits = (cur.min_memory_mb && Number(cur.min_memory_mb) > 0)
        || (cur.min_cpu_cores && Number(cur.min_cpu_cores) > 0);
      const merged: any = { ...cur, clab_kind: newKind };
      if (d && !userHasEdits) {
        merged.min_memory_mb = d.min_memory_mb;
        merged.min_cpu_cores = d.min_cpu_cores;
        merged.requires_hugepages = d.requires_hugepages;
      }
      return { ...prev, [id]: merged };
    });
  };

  const startLabEdit = (t: any) => {
    setLabExpandedId(labExpandedId === t.id ? null : t.id);
    setLabForm(prev => ({
      ...prev,
      [t.id]: {
        clab_kind: t.clab_kind || '',
        image_ref: t.image_ref || '',
        min_memory_mb: t.min_memory_mb || 0,
        min_cpu_cores: t.min_cpu_cores || 0,
        requires_hugepages: !!t.requires_hugepages,
        startup_config: t.startup_config || '',
      },
    }));
  };

  const saveLabConfig = async (id: number) => {
    const draft = labForm[id];
    if (!draft) return;
    setLabSaving(true);
    try {
      await api.patchNodeTemplate(id, {
        clab_kind: draft.clab_kind,
        image_ref: draft.image_ref,
        min_memory_mb: Number(draft.min_memory_mb) || 0,
        min_cpu_cores: Number(draft.min_cpu_cores) || 0,
        requires_hugepages: !!draft.requires_hugepages,
        startup_config: draft.startup_config,
      });
      toast.success('Lab config saved');
      await loadNodeTemplates();
      setLabExpandedId(null);
    } catch (e: any) {
      toast.error(`Save failed: ${e.message}`);
    }
    setLabSaving(false);
  };

  const handleCreate = async () => {
    if (!form.name || !form.model) { toast.error('Name and model required'); return; }
    try {
      await createNodeTemplate({
        name: form.name,
        vendor: form.vendor,
        model: form.model,
        platform: form.platform,
        capacity_gbps: parseFloat(form.capacity_gbps),
        default_mtbf_hours: parseFloat(form.default_mtbf_hours),
        default_mttr_hours: parseFloat(form.default_mttr_hours),
        default_interfaces: interfaces,
      });
      setForm({ name: '', vendor: 'Cisco', model: '', platform: 'iosxr', capacity_gbps: '100', default_mtbf_hours: '50000', default_mttr_hours: '4' });
      setInterfaces([]);
      setShowCreate(false);
      toast.success('Template created');
    } catch { toast.error('Failed to create'); }
  };

  const onDragStart = (event: React.DragEvent, templateId: number) => {
    event.dataTransfer.setData('application/phanero-template', String(templateId));
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div className="space-y-3">
      <button className="btn btn-primary btn-sm w-full" onClick={() => setShowCreate(!showCreate)}>
        <Plus size={14} /> Create Template
      </button>

      {showCreate && (
        <div className="bg-surface-800 rounded-lg p-3 space-y-2 fade-in">
          <input className="w-full" placeholder="Template name (e.g. Cisco ASR 9001)" value={form.name}
            onChange={e => setForm({ ...form, name: e.target.value })} />
          <div className="grid grid-cols-2 gap-2">
            <select className="w-full" value={form.vendor} onChange={e => setForm({ ...form, vendor: e.target.value })}>
              <option value="Cisco">Cisco</option>
              <option value="Juniper">Juniper</option>
              <option value="Arista">Arista</option>
              <option value="Nokia">Nokia</option>
              <option value="Other">Other</option>
            </select>
            <input className="w-full" placeholder="Model" value={form.model}
              onChange={e => setForm({ ...form, model: e.target.value })} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <select className="w-full" value={form.platform} onChange={e => setForm({ ...form, platform: e.target.value })}>
              <option value="iosxr">IOS-XR</option>
              <option value="iosxe">IOS-XE</option>
              <option value="junos">JunOS</option>
              <option value="eos">EOS</option>
              <option value="other">Other</option>
            </select>
            <div>
              <label className="text-[10px] text-surface-500">Capacity (Gbps)</label>
              <input className="w-full" type="number" value={form.capacity_gbps}
                onChange={e => setForm({ ...form, capacity_gbps: e.target.value })} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-surface-500">Default MTBF (hrs)</label>
              <input className="w-full" type="number" value={form.default_mtbf_hours}
                onChange={e => setForm({ ...form, default_mtbf_hours: e.target.value })} />
            </div>
            <div>
              <label className="text-[10px] text-surface-500">Default MTTR (hrs)</label>
              <input className="w-full" type="number" value={form.default_mttr_hours}
                onChange={e => setForm({ ...form, default_mttr_hours: e.target.value })} />
            </div>
          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[10px] text-surface-500">Default Interfaces</span>
              <button className="btn btn-ghost btn-sm text-[10px] p-0.5"
                onClick={() => setInterfaces([...interfaces, { name: '', speed_gbps: 10, description: '' }])}>
                <Plus size={10} /> Add
              </button>
            </div>
            {interfaces.map((iface, i) => (
              <div key={i} className="flex gap-1 mb-1">
                <input className="flex-1 text-[11px]" placeholder="e.g. TenGigE0/0/0/0" value={iface.name}
                  onChange={e => {
                    const u = [...interfaces]; u[i] = { ...u[i], name: e.target.value }; setInterfaces(u);
                  }} />
                <input className="w-16 text-[11px]" type="number" placeholder="Gbps" value={iface.speed_gbps}
                  onChange={e => {
                    const u = [...interfaces]; u[i] = { ...u[i], speed_gbps: parseFloat(e.target.value) || 0 }; setInterfaces(u);
                  }} />
                <button className="btn btn-ghost btn-sm p-0.5 text-red-400"
                  onClick={() => setInterfaces(interfaces.filter((_, j) => j !== i))}>
                  <X size={10} />
                </button>
              </div>
            ))}
          </div>

          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleCreate}>Create</button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </div>
      )}

      <p className="text-[10px] text-surface-500 px-1">
        Drag a template onto the canvas to place a node. Templates are shared across all topologies.
      </p>

      <div className="space-y-1">
        {nodeTemplates.map(t => {
          const tAny: any = t;
          const isDeployable = !!tAny.clab_kind && !!tAny.image_ref;
          const isLabExpanded = labExpandedId === t.id;
          const draft = labForm[t.id];
          return (
          <div
            key={t.id}
            className="rounded-lg border border-surface-700 bg-surface-800/50 hover:bg-surface-800"
          >
            <div
              draggable
              onDragStart={e => onDragStart(e, t.id)}
              className="flex items-center justify-between px-3 py-2 cursor-grab active:cursor-grabbing"
            >
              <div className="flex items-center gap-2">
                <GripVertical size={12} className="text-surface-600" />
                <div>
                  <div className="text-sm font-medium flex items-center gap-1.5">{t.name}
                    {tAny.role === 'customer' && <span className="text-[8px] bg-amber-600/30 text-amber-300 border border-amber-500/40 px-1 py-0.5 rounded">CUSTOMER</span>}
                    {isDeployable && <span className="text-[8px] bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 px-1 py-0.5 rounded" title={`kind=${tAny.clab_kind}, image=${tAny.image_ref}`}>DEPLOYABLE</span>}
                    {!isDeployable && tAny.clab_kind && <span className="text-[8px] bg-amber-600/30 text-amber-300 border border-amber-500/40 px-1 py-0.5 rounded" title="clab_kind set but no image_ref">NEEDS IMAGE</span>}
                  </div>
                  <div className="text-[10px] text-surface-500">
                    {t.vendor} | {t.platform} | {t.capacity_gbps}G | {t.default_interfaces.length} ports
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <button
                  className="btn btn-ghost btn-sm p-1"
                  title="Edit lab/container config"
                  onClick={(e) => { e.stopPropagation(); startLabEdit(t); }}
                >
                  <Container size={12} className={isDeployable ? 'text-emerald-400' : 'text-surface-500'} />
                </button>
                {tAny.role !== 'customer' && (
                  <button
                    className="btn btn-ghost btn-sm p-1 text-red-400"
                    onClick={(e) => { e.stopPropagation(); if (confirm('Delete template?')) { deleteNodeTemplate(t.id); toast.success('Deleted'); } }}
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            </div>
            {isLabExpanded && draft && (
              <div className="px-3 pb-3 pt-1 space-y-2 border-t border-surface-700 bg-surface-900/40">
                <div className="text-[10px] text-surface-500 flex items-center gap-1 mb-1">
                  <Container size={10} /> Lab / Container Configuration
                </div>
                <div>
                  <label className="text-[10px] text-surface-500">Containerlab kind</label>
                  <select
                    className="w-full text-xs"
                    value={draft.clab_kind}
                    onChange={e => onKindChange(t.id, e.target.value)}
                  >
                    {CLAB_KIND_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  {draft.clab_kind && kindDefaults[draft.clab_kind] && (
                    <div className="text-[9px] text-surface-500 mt-0.5">
                      {kindDefaults[draft.clab_kind].description}
                    </div>
                  )}
                </div>
                <div>
                  <label className="text-[10px] text-surface-500">Image reference</label>
                  <div className="flex items-center gap-1">
                    <input
                      className="flex-1 text-xs font-mono"
                      placeholder="e.g. frrouting/frr:latest or ios-xr/xrd-vrouter:25.4.2"
                      value={draft.image_ref}
                      onChange={e => setLabForm(prev => ({ ...prev, [t.id]: { ...prev[t.id], image_ref: e.target.value } }))}
                    />
                    {builtVmImages.length > 0 && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm text-[10px] shrink-0"
                        onClick={() => setPickerOpenId(pickerOpenId === t.id ? null : t.id)}
                        title="Pick from built VM images (qcow → vrnetlab)"
                      >
                        VM Image ▾
                      </button>
                    )}
                  </div>
                  {pickerOpenId === t.id && builtVmImages.length > 0 && (
                    <div className="mt-1 border border-surface-700 rounded bg-surface-900 max-h-[160px] overflow-auto">
                      {builtVmImages.map(vm => (
                        <button
                          key={vm.id}
                          type="button"
                          className="w-full text-left px-2 py-1 hover:bg-surface-800 flex items-center gap-2 text-[10px]"
                          onClick={() => {
                            // Picking a VM image populates image_ref AND clab_kind in one shot —
                            // they're tightly coupled (a vrnetlab image only works with its kind).
                            setLabForm(prev => ({
                              ...prev,
                              [t.id]: {
                                ...prev[t.id],
                                image_ref: vm.built_image_tag,
                                clab_kind: vm.clab_kind,
                              },
                            }));
                            // Trigger the kind-change side effect so resource defaults follow.
                            onKindChange(t.id, vm.clab_kind);
                            setPickerOpenId(null);
                          }}
                        >
                          <span className="font-mono text-green-300 truncate flex-1" title={vm.built_image_tag}>
                            {vm.built_image_tag}
                          </span>
                          <span className="text-surface-500 shrink-0">{vm.clab_kind}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="text-[9px] text-surface-500 mt-0.5">
                    For local images: use the ref reported by <code>docker load</code> (see Lab panel → Device Images),
                    {builtVmImages.length > 0 ? ' or pick from VM images built via vrnetlab.' : ' or build a VM image from a .qcow2 (Lab panel → VM Images).'}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-surface-500">Min memory (MB)</label>
                    <input
                      className="w-full text-xs" type="number"
                      value={draft.min_memory_mb}
                      onChange={e => setLabForm(prev => ({ ...prev, [t.id]: { ...prev[t.id], min_memory_mb: e.target.value } }))}
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-surface-500">Min CPU cores</label>
                    <input
                      className="w-full text-xs" type="number"
                      value={draft.min_cpu_cores}
                      onChange={e => setLabForm(prev => ({ ...prev, [t.id]: { ...prev[t.id], min_cpu_cores: e.target.value } }))}
                    />
                  </div>
                </div>
                <label className="flex items-center gap-2 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!draft.requires_hugepages}
                    onChange={e => setLabForm(prev => ({ ...prev, [t.id]: { ...prev[t.id], requires_hugepages: e.target.checked } }))}
                  />
                  Requires hugepages (XRd-style platforms)
                </label>
                {draft.clab_kind && kindDefaults[draft.clab_kind] && (
                  <button
                    type="button"
                    className="text-[10px] text-primary-400 hover:text-primary-300 underline self-start"
                    onClick={() => {
                      const d = kindDefaults[draft.clab_kind];
                      setLabForm(prev => ({
                        ...prev,
                        [t.id]: {
                          ...prev[t.id],
                          min_memory_mb: d.min_memory_mb,
                          min_cpu_cores: d.min_cpu_cores,
                          requires_hugepages: d.requires_hugepages,
                        },
                      }));
                    }}
                  >
                    Reset to recommended ({kindDefaults[draft.clab_kind].min_memory_mb} MB / {kindDefaults[draft.clab_kind].min_cpu_cores} core
                    {kindDefaults[draft.clab_kind].min_cpu_cores !== 1 ? 's' : ''}
                    {kindDefaults[draft.clab_kind].requires_hugepages ? ' + hugepages' : ''})
                  </button>
                )}
                <div>
                  <label className="text-[10px] text-surface-500">Startup config (optional)</label>
                  <textarea
                    className="w-full text-[11px] font-mono"
                    rows={3}
                    placeholder="Inline config passed to clab via startup-config"
                    value={draft.startup_config}
                    onChange={e => setLabForm(prev => ({ ...prev, [t.id]: { ...prev[t.id], startup_config: e.target.value } }))}
                  />
                </div>
                <div className="flex gap-2 justify-end">
                  <button className="btn btn-ghost btn-sm" onClick={() => setLabExpandedId(null)} disabled={labSaving}>Cancel</button>
                  <button className="btn btn-primary btn-sm" onClick={() => saveLabConfig(t.id)} disabled={labSaving}>
                    <Save size={11} /> Save
                  </button>
                </div>
              </div>
            )}
          </div>
          );
        })}
      </div>
    </div>
  );
}

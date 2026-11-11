/**
 * CustomAttributesSection — lives inside SettingsPanel when Config Design
 * is enabled. Lets an admin define globally-available device and interface
 * attributes that templates can reference (e.g. `device.bgp_asn`,
 * `device.oob_interface.ip`).
 *
 * Definitions are global (one set across all topologies). Per-device values
 * live in custom_attrs_json / custom_iface_refs_json on each node and are
 * edited on the node itself, not here.
 */
import { useEffect, useState } from 'react';
import { Plus, Trash2, Save, X, Pencil } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../utils/api';

type DevDef = {
  id: number;
  key: string;
  label: string;
  description: string;
  type: 'boolean' | 'string' | 'integer' | 'select';
  options: string[];
  default_value: string;
  unique_per_topology: boolean;
  unique_start_value: number;
};

type IfaceDef = {
  id: number;
  key: string;
  label: string;
  description: string;
};

export default function CustomAttributesSection({ enabled }: { enabled: boolean }) {
  const [devDefs, setDevDefs] = useState<DevDef[]>([]);
  const [ifaceDefs, setIfaceDefs] = useState<IfaceDef[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingDev, setEditingDev] = useState<Partial<DevDef> | null>(null);
  const [editingIface, setEditingIface] = useState<Partial<IfaceDef> | null>(null);

  const reload = async () => {
    setLoading(true);
    try {
      const [d, i] = await Promise.all([api.listDeviceAttrDefs(), api.listIfaceAttrDefs()]);
      setDevDefs(d);
      setIfaceDefs(i);
    } catch (e: any) {
      toast.error(`Failed to load: ${e.message || e}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (enabled) reload().catch(() => {}); }, [enabled]);

  if (!enabled) return null;

  // ---- Device attributes ----
  const saveDev = async () => {
    if (!editingDev?.key || !editingDev?.label) {
      toast.error('Key and label are required');
      return;
    }
    try {
      const payload = {
        key: editingDev.key,
        label: editingDev.label,
        description: editingDev.description || '',
        type: editingDev.type || 'string',
        options: editingDev.options || [],
        default_value: editingDev.default_value || '',
        unique_per_topology: !!editingDev.unique_per_topology,
        unique_start_value: Number.isFinite(Number(editingDev.unique_start_value)) ? Number(editingDev.unique_start_value) : 0,
      };
      if (editingDev.id) await api.updateDeviceAttrDef(editingDev.id, payload);
      else await api.createDeviceAttrDef(payload);
      setEditingDev(null);
      await reload();
      toast.success('Saved');
    } catch (e: any) {
      toast.error(e.message || String(e));
    }
  };

  const deleteDev = async (id: number) => {
    if (!confirm('Delete this attribute definition? Per-device values for this key remain stored but become inert.')) return;
    try {
      await api.deleteDeviceAttrDef(id);
      await reload();
      toast.success('Deleted');
    } catch (e: any) { toast.error(e.message || String(e)); }
  };

  // ---- Interface attributes ----
  const saveIface = async () => {
    if (!editingIface?.key || !editingIface?.label) {
      toast.error('Key and label are required');
      return;
    }
    try {
      const payload = {
        key: editingIface.key,
        label: editingIface.label,
        description: editingIface.description || '',
      };
      if (editingIface.id) await api.updateIfaceAttrDef(editingIface.id, payload);
      else await api.createIfaceAttrDef(payload);
      setEditingIface(null);
      await reload();
      toast.success('Saved');
    } catch (e: any) {
      toast.error(e.message || String(e));
    }
  };

  const deleteIface = async (id: number) => {
    if (!confirm('Delete this interface attribute definition?')) return;
    try {
      await api.deleteIfaceAttrDef(id);
      await reload();
      toast.success('Deleted');
    } catch (e: any) { toast.error(e.message || String(e)); }
  };

  return (
    <div className="mt-6 pt-4 border-t border-surface-700 space-y-4">
      <div className="text-xs font-semibold text-surface-200">Custom Attributes (Config Design)</div>
      <p className="text-[10px] text-surface-500">
        Globally-defined attributes that appear on every device. Templates reference them as
        <code className="font-mono text-amber-300"> {'{{ device.<key> }}'} </code>.
      </p>

      {/* ---- Device attributes block ---- */}
      <div className="border border-surface-700 rounded">
        <div className="px-2 py-1.5 bg-surface-800/50 flex items-center justify-between text-[11px]">
          <span className="font-medium">Device Attributes ({devDefs.length})</span>
          <button
            className="btn btn-ghost btn-sm text-[10px]"
            onClick={() => setEditingDev({ key: '', label: '', type: 'string', options: [], default_value: '' })}
            disabled={!!editingDev}
          >
            <Plus size={10} /> Add
          </button>
        </div>
        <div className="divide-y divide-surface-700/60">
          {loading && <div className="px-2 py-2 text-[10px] text-surface-500">Loading…</div>}
          {!loading && devDefs.length === 0 && !editingDev && (
            <div className="px-2 py-3 text-[10px] text-surface-500">
              No custom device attributes yet. Add one to expose new fields on every device (e.g.
              <span className="font-mono"> bgp_asn</span>,
              <span className="font-mono"> loopback_ip</span>).
            </div>
          )}
          {devDefs.map(d => (
            <div key={d.id} className="px-2 py-1.5 flex items-center gap-2 text-[11px]">
              <div className="flex-1 min-w-0">
                <div className="font-mono text-amber-300 truncate flex items-center gap-1.5">
                  device.{d.key}
                  {d.unique_per_topology && (
                    <span className="text-[9px] bg-violet-700/50 px-1 rounded normal-case font-sans" title="Must be unique across all nodes in the topology">unique</span>
                  )}
                </div>
                <div className="text-surface-400 truncate text-[10px]">
                  {d.label} · <span className="text-surface-600">{d.type}</span>
                  {d.type === 'select' && d.options.length > 0 && <span className="text-surface-600"> ({d.options.length} options)</span>}
                  {d.default_value && <span className="text-surface-600"> · default {d.default_value}</span>}
                </div>
              </div>
              <button className="btn btn-ghost btn-sm p-1" onClick={() => setEditingDev({ ...d })} title="Edit">
                <Pencil size={11} />
              </button>
              <button className="btn btn-ghost btn-sm p-1 text-red-400" onClick={() => deleteDev(d.id)} title="Delete">
                <Trash2 size={11} />
              </button>
            </div>
          ))}
        </div>

        {editingDev && (
          <div className="border-t border-surface-700 bg-surface-900/50 p-2 space-y-1.5">
            <div className="grid grid-cols-2 gap-1.5">
              <input
                className="input input-sm text-xs font-mono"
                placeholder="key (snake_case, used in templates)"
                value={editingDev.key || ''}
                onChange={e => setEditingDev(s => ({ ...s, key: e.target.value }))}
              />
              <input
                className="input input-sm text-xs"
                placeholder="Label (human-friendly)"
                value={editingDev.label || ''}
                onChange={e => setEditingDev(s => ({ ...s, label: e.target.value }))}
              />
            </div>
            <input
              className="input input-sm text-xs w-full"
              placeholder="Description (optional)"
              value={editingDev.description || ''}
              onChange={e => setEditingDev(s => ({ ...s, description: e.target.value }))}
            />
            <div className="grid grid-cols-2 gap-1.5">
              <select
                className="input input-sm text-xs"
                value={editingDev.type || 'string'}
                onChange={e => setEditingDev(s => ({ ...s, type: e.target.value as DevDef['type'] }))}
              >
                <option value="boolean">boolean</option>
                <option value="string">string</option>
                <option value="integer">integer</option>
                <option value="select">select</option>
              </select>
              <input
                className="input input-sm text-xs"
                placeholder="Default value"
                value={editingDev.default_value || ''}
                onChange={e => setEditingDev(s => ({ ...s, default_value: e.target.value }))}
              />
            </div>
            {editingDev.type === 'select' && (
              <input
                className="input input-sm text-xs w-full"
                placeholder="Options (comma-separated)"
                value={(editingDev.options || []).join(', ')}
                onChange={e => setEditingDev(s => ({ ...s, options: e.target.value.split(',').map(x => x.trim()).filter(Boolean) }))}
              />
            )}
            <label className="flex items-center gap-2 text-[11px] cursor-pointer">
              <input
                type="checkbox"
                checked={!!editingDev.unique_per_topology}
                onChange={e => setEditingDev(s => ({ ...s, unique_per_topology: e.target.checked }))}
              />
              <span>Unique per topology — reject duplicates across all nodes</span>
            </label>
            {/* Auto-pick starting value — only meaningful for unique integer attrs. */}
            {editingDev.type === 'integer' && editingDev.unique_per_topology && (
              <div className="flex items-center gap-2 text-[11px]">
                <label className="text-surface-400 shrink-0">Auto-pick starts at</label>
                <input
                  className="input input-sm text-xs w-24 font-mono"
                  type="number"
                  value={editingDev.unique_start_value ?? 0}
                  onChange={e => setEditingDev(s => ({ ...s, unique_start_value: parseInt(e.target.value) || 0 }))}
                />
                <span className="text-[10px] text-surface-600">first value the "next available" button picks from</span>
              </div>
            )}
            <div className="flex gap-1.5">
              <button className="btn btn-primary btn-sm text-[10px] flex-1" onClick={saveDev}>
                <Save size={10} /> Save
              </button>
              <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => setEditingDev(null)}>
                <X size={10} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ---- Interface attributes block ---- */}
      <div className="border border-surface-700 rounded">
        <div className="px-2 py-1.5 bg-surface-800/50 flex items-center justify-between text-[11px]">
          <span className="font-medium">Interface Attributes ({ifaceDefs.length})</span>
          <button
            className="btn btn-ghost btn-sm text-[10px]"
            onClick={() => setEditingIface({ key: '', label: '' })}
            disabled={!!editingIface}
          >
            <Plus size={10} /> Add
          </button>
        </div>
        <div className="divide-y divide-surface-700/60">
          {!loading && ifaceDefs.length === 0 && !editingIface && (
            <div className="px-2 py-3 text-[10px] text-surface-500">
              No custom interface attributes yet. Add one to let each device flag a specific interface for a purpose
              (e.g. <span className="font-mono">oob_interface</span>). Templates reference it as
              <span className="font-mono"> {'{{ device.oob_interface.ip }}'}</span>.
            </div>
          )}
          {ifaceDefs.map(d => (
            <div key={d.id} className="px-2 py-1.5 flex items-center gap-2 text-[11px]">
              <div className="flex-1 min-w-0">
                <div className="font-mono text-amber-300 truncate">device.{d.key}</div>
                <div className="text-surface-400 truncate text-[10px]">{d.label}</div>
              </div>
              <button className="btn btn-ghost btn-sm p-1" onClick={() => setEditingIface({ ...d })} title="Edit">
                <Pencil size={11} />
              </button>
              <button className="btn btn-ghost btn-sm p-1 text-red-400" onClick={() => deleteIface(d.id)} title="Delete">
                <Trash2 size={11} />
              </button>
            </div>
          ))}
        </div>

        {editingIface && (
          <div className="border-t border-surface-700 bg-surface-900/50 p-2 space-y-1.5">
            <div className="grid grid-cols-2 gap-1.5">
              <input
                className="input input-sm text-xs font-mono"
                placeholder="key (e.g. oob_interface)"
                value={editingIface.key || ''}
                onChange={e => setEditingIface(s => ({ ...s, key: e.target.value }))}
              />
              <input
                className="input input-sm text-xs"
                placeholder="Label"
                value={editingIface.label || ''}
                onChange={e => setEditingIface(s => ({ ...s, label: e.target.value }))}
              />
            </div>
            <input
              className="input input-sm text-xs w-full"
              placeholder="Description (optional)"
              value={editingIface.description || ''}
              onChange={e => setEditingIface(s => ({ ...s, description: e.target.value }))}
            />
            <div className="flex gap-1.5">
              <button className="btn btn-primary btn-sm text-[10px] flex-1" onClick={saveIface}>
                <Save size={10} /> Save
              </button>
              <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => setEditingIface(null)}>
                <X size={10} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

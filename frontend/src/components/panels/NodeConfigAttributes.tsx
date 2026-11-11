/**
 * NodeConfigAttributes — collapsible Config Design fields inside the Node
 * editor in NodesPanel. Three sections (each gets a disclosure):
 *
 *   1. Route Reflector — is_route_reflector checkbox + multi-select picker
 *      for which other nodes this device peers with.
 *   2. Custom Attributes — dynamic UI driven by DeviceAttributeDefinition.
 *      Booleans render as checkboxes, integers as number inputs, strings
 *      as text inputs, selects as dropdowns.
 *   3. Custom Interface Attributes — for each InterfaceAttributeDefinition,
 *      a dropdown of this device's own interfaces.
 *
 * The section only renders when Config Design is enabled (passed in by the
 * parent) and reads its definitions from /api/attribute-definitions/*.
 *
 * The component is uncontrolled w.r.t. local edit buffers — it gets editForm
 * and onChange from the parent so save flows through the existing saveEdit.
 */
import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Network, Wand2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';

type DevDef = { id: number; key: string; label: string; description: string; type: string; options: string[]; default_value: string; unique_per_topology?: boolean; unique_start_value?: number };
type IfaceDef = { id: number; key: string; label: string; description: string };

interface Props {
  enabled: boolean;
  nodeId: number;
  allNodes: Array<{ id: number; name: string; is_route_reflector?: boolean }>;
  interfaces: Array<{ name: string }>;
  editForm: any;
  setEditForm: (updater: (prev: any) => any) => void;
}

export default function NodeConfigAttributes({ enabled, nodeId, allNodes, interfaces, editForm, setEditForm }: Props) {
  const [devDefs, setDevDefs] = useState<DevDef[]>([]);
  const [ifaceDefs, setIfaceDefs] = useState<IfaceDef[]>([]);
  const [openRR, setOpenRR] = useState(true);
  const [openAttrs, setOpenAttrs] = useState(false);
  const [openIfaceRefs, setOpenIfaceRefs] = useState(false);
  // IMPORTANT: All hooks must be called unconditionally before any early
  // return, otherwise React's hook-count check fires when props change and
  // the component starts/stops bailing out. Keep useStore up here next to
  // the useState/useEffect block — not down by pickNextValue where it reads.
  const currentTopologyId = useStore(s => s.currentTopologyId);

  useEffect(() => {
    if (!enabled) return;
    Promise.all([api.listDeviceAttrDefs(), api.listIfaceAttrDefs()])
      .then(([d, i]) => { setDevDefs(d); setIfaceDefs(i); })
      .catch(() => {});
  }, [enabled]);

  if (!enabled) return null;
  // Customer endpoints aren't routers — they don't carry routing config, so
  // RR + custom device/interface attributes don't apply to them.
  if (editForm?.role === 'customer') return null;

  const peerIds: number[] = Array.isArray(editForm.rr_peer_ids) ? editForm.rr_peer_ids : [];
  const togglePeer = (id: number) => {
    setEditForm(prev => {
      const cur: number[] = Array.isArray(prev.rr_peer_ids) ? prev.rr_peer_ids : [];
      const next = cur.includes(id) ? cur.filter(p => p !== id) : [...cur, id];
      return { ...prev, rr_peer_ids: next };
    });
  };
  // RR tiering. Super RR ⇒ RR (implication enforced below). Hierarchy is
  // auto-detected from topology state: if any node is flagged super_rr, the
  // peer picker for non-RRs filters to non-super RRs (their direct parents)
  // and the picker for non-super RRs filters to super RRs (their parents).
  // In flat mode (no supers exist) non-RRs pick from all RRs and non-super
  // RRs don't really exist — only the "is RR" checkbox is shown.
  const isRR = !!editForm.is_route_reflector;
  const isSuperRR = !!editForm.is_super_route_reflector;
  const anySuperExists = allNodes.some(n => (n as any).is_super_route_reflector);
  const rrPeerOptions = (() => {
    if (!isRR) {
      // Non-RR client. Hierarchical mode → only non-super RRs are valid
      // direct parents. Flat mode → any RR.
      return allNodes.filter(n =>
        n.id !== nodeId
        && (n as any).is_route_reflector
        && (!anySuperExists || !(n as any).is_super_route_reflector)
      );
    }
    if (isRR && !isSuperRR) {
      // Non-super RR. Peers up to super RRs.
      return allNodes.filter(n => n.id !== nodeId && (n as any).is_super_route_reflector);
    }
    // Super RR — no manual peer list; super RRs auto-mesh in templates.
    return [];
  })();

  const setAttrValue = (key: string, value: any) => {
    setEditForm(prev => ({ ...prev, custom_attrs: { ...(prev.custom_attrs || {}), [key]: value } }));
  };

  // Auto-pick the next available integer value for a unique attribute. Pulls
  // from the backend which scans the whole topology chain — accounts for
  // values on every existing node, not just the ones already loaded into
  // the editor. (currentTopologyId is hoisted to the top of the component
  // so the useStore hook runs before the early returns above.)
  const pickNextValue = async (def: DevDef) => {
    if (!currentTopologyId) return;
    try {
      const res = await api.nextUniqueIntegerValue(def.id, currentTopologyId);
      setAttrValue(def.key, String(res.next_value));
      toast.success(`Picked ${res.next_value} (next free ≥ ${res.start})`);
    } catch (e: any) {
      toast.error(`Failed to pick next value: ${e?.message || e}`);
    }
  };

  const setIfaceRef = (key: string, ifaceName: string) => {
    setEditForm(prev => ({ ...prev, custom_iface_refs: { ...(prev.custom_iface_refs || {}), [key]: ifaceName } }));
  };

  const customAttrs: Record<string, any> = editForm.custom_attrs || {};
  const customIfaceRefs: Record<string, string> = editForm.custom_iface_refs || {};

  return (
    <div className="mt-2 pt-2 border-t border-surface-700/60 space-y-1.5">
      {/* ---- Route Reflector ---- */}
      <Disclosure
        open={openRR}
        onToggle={() => setOpenRR(v => !v)}
        title="Route Reflector"
        icon={<Network size={11} />}
        badge={isSuperRR ? 'SUPER-RR' : isRR ? 'RR' : (peerIds.length > 0 ? `${peerIds.length} RR(s)` : null)}
      >
        <label className="flex items-center gap-2 text-[11px] cursor-pointer">
          <input
            type="checkbox"
            checked={isRR}
            onChange={e => {
              const on = e.target.checked;
              setEditForm(prev => ({
                ...prev,
                is_route_reflector: on,
                // Unchecking the RR flag clears Super RR too.
                is_super_route_reflector: on ? prev.is_super_route_reflector : false,
              }));
            }}
          />
          <span>This device is a Route Reflector</span>
        </label>

        {isRR && (
          <label className="flex items-center gap-2 text-[11px] cursor-pointer ml-5">
            <input
              type="checkbox"
              checked={isSuperRR}
              onChange={e => setEditForm(prev => ({
                ...prev,
                is_super_route_reflector: e.target.checked,
                // Super RR implies RR.
                is_route_reflector: e.target.checked ? true : prev.is_route_reflector,
              }))}
            />
            <span>Super Route Reflector <span className="text-surface-500">(top tier — clients are non-super RRs)</span></span>
          </label>
        )}

        {isSuperRR ? (
          <div className="text-[10px] text-surface-500 mt-2">
            Super RRs auto-mesh among themselves in templates via
            <span className="font-mono text-amber-300 mx-1">device.super_route_reflectors</span>.
            Their clients are the non-super RRs that select them as peers below.
          </div>
        ) : isRR ? (
          <>
            <div className="text-[10px] text-surface-500 mt-2">
              {anySuperExists
                ? 'Pick which Super Route Reflectors this RR peers up to. Its own clients (regular nodes) show up automatically via device.rr_clients.'
                : 'RRs auto-mesh via device.all_route_reflectors. If you promote any device to "Super RR", this picker becomes the parent-RR selector.'}
            </div>
            {anySuperExists && (
              <div className="max-h-[120px] overflow-y-auto border border-surface-700 rounded mt-1">
                {rrPeerOptions.length === 0 ? (
                  <div className="text-[10px] text-surface-500 px-2 py-2">
                    No Super Route Reflectors defined yet — promote at least one RR to Super RR first.
                  </div>
                ) : rrPeerOptions.map(n => {
                  const checked = peerIds.includes(n.id);
                  return (
                    <label key={n.id} className="flex items-center gap-2 px-2 py-1 text-[11px] hover:bg-surface-800 cursor-pointer">
                      <input type="checkbox" checked={checked} onChange={() => togglePeer(n.id)} />
                      <span className="flex-1 truncate">{n.name}</span>
                      <span className="text-[9px] bg-violet-800/60 px-1 rounded">SUPER-RR</span>
                    </label>
                  );
                })}
              </div>
            )}
          </>
        ) : (
          <>
            <div className="text-[10px] text-surface-500 mt-2">
              Pick which Route Reflectors this device should peer with as a client.
              {anySuperExists && ' (Filtered to non-super RRs — your direct parents in the hierarchy.)'}
            </div>
            <div className="max-h-[120px] overflow-y-auto border border-surface-700 rounded mt-1">
              {rrPeerOptions.length === 0 ? (
                <div className="text-[10px] text-surface-500 px-2 py-2">
                  {anySuperExists
                    ? 'No non-super RRs defined yet — mark at least one device as an RR (but not Super RR) first.'
                    : 'No Route Reflectors defined in this topology yet — mark a device as an RR first.'}
                </div>
              ) : rrPeerOptions.map(n => {
                const checked = peerIds.includes(n.id);
                return (
                  <label key={n.id} className="flex items-center gap-2 px-2 py-1 text-[11px] hover:bg-surface-800 cursor-pointer">
                    <input type="checkbox" checked={checked} onChange={() => togglePeer(n.id)} />
                    <span className="flex-1 truncate">{n.name}</span>
                    <span className="text-[9px] bg-violet-700/50 px-1 rounded">RR</span>
                  </label>
                );
              })}
            </div>
          </>
        )}
      </Disclosure>

      {/* ---- Custom Attributes ---- */}
      <Disclosure
        open={openAttrs}
        onToggle={() => setOpenAttrs(v => !v)}
        title="Custom Attributes"
        badge={devDefs.length > 0 ? `${devDefs.length}` : 'none defined'}
      >
        {devDefs.length === 0 && (
          <div className="text-[10px] text-surface-500">
            No custom device attributes defined. Add some in Settings → Custom Attributes.
          </div>
        )}
        {devDefs.map(d => {
          const current = customAttrs[d.key];
          const value = current !== undefined ? current : d.default_value;
          return (
            <div key={d.id} className="mb-1.5">
              <label className="text-[10px] text-surface-500 flex items-center gap-1">
                <span className="font-mono text-amber-300">device.{d.key}</span>
                <span className="text-surface-600">— {d.label}</span>
                {d.unique_per_topology && (
                  <span className="text-[9px] bg-violet-700/50 px-1 rounded text-violet-100" title="Must be unique across all nodes in the topology">unique</span>
                )}
              </label>
              {d.type === 'boolean' && (
                <label className="flex items-center gap-2 text-[11px]">
                  <input
                    type="checkbox"
                    checked={value === true || String(value).toLowerCase() === 'true'}
                    onChange={e => setAttrValue(d.key, e.target.checked)}
                  />
                  <span>{d.description || d.label}</span>
                </label>
              )}
              {d.type === 'integer' && (
                <div className="flex items-center gap-1">
                  <input
                    className="flex-1 text-xs"
                    type="number"
                    value={value ?? ''}
                    placeholder={d.default_value || '0'}
                    onChange={e => setAttrValue(d.key, e.target.value)}
                  />
                  {d.unique_per_topology && (
                    <button
                      className="btn btn-ghost btn-sm text-[10px] px-1.5 text-violet-300 hover:bg-violet-900/30"
                      onClick={() => pickNextValue(d)}
                      title={`Pick next available value (counts up from ${d.unique_start_value ?? 0})`}
                      type="button"
                    >
                      <Wand2 size={11} />
                    </button>
                  )}
                </div>
              )}
              {d.type === 'string' && (
                <input
                  className="w-full text-xs"
                  value={value ?? ''}
                  placeholder={d.default_value || '(empty)'}
                  onChange={e => setAttrValue(d.key, e.target.value)}
                />
              )}
              {d.type === 'select' && (
                <select
                  className="w-full text-xs"
                  value={value ?? ''}
                  onChange={e => setAttrValue(d.key, e.target.value)}
                >
                  <option value="">— pick —</option>
                  {d.options.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              )}
            </div>
          );
        })}
      </Disclosure>

      {/* ---- Custom Interface Attributes ---- */}
      <Disclosure
        open={openIfaceRefs}
        onToggle={() => setOpenIfaceRefs(v => !v)}
        title="Interface Attributes"
        badge={ifaceDefs.length > 0 ? `${ifaceDefs.length}` : 'none defined'}
      >
        {ifaceDefs.length === 0 && (
          <div className="text-[10px] text-surface-500">
            No custom interface attributes defined. Add some in Settings → Custom Attributes.
          </div>
        )}
        {ifaceDefs.map(d => {
          const current = customIfaceRefs[d.key] || '';
          return (
            <div key={d.id} className="mb-1.5">
              <label className="text-[10px] text-surface-500 flex items-center gap-1">
                <span className="font-mono text-amber-300">interface.{d.key}</span>
                <span className="text-surface-600">— {d.label}</span>
              </label>
              <select
                className="w-full text-xs"
                value={current}
                onChange={e => setIfaceRef(d.key, e.target.value)}
              >
                <option value="">— none —</option>
                {interfaces.map(i => <option key={i.name} value={i.name}>{i.name}</option>)}
              </select>
            </div>
          );
        })}
      </Disclosure>
    </div>
  );
}

function Disclosure({ open, onToggle, title, badge, icon, children }: {
  open: boolean;
  onToggle: () => void;
  title: string;
  badge?: string | null;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="border border-surface-700 rounded">
      <button
        className="w-full px-2 py-1 flex items-center justify-between gap-2 text-[11px] hover:bg-surface-800/50"
        onClick={onToggle}
      >
        <span className="flex items-center gap-1.5">
          {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          {icon}
          <span className="font-medium">{title}</span>
        </span>
        {badge && <span className="text-[9px] text-surface-500">{badge}</span>}
      </button>
      {open && <div className="px-2 py-2 border-t border-surface-700/60">{children}</div>}
    </div>
  );
}

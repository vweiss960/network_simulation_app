/**
 * PatchPanelModal — visual documentation of the patch panels at a location.
 *
 * Documentation only: nothing here creates, modifies or deletes links, nodes
 * or interfaces. Ports can only reference links/devices/interfaces that
 * already exist in the topology. Panels belong to a LOCATION, so every
 * device with that location sees the same panels.
 *
 * Chaining: a customer-facing panel can sit "in front of" a wan-facing panel.
 * Physical path: WAN link -> wan-facing port -> jumper -> customer-facing
 * port -> device. The device is documented on the customer-facing port and the
 * WAN link is kept in sync on every port of a jumpered path (the backend
 * mirrors it). Ports on adjacent panels are jumpered by dragging from one port
 * to the other; jumpers render as cables between the stacked panels. The
 * compressed view (default) hides intermediate panels and draws cables
 * end-to-end. Left-click traces a port's whole path (and its link on the
 * topology); right-click edits it.
 *
 * Opened from the canvas node right-click menu ("View/Create patch panel").
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import toast from 'react-hot-toast';
import { LayoutGrid, Pencil, Plus, Trash2, X } from 'lucide-react';
import { api } from '../utils/api';
import { useStore } from '../stores/useStore';
import ContextMenu from './ContextMenu';

type Status = 'unused' | 'cabled_free' | 'cabled_reserved' | 'cabled_used';

const STATUS: Record<Status, { label: string; cls: string; swatch: string }> = {
  unused: { label: 'Unused / not cabled', cls: 'bg-surface-950 border-surface-500', swatch: 'bg-surface-950 border border-surface-500' },
  cabled_free: { label: 'Cabled / not used', cls: 'bg-sky-500 border-sky-300', swatch: 'bg-sky-500' },
  cabled_reserved: { label: 'Cabled / reserved', cls: 'bg-amber-500 border-amber-300', swatch: 'bg-amber-500' },
  cabled_used: { label: 'Cabled / used', cls: 'bg-emerald-500 border-emerald-300', swatch: 'bg-emerald-500' },
};
const STATUS_ORDER: Status[] = ['unused', 'cabled_free', 'cabled_reserved', 'cabled_used'];

interface Port {
  label?: string; status: Status; node_id?: number | null; node_name?: string | null;
  interface?: string; link_id?: number | null; notes?: string; missing?: boolean; dangling?: boolean;
  rear_port?: number;
  link?: { far_node: string; far_interface: string; carrier: string; circuit_id: string };
}
interface Panel {
  id: number; location: string; name: string; rack: string; start_u: number | null;
  height_u: number; rows: number; ports_per_row: number; notes: string;
  in_front_of_id: number | null; ports: Record<string, Port>;
}
type PanelForm = { name: string; rack: string; start_u: string; height_u: string; rows: string; ports_per_row: string; notes: string; in_front_of: string };
type PortForm = { status: Status; label: string; node_id: number | null; interface: string; link_id: number | null; notes: string };
type PortRef = { panelId: number; port: number };
type Cable = { from: PortRef; to: PortRef; via: string[] };

const EMPTY_FORM: PanelForm = { name: '', rack: '', start_u: '', height_u: '1', rows: '2', ports_per_row: '24', notes: '', in_front_of: '' };
const key = (r: PortRef) => `${r.panelId}:${r.port}`;

// "API error 409: {...}" -> the server's human message.
function errMsg(e: any): string {
  const m = String(e?.message || e);
  const i = m.indexOf('{');
  if (i >= 0) {
    try {
      const d = JSON.parse(m.slice(i)).detail;
      return typeof d === 'string' ? d : d?.message || m;
    } catch { /* fall through */ }
  }
  return m;
}

interface Props {
  topologyId: number;
  location: string | null;   // null = closed
  onClose: () => void;
  onChanged?: (count: number) => void;
}

export default function PatchPanelModal({ topologyId, location, onClose, onChanged }: Props) {
  const nodes = useStore(s => s.nodes);
  const links = useStore(s => s.links);
  const canEdit = useStore(s => s.canEditCurrentTopology());
  const setAgentHighlights = useStore(s => s.setAgentHighlights);

  const [panels, setPanels] = useState<Panel[]>([]);
  const [activeId, setActiveId] = useState<number | 'new' | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedPort, setSelectedPort] = useState<number | null>(null);
  const [portForm, setPortForm] = useState<PortForm | null>(null);
  const [editingPanel, setEditingPanel] = useState(false);
  const [panelForm, setPanelForm] = useState<PanelForm>(EMPTY_FORM);
  const [forceNeeded, setForceNeeded] = useState<{ message: string; ports: number[] } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState('');
  // Compressed (default): intermediate panels hidden, cables end-to-end.
  const [expanded, setExpanded] = useState(false);
  // Non-modal window: draggable by its header so the canvas behind stays usable.
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  // Left-click traces a port's path; these ports/cables render highlighted.
  const [traced, setTraced] = useState<Set<string>>(new Set());
  const [portMenu, setPortMenu] = useState<{ x: number; y: number; panel: Panel; port: number } | null>(null);
  // Drag-to-connect state (positions relative to the stack container).
  const stackRef = useRef<HTMLDivElement>(null);
  const cableDrag = useRef<{ from: PortRef; sx: number; sy: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [dragLine, setDragLine] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const [cablePaths, setCablePaths] = useState<{ d: string; traced: boolean; label: string; lx: number; ly: number }[]>([]);

  const load = async (keep?: number | 'new' | null) => {
    if (!location) return;
    setLoading(true);
    try {
      const list: Panel[] = await api.listPatchPanels(topologyId, location);
      setPanels(list);
      onChanged?.(list.length);
      setActiveId(prev => {
        const want = keep !== undefined ? keep : prev;
        if (want === 'new') return 'new';
        if (want != null && list.some(p => p.id === want)) return want;
        return list.length ? list[0].id : 'new';
      });
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!location) return;
    setPanels([]); setActiveId(null); setSelectedPort(null); setPortForm(null);
    setEditingPanel(false); setError(''); setPanelForm(EMPTY_FORM);
    setTraced(new Set()); setPortMenu(null); setOffset({ x: 0, y: 0 }); setExpanded(false);
    load(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location, topologyId]);

  // Drop any link highlight we set when the window closes.
  useEffect(() => {
    if (!location) return;
    return () => setAgentHighlights([], []);
  }, [location, setAgentHighlights]);

  // Network devices at this location.
  const siteNodes = useMemo(
    () => nodes.filter(n => (n.location || '') === location && (n.role || 'router') !== 'customer'),
    [nodes, location],
  );
  const siteNodeIds = useMemo(() => new Set(siteNodes.map(n => n.id)), [siteNodes]);
  const nodeById = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);
  // A customer-facing port patches to a network device here or to a customer
  // device (customer nodes may sit anywhere).
  const deviceOptions = useMemo(
    () => [...siteNodes, ...nodes.filter(n => n.role === 'customer')],
    [siteNodes, nodes],
  );
  // Existing links that land on a device at this location — the ONLY things
  // a port can be connected to.
  const siteLinks = useMemo(
    () => links.filter(l => siteNodeIds.has(l.source_node_id) || siteNodeIds.has(l.target_node_id)),
    [links, siteNodeIds],
  );
  // WAN links: those leaving the site (far end is a network device at another
  // location) — what wan-facing ports and ports jumpered to them carry.
  const wanLinks = useMemo(() => siteLinks.filter(l => {
    const farId = siteNodeIds.has(l.source_node_id) ? l.target_node_id : l.source_node_id;
    const far = nodeById.get(farId);
    return !!far && (far.location || '') !== location && (far.role || 'router') !== 'customer';
  }), [siteLinks, siteNodeIds, nodeById, location]);

  const panelById = useMemo(() => new Map(panels.map(p => [p.id, p])), [panels]);
  // Panels directly in front of each panel.
  const frontsOf = useMemo(() => {
    const m = new Map<number, Panel[]>();
    panels.forEach(p => { if (p.in_front_of_id) m.set(p.in_front_of_id, [...(m.get(p.in_front_of_id) || []), p]); });
    return m;
  }, [panels]);
  // "panelId:rearPort" -> the front port jumpered onto it.
  const frontRef = useMemo(() => {
    const m = new Map<string, PortRef>();
    panels.forEach(p => {
      if (!p.in_front_of_id) return;
      Object.entries(p.ports).forEach(([k, v]) => {
        if (v.rear_port) m.set(`${p.in_front_of_id}:${v.rear_port}`, { panelId: p.id, port: +k });
      });
    });
    return m;
  }, [panels]);
  // Depth from the customer side: 0 = nothing in front of it.
  const depth = useMemo(() => {
    const d = new Map<number, number>();
    const calc = (p: Panel, seen: Set<number>): number => {
      if (d.has(p.id)) return d.get(p.id)!;
      if (seen.has(p.id)) return 0;
      seen.add(p.id);
      const fs = frontsOf.get(p.id) || [];
      const v = fs.length ? 1 + Math.max(...fs.map(f => calc(f, seen))) : 0;
      d.set(p.id, v);
      return v;
    };
    panels.forEach(p => calc(p, new Set()));
    return d;
  }, [panels, frontsOf]);
  const role = (p: Panel): 'standalone' | 'customer' | 'intermediate' | 'wan' => {
    const hasFront = (frontsOf.get(p.id) || []).length > 0;
    if (!hasFront && !p.in_front_of_id) return 'standalone';
    if (!hasFront) return 'customer';
    return p.in_front_of_id ? 'intermediate' : 'wan';
  };

  // Tabs: panels sharing a rack, plus anything chained to them, share a tab.
  const groups = useMemo(() => {
    const parent = new Map<number, number>(panels.map(p => [p.id, p.id]));
    const find = (x: number): number => (parent.get(x) === x ? x : (parent.set(x, find(parent.get(x)!)), parent.get(x)!));
    const union = (a: number, b: number) => parent.set(find(a), find(b));
    const byRack = new Map<string, number>();
    panels.forEach(p => {
      const r = p.rack.trim();
      if (r) { if (byRack.has(r)) union(p.id, byRack.get(r)!); else byRack.set(r, p.id); }
      if (p.in_front_of_id && parent.has(p.in_front_of_id)) union(p.id, p.in_front_of_id);
    });
    const m = new Map<number, Panel[]>();
    panels.forEach(p => m.set(find(p.id), [...(m.get(find(p.id)) || []), p]));
    return [...m.entries()].map(([root, ps]) => {
      const racks = [...new Set(ps.map(p => p.rack.trim()).filter(Boolean))].sort();
      return {
        key: `g:${root}`,
        label: racks.length ? `Rack ${racks.join(' + ')}` : ps[0].name,
        // Customer-facing first, then toward the WAN; within a level the
        // higher rack units sit higher.
        panels: [...ps].sort((a, b) =>
          (depth.get(a.id)! - depth.get(b.id)!)
          || ((b.start_u ?? -1) + (b.height_u || 1)) - ((a.start_u ?? -1) + (a.height_u || 1))
          || a.name.localeCompare(b.name)),
      };
    }).sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
  }, [panels, depth]);

  const panel = typeof activeId === 'number' ? panelById.get(activeId) || null : null;
  const activeGroup = panel ? groups.find(g => g.panels.some(p => p.id === panel.id)) || null : null;
  const hasIntermediate = !!activeGroup?.panels.some(p => role(p) === 'intermediate');
  const visiblePanels = (activeGroup?.panels || []).filter(p => expanded || role(p) !== 'intermediate');
  const visibleIds = new Set(visiblePanels.map(p => p.id));

  // Walk a port's jumpers toward the WAN (rear) and the customer (front).
  const tracePath = (start: PortRef): PortRef[] => {
    const back: PortRef[] = [];
    let cur: PortRef | null = start;
    const seen = new Set<string>();
    while (cur && !seen.has(key(cur))) {
      seen.add(key(cur));
      back.push(cur);
      const p: Panel | undefined = panelById.get(cur.panelId);
      const rp: number | undefined = p?.ports[String(cur.port)]?.rear_port;
      cur = p?.in_front_of_id && rp ? { panelId: p.in_front_of_id, port: rp } : null;
    }
    const front: PortRef[] = [];
    let f = frontRef.get(key(start)) || null;
    while (f && !seen.has(key(f))) {
      seen.add(key(f));
      front.unshift(f);
      f = frontRef.get(key(f)) || null;
    }
    return [...front, ...back];   // customer side first
  };
  const portAt = (r: PortRef) => panelById.get(r.panelId)?.ports[String(r.port)];
  const refName = (r: PortRef) => `${panelById.get(r.panelId)?.name ?? '?'} port ${r.port}`;

  // Cables to draw: every jumper (expanded) or end-to-end through hidden
  // intermediate panels (compressed).
  const cables = useMemo<Cable[]>(() => {
    const out: Cable[] = [];
    visiblePanels.forEach(p => {
      if (!p.in_front_of_id) return;
      Object.entries(p.ports).forEach(([k, v]) => {
        if (!v.rear_port) return;
        let to: PortRef = { panelId: p.in_front_of_id!, port: v.rear_port };
        const via: string[] = [];
        // Skip through hidden panels to the next visible one.
        while (!visibleIds.has(to.panelId)) {
          const hp = panelById.get(to.panelId);
          const nxt = hp?.ports[String(to.port)]?.rear_port;
          if (!hp || !hp.in_front_of_id || !nxt) return;  // path ends inside a hidden panel
          via.push(`${hp.name}:${to.port}`);
          to = { panelId: hp.in_front_of_id, port: nxt };
        }
        out.push({ from: { panelId: p.id, port: +k }, to, via });
      });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panels, activeGroup?.key, expanded]);

  // Measure port positions and lay out the cable curves.
  useLayoutEffect(() => {
    const el = stackRef.current;
    if (!el) { setCablePaths([]); return; }
    const measure = () => {
      const base = el.getBoundingClientRect();
      const center = (r: PortRef) => {
        const b = el.querySelector(`[data-pp="${r.panelId}"][data-port="${r.port}"]`)?.getBoundingClientRect();
        return b ? { x: b.left + b.width / 2 - base.left, top: b.top - base.top, bottom: b.bottom - base.top } : null;
      };
      setCablePaths(cables.flatMap(c => {
        const a = center(c.from), b = center(c.to);
        if (!a || !b) return [];
        const down = b.top >= a.bottom;
        const y1 = down ? a.bottom : a.top, y2 = down ? b.top : b.bottom;
        const bend = Math.max(24, Math.abs(y2 - y1) / 2) * (down ? 1 : -1);
        return [{
          d: `M ${a.x} ${y1} C ${a.x} ${y1 + bend}, ${b.x} ${y2 - bend}, ${b.x} ${y2}`,
          traced: traced.has(key(c.from)) && traced.has(key(c.to)),
          label: c.via.length ? `via ${c.via.join(', ')}` : '',
          lx: (a.x + b.x) / 2, ly: (y1 + y2) / 2,
        }];
      }));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [cables, traced]);

  // Window-level listeners: header drag + cable drag.
  useEffect(() => {
    const move = (e: MouseEvent) => {
      const d = dragRef.current;
      if (d) setOffset({ x: d.ox + e.clientX - d.sx, y: d.oy + e.clientY - d.sy });
      const c = cableDrag.current;
      const el = stackRef.current;
      if (c && el) {
        if (!c.moved && Math.hypot(e.clientX - c.sx, e.clientY - c.sy) < 5) return;
        c.moved = true;
        const base = el.getBoundingClientRect();
        const src = el.querySelector(`[data-pp="${c.from.panelId}"][data-port="${c.from.port}"]`)?.getBoundingClientRect();
        if (src) setDragLine({
          x1: src.left + src.width / 2 - base.left, y1: src.top + src.height / 2 - base.top,
          x2: e.clientX - base.left, y2: e.clientY - base.top,
        });
      }
    };
    const up = (e: MouseEvent) => {
      dragRef.current = null;
      const c = cableDrag.current;
      cableDrag.current = null;
      if (!c?.moved) return;
      setDragLine(null);
      suppressClick.current = true;
      setTimeout(() => { suppressClick.current = false; }, 0);
      const t = (document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null)?.closest('[data-port]') as HTMLElement | null;
      if (!t) return;
      connect(c.from, { panelId: +t.dataset.pp!, port: +t.dataset.port! });
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panels]);

  // link id -> ["PP-1 port 7", ...] for links already documented on ports here.
  const linkUsage = useMemo(() => {
    const m = new Map<number, string[]>();
    panels.forEach(p => Object.entries(p.ports).forEach(([k, v]) => {
      if (v.link_id) m.set(v.link_id, [...(m.get(v.link_id) || []), `${p.name} port ${k}`]);
    }));
    return m;
  }, [panels]);

  if (!location) return null;

  const linkLabel = (l: typeof links[number], nearId?: number | null) => {
    const nearIsSrc = nearId != null ? l.source_node_id === nearId : siteNodeIds.has(l.source_node_id);
    const near = nodeById.get(nearIsSrc ? l.source_node_id : l.target_node_id);
    const far = nodeById.get(nearIsSrc ? l.target_node_id : l.source_node_id);
    const nearIf = nearIsSrc ? l.source_interface : l.target_interface;
    const extra = [l.carrier, l.circuit_id].filter(Boolean).join(' · ');
    return `${near?.name ?? '?'}${nearIf ? ':' + nearIf : ''} ↔ ${far?.name ?? '?'}${extra ? ' · ' + extra : ''}`;
  };

  // Extra tooltip lines describing a port's place in a chain.
  const chainTip = (r: PortRef): string[] => {
    const path = tracePath(r);
    if (path.length < 2) return [];
    const idx = path.findIndex(x => key(x) === key(r));
    const lines: string[] = [];
    if (idx > 0) lines.push(`Front ← ${refName(path[idx - 1])}`);
    if (idx < path.length - 1) lines.push(`Rear → ${refName(path[idx + 1])}`);
    const start = portAt(path[0]);
    if (idx > 0 && start?.node_name) {
      lines.push(`Device: ${start.node_name}${start.interface ? ':' + start.interface : ''} (on ${refName(path[0])})`);
    }
    return lines;
  };

  // ------------------------------------------------------------------ ports
  const openPort = (target: Panel, n: number) => {
    const p = target.ports[String(n)];
    setActiveId(target.id);
    setSelectedPort(n);
    setEditingPanel(false);
    setError('');
    setPortForm({
      status: p?.status || 'unused', label: p?.label || '',
      node_id: p?.node_id ?? null, interface: p?.interface || '',
      link_id: p?.link_id ?? null, notes: p?.notes || '',
    });
  };

  // Left-click: highlight the port's whole path, its link and end device.
  const tracePort = (target: Panel, n: number) => {
    if (suppressClick.current) return;
    const path = tracePath({ panelId: target.id, port: n });
    const linkId = path.map(portAt).find(p => p?.link_id)?.link_id;
    const l = linkId ? links.find(x => x.id === linkId) : null;
    const endNode = path.map(portAt).find(p => p?.node_id)?.node_id;
    if (!l && path.length < 2) {
      setTraced(new Set());
      setAgentHighlights([], []);
      return;
    }
    setTraced(new Set(path.map(key)));
    const nodeIds = [...(l ? [l.source_node_id, l.target_node_id] : []), ...(endNode ? [endNode] : [])];
    setAgentHighlights([...new Set(nodeIds)], l ? [l.id] : []);
  };

  const startCableDrag = (target: Panel, n: number, e: React.MouseEvent) => {
    if (e.button !== 0 || !canEdit) return;
    cableDrag.current = { from: { panelId: target.id, port: n }, sx: e.clientX, sy: e.clientY, moved: false };
  };

  const replacePanel = (updated: Panel) => setPanels(ps => ps.map(p => (p.id === updated.id ? updated : p)));

  const connect = async (a: PortRef, b: PortRef) => {
    if (a.panelId === b.panelId) return;
    const pa = panelById.get(a.panelId), pb = panelById.get(b.panelId);
    if (!pa || !pb) return;
    let front: PortRef, rear: PortRef;
    if (pa.in_front_of_id === pb.id) { front = a; rear = b; }
    else if (pb.in_front_of_id === pa.id) { front = b; rear = a; }
    else {
      toast.error(`${pa.name} and ${pb.name} aren't directly in front of each other — set "In front of" on a panel first${!expanded && hasIntermediate ? ', or expand the view to reach intermediate panels' : ''}`);
      return;
    }
    try {
      const res = await api.connectPatchPorts(topologyId, front.panelId, front.port, rear.port);
      await load();
      toast.success(`Connected ${refName(front)} → ${refName(rear)}`
        + (res?.moved_device_to ? ` — device details moved to ${res.moved_device_to.panel} port ${res.moved_device_to.port}` : ''));
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  const disconnect = async (front: PortRef) => {
    try {
      replacePanel(await api.disconnectPatchPort(topologyId, front.panelId, front.port));
      setTraced(new Set());
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const pickLink = (linkId: number | null) => {
    if (!portForm || !panel || selectedPort == null) return;
    if (linkId == null) { setPortForm({ ...portForm, link_id: null }); return; }
    const l = links.find(x => x.id === linkId);
    if (!l) return;
    // Wan-facing ports document no device side.
    if ((frontsOf.get(panel.id) || []).length > 0) {
      setPortForm({ ...portForm, link_id: linkId, node_id: null, interface: '' });
      return;
    }
    // Device side = the link's endpoint at this location (keep the current
    // device when both ends are here and it's one of them).
    let nid: number | null = null;
    if (portForm.node_id != null && (l.source_node_id === portForm.node_id || l.target_node_id === portForm.node_id)) {
      nid = portForm.node_id;
    } else {
      nid = siteNodeIds.has(l.source_node_id) ? l.source_node_id : l.target_node_id;
    }
    const iface = (l.source_node_id === nid ? l.source_interface : l.target_interface) || '';
    setPortForm({ ...portForm, link_id: linkId, node_id: nid, interface: iface });
  };

  const savePort = async (clear = false) => {
    if (!panel || selectedPort == null || !portForm) return;
    const body = clear
      ? { status: 'unused', label: '', node_id: null, interface: '', link_id: null, notes: '' }
      : portForm;
    try {
      await api.updatePatchPort(topologyId, panel.id, selectedPort, body);
      // The link is mirrored onto jumpered ports on other panels: reload all.
      await load();
      setSelectedPort(null); setPortForm(null); setError('');
    } catch (e) {
      setError(errMsg(e));
    }
  };

  // ------------------------------------------------------------------ panels
  // Panels that could sit behind `self` (same location, no loop).
  const behindOptions = (self: Panel | null) => panels.filter(p => {
    if (self && p.id === self.id) return false;
    let cur: Panel | undefined = p;
    const seen = new Set<number>();
    while (cur && !seen.has(cur.id)) {
      if (self && cur.id === self.id) return false;
      seen.add(cur.id);
      cur = cur.in_front_of_id ? panelById.get(cur.in_front_of_id) : undefined;
    }
    return true;
  });

  const startNewPanel = () => {
    setActiveId('new'); setSelectedPort(null); setPortForm(null); setError('');
    setPanelForm({ ...EMPTY_FORM, name: `PP-${panels.length + 1}` });
  };
  const startEditPanel = (p: Panel) => {
    setActiveId(p.id);
    setEditingPanel(true); setSelectedPort(null); setPortForm(null); setForceNeeded(null);
    setConfirmDelete(false); setError('');
    setPanelForm({
      name: p.name, rack: p.rack, start_u: p.start_u != null ? String(p.start_u) : '',
      height_u: String(p.height_u), rows: String(p.rows), ports_per_row: String(p.ports_per_row),
      notes: p.notes, in_front_of: p.in_front_of_id ? String(p.in_front_of_id) : '',
    });
  };
  const formPayload = () => ({
    name: panelForm.name.trim(), rack: panelForm.rack.trim(),
    start_u: panelForm.start_u.trim() ? parseInt(panelForm.start_u, 10) : null,
    height_u: parseInt(panelForm.height_u, 10) || 1,
    rows: parseInt(panelForm.rows, 10) || 1,
    ports_per_row: parseInt(panelForm.ports_per_row, 10) || 1,
    notes: panelForm.notes,
    in_front_of_id: panelForm.in_front_of ? parseInt(panelForm.in_front_of, 10) : null,
  });
  const createPanel = async () => {
    try {
      const p: Panel = await api.createPatchPanel(topologyId, { location, ...formPayload() });
      toast.success(`Created ${p.name}`);
      await load(p.id);
    } catch (e) {
      setError(errMsg(e));
    }
  };
  const savePanel = async (force = false) => {
    if (!panel) return;
    try {
      await api.updatePatchPanel(topologyId, panel.id, formPayload(), force);
      setEditingPanel(false); setForceNeeded(null); setError('');
      await load(panel.id);
    } catch (e: any) {
      const m = String(e?.message || '');
      if (m.includes('API error 409') && m.includes('"ports"')) {
        try { setForceNeeded(JSON.parse(m.slice(m.indexOf('{'))).detail); return; } catch { /* fallthrough */ }
      }
      setError(errMsg(e));
    }
  };
  const deletePanel = async () => {
    if (!panel) return;
    try {
      await api.deletePatchPanel(topologyId, panel.id);
      toast.success(`Deleted ${panel.name}`);
      setConfirmDelete(false); setEditingPanel(false);
      await load(null);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const counts = countStatuses(visiblePanels);
  const selRef: PortRef | null = panel && selectedPort != null ? { panelId: panel.id, port: selectedPort } : null;
  const selRear = selRef ? portAt(selRef)?.rear_port : undefined;
  const selFront = selRef ? frontRef.get(key(selRef)) : undefined;
  const selPath = selRef ? tracePath(selRef) : [];
  const selPathNames = new Set(selPath.map(refName));
  // Wan-facing (or intermediate) panel: its ports carry the WAN link only.
  const selWanSide = !!panel && (frontsOf.get(panel.id) || []).length > 0;
  const selLinks = selWanSide || selPath.length > 1 ? wanLinks : siteLinks;

  const ROLE_BADGE: Record<string, string> = {
    customer: 'customer-facing', intermediate: 'intermediate', wan: 'wan-facing',
  };

  return (
    // Non-modal: the overlay ignores the pointer so the topology behind can
    // still be panned/zoomed; only the window itself is interactive. Close via
    // Cancel or the X (no click-outside close — it discarded open forms).
    <div className="fixed inset-0 z-50 pointer-events-none flex items-center justify-center p-6">
      <div
        className="pointer-events-auto bg-surface-800 border border-surface-600 rounded-lg shadow-2xl w-[1100px] max-w-full max-h-[88vh] flex flex-col"
        style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}
      >
        {/* Header (drag handle) */}
        <div
          className="flex items-center justify-between px-4 py-3 border-b border-surface-700 cursor-move select-none"
          onMouseDown={e => {
            if ((e.target as HTMLElement).closest('button')) return;
            dragRef.current = { sx: e.clientX, sy: e.clientY, ox: offset.x, oy: offset.y };
          }}
        >
          <div className="flex items-center gap-2 min-w-0">
            <LayoutGrid size={14} className="text-amber-400 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">Patch panels — {location}</div>
              <div className="text-[10px] text-surface-500">
                Documentation only — ports reference links that already exist in the topology; links are unchanged.
                {!canEdit && <span className="ml-1 text-amber-400">Read-only: enter edit mode to make changes.</span>}
              </div>
            </div>
          </div>
          <button className="btn btn-ghost btn-sm p-1" onClick={onClose}><X size={14} /></button>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 px-3 border-b border-surface-700 overflow-x-auto">
          {groups.map(g => (
            <button
              key={g.key}
              className={`px-3 py-1.5 text-[11px] font-medium border-b-2 whitespace-nowrap ${activeGroup?.key === g.key ? 'border-amber-400 text-surface-100' : 'border-transparent text-surface-400 hover:text-surface-200'}`}
              onClick={() => { setActiveId(g.panels[0].id); setSelectedPort(null); setPortForm(null); setEditingPanel(false); setError(''); setTraced(new Set()); }}
            >
              {g.label}
              {g.panels.length > 1 && <span className="ml-1 text-surface-500">({g.panels.length} panels)</span>}
            </button>
          ))}
          {canEdit && (
            <button
              className={`px-3 py-1.5 text-[11px] font-medium border-b-2 flex items-center gap-1 whitespace-nowrap ${activeId === 'new' ? 'border-amber-400 text-surface-100' : 'border-transparent text-surface-400 hover:text-surface-200'}`}
              onClick={startNewPanel}
            >
              <Plus size={11} /> New panel
            </button>
          )}
          {loading && <span className="ml-2 text-[10px] text-surface-500">Loading…</span>}
          {hasIntermediate && (
            <div className="ml-auto flex items-center gap-0.5 text-[10px] bg-surface-900 rounded p-0.5 shrink-0">
              {([false, true] as const).map(v => (
                <button
                  key={String(v)}
                  className={`px-2 py-0.5 rounded ${expanded === v ? 'bg-surface-700 text-surface-100' : 'text-surface-400 hover:text-surface-200'}`}
                  onClick={() => setExpanded(v)}
                  title={v ? 'Show every panel and jumper in the chain' : 'Hide intermediate panels; cables drawn end-to-end'}
                >
                  {v ? 'Expanded' : 'Compressed'}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-1 min-h-0">
          {/* Main area */}
          <div className="flex-1 min-w-0 overflow-auto p-4">
            {activeId === 'new' && (
              <div className="max-w-md">
                <div className="text-xs font-medium mb-2">New patch panel at {location}</div>
                <PanelFields form={panelForm} setForm={setPanelForm} behind={behindOptions(null)} />
                <div className="flex justify-end gap-2 mt-3">
                  {/* Cancel returns to the first panel, or closes when there are none yet. */}
                  <button
                    className="btn btn-ghost btn-sm text-xs"
                    onClick={() => { setError(''); if (panels.length) setActiveId(panels[0].id); else onClose(); }}
                  >
                    Cancel
                  </button>
                  <button className="btn btn-primary btn-sm text-xs" disabled={!canEdit || !panelForm.name.trim()} onClick={createPanel}>
                    Create panel
                  </button>
                </div>
                {error && <div className="text-[10px] text-red-400 mt-2">{error}</div>}
              </div>
            )}

            {activeGroup && (
              <>
                <div ref={stackRef} className="relative inline-block min-w-full">
                  <div className="space-y-8">
                    {visiblePanels.map(pp => {
                      const r = role(pp);
                      const hidden = !expanded && r !== 'intermediate'
                        ? activeGroup.panels.filter(x => role(x) === 'intermediate').length : 0;
                      return (
                        <div key={pp.id}>
                          <div className="flex items-center justify-between mb-1 gap-2 flex-wrap">
                            <div className="text-[11px] text-surface-400">
                              <span className={`font-medium ${pp.id === activeId ? 'text-amber-300' : 'text-surface-200'}`}>{pp.name}</span>
                              {r !== 'standalone' && (
                                <span className={`ml-1.5 text-[9px] px-1 rounded ${r === 'customer' ? 'bg-zinc-600/60 text-zinc-200' : 'bg-indigo-500/25 text-indigo-200'}`}>{ROLE_BADGE[r]}</span>
                              )}
                              {pp.in_front_of_id && <span className="ml-1.5 text-[10px] text-surface-500">in front of {panelById.get(pp.in_front_of_id)?.name}</span>}
                              {' '}· {pp.rows}×{pp.ports_per_row} ({pp.rows * pp.ports_per_row} ports)
                              {r === 'wan' && hidden > 0 && !expanded && (
                                <span className="ml-1.5 text-[10px] text-surface-500">· {hidden} intermediate panel{hidden > 1 ? 's' : ''} hidden</span>
                              )}
                              {pp.notes && <span className="ml-2 text-[10px] text-surface-500">{pp.notes}</span>}
                            </div>
                            {canEdit && (
                              <button className="btn btn-ghost btn-sm text-[11px] flex items-center gap-1" onClick={() => startEditPanel(pp)}>
                                <Pencil size={11} /> Edit panel
                              </button>
                            )}
                          </div>
                          <Faceplate
                            panel={pp}
                            tone={r === 'standalone' || r === 'customer' ? 'front' : 'back'}
                            selectedPort={pp.id === activeId ? selectedPort : null}
                            traced={traced}
                            frontRef={frontRef}
                            chainTip={chainTip}
                            onPick={n => tracePort(pp, n)}
                            onPress={(n, e) => startCableDrag(pp, n, e)}
                            onMenu={(n, e) => setPortMenu({ x: e.clientX, y: e.clientY, panel: pp, port: n })}
                          />
                        </div>
                      );
                    })}
                  </div>
                  {/* Cables over the stack (non-interactive). */}
                  <svg className="absolute inset-0 w-full h-full pointer-events-none overflow-visible">
                    {cablePaths.map((c, i) => (
                      <g key={i}>
                        <path d={c.d} fill="none" stroke={c.traced ? '#f472b6' : '#facc15'}
                          strokeOpacity={c.traced ? 0.95 : 0.6} strokeWidth={c.traced ? 2.5 : 1.5}
                          strokeDasharray={c.label ? '5 3' : undefined} />
                        {c.label && (c.traced || cablePaths.length <= 12) && (
                          <text x={c.lx + 4} y={c.ly} fontSize={8} fill="#a1a1aa">{c.label}</text>
                        )}
                      </g>
                    ))}
                    {dragLine && (
                      <line x1={dragLine.x1} y1={dragLine.y1} x2={dragLine.x2} y2={dragLine.y2}
                        stroke="#f472b6" strokeWidth={2} strokeDasharray="4 3" />
                    )}
                  </svg>
                </div>

                {/* Legend */}
                <div className="flex flex-wrap gap-3 mt-3 text-[10px] text-surface-400">
                  {STATUS_ORDER.map(s => (
                    <span key={s} className="flex items-center gap-1">
                      <span className={`inline-block w-3 h-3 rounded-sm ${STATUS[s].swatch}`} />
                      {STATUS[s].label} <span className="text-surface-500">({counts[s]})</span>
                    </span>
                  ))}
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-2 rounded-full bg-red-500" /> references a deleted link/device
                  </span>
                  {cables.length > 0 && (
                    <span className="flex items-center gap-1">
                      <span className="inline-block w-4 border-t-2 border-yellow-400/70" /> jumper
                      {!expanded && hasIntermediate && <span className="text-surface-500">(dashed = through hidden panels)</span>}
                    </span>
                  )}
                  <span className="basis-full text-surface-500">
                    Left-click a port to trace its path and highlight its link on the topology · right-click a port to edit it
                    {canEdit && ' · drag from a port to a port on the panel directly behind/in front to connect them'}
                  </span>
                </div>
              </>
            )}
          </div>

          {/* Side editor */}
          {panel && (portForm || editingPanel) && (
            <div className="w-[320px] shrink-0 border-l border-surface-700 p-3 overflow-y-auto text-xs">
              {portForm && selectedPort != null && selRef && (
                <div className="space-y-2">
                  <div className="text-xs font-medium">{panel.name} · port {selectedPort}</div>
                  {panel.ports[String(selectedPort)]?.missing || panel.ports[String(selectedPort)]?.dangling ? (
                    <div className="text-[10px] text-red-400">The link or device this port documented was deleted from the topology.</div>
                  ) : null}

                  {selFront && (
                    <div className="text-[10px] bg-surface-900 border border-surface-700 rounded p-2 flex items-center justify-between gap-2">
                      <span>Front jumpered from <span className="text-surface-200">{refName(selFront)}</span> — its WAN link and status are kept in sync with this port.</span>
                      {canEdit && <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => disconnect(selFront)}>Disconnect</button>}
                    </div>
                  )}
                  {selRear ? (
                    <div className="text-[10px] bg-surface-900 border border-surface-700 rounded p-2 flex items-center justify-between gap-2">
                      <span>Rear jumpered to <span className="text-surface-200">{refName({ panelId: panel.in_front_of_id!, port: selRear })}</span> — its WAN link and status are kept in sync with this port.</span>
                      {canEdit && <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => disconnect(selRef)}>Disconnect</button>}
                    </div>
                  ) : null}

                  <Field label={selLinks === wanLinks ? `WAN link (links leaving ${location})` : 'Link (existing links at this location)'}>
                    <select
                      className="input input-sm w-full text-xs"
                      value={portForm.link_id ?? ''}
                      disabled={!canEdit}
                      onChange={e => pickLink(e.target.value ? parseInt(e.target.value, 10) : null)}
                    >
                      <option value="">— none —</option>
                      {/* Keep a non-WAN link that's already set selectable so it still shows. */}
                      {[...selLinks, ...siteLinks.filter(l => l.id === portForm.link_id && !selLinks.includes(l))].map(l => {
                        const elsewhere = (linkUsage.get(l.id) || []).filter(w => !selPathNames.has(w));
                        return (
                          <option key={l.id} value={l.id}>
                            {linkLabel(l)}{elsewhere.length ? `  (on ${elsewhere.join(', ')})` : ''}
                          </option>
                        );
                      })}
                    </select>
                    {selLinks.length === 0 && (
                      <div className="text-[10px] text-surface-500 mt-0.5">
                        {selLinks === wanLinks
                          ? `No links leave ${location} yet — add them on the canvas.`
                          : `No links touch a device at ${location} yet — add them on the canvas.`}
                      </div>
                    )}
                  </Field>

                  {selWanSide ? (
                    <div className="text-[10px] text-surface-500">
                      Wan-facing port: the device is documented on the customer-facing port
                      {selPath.length > 1 && selPath[0].panelId !== panel.id ? <> (<span className="text-surface-300">{refName(selPath[0])}</span>{portAt(selPath[0])?.node_name ? ` → ${portAt(selPath[0])!.node_name}${portAt(selPath[0])!.interface ? ':' + portAt(selPath[0])!.interface : ''}` : ''})</> : null}.
                    </div>
                  ) : (
                    <>
                      <Field label="Device">
                        <select
                          className="input input-sm w-full text-xs"
                          value={portForm.node_id ?? ''}
                          disabled={!canEdit}
                          onChange={e => {
                            const nid = e.target.value ? parseInt(e.target.value, 10) : null;
                            const l = portForm.link_id != null ? links.find(x => x.id === portForm.link_id) : null;
                            const keepLink = l && nid != null && (l.source_node_id === nid || l.target_node_id === nid);
                            setPortForm({ ...portForm, node_id: nid, interface: '', link_id: keepLink ? portForm.link_id : null });
                          }}
                        >
                          <option value="">— none —</option>
                          {deviceOptions.map(n => (
                            <option key={n.id} value={n.id}>{n.name}{n.role === 'customer' ? ' (customer)' : ''}</option>
                          ))}
                        </select>
                      </Field>

                      <Field label="Device interface (pre-patch)">
                        <select
                          className="input input-sm w-full text-xs"
                          value={portForm.interface}
                          disabled={!canEdit || portForm.node_id == null}
                          onChange={e => setPortForm({ ...portForm, interface: e.target.value })}
                        >
                          <option value="">— none —</option>
                          {(nodeById.get(portForm.node_id ?? -1)?.interfaces || []).map((i: any) => (
                            <option key={i.name} value={i.name}>{i.name}{i.speed_gbps ? ` (${i.speed_gbps}G)` : ''}</option>
                          ))}
                        </select>
                      </Field>
                    </>
                  )}

                  <Field label="Status">
                    <select
                      className="input input-sm w-full text-xs"
                      value={portForm.status}
                      disabled={!canEdit}
                      onChange={e => setPortForm({ ...portForm, status: e.target.value as Status })}
                    >
                      {STATUS_ORDER.map(s => <option key={s} value={s}>{STATUS[s].label}</option>)}
                    </select>
                  </Field>

                  <Field label="Port label">
                    <input
                      className="input input-sm w-full text-xs" value={portForm.label} disabled={!canEdit}
                      placeholder={String(selectedPort)}
                      onChange={e => setPortForm({ ...portForm, label: e.target.value })}
                    />
                  </Field>

                  <Field label="Notes / circuit ID">
                    <textarea
                      className="input input-sm w-full text-xs h-16" value={portForm.notes} disabled={!canEdit}
                      onChange={e => setPortForm({ ...portForm, notes: e.target.value })}
                    />
                  </Field>

                  {error && <div className="text-[10px] text-red-400">{error}</div>}
                  <div className="flex justify-between gap-2 pt-1">
                    <button className="btn btn-ghost btn-sm text-xs" disabled={!canEdit} onClick={() => savePort(true)}
                      title="Clears status, label, link, device and notes (jumpers stay)">Clear port</button>
                    <div className="flex gap-2">
                      <button className="btn btn-ghost btn-sm text-xs" onClick={() => { setSelectedPort(null); setPortForm(null); setError(''); }}>Cancel</button>
                      <button className="btn btn-primary btn-sm text-xs" disabled={!canEdit} onClick={() => savePort(false)}>Save</button>
                    </div>
                  </div>
                </div>
              )}

              {editingPanel && (
                <div className="space-y-2">
                  <div className="text-xs font-medium">Edit {panel.name}</div>
                  <PanelFields form={panelForm} setForm={setPanelForm} behind={behindOptions(panel)} />
                  {forceNeeded && (
                    <div className="text-[10px] text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded p-2">
                      {forceNeeded.message}: port{forceNeeded.ports.length > 1 ? 's' : ''} {forceNeeded.ports.join(', ')}.
                      <div className="flex justify-end gap-2 mt-1.5">
                        <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => setForceNeeded(null)}>Go back</button>
                        <button className="btn btn-sm text-[10px] bg-amber-600 hover:bg-amber-500 text-white" onClick={() => savePanel(true)}>Remove & save</button>
                      </div>
                    </div>
                  )}
                  {error && <div className="text-[10px] text-red-400">{error}</div>}
                  <div className="flex justify-end gap-2">
                    <button className="btn btn-ghost btn-sm text-xs" onClick={() => { setEditingPanel(false); setForceNeeded(null); setError(''); }}>Cancel</button>
                    <button className="btn btn-primary btn-sm text-xs" disabled={!panelForm.name.trim()} onClick={() => savePanel(false)}>Save</button>
                  </div>
                  <div className="border-t border-surface-700 pt-2 mt-2">
                    {confirmDelete ? (
                      <div className="text-[10px] text-red-300">
                        Delete {panel.name} and all its port documentation?{(frontsOf.get(panel.id) || []).length > 0 && ' Panels in front of it lose their jumpers.'} (Links are not affected.)
                        <div className="flex justify-end gap-2 mt-1.5">
                          <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => setConfirmDelete(false)}>Cancel</button>
                          <button className="btn btn-sm text-[10px] bg-red-600 hover:bg-red-500 text-white" onClick={deletePanel}>Delete panel</button>
                        </div>
                      </div>
                    ) : (
                      <button className="btn btn-ghost btn-sm text-[11px] text-red-400 flex items-center gap-1" onClick={() => setConfirmDelete(true)}>
                        <Trash2 size={11} /> Delete panel
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        {/* Portaled to <body>: the window's CSS transform (drag offset) would
            otherwise become the containing block of this position:fixed
            menu and push it away from the cursor. */}
        {portMenu && createPortal(
          <ContextMenu
            x={portMenu.x}
            y={portMenu.y}
            onClose={() => setPortMenu(null)}
            items={(() => {
              const ref = { panelId: portMenu.panel.id, port: portMenu.port };
              const rear = portMenu.panel.ports[String(portMenu.port)]?.rear_port;
              const front = frontRef.get(key(ref));
              return [
                canEdit
                  ? { label: 'Edit port', group: 'edit' as const, onClick: () => openPort(portMenu.panel, portMenu.port) }
                  : { label: 'View port', onClick: () => openPort(portMenu.panel, portMenu.port) },
                { label: 'Trace path', onClick: () => tracePort(portMenu.panel, portMenu.port) },
                ...(canEdit && rear && portMenu.panel.in_front_of_id
                  ? [{ label: `Disconnect from ${refName({ panelId: portMenu.panel.in_front_of_id, port: rear })}`, group: 'delete' as const, onClick: () => disconnect(ref) }]
                  : []),
                ...(canEdit && front
                  ? [{ label: `Disconnect from ${refName(front)}`, group: 'delete' as const, onClick: () => disconnect(front) }]
                  : []),
              ];
            })()}
          />,
          document.body,
        )}
        {error && !portForm && !editingPanel && activeId !== 'new' && (
          <div className="px-4 py-2 border-t border-surface-700 text-[11px] text-red-400">{error}</div>
        )}
      </div>
    </div>
  );
}

// Per-status port counts for the legend.
function countStatuses(panels: Panel[]): Record<Status, number> {
  const c: Record<Status, number> = { unused: 0, cabled_free: 0, cabled_reserved: 0, cabled_used: 0 };
  for (const panel of panels) {
    const total = panel.rows * panel.ports_per_row;
    for (let i = 1; i <= total; i++) c[(panel.ports[String(i)]?.status || 'unused') as Status]++;
  }
  return c;
}

function uRange(p: Panel): string {
  if (p.start_u == null) return '';
  return p.height_u > 1 ? `U${p.start_u}–U${p.start_u + p.height_u - 1}` : `U${p.start_u}`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[10px] text-surface-400 mb-0.5">{label}</span>
      {children}
    </label>
  );
}

function PanelFields({ form, setForm, behind }: { form: PanelForm; setForm: (f: PanelForm) => void; behind: Panel[] }) {
  const set = (k: keyof PanelForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });
  const total = (parseInt(form.rows, 10) || 0) * (parseInt(form.ports_per_row, 10) || 0);
  return (
    <div className="grid grid-cols-2 gap-2 text-xs">
      <Field label="Name"><input className="input input-sm w-full text-xs" value={form.name} onChange={set('name')} /></Field>
      <Field label="Rack"><input className="input input-sm w-full text-xs" value={form.rack} onChange={set('rack')} placeholder="e.g. R12" /></Field>
      <Field label="Rack elevation (bottom U)"><input className="input input-sm w-full text-xs" type="number" min={1} value={form.start_u} onChange={set('start_u')} placeholder="e.g. 20" /></Field>
      <Field label="Height (U)"><input className="input input-sm w-full text-xs" type="number" min={1} value={form.height_u} onChange={set('height_u')} /></Field>
      <Field label="Rows"><input className="input input-sm w-full text-xs" type="number" min={1} value={form.rows} onChange={set('rows')} /></Field>
      <Field label="Ports per row"><input className="input input-sm w-full text-xs" type="number" min={1} value={form.ports_per_row} onChange={set('ports_per_row')} /></Field>
      <div className="col-span-2">
        <Field label="In front of (wan-facing panel behind this one)">
          <select className="input input-sm w-full text-xs" value={form.in_front_of} onChange={set('in_front_of')}>
            <option value="">— none (not chained) —</option>
            {behind.map(p => <option key={p.id} value={p.id}>{p.name}{p.rack ? ` · ${p.rack}` : ''}</option>)}
          </select>
        </Field>
      </div>
      <div className="col-span-2">
        <Field label="Notes"><textarea className="input input-sm w-full text-xs h-12" value={form.notes} onChange={set('notes')} /></Field>
        <div className={`text-[10px] mt-0.5 ${total > 288 ? 'text-red-400' : 'text-surface-500'}`}>{total} ports{total > 288 ? ' — max 288' : ''}</div>
      </div>
    </div>
  );
}

/** One panel: mounting ears, a faceplate (gray = customer-facing, indigo
 *  slate = wan-facing / behind another panel), ports grouped in sixes like real keystone
 *  panels, and its rack unit label beside it. */
function Faceplate({ panel, tone, selectedPort, traced, frontRef, chainTip, onPick, onPress, onMenu }: {
  panel: Panel; tone: 'front' | 'back'; selectedPort: number | null; traced: Set<string>;
  frontRef: Map<string, PortRef>; chainTip: (r: PortRef) => string[];
  onPick: (n: number) => void; onPress: (n: number, e: React.MouseEvent) => void;
  onMenu: (n: number, e: React.MouseEvent) => void;
}) {
  const totalH = Math.max(44, panel.rows * 30 + 12);
  const units = uRange(panel);
  const plate = tone === 'front'
    ? 'from-zinc-700 via-zinc-800 to-zinc-700 border-zinc-600'
    : 'from-slate-700 via-indigo-950 to-slate-700 border-indigo-800';
  const ear = tone === 'front'
    ? 'from-zinc-500 to-zinc-600 border-zinc-700'
    : 'from-slate-500 to-indigo-900 border-indigo-900';

  const Ear = ({ side }: { side: 'l' | 'r' }) => (
    <div className={`w-5 shrink-0 bg-gradient-to-b ${ear} border flex flex-col items-center justify-between py-1.5`}
      style={{ borderRadius: side === 'l' ? '4px 0 0 4px' : '0 4px 4px 0' }}>
      <span className="w-2 h-2 rounded-full bg-zinc-900 shadow-inner" />
      <span className="w-2 h-2 rounded-full bg-zinc-900 shadow-inner" />
    </div>
  );

  return (
    <div className="inline-flex items-center gap-2 select-none">
      {/* Rack unit label beside the panel */}
      <div className="flex flex-col items-end text-right shrink-0 min-w-[52px]">
        {panel.rack && <span className="text-[10px] font-mono text-surface-400">{panel.rack}</span>}
        <span className="text-[11px] font-mono font-semibold text-surface-200">{units || '—'}</span>
        {panel.height_u > 1 && <span className="text-[9px] text-surface-500">{panel.height_u}U</span>}
      </div>
      <div className="inline-flex" style={{ height: totalH }}>
        <Ear side="l" />
        <div className={`relative px-3 bg-gradient-to-b ${plate} border-y`} style={{ height: totalH }}>
          <div className="h-full flex flex-col justify-evenly">
            {Array.from({ length: panel.rows }, (_, r) => (
              <div key={r} className="flex items-end gap-1.5">
                {Array.from({ length: panel.ports_per_row }, (_, c) => {
                  const n = r * panel.ports_per_row + c + 1;
                  const p = panel.ports[String(n)];
                  const st = (p?.status || 'unused') as Status;
                  const broken = !!(p?.missing || p?.dangling);
                  const ref = { panelId: panel.id, port: n };
                  const isTraced = traced.has(key(ref));
                  const jumpered = !!p?.rear_port || frontRef.has(key(ref));
                  const tip = [
                    `${panel.name} port ${n}${p?.label ? ` (${p.label})` : ''} — ${STATUS[st].label}`,
                    p?.node_name ? `Device: ${p.node_name}${p.interface ? ':' + p.interface : ''}` : '',
                    p?.link ? `Link → ${p.link.far_node}${p.link.far_interface ? ':' + p.link.far_interface : ''}${p.link.carrier ? ' · ' + p.link.carrier : ''}${p.link.circuit_id ? ' · ' + p.link.circuit_id : ''}` : '',
                    ...chainTip(ref),
                    p?.notes ? `Notes: ${p.notes}` : '',
                    broken ? '⚠ references a deleted link/device' : '',
                  ].filter(Boolean).join('\n');
                  return (
                    <div key={c} className={`flex flex-col items-center ${c > 0 && c % 6 === 0 ? 'ml-2' : ''}`}>
                      <button
                        data-pp={panel.id}
                        data-port={n}
                        title={tip}
                        onMouseDown={e => onPress(n, e)}
                        onClick={() => onPick(n)}
                        onContextMenu={e => { e.preventDefault(); e.stopPropagation(); onMenu(n, e); }}
                        className={`relative w-6 h-5 rounded-[3px] border-2 ${STATUS[st].cls} shadow-inner hover:brightness-125 transition ${selectedPort === n ? 'ring-2 ring-white ring-offset-1 ring-offset-zinc-800' : isTraced ? 'ring-2 ring-pink-400 ring-offset-1 ring-offset-zinc-800' : ''}`}
                      >
                        {/* keystone jack opening */}
                        <span className="absolute left-1/2 top-[3px] -translate-x-1/2 w-3 h-2 rounded-[1px] bg-black/50" />
                        {jumpered && <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full bg-yellow-400 border border-zinc-900" />}
                        {broken && <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-red-500 border border-zinc-900" />}
                      </button>
                      <span className="text-[8px] leading-tight text-zinc-300 font-mono max-w-[28px] truncate">{p?.label || n}</span>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="absolute right-2 top-0.5 text-[8px] font-mono text-zinc-400">{panel.name}</div>
        </div>
        <Ear side="r" />
      </div>
    </div>
  );
}

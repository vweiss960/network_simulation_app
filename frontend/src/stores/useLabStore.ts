import { create } from 'zustand';
import toast from 'react-hot-toast';
import { api, type CaptureInfo, type StopResult } from '../utils/api';
import type { Annotation, Device, DeviceKind, LabState, Link, NetworkSpec, NodeTemplate, Topology } from '../types';
import type { ConsoleTab } from '../components/LabConsoleDrawer';

export type Selection = { type: 'node' | 'link'; id: number } | { type: 'annotation'; id: string } | null;
export type CanvasTool = 'select' | 'group' | 'text' | 'note';
export type Page = 'labs' | 'workspace' | 'images' | 'templates' | 'network' | 'users' | 'lessons' | 'lesson' | 'lessonEdit' | 'modules' | 'module' | 'moduleEdit';

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

// Annotations are saved as a whole list, debounced (drags / typing coalesce).
let annTimer: ReturnType<typeof setTimeout> | null = null;
function saveAnnotations(get: () => { currentTopologyId: number | null; annotations: Annotation[] }) {
  if (annTimer) clearTimeout(annTimer);
  annTimer = setTimeout(() => {
    const { currentTopologyId: tid, annotations } = get();
    if (tid) api.putAnnotations(tid, annotations).catch(e => toast.error(`Couldn't save the drawing: ${errMsg(e)}`));
  }, 400);
}

interface LabStore {
  page: Page;
  features: { ai_enabled: boolean; lab_telemetry_enabled: boolean };
  templates: NodeTemplate[];
  kinds: Record<string, DeviceKind>;
  topologies: Topology[];

  currentTopologyId: number | null;
  topology: Topology | null;
  nodes: Device[];
  links: Link[];
  lab: LabState | null;
  busy: string | null;           // label of a long-running lab action in flight
  selection: Selection;
  annotations: Annotation[];
  tool: CanvasTool;

  consoleTabs: ConsoleTab[];
  activeConsoleId: string | null;

  setPage: (p: Page) => void;
  /** The lesson shown on the 'lesson' / 'lessonEdit' pages (null = a new lesson in the editor). */
  lessonId: number | null;
  openLesson: (id: number) => void;
  editLesson: (id: number | null) => void;
  moduleId: number | null;
  openModule: (id: number) => void;
  editModule: (id: number | null) => void;
  bootstrap: () => Promise<void>;
  loadTemplates: () => Promise<void>;
  loadTopologies: () => Promise<void>;
  /** from: the screen rect of the lab card that was clicked (entry transition). */
  openTopology: (id: number, from?: { x: number; y: number; w: number; h: number; name: string }) => Promise<void>;
  enterFrom: { x: number; y: number; w: number; h: number; name: string } | null;
  clearEnter: () => void;
  /** The canvas has fitted its view and is visible (the entry transition waits for it). */
  canvasReady: boolean;
  setCanvasReady: (v: boolean) => void;
  closeTopology: () => void;
  refreshTopology: () => Promise<void>;
  pollLab: () => Promise<void>;
  select: (s: Selection) => void;
  /** Device / Network node whose settings panel is open (right-click → Edit node). */
  editingNode: number | null;
  setEditingNode: (id: number | null) => void;
  setTool: (t: CanvasTool) => void;
  addAnnotation: (type: Annotation['type'], x: number, y: number) => void;
  updateAnnotations: (patches: Record<string, Partial<Annotation>>) => void;
  deleteAnnotation: (id: string) => void;

  addNode: (templateId: number, x: number, y: number) => Promise<void>;
  updateNode: (id: number, data: Partial<Omit<Device, 'network'>> & { interface_count?: number; password?: string; network?: NetworkSpec; defer?: boolean }) => Promise<boolean>;
  discardPending: (id: number) => Promise<void>;
  moveNodes: (positions: Record<number, [number, number]>) => void;
  deleteNode: (id: number) => Promise<void>;
  connect: (a: number, b: number, aIf?: string, bIf?: string) => Promise<void>;
  updateLink: (id: number, data: Partial<Link>) => Promise<boolean>;
  deleteLink: (id: number) => Promise<void>;

  /** all = power every device on first (Deploy / Start all); else only devices that are on. */
  deploy: (all?: boolean) => Promise<void>;
  destroy: () => Promise<void>;
  /** Save configs on the devices and stop them (kept). Returns the result, or null on error. */
  stopLab: (force?: boolean) => Promise<StopResult | null>;
  nodeAction: (id: number, action: 'start' | 'stop' | 'restart') => Promise<void>;

  openConsole: (nodeId: number, type?: 'console' | 'logs', cli?: string) => void;
  closeConsole: (id: string) => void;
  /** Running packet captures: capture id → link id (drives the cable marker). */
  capturing: Record<string, number>;
  setCapturing: (captureId: string, linkId: number | null) => void;
  openCapture: (linkId: number, filter?: string) => Promise<void>;
  restoreCaptures: () => Promise<void>;
  setActiveConsole: (id: string) => void;
}

export const useLabStore = create<LabStore>((set, get) => ({
  page: 'labs',
  features: { ai_enabled: false, lab_telemetry_enabled: false },
  templates: [],
  kinds: {},
  topologies: [],
  currentTopologyId: null,
  topology: null,
  nodes: [],
  links: [],
  lab: null,
  busy: null,
  selection: null,
  annotations: [],
  tool: 'select',
  editingNode: null,
  enterFrom: null,
  clearEnter: () => set({ enterFrom: null }),
  canvasReady: false,
  setCanvasReady: (canvasReady) => set({ canvasReady }),
  consoleTabs: [],
  capturing: {},
  activeConsoleId: null,

  lessonId: null,
  openLesson: (lessonId) => set({ page: 'lesson', lessonId }),
  editLesson: (lessonId) => set({ page: 'lessonEdit', lessonId }),
  moduleId: null,
  openModule: (moduleId) => set({ page: 'module', moduleId }),
  editModule: (moduleId) => set({ page: 'moduleEdit', moduleId }),

  setPage: (page) => {
    set({ page });
    // Images may have been built / loaded since the palette was fetched.
    if (page === 'workspace' || page === 'templates') get().loadTemplates().catch(() => {});
  },

  bootstrap: async () => {
    const [features, kinds] = await Promise.all([api.getFeatures(), api.listKinds()]);
    set({ features, kinds: Object.fromEntries(kinds.kinds.map(k => [k.kind, k])) });
    await Promise.all([get().loadTemplates(), get().loadTopologies()]);
  },

  loadTemplates: async () => set({ templates: await api.listTemplates(true) }),
  loadTopologies: async () => set({ topologies: await api.listTopologies() }),

  openTopology: async (id, from) => {
    set({ currentTopologyId: id, page: 'workspace', selection: null, consoleTabs: [], activeConsoleId: null,
          enterFrom: from ?? null, canvasReady: false,
          // Drop the previous visit's record: until this lab's data arrives the
          // canvas must not mistake "not loaded yet" for "empty lab".
          topology: null,
          nodes: [], links: [], lab: null, annotations: [], tool: 'select', editingNode: null, capturing: {} });
    await Promise.all([get().refreshTopology(), get().pollLab(), get().loadTemplates().catch(() => {})]);
    get().restoreCaptures();
  },

  closeTopology: () => {
    // A lesson lab goes back to its lesson (the editor for an authoring lab).
    const t = get().topology;
    const back: Partial<LabStore> = t?.lesson_id && t.lesson_role
      ? { page: t.lesson_role === 'authoring' ? 'lessonEdit' : 'lesson', lessonId: t.lesson_id }
      : { page: 'labs' };
    set({ currentTopologyId: null, topology: null, nodes: [], links: [], lab: null, ...back,
          consoleTabs: [], activeConsoleId: null, selection: null });
    get().loadTopologies();
  },

  refreshTopology: async () => {
    const tid = get().currentTopologyId;
    if (!tid) return;
    const [topology, nodes, links, annotations] = await Promise.all([
      api.getTopology(tid), api.listNodes(tid), api.listLinks(tid), api.getAnnotations(tid).catch(() => null)]);
    if (get().currentTopologyId === tid) set({ topology, nodes, links, ...(annotations ? { annotations } : {}) });
  },

  pollLab: async () => {
    const tid = get().currentTopologyId;
    if (!tid) return;
    try {
      const lab = await api.getLabStatus(tid);
      if (get().currentTopologyId === tid) set({ lab });
    } catch { /* transient */ }
  },

  select: (selection) => set({ selection }),
  setEditingNode: (editingNode) => set({ editingNode }),
  setTool: (tool) => set({ tool }),

  addAnnotation: (type, x, y) => {
    const id = Math.random().toString(36).slice(2, 10);
    const size = type === 'group' ? { w: 360, h: 220 } : type === 'note' ? { w: 200, h: 110 } : { w: 0, h: 0 };
    const text = type === 'group' ? 'Group' : type === 'note' ? '' : 'Text';
    const color = type === 'note' ? 'yellow' : 'blue';
    // Group boxes are placed with the click as their top-left corner area.
    const pos = type === 'group' ? { x: x - 40, y: y - 30 } : { x, y };
    const a: Annotation = { id, type, ...pos, ...size, text, color, locked: false };
    set({ annotations: [...get().annotations, a], selection: { type: 'annotation', id }, tool: 'select' });
    saveAnnotations(get);
  },

  updateAnnotations: (patches) => {
    set({ annotations: get().annotations.map(a => (patches[a.id] ? { ...a, ...patches[a.id] } : a)) });
    saveAnnotations(get);
  },

  deleteAnnotation: (id) => {
    set({ annotations: get().annotations.filter(a => a.id !== id),
          selection: get().selection?.type === 'annotation' && get().selection?.id === id ? null : get().selection });
    saveAnnotations(get);
  },

  addNode: async (templateId, x, y) => {
    const tid = get().currentTopologyId!;
    try {
      const n = await api.createNode(tid, { template_id: templateId, x, y });
      set({ nodes: [...get().nodes, n], selection: { type: 'node', id: n.id } });
      get().pollLab();
    } catch (e) { toast.error(errMsg(e)); }
  },

  updateNode: async (id, data) => {
    const tid = get().currentTopologyId!;
    try {
      const n = await api.updateNode(tid, id, data);
      set({ nodes: get().nodes.map(x => (x.id === id ? n : x)) });
      get().pollLab();
      return true;
    } catch (e) { toast.error(errMsg(e)); return false; }
  },

  discardPending: async (id) => {
    const tid = get().currentTopologyId!;
    try {
      const n = await api.discardPending(tid, id);
      set({ nodes: get().nodes.map(x => (x.id === id ? n : x)) });
    } catch (e) { toast.error(errMsg(e)); }
  },

  moveNodes: (positions) => {
    const tid = get().currentTopologyId!;
    set({ nodes: get().nodes.map(n => (positions[n.id] ? { ...n, x: positions[n.id][0], y: positions[n.id][1] } : n)) });
    api.saveNodePositions(tid, positions).catch(e => toast.error(errMsg(e)));
  },

  deleteNode: async (id) => {
    const tid = get().currentTopologyId!;
    try {
      await api.deleteNode(tid, id);
      set({
        nodes: get().nodes.filter(n => n.id !== id),
        links: get().links.filter(l => l.source_node_id !== id && l.target_node_id !== id),
        selection: null,
        editingNode: get().editingNode === id ? null : get().editingNode,
        consoleTabs: get().consoleTabs.filter(t => t.nodeId !== id),
      });
      get().pollLab();
    } catch (e) { toast.error(errMsg(e)); }
  },

  connect: async (a, b, aIf, bIf) => {
    const tid = get().currentTopologyId!;
    try {
      const l = await api.createLink(tid, { source_node_id: a, target_node_id: b, source_interface: aIf, target_interface: bIf });
      set({ links: [...get().links, l] });
      get().pollLab();
    } catch (e) { toast.error(errMsg(e)); }
  },

  updateLink: async (id, data) => {
    const tid = get().currentTopologyId!;
    try {
      const l = await api.updateLink(tid, id, data);
      set({ links: get().links.map(x => (x.id === id ? l : x)) });
      get().pollLab();
      return true;
    } catch (e) { toast.error(errMsg(e)); return false; }
  },

  deleteLink: async (id) => {
    const tid = get().currentTopologyId!;
    try {
      await api.deleteLink(tid, id);
      set({ links: get().links.filter(l => l.id !== id), selection: null });
      get().pollLab();
    } catch (e) { toast.error(errMsg(e)); }
  },

  deploy: async (all = false) => {
    const tid = get().currentTopologyId!;
    const wasStopped = get().lab?.status === 'stopped';
    set({ busy: wasStopped ? 'Starting…' : 'Deploying…' });
    const poll = setInterval(() => get().pollLab(), 3000);
    try {
      const r = await api.deployLab(tid, all);
      const missing = (r.skipped_nodes ?? []).filter((s: { off?: boolean }) => !s.off);
      if (r.started) {
        toast.success('Lab started — devices come back with their saved config');
      } else if (missing.length) {
        toast(`Skipped ${missing.length} device(s) without an image`, { icon: '⚠️' });
      } else {
        toast.success('Lab deployed');
      }
    } catch (e) { toast.error(errMsg(e), { duration: 8000 }); }
    finally {
      clearInterval(poll);
      set({ busy: null });
      get().pollLab();
      get().refreshTopology();     // saved "next restart" changes were applied
    }
  },

  destroy: async () => {
    const tid = get().currentTopologyId!;
    set({ busy: 'Destroying…' });
    try {
      const r = await api.destroyLab(tid);
      toast.success(`Lab destroyed (${r.removed_count} containers removed)`);
      // Captures stop with the lab but stay open to read and download.
      const keep = get().consoleTabs.filter(t => t.type === 'capture');
      set({ consoleTabs: keep, activeConsoleId: keep[keep.length - 1]?.id ?? null, capturing: {} });
    } catch (e) { toast.error(errMsg(e)); }
    finally { set({ busy: null }); get().pollLab(); }
  },

  stopLab: async (force = false) => {
    const tid = get().currentTopologyId!;
    set({ busy: force ? 'Stopping…' : 'Saving configs…' });
    try {
      const r = await api.stopLab(tid, force);
      if (r.stopped) {
        const n = r.saved.length;
        toast.success(`Lab stopped${n ? ` — ${n} device config${n === 1 ? '' : 's'} saved on the devices` : ''}`);
        set({ capturing: {} });
      }
      return r;
    } catch (e) { toast.error(errMsg(e)); return null; }
    finally { set({ busy: null }); get().pollLab(); }
  },

  nodeAction: async (id, action) => {
    const tid = get().currentTopologyId!;
    const name = get().nodes.find(n => n.id === id)?.name ?? 'device';
    const t = toast.loading(`${action[0].toUpperCase()}${action.slice(1)}ing ${name}…`);
    try {
      const r = await api[`${action}Node` as 'startNode'](tid, id);
      if (r?.action === 'recreate') toast.success(`${name}: restarting with its saved changes`, { id: t });
      else if (r?.action === 'deploy') toast.success(`${name} deployed on its own — start other devices when you need them`, { id: t });
      else if (r?.action === 'reconcile') toast.success(`${name} is starting; its cables connect as it comes up`, { id: t });
      else toast.success(`${name}: ${action} done`, { id: t });
    } catch (e) { toast.error(errMsg(e), { id: t }); }
    finally { get().pollLab(); get().refreshTopology(); }
  },

  openConsole: (nodeId, type = 'console', cli) => {
    const node = get().nodes.find(n => n.id === nodeId);
    if (!node) return;
    const id = `${type}:${nodeId}${cli ? ':' + cli : ''}`;
    const tabs = get().consoleTabs;
    if (!tabs.find(t => t.id === id)) {
      set({ consoleTabs: [...tabs, { id, type, nodeId, nodeName: node.name, cli }] });
    }
    set({ activeConsoleId: id });
  },

  closeConsole: (id) => {
    const closing = get().consoleTabs.find(t => t.id === id);
    const tid = get().currentTopologyId;
    if (closing?.type === 'capture' && closing.captureId && tid) {
      // Closing a capture's tab ends it and discards its file.
      api.deleteCapture(tid, closing.captureId).catch(() => {});
      get().setCapturing(closing.captureId, null);
    }
    const tabs = get().consoleTabs.filter(t => t.id !== id);
    set({ consoleTabs: tabs, activeConsoleId: get().activeConsoleId === id ? (tabs[tabs.length - 1]?.id ?? null) : get().activeConsoleId });
  },

  setActiveConsole: (id) => set({ activeConsoleId: id }),

  setCapturing: (cid, linkId) => {
    const cur = get().capturing;
    if (linkId === null ? !(cid in cur) : cur[cid] === linkId) return;
    const next = { ...cur };
    if (linkId === null) delete next[cid]; else next[cid] = linkId;
    set({ capturing: next });
  },

  openCapture: async (linkId, filter = '') => {
    const tid = get().currentTopologyId;
    if (!tid) return;
    try {
      const cap = await api.startCapture(tid, linkId, filter);
      const id = `capture:${cap.id}`;
      set({ consoleTabs: [...get().consoleTabs, captureTab(cap, get().links, get().nodes)], activeConsoleId: id });
      get().setCapturing(cap.id, linkId);
    } catch (e) { toast.error(`Couldn't start the capture: ${errMsg(e)}`); }
  },

  // Captures outlive the page: reopening a lab shows the ones still on the server.
  restoreCaptures: async () => {
    const tid = get().currentTopologyId;
    if (!tid) return;
    try {
      const caps = await api.listCaptures(tid);
      if (get().currentTopologyId !== tid || !caps.length) return;
      const have = new Set(get().consoleTabs.map(t => t.id));
      const add = caps.filter(c => !have.has(`capture:${c.id}`)).reverse().map(c => captureTab(c, get().links, get().nodes));
      set({ consoleTabs: [...get().consoleTabs, ...add] });
      caps.filter(c => c.state === 'running').forEach(c => get().setCapturing(c.id, c.link_id));
    } catch { /* captures are optional */ }
  },
}));

/** Tab for a capture, labelled with the two device names. */
function captureTab(cap: CaptureInfo, links: Link[], nodes: Device[]): ConsoleTab {
  const l = links.find(x => x.id === cap.link_id);
  const name = (id?: number) => nodes.find(n => n.id === id)?.name ?? '?';
  return { id: `capture:${cap.id}`, type: 'capture', nodeId: 0, captureId: cap.id, linkId: cap.link_id,
           nodeName: l ? `${name(l.source_node_id)} ↔ ${name(l.target_node_id)}` : cap.title };
}

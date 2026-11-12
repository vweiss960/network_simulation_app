import { create } from 'zustand';
import toast from 'react-hot-toast';
import { api } from '../utils/api';
import type { Device, DeviceKind, LabState, Link, NetworkSpec, NodeTemplate, Topology } from '../types';
import type { ConsoleTab } from '../components/LabConsoleDrawer';

export type Selection = { type: 'node' | 'link'; id: number } | null;
export type Page = 'labs' | 'workspace' | 'images' | 'templates' | 'network' | 'users';

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

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

  consoleTabs: ConsoleTab[];
  activeConsoleId: string | null;

  setPage: (p: Page) => void;
  bootstrap: () => Promise<void>;
  loadTemplates: () => Promise<void>;
  loadTopologies: () => Promise<void>;
  openTopology: (id: number) => Promise<void>;
  closeTopology: () => void;
  refreshTopology: () => Promise<void>;
  pollLab: () => Promise<void>;
  select: (s: Selection) => void;

  addNode: (templateId: number, x: number, y: number) => Promise<void>;
  updateNode: (id: number, data: Partial<Omit<Device, 'network'>> & { interface_count?: number; password?: string; network?: NetworkSpec }) => Promise<boolean>;
  moveNodes: (positions: Record<number, [number, number]>) => void;
  deleteNode: (id: number) => Promise<void>;
  connect: (a: number, b: number, aIf?: string, bIf?: string) => Promise<void>;
  updateLink: (id: number, data: Partial<Link>) => Promise<boolean>;
  deleteLink: (id: number) => Promise<void>;

  deploy: () => Promise<void>;
  destroy: () => Promise<void>;
  nodeAction: (id: number, action: 'start' | 'stop' | 'restart') => Promise<void>;

  openConsole: (nodeId: number, type?: 'console' | 'logs', cli?: string) => void;
  closeConsole: (id: string) => void;
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
  consoleTabs: [],
  activeConsoleId: null,

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

  openTopology: async (id) => {
    set({ currentTopologyId: id, page: 'workspace', selection: null, consoleTabs: [], activeConsoleId: null,
          nodes: [], links: [], lab: null });
    await Promise.all([get().refreshTopology(), get().pollLab(), get().loadTemplates().catch(() => {})]);
  },

  closeTopology: () => {
    set({ currentTopologyId: null, topology: null, nodes: [], links: [], lab: null, page: 'labs',
          consoleTabs: [], activeConsoleId: null, selection: null });
    get().loadTopologies();
  },

  refreshTopology: async () => {
    const tid = get().currentTopologyId;
    if (!tid) return;
    const [topology, nodes, links] = await Promise.all([api.getTopology(tid), api.listNodes(tid), api.listLinks(tid)]);
    if (get().currentTopologyId === tid) set({ topology, nodes, links });
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

  deploy: async () => {
    const tid = get().currentTopologyId!;
    set({ busy: 'Deploying…' });
    const poll = setInterval(() => get().pollLab(), 3000);
    try {
      const r = await api.deployLab(tid);
      if (r.skipped_nodes?.length) {
        toast(`Skipped ${r.skipped_nodes.length} device(s) without an image`, { icon: '⚠️' });
      } else {
        toast.success('Lab deployed');
      }
    } catch (e) { toast.error(errMsg(e), { duration: 8000 }); }
    finally {
      clearInterval(poll);
      set({ busy: null });
      get().pollLab();
    }
  },

  destroy: async () => {
    const tid = get().currentTopologyId!;
    set({ busy: 'Destroying…' });
    try {
      const r = await api.destroyLab(tid);
      toast.success(`Lab destroyed (${r.removed_count} containers removed)`);
      set({ consoleTabs: [], activeConsoleId: null });
    } catch (e) { toast.error(errMsg(e)); }
    finally { set({ busy: null }); get().pollLab(); }
  },

  nodeAction: async (id, action) => {
    const tid = get().currentTopologyId!;
    const name = get().nodes.find(n => n.id === id)?.name ?? 'device';
    const t = toast.loading(`${action[0].toUpperCase()}${action.slice(1)}ing ${name}…`);
    try {
      await api[`${action}Node` as 'startNode'](tid, id);
      toast.success(`${name}: ${action} done`, { id: t });
    } catch (e) { toast.error(errMsg(e), { id: t }); }
    finally { get().pollLab(); }
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
    const tabs = get().consoleTabs.filter(t => t.id !== id);
    set({ consoleTabs: tabs, activeConsoleId: get().activeConsoleId === id ? (tabs[tabs.length - 1]?.id ?? null) : get().activeConsoleId });
  },

  setActiveConsole: (id) => set({ activeConsoleId: id }),
}));

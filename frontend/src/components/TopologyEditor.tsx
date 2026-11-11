import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  Node,
  Edge,
  BackgroundVariant,
  NodeChange,
  applyNodeChanges,
  useReactFlow,
  ReactFlowProvider,
  Viewport,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { RefreshCw, X } from 'lucide-react';
import GeneratedConfigModal from './GeneratedConfigModal';
import PatchPanelModal from './PatchPanelModal';
import SiteTemplateInstantiateModal from './SiteTemplateInstantiateModal';
import type { SiteTemplate } from '../types';
import { useStore } from '../stores/useStore';
import { api } from '../utils/api';
import type { NodeInstance, LinkData } from '../types';
import { buildActualUtilMap } from '../utils/actualUtilization';
import { effectiveLatency, latencyLabel } from '../utils/latency';
import { getLinkColor, getLinkWidth, resolveViewColors, adHocLayerColor, isManualColor, COST_STEPS, LATENCY_STEPS, LATENCY_NONE_COLOR, CIRCUIT_COST_STEPS, CIRCUIT_COST_NONE_COLOR } from '../utils/colors';
import { xyToLatLng } from '../utils/geo';
import RouterNode from './RouterNode';
import CustomerNode from './CustomerNode';
import SatNode from './SatNode';
import { SAT_DISH, SAT_POP, ORBIT_COLORS, isSatelliteLink, satLinkLabel, satLinkVisible } from '../utils/satellite';
import { customerLinkUtilization } from '../utils/customerFlows';
import LocationNode from './LocationNode';
import CustomEdge from './CustomEdge';
import ContextMenu, { viewOnlyItems, type MenuItem } from './ContextMenu';
import LinkNetemModal from './LinkNetemModal';
import LabConsoleDrawer from './LabConsoleDrawer';
import GeographicCanvas from './GeographicCanvas';
import KeyboardShortcutsModal from './KeyboardShortcutsModal';
import NodeSearchBox from './NodeSearchBox';
import { isTypingTarget, panDirection, makeRightDoubleClickDetector, LOGICAL_SHORTCUTS, GEO_SHORTCUTS, CLOSE_MENUS_EVENT } from '../utils/hotkeys';
import { editSessionErrorMessage } from '../utils/editSession';
import toast from 'react-hot-toast';

const nodeTypes = { router: RouterNode, customer: CustomerNode, location: LocationNode, satdish: SatNode, satpop: SatNode };
// React Flow node type for a node role.
const rfNodeType = (role?: string): string =>
  role === 'customer' ? 'customer' : role === SAT_DISH ? 'satdish' : role === SAT_POP ? 'satpop' : 'router';
const edgeTypes = { custom: CustomEdge };

interface CtxMenu {
  x: number;
  y: number;
  type: 'node' | 'edge' | 'pane';
  id: number;
  flowX?: number;  // flow-space coords for pane clicks
  flowY?: number;
  subMenu?: 'add-node';  // tracks submenu state
  fromKey?: boolean;  // opened with the "n" hotkey → highlight the first item
}

function TopologyEditorInner() {
  const {
    nodes: designNodes, links: designLinks, flows, colorScheme, setColorScheme, linkColors,
    addNode, updateNode, updateNodeLocal, deleteNode, deleteLink, showInterfaceNames, linkLabelFields, groupByLocation,
    setSelectedNode, setSelectedLink, setSidePanel, setPendingLink, setHighlightedFlowPaths, setIsolatedNodeIds,
    simulationResult, highlightedFlowPaths, nodeTemplates, pendingLink, selectedLinkId, selectedNodeId,
    pathBuilder, pathBuilderAddLink, cancelPathBuilder,
    maintenanceNodeIds, maintenanceLinkIds, hidePlanned, hiddenCustomerIds,
    isSimulating, isMaintenanceRunning, isMonteCarlo, isAnalyzingAvailability, agentHighlightNodeIds, agentHighlightLinkIds, customers,
    selectedLinkIds, setSelectedLinkIds,
    topologies, currentTopologyId,
    linkWidthBySpeed, linkColorByUtilization, showSatelliteLinks,
    utilizationView, actualUtilization, setUtilizationView, loadActualUtilization,
    customerUtilEnabled, customerUtilIds, setCustomerUtilEnabled,
  } = useStore();
  const labMode = useStore(s => (s as any).labMode);
  const labView = useStore(s => (s as any).labView);
  const viewMode = useStore(s => (s as any).viewMode) as 'logical' | 'geographic';
  const geoEnabled = useStore(s => s.features?.geo_enabled);
  // Feature flags drive whether config-design-only context menu items
  // (e.g. "View generated config") appear at all.
  const [features, setFeatures] = useState<{ config_design_enabled?: boolean; patch_panels_enabled?: boolean } | null>(null);
  useEffect(() => { api.getFeatures().then(setFeatures).catch(() => setFeatures({})); }, []);
  // Currently-viewing generated config — null means the modal is closed.
  const [viewConfigNode, setViewConfigNode] = useState<{ id: number; name: string } | null>(null);
  // Patch panels (documentation only): the location whose panels are open,
  // plus a per-location panel count so the menu reads View (N) / Create.
  const [patchPanelLocation, setPatchPanelLocation] = useState<string | null>(null);
  const [panelCounts, setPanelCounts] = useState<Record<string, number>>({});
  // Site template being dropped at a canvas point (variables modal open).
  const [siteDrop, setSiteDrop] = useState<{ template: SiteTemplate; x: number; y: number } | null>(null);
  const siteTemplates = useStore(s => s.siteTemplates);
  useEffect(() => { useStore.getState().loadSiteTemplates().catch(() => {}); }, []);
  const autoSimulate = useStore(s => (s as any).autoSimulate) as boolean;
  const setAutoSimulate = useStore(s => (s as any).setAutoSimulate) as (on: boolean) => void;
  const consoleTabs = useStore(s => (s as any).consoleTabs) as any[];
  const activeConsoleTabId = useStore(s => (s as any).activeConsoleTabId) as string | null;
  const openConsoleTab = useStore(s => (s as any).openConsoleTab) as (type: 'console' | 'logs', nodeId: number, nodeName: string, cli?: string) => void;
  const closeConsoleTab = useStore(s => (s as any).closeConsoleTab) as (id: string) => void;
  const setActiveConsoleTab = useStore(s => (s as any).setActiveConsoleTab) as (id: string) => void;
  // While in lab mode, keep a fresh per-container status so each node can
  // show whether it's booting, up, or down. Polled every 5s — lightweight.
  // The Refresh button on the canvas calls the same poll on demand so users
  // don't have to wait for the next tick after stopping/starting a node.
  const [labStatus, setLabStatus] = useState<{ containers: any[] } | null>(null);
  const [labStatusBusy, setLabStatusBusy] = useState(false);
  const fetchLabStatus = useCallback(async () => {
    if (!currentTopologyId) return;
    setLabStatusBusy(true);
    try {
      const r = await api.getLabStatus(currentTopologyId);
      setLabStatus(r as any);
    } catch { /* transient — keep previous status */ }
    finally { setLabStatusBusy(false); }
  }, [currentTopologyId]);
  useEffect(() => {
    if (!labMode || !currentTopologyId) {
      setLabStatus(null);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await fetchLabStatus();
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [labMode, currentTopologyId, fetchLabStatus]);

  // Subset state lives in the store (shared with LabPanel) so canvas clicks
  // and panel checkboxes stay in lockstep without round-tripping via API.
  // We still refetch every 5s in lab mode in case another browser tab edited it.
  const labDeploySubsetArr = useStore(s => (s as any).labDeploySubset) as number[];
  const labSubsetExplicit = useStore(s => (s as any).labDeploySubsetExplicit) as boolean;
  const labSubsetSelectMode = useStore(s => (s as any).labSubsetSelectMode) as boolean;
  const loadLabDeploySubset = useStore(s => (s as any).loadLabDeploySubset) as (id: number) => Promise<void>;
  const toggleLabDeploySubsetNode = useStore(s => (s as any).toggleLabDeploySubsetNode) as (id: number, nid: number, all: number[]) => Promise<void>;
  const labSubset = useMemo(() => new Set(labDeploySubsetArr), [labDeploySubsetArr]);
  // Sub-topology views: the canvas shows the UNION of the active (stacked)
  // views. 'default' = all nodes minus defaultExcludedNodeIds; 'adhoc' = a
  // transient selection; a number = a saved view. The union Set + per-view
  // layer colors are derived after `nodes` is defined (see below).
  const activeViewIds = useStore(s => (s as any).activeViewIds) as Array<number | 'default' | 'adhoc'>;
  const defaultExcludedNodeIds = useStore(s => (s as any).defaultExcludedNodeIds) as number[];
  const adHocView = useStore(s => (s as any).adHocView) as number[] | null;
  const savedViewsForFilter = useStore(s => (s as any).savedViews) as Array<{ id: number; node_ids: number[]; color: string }>;
  const editingViewId = useStore(s => (s as any).editingViewId) as number | 'default' | null;
  // Protected topology: nodes aren't draggable unless the user holds the edit session.
  const canEdit = useStore(s => (s as any).canEditCurrentTopology()) as boolean;
  // While editing one view, non-members render ghosted (relative to that view)
  // so the user can right-click and add them.
  const editingSubTopology = editingViewId != null;
  useEffect(() => {
    if (!labMode || !currentTopologyId) return;
    let cancelled = false;
    const fetchSubset = async () => {
      if (cancelled) return;
      await loadLabDeploySubset(currentTopologyId);
    };
    fetchSubset();
    const id = setInterval(fetchSubset, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [labMode, currentTopologyId, loadLabDeploySubset]);

  // Build node_id → container status map by matching the clab container name
  // computed on the backend's lab view to the container reported by docker.
  const labStatusByNodeId = useMemo<Record<number, any>>(() => {
    if (!labMode || !labView?.nodes || !labStatus?.containers) return {};
    const byName: Record<string, any> = {};
    for (const c of labStatus.containers) {
      if (c?.name) byName[c.name] = c;
    }
    const out: Record<number, any> = {};
    for (const n of labView.nodes as any[]) {
      const c = byName[n.lab_container_name];
      if (c) out[n.id] = c;
    }
    return out;
  }, [labMode, labView, labStatus]);
  // In lab mode the canvas renders the translated lab view instead of the
  // design — same node IDs/positions, but templates and interface names
  // swapped for what'll actually deploy.
  const nodes: NodeInstance[] = (labMode && labView?.nodes?.length) ? (labView.nodes as NodeInstance[]) : designNodes;

  // Resolve a view id to its node-id set.
  const viewNodeIds = useCallback((vid: number | 'default' | 'adhoc'): number[] => {
    if (vid === 'default') {
      const ex = new Set(defaultExcludedNodeIds);
      return nodes.filter(n => !ex.has(n.id)).map(n => n.id);
    }
    if (vid === 'adhoc') return adHocView || [];
    return savedViewsForFilter.find(v => v.id === vid)?.node_ids || [];
  }, [nodes, defaultExcludedNodeIds, adHocView, savedViewsForFilter]);
  // Displayed union of all active views (drives canvas filtering below). The
  // plain Default view (nothing stacked, nothing removed) returns null = "no
  // filter": it short-circuits the visibleNodes/visibleLinks filters and avoids
  // rebuilding a full Set on every node drag (matches the pre-rework behavior).
  const subTopology = useMemo<Set<number> | null>(() => {
    if (activeViewIds.length === 1 && activeViewIds[0] === 'default' && defaultExcludedNodeIds.length === 0) {
      return null;
    }
    const s = new Set<number>();
    for (const vid of activeViewIds) for (const id of viewNodeIds(vid)) s.add(id);
    return s;
  }, [activeViewIds, defaultExcludedNodeIds, viewNodeIds]);
  // The single view being edited (add/remove + ghosting target this, not the union).
  const editingViewSet = useMemo(
    () => (editingViewId == null ? null : new Set(viewNodeIds(editingViewId))),
    [editingViewId, viewNodeIds],
  );
  // Active non-default view layers (in stacking order) with their effective
  // colors. These paint member nodes/links so each stacked view reads as a
  // distinct color (the Default view is neutral and contributes none).
  const activeColoredLayers = useMemo(() => {
    // Stack order = the saved-views list order (drag-reorderable in ViewsPanel).
    // Iterate top→bottom and let later entries overwrite, so the BOTTOM-most
    // active view's color wins where views overlap. Ad-hoc sits at the bottom.
    // Colors are resolved STABLY (by id, collision-free) so reordering the stack
    // changes only which view wins overlaps — never the colors themselves.
    const colorMap = resolveViewColors(savedViewsForFilter);
    const layers: { color: string; set: Set<number> }[] = [];
    savedViewsForFilter.forEach(sv => {
      if (!activeViewIds.includes(sv.id)) return;
      layers.push({ color: colorMap.get(sv.id)!, set: new Set(viewNodeIds(sv.id)) });
    });
    if (activeViewIds.includes('adhoc')) {
      layers.push({ color: adHocLayerColor(colorMap.values()), set: new Set(viewNodeIds('adhoc')) });
    }
    return layers;
  }, [activeViewIds, savedViewsForFilter, viewNodeIds]);
  // Per-node and per-link view color: the topmost (last-stacked) active layer
  // that contains the node (or both of a link's endpoints) wins.
  const nodeViewColor = useMemo(() => {
    const m = new Map<number, string>();
    for (const layer of activeColoredLayers) for (const id of layer.set) m.set(id, layer.color);
    return m;
  }, [activeColoredLayers]);
  // linkViewColor is defined after `links` below (it depends on it).

  // Lab-mode click-to-toggle: only intercepts clicks when the "Select Nodes
  // to Deploy" mode toggle in the Lab panel is on. Routes through the store
  // action so the panel checkboxes update instantly.
  const toggleNodeInSubset = useCallback(async (nodeId: number) => {
    if (!labMode || !currentTopologyId || !labSubsetSelectMode) return;
    const allDeployable = nodes.filter(n => (n as any).role !== 'customer').map(n => n.id);
    await toggleLabDeploySubsetNode(currentTopologyId, nodeId, allDeployable);
  }, [labMode, currentTopologyId, labSubsetSelectMode, nodes, toggleLabDeploySubsetNode]);
  const links: LinkData[] = (labMode && labView?.links) ? (labView.links as LinkData[]) : designLinks;
  // Per-link view color: a link attached to ANY node in a view takes that
  // view's color (topmost active layer wins). Declared after `links`.
  const linkViewColor = useMemo(() => {
    const m = new Map<number, string>();
    if (activeColoredLayers.length === 0) return m;
    for (const layer of activeColoredLayers) {
      for (const l of links) {
        if (layer.set.has(l.source_node_id) || layer.set.has(l.target_node_id)) m.set(l.id, layer.color);
      }
    }
    return m;
  }, [activeColoredLayers, links]);
  const currentTopo = topologies.find(t => t.id === currentTopologyId);
  const isChildTopology = !!currentTopo?.parent_topology_id;

  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const rfInstance = useReactFlow();
  // "Add to topology" from the Site Templates panel: open the variables form
  // and drop the site centred in the current viewport.
  const siteTemplateToPlace = useStore(s => s.siteTemplateToPlace);
  useEffect(() => {
    if (siteTemplateToPlace == null) return;
    useStore.getState().setSiteTemplateToPlace(null);
    const t = useStore.getState().siteTemplates.find(x => x.id === siteTemplateToPlace);
    const el = reactFlowWrapper.current;
    if (!t || !el) return;
    const r = el.getBoundingClientRect();
    const c = rfInstance.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
    // Centre the site's layout (node offsets) on that point; ~44px icons.
    const ns = t.definition.nodes;
    const xs = ns.map(n => n.dx), ys = ns.map(n => n.dy);
    const midX = ns.length ? (Math.min(...xs) + Math.max(...xs)) / 2 + 22 : 0;
    const midY = ns.length ? (Math.min(...ys) + Math.max(...ys)) / 2 + 22 : 0;
    setSiteDrop({ template: t, x: Math.round(c.x - midX), y: Math.round(c.y - midY) });
  }, [siteTemplateToPlace, rfInstance]);
  const isDragging = useRef(false);
  const lastDragPos = useRef<Record<string, { x: number; y: number }>>({});
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const [netemLink, setNetemLink] = useState<{ id: number; label: string } | null>(null);

  // Link drawing state
  const [linkSourceNodeId, setLinkSourceNodeId] = useState<number | null>(null);
  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);
  const isDrawingLink = linkSourceNodeId !== null;
  // Start drawing a link from a node (context menu or the node's hover
  // link button); the next node click picks the target.
  const startLinkFrom = useCallback((nodeId: number) => {
    setLinkSourceNodeId(nodeId);
    toast('Click the target node to connect', { icon: '🔗' });
  }, []);

  // Multi-select state
  const [selectedNodeIds, setSelectedNodeIds] = useState<Set<number>>(new Set());
  const selectedNodeIdsRef = useRef<Set<number>>(new Set());
  selectedNodeIdsRef.current = selectedNodeIds;
  // Ids currently rendered on the canvas (after the view filter / hide toggles).
  // Box-select reads this so it only grabs visible nodes, never ones hidden by
  // the active sub-topology view. Kept in sync below, once visibleNodes exists.
  const visibleNodeIdsRef = useRef<Set<number>>(new Set());
  // Mirror the local multi-selection into the store (one-way) so panels can
  // read it — e.g. NodesPanel's bulk-color section.
  const setMultiSelectedNodeIds = useStore(s => (s as any).setMultiSelectedNodeIds) as (ids: number[]) => void;
  useEffect(() => {
    setMultiSelectedNodeIds(Array.from(selectedNodeIds));
  }, [selectedNodeIds, setMultiSelectedNodeIds]);

  // Track ctrl key globally for edge multi-select (ReactFlow edge events may not carry ctrlKey reliably)
  const ctrlKeyRef = useRef(false);
  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.key === 'Control' || e.key === 'Meta') ctrlKeyRef.current = true; };
    const up = (e: KeyboardEvent) => { if (e.key === 'Control' || e.key === 'Meta') ctrlKeyRef.current = false; };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); };
  }, []);

  // Navigation hotkeys. "?" and "o" work in both views; the rest drive the
  // logical canvas only (GeographicCanvas registers its own map hotkeys).
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [nodeSearchOpen, setNodeSearchOpen] = useState(false);
  const closeNodeSearch = useCallback(() => setNodeSearchOpen(false), []);
  const isGeoView = viewMode === 'geographic' && !!geoEnabled;
  const actionKeysRef = useRef<(key: string) => boolean>(() => false);
  const linkCycleRef = useRef<{ nodeId: number; linkId: number } | null>(null);
  const lastMouseRef = useRef<{ x: number; y: number } | null>(null);
  // Last cursor position (screen coords) if it's over the canvas, else null.
  const cursorInCanvas = () => {
    const r = reactFlowWrapper.current?.getBoundingClientRect(), m = lastMouseRef.current;
    return r && m && m.x >= r.left && m.x <= r.right && m.y >= r.top && m.y <= r.bottom ? m : null;
  };
  // Copy/paste: snapshot of the copied nodes (so paste works even if the
  // originals are later edited or deleted), and the copy actions, re-assigned
  // each render so menu items and hotkeys always see current state.
  const clipboardRef = useRef<NodeInstance[]>([]);
  // Paste is only allowed back into the topology the nodes were copied from
  // (route-reflector peers / customer ids are topology-specific).
  const clipboardTopoRef = useRef<number | null>(null);
  const nodeCopyRef = useRef<{
    copy: () => boolean;
    paste: (at?: { x: number; y: number }) => void;
    duplicate: (ids?: number[]) => void;
  }>({ copy: () => false, paste: () => {}, duplicate: () => {} });
  const closeShortcuts = useCallback(() => setShowShortcuts(false), []);
  // Mount the map on first visit, then keep it (see geoLayer below).
  const [geoVisited, setGeoVisited] = useState(isGeoView);
  if (isGeoView && !geoVisited) setGeoVisited(true);
  // Remember the logical viewport per topology: React Flow unmounts while the
  // geographic view is shown, and would otherwise re-fit on return.
  const savedViewport = currentTopologyId != null ? savedLogicalViewport.get(currentTopologyId) : undefined;
  const onMoveEnd = useCallback((_e: unknown, vp: Viewport) => {
    if (currentTopologyId != null) savedLogicalViewport.set(currentTopologyId, vp);
  }, [currentTopologyId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return;
      // A full-screen editor (e.g. the site template editor) is open over the canvas.
      if (document.querySelector('[data-canvas-keys-off]')) return;
      // Ctrl/Cmd + C / V / D: copy, paste, duplicate nodes (logical view).
      // Ctrl/Cmd+Shift+S: show / hide all satellite links (both views).
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && !e.altKey && e.key.toLowerCase() === 's') {
        e.preventDefault();
        const st = useStore.getState() as any;
        st.toggleShowSatelliteLinks();
        toast(`Satellite links ${st.showSatelliteLinks ? 'hidden' : 'shown'}`, { id: 'sat-toggle' });
        return;
      }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && !isGeoView) {
        const k = e.key.toLowerCase();
        // Leave Ctrl+C alone when the user has text highlighted on the page.
        if (k === 'c' && !window.getSelection()?.toString() && nodeCopyRef.current.copy()) e.preventDefault();
        else if (k === 'v' && clipboardRef.current.length) {
          e.preventDefault();
          const m = cursorInCanvas();
          nodeCopyRef.current.paste(m ? rfInstance.screenToFlowPosition(m) : undefined);
        } else if (k === 'd') {
          e.preventDefault();  // also stops the browser's bookmark dialog
          nodeCopyRef.current.duplicate();
        } else if (k === 'l' || k === 's' || k === 'u') {
          // Link display toggles (same as the Links panel switches). These
          // override the browser's address-bar / save-page / view-source keys.
          e.preventDefault();
          const st = useStore.getState() as any;
          const [label, toggle, flag] = k === 'l' ? ['Link labels', st.toggleInterfaceNames, 'showInterfaceNames']
            : k === 's' ? ['Width by speed', st.toggleLinkWidthBySpeed, 'linkWidthBySpeed']
            : ['Color by utilization', st.toggleLinkColorByUtilization, 'linkColorByUtilization'];
          toggle();
          toast(`${label} ${(useStore.getState() as any)[flag] ? 'on' : 'off'}`, { id: 'link-toggle' });
        }
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '?') { e.preventDefault(); setShowShortcuts(true); return; }
      if (e.key === 'o' || e.key === 'O') {
        if (!geoEnabled || !currentTopologyId) return;
        e.preventDefault();
        (useStore.getState() as any).setViewMode(isGeoView ? 'logical' : 'geographic');
        return;
      }
      if (e.key === 'f' || e.key === 'F') {
        // "Full screen": close the side panel and menus in either view; the
        // map and toolbar close their own groups / popovers on the event.
        // Ignored while a dialog (dimmed full-screen backdrop) is open.
        if (document.querySelector('.fixed.inset-0[class*="bg-black"]')) return;
        e.preventDefault();
        setCtxMenu(null);
        setNodeSearchOpen(false);
        setLinkSourceNodeId(null);
        setMousePos(null);
        setPatchPanelLocation(null);
        useStore.getState().setSidePanel('none');
        window.dispatchEvent(new Event(CLOSE_MENUS_EVENT));
        return;
      }
      if (isGeoView) return;
      const dir = panDirection(e.key);
      if (dir) {
        e.preventDefault();
        const vp = rfInstance.getViewport();
        const step = 100;
        rfInstance.setViewport({ x: vp.x - dir[0] * step, y: vp.y - dir[1] * step, zoom: vp.zoom }, { duration: 150 });
      } else if (e.key === 'PageUp') {
        e.preventDefault(); rfInstance.zoomIn({ duration: 200 });
      } else if (e.key === 'PageDown') {
        e.preventDefault(); rfInstance.zoomOut({ duration: 200 });
      } else if (e.key === 'r' || e.key === 'R') {
        e.preventDefault(); rfInstance.fitView({ duration: 300, padding: 0.1 });
      } else if (e.key === '/') {
        e.preventDefault(); setNodeSearchOpen(true);
      } else if (['e', 'E', 'n', 'N', 'p', 'P', 'l', 'L'].includes(e.key)) {
        if (actionKeysRef.current(e.key.toLowerCase())) e.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isGeoView, geoEnabled, currentTopologyId, rfInstance]);

  // Don't resurrect a stale "/" search when coming back from the map.
  useEffect(() => { if (isGeoView) setNodeSearchOpen(false); }, [isGeoView]);

  // Right double-click zooms out around the cursor (left double-click zoom-in
  // is React Flow's built-in). Capture phase so the second right-click doesn't
  // also open a context menu.
  useEffect(() => {
    const el = reactFlowWrapper.current;
    if (!el || isGeoView) return;
    const isRightDouble = makeRightDoubleClickDetector();
    const onCtx = (e: MouseEvent) => {
      if (!isRightDouble(e)) return;
      e.preventDefault();
      e.stopPropagation();
      setCtxMenu(null);
      const b = el.getBoundingClientRect();
      const px = e.clientX - b.left, py = e.clientY - b.top;
      const { x, y, zoom } = rfInstance.getViewport();
      const z2 = Math.max(0.05, zoom / 1.6);
      // Keep the flow point under the cursor fixed while zooming.
      rfInstance.setViewport({ x: px - (px - x) * (z2 / zoom), y: py - (py - y) * (z2 / zoom), zoom: z2 }, { duration: 200 });
    };
    // Last cursor position, so the "n" hotkey can open the canvas menu there.
    const onMove = (e: MouseEvent) => { lastMouseRef.current = { x: e.clientX, y: e.clientY }; };
    // Forget it on leave, so paste/menu fall back instead of using a stale spot.
    const onLeave = () => { lastMouseRef.current = null; };
    el.addEventListener('contextmenu', onCtx, true);
    el.addEventListener('mousemove', onMove);
    el.addEventListener('mouseleave', onLeave);
    return () => {
      el.removeEventListener('contextmenu', onCtx, true);
      el.removeEventListener('mousemove', onMove);
      el.removeEventListener('mouseleave', onLeave);
    };
  }, [isGeoView, rfInstance]);

  // Auto-center viewport when a node or link is selected from a panel.
  // Skipped in lab mode — there, a click/double-click opens a console and the
  // viewport jump fights with the user's intent to stay put.
  const centerOnNode = (nodeId: number) => {
    const node = nodes.find(n => n.id === nodeId);
    if (node) {
      rfInstance.setCenter(node.x + 22, node.y + 22, { zoom: Math.max(rfInstance.getZoom(), 0.5), duration: 400 });
    }
  };
  useEffect(() => {
    if (labMode) return;
    if (selectedNodeId) centerOnNode(selectedNodeId);
  }, [selectedNodeId, labMode]);

  // Explicit center request from a panel (e.g. clicking a node in the
  // Customers list). Pans without selecting.
  const centerNodeRequest = useStore(s => s.centerNodeRequest);
  useEffect(() => {
    if (centerNodeRequest && !isGeoView) centerOnNode(centerNodeRequest.id);
  }, [centerNodeRequest]);

  useEffect(() => {
    if (labMode) return;
    if (selectedLinkId) {
      const link = links.find(l => l.id === selectedLinkId);
      if (link) {
        const srcNode = nodes.find(n => n.id === link.source_node_id);
        const tgtNode = nodes.find(n => n.id === link.target_node_id);
        if (srcNode && tgtNode) {
          const cx = (srcNode.x + tgtNode.x) / 2;
          const cy = (srcNode.y + tgtNode.y) / 2;
          const zoom = rfInstance.getZoom();
          rfInstance.setCenter(cx, cy, { zoom: Math.max(zoom, 0.5), duration: 400 });
        }
      }
    }
  }, [selectedLinkId, labMode]);

  // Right-click box selection state
  const rightClickStart = useRef<{ x: number; y: number } | null>(null);
  // Set to true the first time a right-click drag exceeds the threshold.
  // Survives mouseup so the contextmenu handler (which fires AFTER mouseup)
  // can suppress the menu when a box-select just happened. Cleared on the
  // next mousedown so unrelated right-clicks aren't affected.
  const wasDraggingRef = useRef(false);
  const [selectionBox, setSelectionBox] = useState<{ startX: number; startY: number; currentX: number; currentY: number } | null>(null);
  const isBoxSelecting = selectionBox !== null;

  // Track mouse in screen coordinates while drawing a link
  useEffect(() => {
    if (!isDrawingLink || !reactFlowWrapper.current) return;
    const el = reactFlowWrapper.current;

    const onMove = (e: MouseEvent) => {
      const bounds = el.getBoundingClientRect();
      setMousePos({ x: e.clientX - bounds.left, y: e.clientY - bounds.top });
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setLinkSourceNodeId(null);
        setMousePos(null);
      }
    };

    el.addEventListener('mousemove', onMove);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      el.removeEventListener('mousemove', onMove);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isDrawingLink]);

  // Right-click drag: box select; right-click (no drag): context menu
  useEffect(() => {
    const el = reactFlowWrapper.current;
    if (!el) return;

    const onMouseDown = (e: MouseEvent) => {
      if (e.button === 2) {
        rightClickStart.current = { x: e.clientX, y: e.clientY };
        wasDraggingRef.current = false;
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!rightClickStart.current) return;
      const dx = e.clientX - rightClickStart.current.x;
      const dy = e.clientY - rightClickStart.current.y;
      if (Math.sqrt(dx * dx + dy * dy) > 5) {
        wasDraggingRef.current = true;
        const bounds = el.getBoundingClientRect();
        setSelectionBox({
          startX: rightClickStart.current.x - bounds.left,
          startY: rightClickStart.current.y - bounds.top,
          currentX: e.clientX - bounds.left,
          currentY: e.clientY - bounds.top,
        });
      }
    };

    const onMouseUp = (e: MouseEvent) => {
      if (e.button !== 2) return;
      // If we were box selecting, finalize selection
      if (selectionBox) {
        // Convert screen box corners to flow coordinates
        const bounds = el.getBoundingClientRect();
        const topLeft = rfInstance.screenToFlowPosition({
          x: Math.min(selectionBox.startX, selectionBox.currentX) + bounds.left,
          y: Math.min(selectionBox.startY, selectionBox.currentY) + bounds.top,
        });
        const bottomRight = rfInstance.screenToFlowPosition({
          x: Math.max(selectionBox.startX, selectionBox.currentX) + bounds.left,
          y: Math.max(selectionBox.startY, selectionBox.currentY) + bounds.top,
        });
        // Find VISIBLE nodes within the box. Nodes hidden by the active
        // sub-topology view aren't on the canvas, so the box must not grab them.
        const visible = visibleNodeIdsRef.current;
        const ids = new Set<number>();
        for (const n of nodes) {
          if (!visible.has(n.id)) continue;
          if (n.x >= topLeft.x && n.x <= bottomRight.x && n.y >= topLeft.y && n.y <= bottomRight.y) {
            ids.add(n.id);
          }
        }
        setSelectedNodeIds(ids);
        setSelectionBox(null);
      }
      rightClickStart.current = null;
    };

    el.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      el.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [selectionBox, nodes, rfInstance]);

  // Handle keyboard events for delete and escape
  const handleKeyDown = useCallback(async (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      if (selectedNodeIdsRef.current.size > 0) setSelectedNodeIds(new Set());
      setSelectedLinkIds([]);
      return;
    }

    if (e.key === 'Delete' || e.key === 'Backspace') {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      // Protected topology not in edit mode → no delete.
      if (!useStore.getState().canEditCurrentTopology()) return;

      const ids = Array.from(selectedNodeIdsRef.current);
      if (ids.length > 0) {
        e.preventDefault();
        e.stopPropagation();
        if (confirm(`Delete ${ids.length} selected node${ids.length > 1 ? 's' : ''} and their links?`)) {
          const idsToDelete = [...ids]; // snapshot
          setSelectedNodeIds(new Set());
          try {
            await useStore.getState().deleteNodes(idsToDelete);
            toast.success(`Deleted ${idsToDelete.length} node${idsToDelete.length > 1 ? 's' : ''}`);
          } catch (err) { console.error('Bulk delete failed:', err); }
        }
      }
    }
  }, []);

  // Build link utilization map — simulated demand, or measured peaks when the
  // Demand/Actual toggle is on an actual period.
  // Only the utilization coloring is driven by measured data; other schemes
  // (cost/GB, criticality) keep reading the simulation result.
  // Customers panel "Show Customer Link Utilization": links carry only the
  // selected customers' (simulated) load, colored as utilization; the rest dim.
  // (Ids of since-deleted customers are ignored.)
  const utilCustomerIds = useMemo(
    () => customerUtilIds.filter(id => customers.some(c => c.id === id)), [customerUtilIds, customers]);
  const customerUtilActive = customerUtilEnabled && utilCustomerIds.length > 0 && !!simulationResult && !labMode;
  const linkScheme = customerUtilActive ? 'utilization' : colorScheme;
  const colorByUtil = linkColorByUtilization || customerUtilActive;
  const showActualUtil = !customerUtilActive && utilizationView !== 'demand' && (colorScheme === 'utilization' || linkColorByUtilization);
  const linkUtilMap = useMemo(() => {
    if (customerUtilActive) return customerLinkUtilization(simulationResult, flows, designNodes, designLinks, utilCustomerIds) as Record<number, any>;
    if (showActualUtil) return buildActualUtilMap(actualUtilization, designLinks, utilizationView) as Record<number, any>;
    const map: Record<number, any> = {};
    if (simulationResult) {
      for (const lu of simulationResult.link_utilizations) map[lu.link_id] = lu;
    }
    return map;
  }, [simulationResult, showActualUtil, utilizationView, actualUtilization, designLinks, customerUtilActive, utilCustomerIds, flows, designNodes]);

  // Actual utilization is matched to links server-side by endpoint interface,
  // so re-derive it when links change (added, re-pointed, interface renamed).
  // Also refresh in Demand view when data exists (the Links panel shows it).
  // 30d <-> 90d needs no refetch: one response carries both periods.
  const actualViewOn = utilizationView !== 'demand';
  useEffect(() => {
    if (!currentTopologyId) return;
    if (!actualViewOn && !useStore.getState().actualUtilization?.metric_rows) return;
    loadActualUtilization().catch(() => {});
  }, [actualViewOn, currentTopologyId, designLinks, loadActualUtilization]);

  // Build highlighted link ID set from flow paths
  const highlightedLinkIds = useMemo(() => {
    if (!highlightedFlowPaths) return new Set<number>();
    const set = new Set<number>();
    // Use link_ids_per_path if available (specific link IDs)
    if (highlightedFlowPaths.link_ids_per_path) {
      for (const linkIds of highlightedFlowPaths.link_ids_per_path) {
        for (const lid of linkIds) set.add(lid);
      }
    }
    return set;
  }, [highlightedFlowPaths]);
  // Per-path highlight colors (path analysis); a link on several paths takes
  // the first one's color. Absent → highlighted links stay amber.
  const highlightColorByLink = useMemo(() => {
    const m = new Map<number, string>();
    const colors = highlightedFlowPaths?.path_colors;
    if (!colors) return m;
    highlightedFlowPaths!.link_ids_per_path.forEach((lids, i) => {
      for (const lid of lids) if (!m.has(lid)) m.set(lid, colors[i % colors.length]);
    });
    return m;
  }, [highlightedFlowPaths]);

  // Build location grouping maps (always computed, only used when groupByLocation is true)
  const locationData = useMemo(() => {
    const locMap: Record<string, typeof nodes> = {};
    const nodeToLoc: Record<number, string> = {};
    for (const n of nodes) {
      const loc = n.location?.trim();
      if (loc) {
        if (!locMap[loc]) locMap[loc] = [];
        locMap[loc].push(n);
        nodeToLoc[n.id] = loc;
      }
    }
    return { locMap, nodeToLoc };
  }, [nodes]);

  // Convert store nodes to React Flow format
  const visibleNodes = useMemo(() => {
    let filtered = nodes;
    if (hidePlanned) filtered = filtered.filter(n => !n.is_planned);
    if (hiddenCustomerIds.size > 0) {
      filtered = filtered.filter(n => {
        if (n.role !== 'customer' || !n.customer_id) return true;
        return !hiddenCustomerIds.has(n.customer_id);
      });
    }
    // Sub-topology view: keep only the selected nodes — UNLESS the user is
    // editing the view, in which case we render everything (non-members get
    // ghosted via outOfView below so they can be right-clicked to add).
    if (subTopology && !editingSubTopology) filtered = filtered.filter(n => subTopology.has(n.id));
    return filtered;
  }, [nodes, hidePlanned, hiddenCustomerIds, subTopology, editingSubTopology]);
  // Keep the box-select ref in sync with what's actually on the canvas.
  const visibleNodeIdSet = useMemo(() => new Set(visibleNodes.map(n => n.id)), [visibleNodes]);
  visibleNodeIdsRef.current = visibleNodeIdSet;
  // Nodes on the highlighted flow path that the views / customer / planned
  // filters hide. They're temporarily drawn as translucent "ghosts" so the
  // whole path is visible, and drop away when the highlight changes or clears.
  // Box-select keeps using visibleNodeIdSet, so ghosts can't be grabbed.
  const pathGhostNodes = useMemo(() => {
    if (groupByLocation || !highlightedFlowPaths?.paths?.length) return [];
    const onPath = new Set<number>();
    for (const p of highlightedFlowPaths.paths) for (const id of p) onPath.add(id);
    return nodes.filter(n => onPath.has(n.id) && !visibleNodeIdSet.has(n.id));
  }, [highlightedFlowPaths, nodes, visibleNodeIdSet, groupByLocation]);
  const visibleLinks = useMemo(() => {
    const hiddenNodeIds = new Set<number>();
    if (hidePlanned) nodes.filter(n => n.is_planned).forEach(n => hiddenNodeIds.add(n.id));
    if (hiddenCustomerIds.size > 0) {
      nodes.forEach(n => {
        if (n.role === 'customer' && n.customer_id && hiddenCustomerIds.has(n.customer_id)) {
          hiddenNodeIds.add(n.id);
        }
      });
    }
    // Sub-topology view: any node NOT in the set is hidden, so links to it
    // drop out via the endpoint check below — leaving only interconnecting
    // links. Skip during edit-mode so non-member nodes' links stay visible
    // (ghosted) for context.
    if (subTopology && !editingSubTopology) nodes.forEach(n => { if (!subTopology.has(n.id)) hiddenNodeIds.add(n.id); });
    if (hiddenNodeIds.size === 0 && !hidePlanned) return links;
    return links.filter(l => {
      if (hidePlanned && l.is_planned) return false;
      return !hiddenNodeIds.has(l.source_node_id) && !hiddenNodeIds.has(l.target_node_id);
    });
  }, [links, nodes, hidePlanned, hiddenCustomerIds, subTopology, editingSubTopology]);
  // Links on the highlighted flow path that are filtered out (see pathGhostNodes);
  // temporarily added to the canvas while the path is highlighted.
  const pathGhostLinks = useMemo(() => {
    if (highlightedLinkIds.size === 0 || visibleLinks === links) return [];
    const shown = new Set(visibleLinks.map(l => l.id));
    return links.filter(l => highlightedLinkIds.has(l.id) && !shown.has(l.id));
  }, [highlightedLinkIds, links, visibleLinks]);

  const customerColorMap = useMemo(() => {
    const map: Record<number, string> = {};
    for (const c of customers) { if (c.color) map[c.id] = c.color; }
    return map;
  }, [customers]);

  // Satellite badges (links are hidden by default, so nodes say what they
  // connect to): dish → its PoPs (orbit · provider → PoP), PoP → dish count.
  // Only satellite links feed the badges; keying on them keeps ordinary link
  // edits from rebuilding every canvas node.
  const satLinksKey = links.filter(isSatelliteLink)
    .map(l => `${l.id}:${l.source_node_id}:${l.target_node_id}:${l.orbit}:${l.carrier}`).join('|');
  const satSummaryByNode = useMemo(() => {
    const m = new Map<number, { links?: { orbit: string; carrier: string; peer: string }[]; dishes?: number }>();
    const nameOf = new Map(nodes.map(n => [n.id, n.name] as const));
    const roleOf = new Map(nodes.map(n => [n.id, n.role] as const));
    for (const n of nodes) {
      if (n.role === SAT_DISH) m.set(n.id, { links: [] });
      else if (n.role === SAT_POP) m.set(n.id, { dishes: 0 });
    }
    for (const l of links) {
      if (!isSatelliteLink(l)) continue;
      const [dish, pop] = roleOf.get(l.source_node_id) === SAT_DISH
        ? [l.source_node_id, l.target_node_id] : [l.target_node_id, l.source_node_id];
      m.get(dish)?.links?.push({ orbit: l.orbit || '', carrier: l.carrier || '', peer: nameOf.get(pop) || '?' });
      const p = m.get(pop);
      if (p) p.dishes = (p.dishes || 0) + 1;
    }
    return m;
  }, [nodes, satLinksKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  const storeRfNodes: Node[] = useMemo(() => {
    // Hover link button on nodes: edit mode on the design canvas only.
    const linkable = canEdit && !labMode && !pathBuilder.active && !isDrawingLink;
    if (groupByLocation) {
      const { locMap, nodeToLoc } = locationData;
      const rfNodes: Node[] = [];

      // Create location group nodes
      for (const [loc, locNodes] of Object.entries(locMap)) {
        // Average position of all nodes in this location
        const avgX = locNodes.reduce((s, n) => s + n.x, 0) / locNodes.length;
        const avgY = locNodes.reduce((s, n) => s + n.y, 0) / locNodes.length;
        rfNodes.push({
          id: `loc-${loc}`,
          type: 'location',
          position: { x: avgX, y: avgY },
          data: { label: loc, count: locNodes.length, nodeId: -1 },
          draggable: true,
        });
      }

      // Add ungrouped nodes (no location set)
      for (const n of nodes) {
        if (!nodeToLoc[n.id]) {
          const isMultiSelected = selectedNodeIds.has(n.id);
          const isInherited = !!n.is_inherited;
          rfNodes.push({
            id: String(n.id),
            type: rfNodeType(n.role) as any,
            position: { x: n.x, y: n.y },
            data: { label: n.name, vendor: n.vendor, model: n.model, nodeId: n.id, multiSelected: isMultiSelected, inMaintenance: maintenanceNodeIds.has(n.id), isPlanned: !!n.is_planned, isInherited, isPlanningLocal: isChildTopology && !isInherited, nodeColor: n.color || '', customerColor: n.customer_id ? customerColorMap[n.customer_id] : undefined, labStatus: labStatusByNodeId[n.id], labMode, labOutOfSubset: labMode && labSubsetExplicit && !labSubset.has(n.id), isSuperRR: !!(n as any).is_super_route_reflector, outOfView: !!(editingViewSet && !editingViewSet.has(n.id)), viewColor: nodeViewColor.get(n.id), satSummary: satSummaryByNode.get(n.id), onStartLink: linkable ? startLinkFrom : undefined },
            // Inherited nodes ARE draggable — the new position is stored as a
            // local override per child topology without touching the parent.
            draggable: !isDrawingLink && !pathBuilder.active && canEdit,
            className: [n.is_planned ? 'planned' : '', isInherited ? 'inherited' : ''].filter(Boolean).join(' ') || undefined,
          });
        }
      }

      return rfNodes;
    }

    // Normal (ungrouped) view
    const ghostIds = new Set(pathGhostNodes.map(n => n.id));
    return [...visibleNodes, ...pathGhostNodes].map(n => {
      let pbRole: string | undefined;
      if (pathBuilder.active) {
        if (n.id === pathBuilder.sourceNodeId) pbRole = 'source';
        else if (n.id === pathBuilder.destNodeId) pbRole = 'dest';
        else if (n.id === pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1]) pbRole = 'head';
        else if (pathBuilder.nodeIds.includes(n.id)) pbRole = 'onpath';
      }
      const isMultiSelected = selectedNodeIds.has(n.id);
      const isInherited = !!n.is_inherited;
      return {
        id: String(n.id),
        type: rfNodeType(n.role) as any,
        position: { x: n.x, y: n.y },
        data: { label: n.name, vendor: n.vendor, model: n.model, nodeId: n.id, pbRole, multiSelected: isMultiSelected, inMaintenance: maintenanceNodeIds.has(n.id), isPlanned: !!n.is_planned, isInherited, isPlanningLocal: isChildTopology && !isInherited, nodeColor: n.color || '', customerColor: n.customer_id ? customerColorMap[n.customer_id] : undefined, labStatus: labStatusByNodeId[n.id], labMode, labOutOfSubset: labMode && labSubsetExplicit && !labSubset.has(n.id), isSuperRR: !!(n as any).is_super_route_reflector, outOfView: !!(editingViewSet && !editingViewSet.has(n.id)), viewColor: nodeViewColor.get(n.id), satSummary: satSummaryByNode.get(n.id), pathGhost: ghostIds.has(n.id), onStartLink: linkable && !ghostIds.has(n.id) ? startLinkFrom : undefined },
        // Inherited nodes ARE draggable — the new position is stored as a
        // local override per child topology without touching the parent.
        draggable: !isDrawingLink && !pathBuilder.active && canEdit,
        className: [n.is_planned ? 'planned' : '', isInherited ? 'inherited' : ''].filter(Boolean).join(' ') || undefined,
      };
    });
  }, [nodes, visibleNodes, pathGhostNodes, isDrawingLink, pathBuilder, selectedNodeIds, groupByLocation, locationData, maintenanceNodeIds, customerColorMap, labMode, labStatusByNodeId, labSubset, labSubsetExplicit, nodeViewColor, editingViewSet, canEdit, startLinkFrom, satSummaryByNode]);

  const [rfNodes, setRfNodes] = useState<Node[]>([]);

  useEffect(() => {
    if (!isDragging.current) setRfNodes(storeRfNodes);
  }, [storeRfNodes]);

  // Compute edges — include temporary pending link as dashed edge
  const rfEdges: Edge[] = useMemo(() => {
    const { nodeToLoc } = locationData;
    // Hidden links on the highlighted path are drawn like any other path link
    // (only their hidden endpoints are ghosted) so the full path stays easy to
    // follow. Ungrouped view only — ghost nodes aren't added when grouping.
    // Satellite links are hidden unless their dish / PoP is selected, they're
    // on a highlighted path, or "Show satellite links" is on.
    const satSelected = new Set<number>(selectedNodeIds);
    if (selectedNodeId != null) satSelected.add(selectedNodeId);
    const satOpts = {
      // Path Builder needs every link clickable.
      showAll: showSatelliteLinks || pathBuilder.active,
      selectedNodeIds: satSelected,
      focusedLinkIds: new Set<number>([
        ...highlightedLinkIds, ...selectedLinkIds, ...agentHighlightLinkIds, ...maintenanceLinkIds,
        ...(selectedLinkId != null ? [selectedLinkId] : []),
      ]),
    };
    const edgeLinks = (groupByLocation ? visibleLinks : [...visibleLinks, ...pathGhostLinks])
      .filter(l => !isSatelliteLink(l) || satLinkVisible(l, satOpts));
    // Compute average circuit cost for the circuit_cost color scheme
    // Average over the normally-visible links only, so a path highlight that
    // pulls in hidden links doesn't recolor the rest of the canvas.
    const linksWithCost = visibleLinks.filter(l => (l.circuit_cost || 0) > 0);
    const avgCircuitCost = linksWithCost.length > 0
      ? linksWithCost.reduce((sum, l) => sum + (l.circuit_cost || 0), 0) / linksWithCost.length
      : 0;

    if (groupByLocation) {
      // Aggregate links between locations
      const locLinkMap: Record<string, { totalBw: number; count: number; linkIds: number[]; latencySum: number; latencyCount: number }> = {};
      for (const l of edgeLinks) {
        const srcLoc = nodeToLoc[l.source_node_id];
        const tgtLoc = nodeToLoc[l.target_node_id];
        const srcKey = srcLoc ? `loc-${srcLoc}` : String(l.source_node_id);
        const tgtKey = tgtLoc ? `loc-${tgtLoc}` : String(l.target_node_id);
        if (srcKey === tgtKey) continue; // intra-location link, skip
        const pairKey = [srcKey, tgtKey].sort().join('|');
        if (!locLinkMap[pairKey]) locLinkMap[pairKey] = { totalBw: 0, count: 0, linkIds: [], latencySum: 0, latencyCount: 0 };
        locLinkMap[pairKey].totalBw += l.bandwidth_gbps;
        const eff = effectiveLatency(l);
        if (eff != null && eff > 0) { locLinkMap[pairKey].latencySum += eff; locLinkMap[pairKey].latencyCount++; }
        locLinkMap[pairKey].count++;
        locLinkMap[pairKey].linkIds.push(l.id);
      }

      // Count parallel aggregated edges per pair for curve offset
      const aggKeys = Object.keys(locLinkMap);
      const edges: Edge[] = aggKeys.map((pairKey) => {
        const [srcKey, tgtKey] = pairKey.split('|');
        const agg = locLinkMap[pairKey];

        // Compute average utilization across all links in this aggregate
        let avgUtil = 0;
        let utilCount = 0;
        for (const lid of agg.linkIds) {
          const u = linkUtilMap[lid];
          if (u) { avgUtil += u.utilization_pct; utilCount++; }
        }
        if (utilCount > 0) avgUtil = Math.round(avgUtil / utilCount);

        // Average latency of the member links that have one set.
        const avgLatency = agg.latencyCount > 0 ? Math.round((agg.latencySum / agg.latencyCount) * 10) / 10 : 0;
        const color = getLinkColor(colorScheme, utilCount > 0 ? { utilization_pct: avgUtil, cost_per_gb: 0 } : undefined, flows.length, linkColors,
          { latency_effective_ms: avgLatency || null, latency_effective_source: avgLatency > 0 ? 'measured' : 'unknown' }, avgCircuitCost);
        const width = getLinkWidth(agg.totalBw, linkWidthBySpeed);

        let label = `${agg.count} link${agg.count > 1 ? 's' : ''}`;
        if (utilCount > 0 && colorScheme === 'utilization') label += ` (${avgUtil}%)`;
        else if (avgLatency > 0 && colorScheme === 'latency') label += ` (~${avgLatency} ms)`;

        return {
          id: `agg-${pairKey}`,
          source: srcKey,
          target: tgtKey,
          type: 'custom',
          label,
          data: { color, width, opacity: 1, curveOffset: 0 },
        };
      });

      return edges;
    }

    // Normal (ungrouped) view
    // Count parallel edges per node pair to offset their curves
    const pairCount: Record<string, number> = {};
    const pairIndex: Record<number, number> = {};
    for (const l of edgeLinks) {
      const key = [Math.min(l.source_node_id, l.target_node_id), Math.max(l.source_node_id, l.target_node_id)].join('-');
      pairCount[key] = (pairCount[key] || 0) + 1;
    }
    const pairSeen: Record<string, number> = {};
    for (const l of edgeLinks) {
      const key = [Math.min(l.source_node_id, l.target_node_id), Math.max(l.source_node_id, l.target_node_id)].join('-');
      pairSeen[key] = (pairSeen[key] || 0);
      pairIndex[l.id] = pairSeen[key];
      pairSeen[key]++;
    }

    const pbLinkSet = new Set(pathBuilder.linkIds);

    const edges: Edge[] = edgeLinks.map(l => {
      const util = linkUtilMap[l.id];
      const isHighlighted = highlightedLinkIds.has(l.id);
      const isSelected = l.id === selectedLinkId;
      const isOnBuiltPath = pathBuilder.active && pbLinkSet.has(l.id);

      // In path builder mode, highlight built path in green, dim others
      // Color-by-utilization color (off in lab mode); used for the line
      // override below and for the % label / over-capacity glow.
      const utilColor = (!labMode && colorByUtil && util) ? getLinkColor('utilization', util, flows.length, linkColors, l, avgCircuitCost) : '';
      let color: string;
      let opacity: number;
      const schemeColor = getLinkColor(linkScheme, util, flows.length, linkColors, l, avgCircuitCost);
      if (pathBuilder.active) {
        if (isOnBuiltPath) {
          color = '#10b981';
          opacity = 1;
        } else {
          const headNode = pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1];
          const isClickable = (l.source_node_id === headNode || l.target_node_id === headNode)
            && !pbLinkSet.has(l.id);
          color = isClickable ? '#a78bfa' : getLinkColor(linkScheme, util, flows.length, linkColors, l, avgCircuitCost);
          opacity = isClickable ? 0.8 : 0.25;
        }
      } else {
        const isInMaintenance = maintenanceLinkIds.has(l.id) || maintenanceNodeIds.has(l.source_node_id) || maintenanceNodeIds.has(l.target_node_id);
        const isPlannedLink = l.is_planned;
        const isAgentHighlightedLink = agentHighlightLinkIds.includes(l.id);
        const isLinkMultiSelected = selectedLinkIds.includes(l.id);
        // Planning-local links (this topology has a parent, and the link is
        // local — not inherited) get a yellow default so the user's planning-
        // layer additions stand out against inherited items. User color wins.
        const planningLocalDefault = (isChildTopology && !l.is_inherited) ? '#eab308' : '';
        // Color by Utilization wins over everything below — including a
        // per-link user color and any active sub-topology view color — so the
        // heat map is never broken by applied colors. Off in lab mode.
        const utilOverride = utilColor;
        // A real user-chosen color (empty / the default grey defer to the view).
        const manualColor = isManualColor(l.color) ? l.color : '';
        // The active sub-topology view's layer color for this link (both
        // endpoints in the view). Applies only when there's no manual color.
        const viewColor = linkViewColor.get(l.id) || '';
        // Lab mode runs against a light grey canvas — use medium grey lines.
        const labModeDefault = labMode ? '#4b5563' : '';
        // Satellite links default to their orbit's color.
        const satColor = isSatelliteLink(l) ? (ORBIT_COLORS[l.orbit || ''] || '#38bdf8') : '';
        const lineColor = utilOverride || manualColor || viewColor || satColor || planningLocalDefault || labModeDefault || '#b1b1b7';
        // Planned links use the existing purple-indigo so they remain visually
        // distinct from planning-layer additions (which are now yellow).
        const plannedColor = l.color || '#818cf8';
        // isHighlighted (= this link sits on the currently-shown flow path)
        // wins over isPlannedLink so a planned link participating in a
        // visualised demand lights up amber + full opacity the same as
        // its non-planned neighbours, instead of staying indigo + half-faded.
        color = isInMaintenance ? '#ef4444' : isAgentHighlightedLink ? '#a855f7' : isHighlighted ? (highlightColorByLink.get(l.id) || '#f59e0b') : isPlannedLink ? plannedColor : isSelected ? '#3b82f6' : isLinkMultiSelected ? '#38bdf8' : lineColor;
        opacity = isInMaintenance ? 0.4 : isHighlighted ? 1 : isPlannedLink ? 0.5 : (highlightedFlowPaths && !isSelected && !isAgentHighlightedLink && !isLinkMultiSelected) ? 0.15 : 1;
        // Customer utilization: links the selected customers don't use fade out.
        if (customerUtilActive && !util && !isHighlighted && !isSelected && !isAgentHighlightedLink && !isLinkMultiSelected) opacity = Math.min(opacity, 0.15);
      }

      // Color-by-utilization styling: every link gets a tinted % label,
      // over-capacity links draw heavier (CustomEdge adds a glow).
      const utilStyle = !pathBuilder.active && !labMode && colorByUtil && !!util;
      const utilPct = util?.utilization_pct ?? 0;
      const width = getLinkWidth(l.bandwidth_gbps, linkWidthBySpeed) + (utilStyle && utilPct >= 100 ? 1.5 : 0);

      let label: string | undefined;
      // In lab mode, suppress all simulation/cost-driven labels — the lab
      // view is about deployment, not analytics.
      if (labMode) {
        label = undefined;
      } else if (util && linkScheme === 'utilization' && !pathBuilder.active) label = `${util.utilization_pct}%`;
      else if (linkScheme === 'cost_per_gb' && !pathBuilder.active) {
        const cc = l.circuit_cost || 0;
        const used = util?.used_gbps || 0;
        label = cc > 0 && used > 0 ? `$${(cc / used).toFixed(2)}/G` : cc > 0 ? '-' : '$0';
      }
      else if (linkScheme === 'circuit_cost' && !pathBuilder.active) label = `$${(l.circuit_cost || 0).toLocaleString()}`;
      else if (linkScheme === 'latency' && !pathBuilder.active) label = effectiveLatency(l) != null ? latencyLabel(l) : undefined;
      // Satellite links always say what they are.
      if (isSatelliteLink(l) && !labMode) label = label ? `${satLinkLabel(l)} · ${label}` : satLinkLabel(l);

      // Compute curve offset for parallel edges
      const key = [Math.min(l.source_node_id, l.target_node_id), Math.max(l.source_node_id, l.target_node_id)].join('-');
      const total = pairCount[key] || 1;
      const idx = pairIndex[l.id] || 0;
      let curveOffset = 0;
      if (total > 1) {
        const spacing = 50;
        curveOffset = (idx - (total - 1) / 2) * spacing;
      } else if (isSatelliteLink(l)) {
        curveOffset = 70;  // a lone satellite link still draws as an arc
      }

      // Edit-mode: a link touching any non-member endpoint is "out of view"
      // and gets ghosted by CustomEdge so member-only links stay prominent.
      const linkOutOfView = !!(editingViewSet
        && (!editingViewSet.has(l.source_node_id) || !editingViewSet.has(l.target_node_id)));
      return {
        id: `e-${l.id}`,
        source: String(l.source_node_id),
        target: String(l.target_node_id),
        type: 'custom',
        animated: isHighlighted,
        label,
        data: {
          linkId: l.id, edgeColor: color, schemeColor, width, opacity, utilStyle, utilPct,
          // Tinted % label only for the utilization scheme; cost schemes keep
          // their own labels even while links are colored by utilization.
          utilLabel: utilStyle && linkScheme === 'utilization',
          utilColor: utilStyle ? utilColor : undefined,
          // Estimated latency labels get a dashed border (vs. measured).
          labelDashed: linkScheme === 'latency' && l.latency_effective_source === 'estimated',
          satellite: isSatelliteLink(l), selected: isSelected, multiSelected: selectedLinkIds.includes(l.id), curveOffset,
          isPlanned: l.is_planned, isInherited: !!l.is_inherited,
          isPlanningLocal: isChildTopology && !l.is_inherited,
          onBuiltPath: isOnBuiltPath,
          isAgentHighlighted: agentHighlightLinkIds.includes(l.id),
          outOfView: linkOutOfView,
          linkLabelFields: showInterfaceNames ? linkLabelFields : [],
          linkData: showInterfaceNames ? l : undefined,
        },
      };
    });

    // Add temporary dashed edge for pending link (target selected, awaiting interfaces)
    if (pendingLink) {
      edges.push({
        id: 'pending-link',
        source: String(pendingLink.source_node_id),
        target: String(pendingLink.target_node_id),
        type: 'custom',
        animated: true,
        label: 'pending...',
        data: { edgeColor: '#f59e0b', width: 2.5, opacity: 0.85, pending: true },
      });
    }

    return edges;
  }, [links, visibleLinks, pathGhostLinks, linkScheme, customerUtilActive, linkColors, linkUtilMap, highlightedFlowPaths, highlightedLinkIds, flows.length, pendingLink, selectedLinkId, selectedLinkIds, pathBuilder, showInterfaceNames, linkLabelFields, groupByLocation, locationData, maintenanceLinkIds, maintenanceNodeIds, agentHighlightLinkIds, linkWidthBySpeed, colorByUtil, linkViewColor, highlightColorByLink, showSatelliteLinks, selectedNodeId, selectedNodeIds]);

  // Source node position in screen coordinates for drawing line
  const sourceScreenPos = useMemo(() => {
    if (!linkSourceNodeId || !reactFlowWrapper.current) return null;
    const n = nodes.find(n => n.id === linkSourceNodeId);
    if (!n) return null;
    // Use measured node width so the line starts at the icon center, not offset by label width
    const rfNode = rfInstance.getNodes().find(rn => rn.id === String(linkSourceNodeId));
    const nodeW = rfNode?.width ?? 44;
    const screenPos = rfInstance.flowToScreenPosition({ x: n.x + nodeW / 2, y: n.y + 22 });
    const bounds = reactFlowWrapper.current.getBoundingClientRect();
    return { x: screenPos.x - bounds.left, y: screenPos.y - bounds.top };
  }, [linkSourceNodeId, nodes, rfInstance, mousePos]); // mousePos dep ensures it updates as viewport changes

  // Track the previous position of the dragged node to compute delta for multi-drag
  const dragPrevPos = useRef<{ x: number; y: number } | null>(null);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    if (isDrawingLink) return; // Don't process node drags while drawing
    // Filter out React Flow's select changes — we manage selection ourselves via selectedNodeIds
    const filteredChanges = changes.filter(c => c.type !== 'select');
    if (filteredChanges.length > 0) {
      setRfNodes(nds => applyNodeChanges(filteredChanges, nds));
    }
    for (const change of changes) {
      if (change.type === 'position') {
        const nodeId = parseInt(change.id);
        if (isNaN(nodeId)) continue;
        if (change.dragging && change.position) {
          isDragging.current = true;
          const selected = selectedNodeIdsRef.current;
          const isMultiDrag = selected.size > 1 && selected.has(nodeId);

          // Initialize dragPrevPos on first drag event from the node's current store position
          if (isMultiDrag && !dragPrevPos.current) {
            const storeNode = nodes.find(n => n.id === nodeId);
            if (storeNode) dragPrevPos.current = { x: storeNode.x, y: storeNode.y };
          }

          if (isMultiDrag && dragPrevPos.current) {
            // Compute delta from previous position of the dragged node
            const dx = change.position.x - dragPrevPos.current.x;
            const dy = change.position.y - dragPrevPos.current.y;
            if (dx !== 0 || dy !== 0) {
              // Move all other selected nodes by the same delta
              setRfNodes(nds => nds.map(n => {
                const nId = parseInt(n.id);
                if (isNaN(nId) || nId === nodeId || !selected.has(nId)) return n;
                const newX = n.position.x + dx;
                const newY = n.position.y + dy;
                const { latitude, longitude } = xyToLatLng(newX, newY);
                updateNodeLocal(nId, { x: newX, y: newY, latitude, longitude });
                return { ...n, position: { x: newX, y: newY } };
              }));
            }
          }

          dragPrevPos.current = { x: change.position.x, y: change.position.y };
          lastDragPos.current[change.id] = { x: change.position.x, y: change.position.y };
          const { latitude, longitude } = xyToLatLng(change.position.x, change.position.y);
          updateNodeLocal(nodeId, { x: change.position.x, y: change.position.y, latitude, longitude });
        } else if (change.dragging === false) {
          isDragging.current = false;
          dragPrevPos.current = null;

          // Save positions for all selected nodes to backend
          const selected = selectedNodeIdsRef.current;
          if (selected.size > 1 && selected.has(nodeId)) {
            for (const id of selected) {
              const pos = lastDragPos.current[String(id)];
              if (pos) {
                updateNode(id, { x: Math.round(pos.x), y: Math.round(pos.y) }).catch(() => {});
                delete lastDragPos.current[String(id)];
              } else {
                // Node was moved via delta, get its current position from store
                const storeNode = nodes.find(n => n.id === id);
                if (storeNode) updateNode(id, { x: Math.round(storeNode.x), y: Math.round(storeNode.y) }).catch(() => {});
              }
            }
          } else {
            const pos = change.position || lastDragPos.current[change.id];
            if (pos) updateNode(nodeId, { x: Math.round(pos.x), y: Math.round(pos.y) }).catch(() => {});
          }
          delete lastDragPos.current[change.id];
        }
      }
    }
  }, [updateNode, updateNodeLocal, isDrawingLink, nodes]);

  // Right-click handlers
  // Helper: was the right-click a drag (pan) or a stationary click?
  const wasRightClickDrag = useCallback((_event: React.MouseEvent) => {
    // mouseup clears rightClickStart before contextmenu fires, so reading
    // the anchor here doesn't work — use the explicit drag flag instead.
    return wasDraggingRef.current;
  }, []);

  const onNodeContextMenu = useCallback((event: React.MouseEvent, node: Node) => {
    event.preventDefault();
    if (wasRightClickDrag(event)) return;
    setCtxMenu({ x: event.clientX, y: event.clientY, type: 'node', id: parseInt(node.id) });
  }, [wasRightClickDrag]);

  const onEdgeContextMenu = useCallback((event: React.MouseEvent, edge: Edge) => {
    event.preventDefault();
    if (wasRightClickDrag(event)) return;
    setCtxMenu({ x: event.clientX, y: event.clientY, type: 'edge', id: parseInt(edge.id.replace('e-', '')) });
  }, [wasRightClickDrag]);

  const onPaneContextMenu = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    if (wasRightClickDrag(event)) return;
    const flowPos = rfInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    setCtxMenu({ x: event.clientX, y: event.clientY, type: 'pane', id: 0, flowX: flowPos.x, flowY: flowPos.y });
  }, [rfInstance, wasRightClickDrag]);

  const nodeName = (id: number) => nodes.find(n => n.id === id)?.name ?? `Node ${id}`;

  // Patch panels are per location: when a node menu opens, fetch how many
  // panels its location has so the item can read "View (N)" vs "Create".
  useEffect(() => {
    if (!ctxMenu || ctxMenu.type !== 'node' || !features?.patch_panels_enabled || !currentTopologyId) return;
    if (isChildTopology || labMode) return;
    const loc = nodes.find(n => n.id === ctxMenu.id)?.location || '';
    if (!loc) return;
    let cancelled = false;
    api.listPatchPanels(currentTopologyId, loc)
      .then(list => { if (!cancelled) setPanelCounts(c => ({ ...c, [loc]: list.length })); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [ctxMenu, features, currentTopologyId, isChildTopology, labMode, nodes]);

  const ctxMenuItems = useMemo(() => {
    if (!ctxMenu) return [];
    if (ctxMenu.type === 'node') {
      const node = nodes.find(n => n.id === ctxMenu.id);
      const isInheritedNode = !!node?.is_inherited;
      const items: MenuItem[] = [];

      // Lab mode: surface live-container shortcuts and skip design-edit options
      if (labMode) {
        const labNode = (labView?.nodes || []).find((n: any) => n.id === ctxMenu.id);
        const cstate = labStatusByNodeId[ctxMenu.id];
        const cname = labNode?.lab_container_name;
        if (cname) {
          const kind = labNode?.lab_kind || '';
          const imageRef: string = (labNode?.lab_image || '').toLowerCase();
          const isFRR = kind === 'linux' && imageRef.includes('frr');
          // Lifecycle actions — surface based on the container's current state
          const cReady = cstate?.ready_state;
          const isRunning = cReady === 'up' || cReady === 'booting' || cReady === 'starting';
          const isDeployed = !!cstate;  // has a container record at all
          const apiRef = api as any;
          if (!isRunning) {
            items.push({
              label: isDeployed ? `Start ${node?.name}` : `Deploy ${node?.name} (only this node)`,
              onClick: async () => {
                try {
                  const r = await apiRef.startLabNode(currentTopologyId, ctxMenu.id);
                  toast.success(`${node?.name} ${r.action}`);
                } catch (e: any) {
                  toast.error(`Start failed: ${parseLabError(e) || e.message}`, { duration: 8000 });
                }
              },
            });
          }
          if (isRunning) {
            items.push({
              label: `Stop ${node?.name} (frees RAM, keeps wiring)`,
              onClick: async () => {
                if (!confirm(`Stop ${node?.name}? Its container stops but wiring/config persist for a fast restart.`)) return;
                try {
                  await apiRef.stopLabNode(currentTopologyId, ctxMenu.id);
                  toast.success(`${node?.name} stopped`);
                } catch (e: any) {
                  toast.error(`Stop failed: ${parseLabError(e) || e.message}`);
                }
              },
              danger: true,
              // Lab lifecycle actions run the live lab, not the saved topology,
              // so they stay with the view items (available in View mode too).
              group: 'view',
            });
            items.push({
              label: `Restart ${node?.name}`,
              onClick: async () => {
                try {
                  await apiRef.restartLabNode(currentTopologyId, ctxMenu.id);
                  toast.success(`${node?.name} restarted`);
                } catch (e: any) {
                  toast.error(`Restart failed: ${parseLabError(e) || e.message}`);
                }
              },
            });
          }
          const sshUser: string | undefined = labNode?.lab_ssh_user;
          const sshPort: number | undefined = labNode?.lab_ssh_host_port;
          // "Connect to device" launches an actual SSH session via the
          // ssh:// URI scheme (OS-registered terminal handler picks it up).
          // Only for kinds that publish SSHD on a host port.
          if (sshUser && sshPort) {
            const host = window.location.hostname;
            const sshUri = `ssh://${sshUser}@${host}:${sshPort}`;
            items.push({
              label: `Connect to ${node?.name} (SSH)`,
              onClick: () => {
                window.open(sshUri, '_self');
                toast(`Opening ${sshUri} — your OS should launch a terminal.`, { duration: 6000 });
              },
            });
            items.push({
              label: `Copy: ssh -p ${sshPort} ${sshUser}@${host}`,
              onClick: () => {
                const cmd = `ssh -p ${sshPort} ${sshUser}@${host}`;
                navigator.clipboard.writeText(cmd).then(() => toast.success(`Copied: ${cmd}`));
              },
            });
          }
          // Pick the most natural CLI entrypoint per platform.
          //   FRR    → vtysh (IOS-like)
          //   XRd CP → xr_cli
          //   SRL    → sr_cli
          //   ceos   → Cli
          //   else   → sh
          let cli = 'sh';
          let cliLabel = 'shell';
          if (isFRR) { cli = 'vtysh'; cliLabel = 'vtysh (FRR CLI)'; }
          else if (kind === 'cisco_xrd') { cli = '/pkg/bin/xr_cli'; cliLabel = 'xr_cli'; }
          else if (kind === 'nokia_srlinux') { cli = 'sr_cli'; cliLabel = 'sr_cli'; }
          else if (kind === 'arista_ceos') { cli = 'Cli'; cliLabel = 'Cli'; }
          // Browser-based console (xterm.js bridged to docker exec over WS)
          items.push({
            label: `Open ${cliLabel} in browser`,
            onClick: () => openConsoleTab('console', ctxMenu.id, node?.name || `node-${ctxMenu.id}`, cli),
          });
          items.push({
            label: `Open logs in browser`,
            onClick: () => openConsoleTab('logs', ctxMenu.id, node?.name || `node-${ctxMenu.id}`),
          });
          items.push({
            label: `Copy: open ${cliLabel} on ${node?.name}`,
            onClick: () => {
              const cmd = `docker exec -it ${cname} ${cli}`;
              navigator.clipboard.writeText(cmd).then(() => toast.success(`Copied: ${cmd}`));
            },
          });
          // Always also offer a raw shell — useful when the CLI is unreachable
          if (cli !== 'sh') {
            items.push({
              label: `Copy: open shell on ${node?.name}`,
              onClick: () => {
                const cmd = `docker exec -it ${cname} sh`;
                navigator.clipboard.writeText(cmd).then(() => toast.success(`Copied: ${cmd}`));
              },
            });
          }
          items.push({
            label: `Copy: docker logs -f`,
            onClick: () => {
              const cmd = `docker logs -f ${cname}`;
              navigator.clipboard.writeText(cmd).then(() => toast.success(`Copied: ${cmd}`));
            },
          });
          // Only offer the internal-IP SSH shortcut for kinds that actually
          // run SSHD (FRR/linux don't). Strip any /CIDR suffix from clab's
          // mgmt IP so the ssh command isn't malformed.
          if (cstate?.mgmt_ip && sshUser) {
            const ip = String(cstate.mgmt_ip).split('/')[0];
            items.push({
              label: `Copy: ssh ${sshUser}@${ip} (from lab host only)`,
              onClick: () => {
                const cmd = `ssh ${sshUser}@${ip}`;
                navigator.clipboard.writeText(cmd).then(() => toast.success(`Copied: ${cmd}`));
              },
            });
          }
        }
        if (cstate?.ready_state) {
          items.push({
            label: `State: ${cstate.ready_state}${cstate.uptime_sec != null ? ` (${cstate.uptime_sec}s)` : ''}`,
            onClick: () => {},
            disabled: true,
          });
        }
        return items;
      }

      if (!isInheritedNode) {
        // Protected + not editing → read-only "View" (no edit form); else "Edit".
        items.push(canEdit
          ? { label: `Edit "${node?.name || 'Node'}"`, group: 'edit', onClick: () => { setSelectedNode(ctxMenu.id, true); setSidePanel('nodes'); } }
          : { label: `View "${node?.name || 'Node'}"`, onClick: () => { setSelectedNode(ctxMenu.id, false); setSidePanel('nodes'); } });
      }
      // Create/edit section order: Edit, Add Link, Duplicate, then the rest.
      if (canEdit && !isDrawingLink) {
        items.push({
          label: 'Add Link from here',
          group: 'edit',
          accent: true,
          onClick: () => startLinkFrom(ctxMenu.id),
        });
      } else if (isDrawingLink) {
        items.push({
          label: 'Cancel link',
          group: 'edit',
          onClick: () => { setLinkSourceNodeId(null); setMousePos(null); },
        });
      }
      if (canEdit) {
        // Right-clicked a node inside a multi-selection → duplicate them all.
        const group = selectedNodeIds.has(ctxMenu.id) && selectedNodeIds.size > 1 ? Array.from(selectedNodeIds) : [ctxMenu.id];
        items.push({
          label: group.length > 1 ? `Duplicate ${group.length} selected nodes` : 'Duplicate node',
          group: 'edit',
          onClick: () => nodeCopyRef.current.duplicate(group),
        });
      }
      // SSH option
      if (node?.mgmt_ip) {
        items.push({
          label: `SSH to ${node.mgmt_ip}`,
          onClick: () => {
            const cmd = `ssh ${node.mgmt_ip}`;
            navigator.clipboard.writeText(cmd).then(() => {
              toast.success(`Copied "${cmd}" to clipboard`);
            }).catch(() => {
              toast(`SSH command: ${cmd}`, { duration: 5000 });
            });
            // Also try opening ssh:// URI (works on some OS/browser combos)
            window.open(`ssh://${node.mgmt_ip}`, '_blank');
          },
        });
      }
      // If multiple nodes are selected, offer bulk delete — only when none of
      // the selected are inherited (inherited items can't be deleted from a
      // child topology). For the single-target case, skip the option entirely
      // when the target is inherited.
      const selectedNodesArr = Array.from(selectedNodeIds)
        .map(id => nodes.find(n => n.id === id))
        .filter((n): n is NonNullable<typeof n> => !!n);
      const anySelectedInherited = selectedNodesArr.some(n => n.is_inherited);
      if (canEdit && selectedNodeIds.size > 1 && selectedNodeIds.has(ctxMenu.id) && !anySelectedInherited) {
        const idsToDelete = Array.from(selectedNodeIds);
        items.push({
          label: `Delete ${idsToDelete.length} Selected Nodes`,
          onClick: async () => {
            if (confirm(`Delete ${idsToDelete.length} selected nodes and their links?`)) {
              setSelectedNodeIds(new Set());
              try {
                await useStore.getState().deleteNodes(idsToDelete);
                toast.success(`Deleted ${idsToDelete.length} nodes`);
              } catch {}
            }
          },
          danger: true,
        });
      } else if (canEdit && !isInheritedNode) {
        items.push(
          { label: 'Delete Node', onClick: () => { if (confirm(`Delete ${node?.name}?`)) { deleteNode(ctxMenu.id); toast.success('Node deleted'); } }, danger: true },
        );
      }
      // Sub-topology: derive a filtered view from the current multi-selection
      // (only meaningful with 2+ nodes; links between them come along for free).
      if (!labMode && selectedNodeIds.size > 1 && selectedNodeIds.has(ctxMenu.id)) {
        const ids = Array.from(selectedNodeIds);
        items.push({
          label: `View ${ids.length} selected as sub-topology`,
          onClick: () => {
            useStore.getState().setAdHocView(ids);
            setSidePanel('views');
          },
        });
        if (canEdit) {
          items.push({
            label: `Save ${ids.length} selected as view…`,
            group: 'edit',
            onClick: async () => {
              const name = window.prompt('Name this sub-topology view:');
              if (!name || !name.trim() || !currentTopologyId) return;
              try {
                await useStore.getState().saveTopologyView(currentTopologyId, name.trim(), ids);
                toast.success(`Saved view "${name.trim()}"`);
                setSidePanel('views');
              } catch (e: any) {
                toast.error(`Save failed: ${e.message || e}`);
              }
            },
          });
        }
      }
      // View edit mode: while editing one view, surface Add / Remove actions in
      // the right-click menu (single-click edits and multi-select batch ops).
      // Membership is relative to the edited view, not the displayed union.
      if (!labMode && canEdit && editingViewSet) {
        const viewLabel = editingViewId === 'default'
          ? 'Default view'
          : (savedViewsForFilter.find(v => v.id === editingViewId) as any)?.name || 'view';
        const inMulti = selectedNodeIds.size > 1 && selectedNodeIds.has(ctxMenu.id);
        const targets = inMulti ? Array.from(selectedNodeIds) : [ctxMenu.id];
        const toAdd = targets.filter(id => !editingViewSet.has(id));
        const toRemove = targets.filter(id => editingViewSet.has(id));
        if (toAdd.length > 0) {
          items.push({
            label: inMulti ? `Add ${toAdd.length} selected to ${viewLabel}` : `Add to ${viewLabel}`,
            group: 'edit',
            onClick: () => {
              useStore.getState().addNodesToEditingView(toAdd);
              toast.success(`Added ${toAdd.length} node${toAdd.length === 1 ? '' : 's'} to ${viewLabel}`);
            },
          });
        }
        if (toRemove.length > 0) {
          items.push({
            label: inMulti ? `Remove ${toRemove.length} selected from ${viewLabel}` : `Remove from ${viewLabel}`,
            group: 'delete',
            onClick: () => {
              useStore.getState().removeNodesFromEditingView(toRemove);
              toast.success(`Removed ${toRemove.length} node${toRemove.length === 1 ? '' : 's'} from ${viewLabel}`);
            },
          });
        }
      }
      // Config Design: surface the last cached render for this device.
      // Only shown when the feature is enabled, in non-lab views, and for
      // router-ish nodes (customer endpoints never get a generated config).
      if (
        features?.config_design_enabled
        && !labMode
        && (node?.role || 'router') !== 'customer'
      ) {
        items.push({
          label: 'View generated config',
          onClick: () => setViewConfigNode({ id: ctxMenu.id, name: node?.name || `node-${ctxMenu.id}` }),
        });
      }
      // Patch panels: documentation of how existing links reach this device
      // through panels at its location (root topologies only for now).
      if (
        features?.patch_panels_enabled
        && !labMode
        && !isChildTopology
        && (node?.role || 'router') !== 'customer'
      ) {
        const loc = node?.location || '';
        const count = loc ? panelCounts[loc] : undefined;
        // Read-only users can view existing panels but get no create prompts.
        if (loc && (canEdit || count !== 0)) {
          items.push({
            label: count ? `View patch panels (${count})` : count === 0 ? 'Create patch panel' : 'Patch panels…',
            group: count === 0 ? 'edit' : 'view',
            onClick: () => setPatchPanelLocation(loc),
          });
        } else if (!loc && canEdit) {
          items.push({ label: 'Patch panels (set device location first)', group: 'edit', onClick: () => {}, disabled: true });
        }
      }
      // Path analysis: use this node as the A or Z end (analysis only, so it
      // stays in the view section and works on read-only topologies too).
      if (!labMode) {
        const setEnd = (which: 'a' | 'z') => {
          useStore.getState().setPathEnd(which, { node_id: ctxMenu.id });
          setSidePanel('paths');
        };
        items.push({ label: 'Path from here (A)', onClick: () => setEnd('a') });
        items.push({ label: 'Path to here (Z)', onClick: () => setEnd('z') });
      }
      // Route-reflector: only meaningful for clients (non-RR) with peers set.
      // Highlights the peer nodes AND draws shortest paths to each, mirroring
      // how flow demands are rendered on the canvas.
      const isRR = !!(node as any)?.is_route_reflector;
      const peerIds: number[] = Array.isArray((node as any)?.rr_peer_ids) ? (node as any).rr_peer_ids : [];
      if (!labMode && !isRR && peerIds.length > 0 && currentTopologyId) {
        items.push({
          label: `Show RR peers (${peerIds.length}) and shortest paths`,
          onClick: async () => {
            try {
              const res = await api.computeShortestPaths(currentTopologyId, ctxMenu.id, peerIds);
              useStore.getState().setAgentHighlights(peerIds, []);
              useStore.getState().setHighlightedFlowPaths(res);
              const reached = res.paths.length;
              const unreached = res.unreachable_target_ids?.length || 0;
              if (unreached > 0) {
                toast(`Reached ${reached} of ${peerIds.length} RR(s); ${unreached} unreachable`, { icon: '⚠️' });
              } else {
                toast.success(`Highlighting ${reached} RR peer path(s)`);
              }
            } catch (e: any) {
              toast.error(`Failed to compute paths: ${e?.message || e}`);
            }
          },
        });
      }
      return items;
    } else if (ctxMenu.type === 'edge') {
      const link = links.find(l => l.id === ctxMenu.id);
      const isInheritedLink = !!link?.is_inherited;
      const label = link ? `${nodeName(link.source_node_id)} ↔ ${nodeName(link.target_node_id)}` : 'Link';
      const items: MenuItem[] = [];
      if (!isInheritedLink) {
        items.push(canEdit
          ? { label: `Edit "${label}"`, group: 'edit', onClick: () => { setSelectedLink(ctxMenu.id, true); setSidePanel('links'); } }
          : { label: `View "${label}"`, onClick: () => { setSelectedLink(ctxMenu.id, false); setSidePanel('links'); } });
        if (canEdit) {
          items.push({ label: 'Delete Link', onClick: () => { if (confirm(`Delete this link?`)) { deleteLink(ctxMenu.id); toast.success('Link deleted'); } }, danger: true });
        }
      }
      // Lab mode: query/modify containerlab link impairments (netem).
      if (labMode) {
        items.push({ label: '⚡ Link degradation…', onClick: () => setNetemLink({ id: ctxMenu.id, label }) });
      }
      return items;
    } else {
      // Pane right-click
      if (ctxMenu.subMenu === 'add-node') {
        // Show template list
        const items: MenuItem[] = [];
        const routerTemplates = nodeTemplates.filter(t => !['customer', SAT_DISH, SAT_POP].includes((t as any).role));
        if (routerTemplates.length > 0) {
          for (const tmpl of routerTemplates) {
            items.push({
              label: `${tmpl.vendor} ${tmpl.model}`,
              group: 'edit',
              onClick: async () => {
                try {
                  await addNode({
                    template_id: tmpl.id,
                    name: `${tmpl.name}-${nodes.length + 1}`,
                    x: ctxMenu.flowX || 0,
                    y: ctxMenu.flowY || 0,
                  });
                  toast.success(`Added ${tmpl.name}`);
                } catch { toast.error('Failed to add node'); }
              },
            });
          }
        } else {
          items.push({ label: 'No templates available', group: 'edit', onClick: () => {}, disabled: true });
        }
        return items;
      }
      // Top-level pane menu — node-creation entries are hidden in lab mode
      // (the canvas there is the deployable view, not the design surface).
      const customerTmpl = nodeTemplates.find(t => (t as any).role === 'customer');
      const paneItems: MenuItem[] = [];
      if (!labMode && canEdit) {
        paneItems.push({
          label: 'Add Node',
          group: 'edit',
          keepOpen: true,
          onClick: () => setCtxMenu({ ...ctxMenu, subMenu: 'add-node' }),
        });
        paneItems.push({
          label: 'Add Site Template',
          group: 'edit',
          onClick: () => {},
          children: siteTemplates.length
            ? siteTemplates.map(t => ({
                label: t.name,
                onClick: () => setSiteDrop({ template: t, x: ctxMenu.flowX || 0, y: ctxMenu.flowY || 0 }),
              }))
            : [{ label: 'None yet — create one in Site Templates', onClick: () => setSidePanel('site-templates') }],
        });
        const clip = clipboardRef.current;
        if (clip.length && clipboardTopoRef.current === currentTopologyId) {
          paneItems.push({
            label: `Paste ${clip.length > 1 ? `${clip.length} nodes` : `"${clip[0].name}"`}`,
            group: 'edit',
            onClick: () => nodeCopyRef.current.paste({ x: ctxMenu.flowX || 0, y: ctxMenu.flowY || 0 }),
          });
        }
        for (const [role, label] of [[SAT_DISH, 'Add Satellite Dish'], [SAT_POP, 'Add Satellite PoP']] as const) {
          const tmpl = nodeTemplates.find(t => (t as any).role === role);
          if (!tmpl) continue;
          paneItems.push({
            label,
            group: 'edit',
            onClick: async () => {
              try {
                await addNode({ template_id: tmpl.id, name: `${tmpl.name.replace(/ /g, '-')}-${nodes.length + 1}`, x: ctxMenu.flowX || 0, y: ctxMenu.flowY || 0 });
                toast.success(`Added ${tmpl.name.toLowerCase()}`);
              } catch (e: any) { toast.error(e?.message?.includes('409') ? 'A node with that name already exists' : 'Failed to add node'); }
            },
          });
        }
        if (customerTmpl) {
          paneItems.push({
            label: 'Add Customer Endpoint',
            group: 'edit',
            onClick: async () => {
              try {
                await addNode({
                  template_id: customerTmpl.id,
                  name: `${customerTmpl.name}-${nodes.length + 1}`,
                  x: ctxMenu.flowX || 0,
                  y: ctxMenu.flowY || 0,
                });
                toast.success('Added customer endpoint');
              } catch (e: any) { toast.error(e?.message?.includes('409') ? 'A node with that name already exists' : 'Failed to add node'); }
            },
          });
        }
      }
      // Route-reflector visualization. In flat mode, highlight every RR in
      // the topology with the same color. In hierarchical mode (any super
      // RR present), super RRs get a distinct color (handled by RouterNode
      // via the isSuperRR flag) AND we draw shortest paths from each
      // non-super RR to its assigned super RRs — same path-rendering
      // pipeline that flow demands use.
      const rrNodes = nodes.filter(n => (n as any).is_route_reflector);
      if (rrNodes.length > 0 && currentTopologyId) {
        const superRRs = rrNodes.filter(n => (n as any).is_super_route_reflector);
        const nonSuperRRs = rrNodes.filter(n => !(n as any).is_super_route_reflector);
        const isHierarchical = superRRs.length > 0;
        const label = isHierarchical
          ? `Show Route Reflectors (${superRRs.length} super, ${nonSuperRRs.length} regular)`
          : `Show Route Reflectors (${rrNodes.length})`;
        paneItems.push({
          label,
          onClick: async () => {
            useStore.getState().setAgentHighlights(rrNodes.map(n => n.id), []);
            useStore.getState().setHighlightedFlowPaths(null);
            if (!isHierarchical || nonSuperRRs.length === 0) {
              toast(`Highlighting ${rrNodes.length} route reflector(s)`, { icon: '✨' });
              return;
            }
            // Hierarchical: walk each non-super RR's rr_peer_ids that point
            // at a super RR, fetch shortest paths, and merge into one
            // FlowPath-shaped object the canvas can render.
            const superIds = new Set(superRRs.map(s => s.id));
            const allPaths: number[][] = [];
            const allLinkIds: number[][] = [];
            let unreached = 0;
            for (const nrr of nonSuperRRs) {
              const targets = ((nrr as any).rr_peer_ids || [])
                .filter((pid: number) => superIds.has(pid));
              if (targets.length === 0) continue;
              try {
                const res = await api.computeShortestPaths(currentTopologyId, nrr.id, targets);
                allPaths.push(...(res.paths || []));
                allLinkIds.push(...(res.link_ids_per_path || []));
                unreached += (res.unreachable_target_ids || []).length;
              } catch { /* skip — partial paths still render */ }
            }
            if (allPaths.length > 0) {
              useStore.getState().setHighlightedFlowPaths({
                flow_id: -1,
                flow_name: 'rr-hierarchy',
                paths: allPaths,
                link_ids_per_path: allLinkIds,
                bandwidth_per_path_gbps: allPaths.map(() => 0),
              } as any);
            }
            const msg = `Highlighting ${superRRs.length} super + ${nonSuperRRs.length} regular RR(s); ${allPaths.length} path(s)`;
            if (unreached > 0) toast(`${msg}, ${unreached} unreachable`, { icon: '⚠️' });
            else toast.success(msg);
          },
        });
      }
      // "Clear highlights" shows up only when there's actually something to clear.
      const hasAgentHighlights = agentHighlightNodeIds.length > 0 || agentHighlightLinkIds.length > 0;
      const hasFlowHighlights = !!highlightedFlowPaths;
      if (hasAgentHighlights || hasFlowHighlights) {
        paneItems.push({
          label: 'Clear highlights',
          onClick: () => {
            useStore.getState().setAgentHighlights([], []);
            useStore.getState().setHighlightedFlowPaths(null);
          },
        });
      }
      return paneItems;
    }
  }, [ctxMenu, nodes, links, nodeTemplates, isDrawingLink, setSelectedNode, setSelectedLink, setSidePanel, deleteNode, deleteLink, addNode, selectedNodeIds, labMode, labView, labStatusByNodeId, openConsoleTab, agentHighlightNodeIds, agentHighlightLinkIds, highlightedFlowPaths, currentTopologyId, features, canEdit, editingViewSet, editingViewId, savedViewsForFilter, isChildTopology, panelCounts, siteTemplates, startLinkFrom]);

  // Read-only (protected, not editing) menus keep only their view section.
  const shownCtxItems = canEdit ? ctxMenuItems : viewOnlyItems(ctxMenuItems);

  const onEdgeClick = useCallback((event: React.MouseEvent, edge: Edge) => {
    if (isDrawingLink) return;

    const linkId = parseInt(edge.id.replace('e-', ''));

    // Path builder: clicking a link adds it to the path
    if (pathBuilder.active) {
      const headNode = pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1];
      const link = links.find(l => l.id === linkId);
      if (!link) return;

      // Determine the next node from this link
      let nextNode: number | null = null;
      if (link.source_node_id === headNode) nextNode = link.target_node_id;
      else if (link.target_node_id === headNode) nextNode = link.source_node_id;
      if (nextNode === null) {
        toast.error('That link doesn\'t connect to the current path head');
        return;
      }
      if (pathBuilder.nodeIds.includes(nextNode) && nextNode !== pathBuilder.destNodeId) {
        toast.error('Cannot create a loop in the path');
        return;
      }

      pathBuilderAddLink(linkId, nextNode);

      if (nextNode === pathBuilder.destNodeId) {
        toast.success('Path complete! Click "Apply Path" in the Flows panel.');
      }
      return;
    }

    // Ctrl+click: toggle multi-select
    if (ctrlKeyRef.current || event.ctrlKey || event.metaKey) {
      let current = [...useStore.getState().selectedLinkIds];
      // Pull in the single-selected link if starting a multi-select
      const singleSelected = useStore.getState().selectedLinkId;
      if (singleSelected && !current.includes(singleSelected)) {
        current.push(singleSelected);
      }
      setSelectedLink(null);
      if (current.includes(linkId)) {
        setSelectedLinkIds(current.filter(id => id !== linkId));
      } else {
        setSelectedLinkIds([...current, linkId]);
      }
      return;
    }

    setSelectedLinkIds([]);
    setPendingLink(null);
    setHighlightedFlowPaths(null);
    setSelectedLink(linkId);
    setSidePanel('links');
  }, [setSelectedLink, setSidePanel, setHighlightedFlowPaths, isDrawingLink, setPendingLink, pathBuilder, links, pathBuilderAddLink]);

  const onNodeClick = useCallback((event: React.MouseEvent, node: Node) => {
    const nodeId = parseInt(node.id);

    // Path builder: ignore node clicks (use link clicks to build path)
    if (pathBuilder.active) return;

    // Lab mode + "Select Nodes to Deploy" is active: a plain click toggles
    // the node's membership in the deploy subset instead of opening the
    // side panel. Disabled by default so click→select-node behavior remains
    // the norm in lab mode.
    if (labMode && labSubsetSelectMode) {
      const designNode = nodes.find(n => n.id === nodeId);
      if (designNode && (designNode as any).role !== 'customer') {
        toggleNodeInSubset(nodeId);
      }
      return;
    }

    // If drawing a link, clicking a node completes the connection
    if (isDrawingLink && linkSourceNodeId !== null && linkSourceNodeId !== nodeId) {
      setPendingLink({ source_node_id: linkSourceNodeId, target_node_id: nodeId });
      setSidePanel('links');
      setLinkSourceNodeId(null);
      setMousePos(null);
      toast('Select interfaces to complete the link', { icon: '🔗' });
      return;
    }

    if (isDrawingLink && linkSourceNodeId === nodeId) {
      setLinkSourceNodeId(null);
      setMousePos(null);
      return;
    }

    // Shift+click: toggle multi-select without opening side panel
    if (event.shiftKey) {
      setSelectedNodeIds(prev => {
        const next = new Set(prev);
        if (next.has(nodeId)) next.delete(nodeId);
        else next.add(nodeId);
        return next;
      });
      return;
    }

    // Normal click: set this as the only selected node and open side panel
    setSelectedNodeIds(new Set([nodeId]));
    setPendingLink(null);
    setHighlightedFlowPaths(null);
    setSelectedNode(nodeId);
    setSidePanel('nodes');
  }, [setSelectedNode, setSidePanel, setHighlightedFlowPaths, isDrawingLink, linkSourceNodeId, setPendingLink, pathBuilder, labMode, labSubsetSelectMode, nodes, toggleNodeInSubset]);

  // Double-click in lab mode opens the browser console immediately. Mirrors
  // the per-kind CLI selection from the right-click menu so the user lands
  // in vtysh on FRR, xr_cli on XRd, etc., instead of plain /bin/sh.
  const onNodeDoubleClick = useCallback((_event: React.MouseEvent, node: Node) => {
    if (!labMode) return;
    const nodeId = parseInt(node.id);
    const labNode = (labView?.nodes || []).find((n: any) => n.id === nodeId);
    if (!labNode?.lab_container_name) return;  // not deployable / no container yet
    const kind = labNode.lab_kind || '';
    const imageRef: string = (labNode.lab_image || '').toLowerCase();
    const isFRR = kind === 'linux' && imageRef.includes('frr');
    let cli = 'sh';
    if (isFRR) cli = 'vtysh';
    else if (kind === 'cisco_xrd') cli = '/pkg/bin/xr_cli';
    else if (kind === 'nokia_srlinux') cli = 'sr_cli';
    else if (kind === 'arista_ceos') cli = 'Cli';
    const nodeName = (node.data as any)?.name || `node-${nodeId}`;
    openConsoleTab('console', nodeId, nodeName, cli);
  }, [labMode, labView, openConsoleTab]);

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(async (event: React.DragEvent) => {
    event.preventDefault();
    const templateId = event.dataTransfer.getData('application/phanero-template');
    if (!templateId) return;
    const position = rfInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    const template = nodeTemplates.find(t => t.id === parseInt(templateId));
    if (!template) return;
    try {
      await addNode({ template_id: template.id, name: `${template.name}-${nodes.length + 1}`, x: position.x, y: position.y });
      toast.success('Node added');
    } catch (e: any) { toast.error(e?.message?.includes('409') ? 'A node with that name already exists — try a different name' : 'Failed to add node'); }
  }, [rfInstance, nodeTemplates, addNode, nodes.length]);

  const onPaneClick = useCallback(() => {
    if (isDrawingLink) {
      setLinkSourceNodeId(null);
      setMousePos(null);
      toast('Link cancelled');
      return;
    }
    setPendingLink(null);
    setSelectedNode(null);
    setSelectedLink(null);
    setHighlightedFlowPaths(null);
    setIsolatedNodeIds([]);
    useStore.getState().setAgentHighlights([], []);
    setCtxMenu(null);
    setSelectedNodeIds(new Set());
    setSelectedLinkIds([]);
  }, [setSelectedNode, setSelectedLink, setHighlightedFlowPaths, setIsolatedNodeIds, isDrawingLink, setPendingLink]);

  // Geographic view replaces the React Flow canvas entirely with the
  // MapLibre map (the globe projection can't host a flat React Flow plane).
  // All hooks above run unconditionally, so this branch is safe.
  // Once opened, the map stays mounted (just hidden) while in the logical view,
  // so switching back is instant — no style/tile reload or globe re-init. It
  // must sit at the same tree position in both returns below or React would
  // remount it.
  // Nodes the copy actions apply to: the box/shift multi-selection if any,
  // otherwise the single selected node.
  // selectedNodeIds isn't cleared when a link is clicked or a node is picked
  // from a panel/search, so only trust it when it's a real multi-selection or
  // nothing else is selected.
  const copySources = (ids?: number[]): NodeInstance[] => {
    const { selectedNodeId: single, selectedLinkId } = useStore.getState();
    const sel = ids ?? (selectedLinkId != null ? []
      : selectedNodeIds.size > 1 || single == null ? Array.from(selectedNodeIds)
      : [single]);
    return sel.map(id => nodes.find(n => n.id === id)).filter((n): n is NodeInstance => !!n);
  };
  // Create copies of `sources`: same settings, a "-copy" name, offset 60px
  // (or placed so the first copy lands at `at`, keeping the group's layout).
  // Not copied: links, management IP, Prometheus id, and any custom attribute
  // flagged unique-per-topology (each would collide with the original).
  const createCopies = async (sources: NodeInstance[], at?: { x: number; y: number }) => {
    if (!sources.length) return;
    if (!canEdit) { toast('Enter edit mode to paste nodes'); return; }
    let uniqueKeys: string[] = [];
    try { uniqueKeys = (await api.listDeviceAttrDefs()).filter(d => d.unique_per_topology).map(d => d.key); }
    catch { /* attribute definitions unavailable — nothing to strip */ }
    const taken = new Set(useStore.getState().nodes.map(n => n.name.toLowerCase()));
    const copyName = (name: string) => {
      const base = `${name.replace(/-copy\d*$/, '')}-copy`;
      let candidate = base;
      for (let i = 2; taken.has(candidate.toLowerCase()); i++) candidate = `${base}${i}`;
      taken.add(candidate.toLowerCase());
      return candidate;
    };
    const snap = (v: number) => Math.round(v / 20) * 20;
    const dx = at ? at.x - sources[0].x : 60, dy = at ? at.y - sources[0].y : 60;
    const created: number[] = [];
    for (const n of sources) {
      const attrs = { ...((n as any).custom_attrs || {}) };
      for (const k of uniqueKeys) delete attrs[k];
      try {
        const copy = await addNode({
          template_id: n.template_id, name: copyName(n.name),
          vendor: n.vendor, model: n.model, platform: n.platform,
          x: snap(n.x + dx), y: snap(n.y + dy),
          geo_latitude: n.geo_latitude ?? null, geo_longitude: n.geo_longitude ?? null,
          interfaces: n.interfaces, mtbf_hours: n.mtbf_hours, mttr_hours: n.mttr_hours,
          capacity_gbps: n.capacity_gbps, location: n.location, is_planned: n.is_planned,
          role: n.role, customer_id: n.customer_id, color: n.color,
          is_route_reflector: !!n.is_route_reflector, is_super_route_reflector: !!n.is_super_route_reflector,
          rr_peer_ids: n.rr_peer_ids || [], custom_attrs: attrs,
          custom_iface_refs: (n as any).custom_iface_refs || {},
        });
        created.push(copy.id);
      } catch (err: any) {
        toast.error(`Couldn't copy ${n.name}: ${parseLabError(err) || err?.message || 'failed'}`);
      }
    }
    if (created.length) {
      setSelectedNodeIds(new Set(created));
      toast.success(`Created ${created.length} cop${created.length > 1 ? 'ies' : 'y'}`);
    }
  };
  // A modal is up — keyboard actions must not touch the canvas behind it.
  const canvasBlocked = !!(patchPanelLocation || viewConfigNode || netemLink || siteDrop);
  nodeCopyRef.current = {
    copy: () => {
      if (canvasBlocked) return false;
      const src = copySources();
      if (!src.length) return false;
      clipboardRef.current = src.map(n => ({ ...n }));
      clipboardTopoRef.current = currentTopologyId;
      toast(`Copied ${src.length} node${src.length > 1 ? 's' : ''} — Ctrl+V to paste`, { id: 'node-copy' });
      return true;
    },
    paste: at => {
      if (canvasBlocked) return;
      if (clipboardTopoRef.current !== currentTopologyId) {
        toast.error('Nodes can only be pasted into the topology they were copied from');
        return;
      }
      createCopies(clipboardRef.current, at);
    },
    duplicate: ids => { if (!canvasBlocked) createCopies(copySources(ids)); },
  };

  // Selection-aware action hotkeys (logical view). Re-assigned every render so
  // it sees current state; the keydown effect above calls through the ref.
  // Returns true when the key did something.
  actionKeysRef.current = (key: string): boolean => {
    if (canvasBlocked) return false;
    const st = useStore.getState();
    const node = st.selectedNodeId != null ? nodes.find(n => n.id === st.selectedNodeId) : undefined;
    const link = st.selectedLinkId != null ? links.find(l => l.id === st.selectedLinkId) : undefined;

    if (key === 'e') {
      if (node) {
        setSelectedNode(node.id, canEdit && !node.is_inherited);
        setSidePanel('nodes');
      } else if (link) {
        setSelectedLink(link.id, canEdit && !link.is_inherited);
        setSidePanel('links');
      } else if (currentTopo?.is_protected && !canEdit) {
        st.beginEditSession()
          .then(() => toast.success('Edit mode on'))
          .catch((err: any) => toast.error(editSessionErrorMessage(err)));
      } else return false;
      return true;
    }

    if (key === 'n') {
      // Selected node/link → its menu, anchored on it; otherwise the canvas
      // menu at the last cursor position (or the canvas centre).
      if (node) {
        const p = rfInstance.flowToScreenPosition({ x: node.x + 22, y: node.y + 22 });
        setCtxMenu({ x: p.x, y: p.y, type: 'node', id: node.id, fromKey: true });
      } else if (link) {
        const a = nodes.find(n => n.id === link.source_node_id), b = nodes.find(n => n.id === link.target_node_id);
        if (!a || !b) return false;
        const p = rfInstance.flowToScreenPosition({ x: (a.x + b.x) / 2 + 22, y: (a.y + b.y) / 2 + 22 });
        setCtxMenu({ x: p.x, y: p.y, type: 'edge', id: link.id, fromKey: true });
      } else {
        const el = reactFlowWrapper.current;
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const pt = cursorInCanvas() ?? { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        const f = rfInstance.screenToFlowPosition(pt);
        setCtxMenu({ x: pt.x, y: pt.y, type: 'pane', id: 0, flowX: f.x, flowY: f.y, fromKey: true });
      }
      return true;
    }

    if (key === 'p') {
      if (!node) return false;
      if (!features?.patch_panels_enabled || labMode || isChildTopology || (node.role || 'router') === 'customer') {
        toast('Patch panels aren\'t available for this node');
        return true;
      }
      if (!node.location) { toast('Set the device location first to use patch panels'); return true; }
      const loc = node.location;
      if (canEdit) { setPatchPanelLocation(loc); return true; }
      // Read-only: like the context menu, only open when panels exist (no create).
      if (!currentTopologyId) return true;
      api.listPatchPanels(currentTopologyId, loc)
        .then(list => list.length ? setPatchPanelLocation(loc) : toast('No patch panels at this location'))
        .catch(() => toast.error('Could not load patch panels'));
      return true;
    }

    if (key === 'l') {
      // Cycle through the selected node's links. Selecting a link deselects
      // the node, so remember the anchor node between presses.
      const cyc = linkCycleRef.current;
      const anchorId = node ? node.id : (link && cyc && cyc.linkId === link.id ? cyc.nodeId : null);
      if (anchorId == null) return false;
      const own = visibleLinks
        .filter(l => l.source_node_id === anchorId || l.target_node_id === anchorId)
        .sort((x, y) => nodeName(x.source_node_id === anchorId ? x.target_node_id : x.source_node_id)
          .localeCompare(nodeName(y.source_node_id === anchorId ? y.target_node_id : y.source_node_id)));
      if (!own.length) { toast(`${nodeName(anchorId)} has no links`); return true; }
      const prevIdx = !node && cyc ? own.findIndex(l => l.id === cyc.linkId) : -1;
      const next = own[(prevIdx + 1) % own.length];
      linkCycleRef.current = { nodeId: anchorId, linkId: next.id };
      setSelectedLink(next.id);
      const other = next.source_node_id === anchorId ? next.target_node_id : next.source_node_id;
      toast(`Link ${own.indexOf(next) + 1}/${own.length}: ${nodeName(anchorId)} ↔ ${nodeName(other)}`, { id: 'link-cycle' });
      return true;
    }
    return false;
  };

  // Map marker colors match the logical canvas: the node's own color, else
  // its active view's color, else (customer nodes) the customer's color.
  const geoNodeColors = useMemo(() => {
    const m = new Map<number, string>();
    for (const n of nodes) {
      const c = isManualColor(n.color) ? n.color
        : nodeViewColor.get(n.id) || (n.role === 'customer' && n.customer_id ? customerColorMap[n.customer_id] : '');
      if (c) m.set(n.id, c);
    }
    return m;
  }, [nodes, nodeViewColor, customerColorMap]);
  const geoLayer = geoVisited && geoEnabled ? (
    <div className="absolute inset-0" style={isGeoView ? undefined : { display: 'none' }}>
      <GeographicCanvas active={isGeoView} nodeColors={geoNodeColors} />
    </div>
  ) : null;

  if (isGeoView) {
    return (
      <div className="relative w-full h-full">
        {geoLayer}
        {showShortcuts && <KeyboardShortcutsModal title="Geographic view shortcuts" shortcuts={GEO_SHORTCUTS} onClose={closeShortcuts} />}
      </div>
    );
  }

  return (
    <div className="relative w-full h-full">
    {geoLayer}
    <div
      ref={reactFlowWrapper}
      className={`w-full h-full ${isDrawingLink ? 'cursor-crosshair' : ''} ${labMode ? 'lab-mode-canvas' : ''}`}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      style={{ outline: 'none' }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        onNodesChange={onNodesChange}
        onEdgeClick={onEdgeClick}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onNodeContextMenu={onNodeContextMenu}
        onEdgeContextMenu={onEdgeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        // Coming back from the geographic view: resume the saved viewport
        // instead of re-fitting the whole topology.
        fitView={!savedViewport}
        defaultViewport={savedViewport}
        onMoveEnd={onMoveEnd}
        minZoom={0.05}
        maxZoom={2}
        snapToGrid
        snapGrid={[20, 20]}
        selectionOnDrag={false}
        multiSelectionKeyCode="Shift"
        deleteKeyCode={null}
        disableKeyboardA11y
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color={labMode ? '#6b7280' : '#1e293b'} />
        <Controls />



      </ReactFlow>

      {showShortcuts && <KeyboardShortcutsModal title="Logical view shortcuts" shortcuts={LOGICAL_SHORTCUTS} onClose={closeShortcuts} />}

      {/* "/" node search: jump to (center + select) a node by name. */}
      {nodeSearchOpen && (
        <NodeSearchBox
          nodes={visibleNodes}
          onClose={closeNodeSearch}
          onPick={n => {
            setNodeSearchOpen(false);
            // Outside lab mode, selecting a new node already auto-centers it
            // (selectedNodeId effect); a second setCenter would just fight it.
            if (labMode || n.id === selectedNodeId) {
              rfInstance.setCenter(n.x + 22, n.y + 22, { zoom: Math.max(rfInstance.getZoom(), 0.5), duration: 400 });
            }
            setSelectedNode(n.id);
          }}
        />
      )}

      {/* Drawing bezier curve from source node to cursor */}
      {isDrawingLink && sourceScreenPos && mousePos && (() => {
        const sx = sourceScreenPos.x;
        const sy = sourceScreenPos.y;
        const tx = mousePos.x;
        const ty = mousePos.y;
        const dx = Math.abs(tx - sx);
        const dy = Math.abs(ty - sy);
        const offset = Math.max(50, Math.min(dx, dy) * 0.5);
        // Determine primary direction for control points
        const horizontal = dx > dy;
        const c1x = horizontal ? sx + (tx > sx ? offset : -offset) : sx;
        const c1y = horizontal ? sy : sy + (ty > sy ? offset : -offset);
        const c2x = horizontal ? tx + (tx > sx ? -offset : offset) : tx;
        const c2y = horizontal ? ty : ty + (ty > sy ? -offset : offset);
        const path = `M ${sx} ${sy} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${tx} ${ty}`;

        return (
          <svg
            style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 10 }}
          >
            {/* Glow effect */}
            <path
              d={path}
              fill="none"
              stroke="#3b82f6"
              strokeWidth={6}
              opacity={0.15}
              strokeLinecap="round"
            />
            {/* Main curve */}
            <path
              d={path}
              fill="none"
              stroke="#3b82f6"
              strokeWidth={2.5}
              opacity={0.9}
              strokeLinecap="round"
            />
            {/* Animated dashes on top */}
            <path
              d={path}
              fill="none"
              stroke="#93c5fd"
              strokeWidth={2}
              strokeDasharray="6 4"
              strokeLinecap="round"
              opacity={0.7}
            >
              <animate attributeName="stroke-dashoffset" from="10" to="0" dur="0.4s" repeatCount="indefinite" />
            </path>
            {/* Cursor dot */}
            <circle cx={tx} cy={ty} r={5} fill="#3b82f6" opacity={0.7} />
            <circle cx={tx} cy={ty} r={3} fill="#93c5fd" opacity={0.9} />
          </svg>
        );
      })()}

      {/* Right-click box selection overlay */}
      {selectionBox && (
        <svg
          style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 10 }}
        >
          <rect
            x={Math.min(selectionBox.startX, selectionBox.currentX)}
            y={Math.min(selectionBox.startY, selectionBox.currentY)}
            width={Math.abs(selectionBox.currentX - selectionBox.startX)}
            height={Math.abs(selectionBox.currentY - selectionBox.startY)}
            fill="rgba(59, 130, 246, 0.1)"
            stroke="#3b82f6"
            strokeWidth={1.5}
            strokeDasharray="6 3"
            rx={2}
          />
        </svg>
      )}

      {/* Drawing mode indicator */}
      {isDrawingLink && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-primary-600/90 text-white text-xs px-4 py-1.5 rounded-full shadow-lg fade-in flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full bg-white animate-pulse" />
          Drawing link from {nodeName(linkSourceNodeId!)} — click target node or press Esc to cancel
        </div>
      )}

      {/* Path builder indicator */}
      {pathBuilder.active && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-emerald-600/90 text-white text-xs px-4 py-1.5 rounded-full shadow-lg fade-in flex items-center gap-2 z-20">
          <span className="inline-block w-2 h-2 rounded-full bg-white animate-pulse" />
          Building path: {pathBuilder.nodeIds.map(id => nodeName(id)).join(' → ')}
          {pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1] === pathBuilder.destNodeId
            ? ' — Path complete!'
            : ` — click a link from ${nodeName(pathBuilder.nodeIds[pathBuilder.nodeIds.length - 1])} toward ${nodeName(pathBuilder.destNodeId!)}`
          }
        </div>
      )}

      {/* Multi-select indicator — only show when 2+ nodes selected */}
      {/* Active sub-topology view indicator — top-left so it never collides with
          the centered selection banners. Hidden when the plain Default view (no
          exclusions, nothing stacked) is showing — that's the normal full view. */}
      {(() => {
        const isPlainDefault = activeViewIds.length === 1 && activeViewIds[0] === 'default'
          && defaultExcludedNodeIds.length === 0 && editingViewId == null;
        if (isPlainDefault) return null;
        const labelFor = (vid: number | 'default' | 'adhoc') =>
          vid === 'default' ? 'Default'
            : vid === 'adhoc' ? 'Selection'
            : ((savedViewsForFilter.find(v => v.id === vid) as any)?.name || `#${vid}`);
        const editLabel = editingViewId == null ? '' : labelFor(editingViewId);
        return (
          <div className={`absolute top-3 left-3 z-20 text-white text-[11px] font-medium pl-3 pr-1.5 py-1 rounded-full shadow-lg fade-in flex items-center gap-2 ${
            editingSubTopology ? 'bg-amber-600/90' : 'bg-violet-600/90'
          }`}>
            <span>
              {editingSubTopology
                ? `Editing ${editLabel}`
                : `Views: ${activeViewIds.map(labelFor).join(' + ')}`}
              {' '}({(subTopology?.size ?? nodes.length)} node{(subTopology?.size ?? nodes.length) === 1 ? '' : 's'})
              {editingSubTopology && (
                <span className="ml-2 opacity-80">· Right-click ghosted nodes to add / remove</span>
              )}
            </span>
            {editingSubTopology ? (
              <button
                className="bg-white/20 hover:bg-white/30 rounded-full px-2 py-0.5 transition-colors"
                onClick={() => useStore.getState().setEditingView(null)}
                title="Stop editing"
              >
                Done
              </button>
            ) : activeViewIds.some(v => v !== 'default') ? (
              <button
                className="bg-white/20 hover:bg-white/30 rounded-full px-2 py-0.5 transition-colors"
                onClick={() => useStore.getState().resetToDefaultView()}
                title="Return to the Default view"
              >
                Reset
              </button>
            ) : null}
          </div>
        );
      })()}
      {selectedNodeIds.size > 1 && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 bg-primary-600/90 text-white text-[11px] font-medium px-3 py-1 rounded-full shadow-lg fade-in cursor-pointer"
          onClick={() => { useStore.getState().setSelectedNode(null); setSidePanel('nodes'); }}>
          {selectedNodeIds.size} nodes selected — click to apply color
        </div>
      )}
      {selectedLinkIds.length > 0 && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 bg-sky-600/90 text-white text-[11px] font-medium px-3 py-1 rounded-full shadow-lg fade-in cursor-pointer"
          onClick={() => { useStore.getState().setSelectedLink(null); setSidePanel('links'); }}>
          {selectedLinkIds.length} links selected — click to apply color
        </div>
      )}

      {/* Analysis running indicator — shared across simulation, Monte Carlo,
          maintenance, and per-flow availability. While any of these is in
          flight the global lock blocks starting another, so this single
          spinner tells the user the backend is busy. */}
      {(isSimulating || isMaintenanceRunning || isMonteCarlo || isAnalyzingAvailability) && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-20 bg-surface-800/95 border border-primary-500/50 text-primary-300 text-[11px] font-medium px-4 py-2 rounded-lg shadow-lg fade-in flex items-center gap-2">
          <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
          {isMaintenanceRunning ? 'Running maintenance analysis...'
            : isMonteCarlo ? 'Running Monte Carlo...'
            : isAnalyzingAvailability ? 'Analyzing availability...'
            : 'Running simulation...'}
        </div>
      )}

      {/* Right-click context menu */}
      {labMode && (
        <LabConsoleDrawer
          tabs={consoleTabs}
          activeId={activeConsoleTabId}
          onSetActive={setActiveConsoleTab}
          onClose={closeConsoleTab}
        />
      )}
      {ctxMenu && shownCtxItems.length > 0 && (
        <ContextMenu
          key={`${ctxMenu.type}-${ctxMenu.id}-${ctxMenu.x}-${ctxMenu.y}-${ctxMenu.subMenu || ''}`}
          x={ctxMenu.x} y={ctxMenu.y} items={shownCtxItems} onClose={() => setCtxMenu(null)}
          highlightFirst={ctxMenu.fromKey}
        />
      )}
      {netemLink && currentTopologyId && (
        <LinkNetemModal topologyId={currentTopologyId} linkId={netemLink.id} label={netemLink.label} onClose={() => setNetemLink(null)} />
      )}

      {/* Lab-mode container status: manual refresh. Same poll as the 5s
          background tick, but lets the user pull the latest immediately
          after stopping/starting/restarting a node so badges update without
          waiting for the next interval. */}
      {labMode && (
        <div className="absolute bottom-4 right-4 bg-surface-800/90 border border-surface-600 rounded-lg px-3 py-2 text-xs fade-in flex items-center gap-2">
          <span className="text-surface-400">Container status</span>
          <button
            className="btn btn-ghost btn-sm text-[11px] px-2 py-0.5"
            onClick={fetchLabStatus}
            disabled={labStatusBusy}
            title="Refresh live container status (auto-polls every 5s)"
          >
            <RefreshCw size={12} className={labStatusBusy ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
      )}

      {/* Auto-simulate toggle — hidden in lab mode (no simulation there).
          When off, edits invalidate the result but the user re-runs manually
          from the Simulation panel. When on, edits trigger a debounced run. */}
      {!labMode && (
        <div className="absolute bottom-4 right-4 bg-surface-800/90 border border-surface-600 rounded-lg px-3 py-2 text-xs fade-in flex items-center gap-2">
          <span className={autoSimulate ? 'text-surface-200' : 'text-surface-400'}>Auto-simulate</span>
          <button
            role="switch"
            aria-checked={autoSimulate}
            onClick={() => setAutoSimulate(!autoSimulate)}
            className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
              autoSimulate ? 'bg-primary-600' : 'bg-surface-600'
            }`}
            title={
              autoSimulate
                ? 'Auto-simulate is ON — edits re-run the simulation automatically'
                : 'Auto-simulate is OFF — re-run manually from the Simulation panel'
            }
          >
            <span
              className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                autoSimulate ? 'translate-x-3.5' : 'translate-x-0.5'
              }`}
            />
          </button>
          {autoSimulate && isSimulating && (
            <span className="text-[10px] text-primary-300 animate-pulse">running…</span>
          )}
        </div>
      )}

      {/* Legend — hidden in lab mode (simulation-driven coloring doesn't apply there) */}
      {!labMode && (
      <div className="absolute bottom-4 left-16 bg-surface-900/90 backdrop-blur border border-surface-700 rounded-xl p-3 text-xs fade-in">
        {/* Link color scheme (moved here from the toolbar) — quiet text tabs
            that sit with the meter they control. */}
        <div className="flex items-center gap-2 mb-2.5">
          <span className="text-surface-500 w-12 text-[11px]">Color</span>
          <div className="flex items-center gap-3">
            {([
              ['utilization', 'Utilization', 'Color and label links by utilization'],
              ['cost_per_gb', 'Cost/GB', 'Color and label links by cost per Gbps carried'],
              ['circuit_cost', 'Circuit cost', 'Color and label links by circuit cost'],
              ['latency', 'Latency', 'Color and label links by one-way latency'],
            ] as const).map(([v, label, title]) => (
              <button
                key={v}
                title={title}
                onClick={() => setColorScheme(v)}
                className={`text-[11px] pb-0.5 border-b transition-colors ${colorScheme === v ? 'text-surface-100 border-primary-400' : 'text-surface-500 border-transparent hover:text-surface-300'}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {customerUtilActive && (
          <div className="flex items-center gap-2 mb-3 text-[11px]">
            <span className="text-surface-500 w-12">Customer</span>
            <span className="text-surface-200 truncate max-w-[220px]" title="Utilization from these customers' flows only; other links are dimmed">
              {utilCustomerIds.map(id => customers.find(c => c.id === id)?.name).join(', ')}
            </span>
            <button className="text-surface-500 hover:text-surface-200" title="Turn off customer utilization" onClick={() => setCustomerUtilEnabled(false)}>
              <X size={12} />
            </button>
          </div>
        )}
        {!customerUtilActive && (colorScheme === 'utilization' || linkColorByUtilization) && (
          <div className="flex items-center gap-2 mb-3">
            <span className="text-surface-500 w-12 text-[11px]">Show</span>
            <div className="flex bg-surface-950/60 border border-surface-700 rounded-lg p-0.5">
              {([
                ['demand', 'Demand', 'Anticipated utilization from simulated demands'],
                ['actual_30d', '30-day max', 'Measured peak over the last 30 days (imported interface metrics)'],
                ['actual_90d', '90-day max', 'Measured peak over the last 90 days (imported interface metrics)'],
              ] as const).map(([v, label, title]) => (
                <button
                  key={v}
                  title={title}
                  onClick={() => setUtilizationView(v)}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${utilizationView === v ? 'bg-surface-700 text-white shadow-sm' : 'text-surface-400 hover:text-surface-200'}`}
                >
                  {label}
                </button>
              ))}
            </div>
            {utilizationView !== 'demand' && (
              <span className="text-surface-500" title="Links whose interfaces matched an imported metric row. Import data from the Links panel.">
                {actualUtilization
                  ? actualUtilization.metric_rows === 0
                    ? 'No data imported (Links panel)'
                    : `${actualUtilization.links_with_data}/${actualUtilization.total_links} links with data`
                  : 'loading…'}
              </span>
            )}
          </div>
        )}
        {/* One meter style for every scheme: a colored bar per step with its
            threshold beneath. Steps come from the same tables as the links. */}
        <ColorMeter {...(linkScheme === 'cost_per_gb'
          ? { label: '$/Gbps', steps: COST_STEPS.map(st => [st.color, st.label] as const) }
          : linkScheme === 'circuit_cost'
            ? { label: '× avg', steps: [[CIRCUIT_COST_NONE_COLOR, 'none'] as const, ...CIRCUIT_COST_STEPS.map(st => [st.color, st.label] as const)] }
            : linkScheme === 'latency'
              ? { label: 'ms', steps: [[LATENCY_NONE_COLOR, 'n/a'] as const, ...LATENCY_STEPS.map(st => [st.color, st.label] as const)] }
              : { label: 'Util. %', steps: [
                  [linkColors.unused, '0'], [linkColors.low, '<25'], [linkColors.mid, '<50'],
                  [linkColors.high, '<75'], [linkColors.critical, '<90'], [linkColors.overloaded, '90+'],
                ] as const })} />
      </div>
      )}

      <GeneratedConfigModal
        nodeId={viewConfigNode?.id ?? null}
        nodeName={viewConfigNode?.name}
        onClose={() => setViewConfigNode(null)}
      />
      {siteDrop && (
        <SiteTemplateInstantiateModal
          template={siteDrop.template}
          position={{ x: siteDrop.x, y: siteDrop.y }}
          onClose={() => setSiteDrop(null)}
        />
      )}
      {currentTopologyId && (
        <PatchPanelModal
          topologyId={currentTopologyId}
          location={patchPanelLocation}
          onClose={() => setPatchPanelLocation(null)}
          onChanged={count => patchPanelLocation && setPanelCounts(c => ({ ...c, [patchPanelLocation]: count }))}
        />
      )}
    </div>
    </div>
  );
}

/** Pull a FastAPI HTTPException detail out of our `API error N: {...}` style. */
function parseLabError(e: any): string | null {
  try {
    const body = e?.message?.split(': ').slice(1).join(': ');
    const parsed = body ? JSON.parse(body) : null;
    if (parsed?.detail && typeof parsed.detail === 'string') return parsed.detail;
  } catch { /* fall through */ }
  return null;
}

// Last logical viewport per topology (session-only), restored when returning
// from the geographic view.
const savedLogicalViewport = new Map<number, Viewport>();

// Wrap with ReactFlowProvider so useReactFlow() works
export default function TopologyEditor() {
  return (
    <ReactFlowProvider>
      <TopologyEditorInner />
    </ReactFlowProvider>
  );
}

/** Legend meter: one bar per color step with its threshold label beneath. */
function ColorMeter({ label, steps }: { label: string; steps: readonly (readonly [string, string])[] }) {
  return (
    <div className="flex items-start gap-2">
      <span className="text-surface-500 w-12 text-[11px] leading-none pt-px">{label}</span>
      <div className="flex gap-0.5">
        {steps.map(([c, l]) => (
          <div key={l} className="w-9">
            <div className="h-1.5 rounded-full" style={{ background: c }} />
            <div className="mt-1 font-mono text-[10px] text-surface-400">{l}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

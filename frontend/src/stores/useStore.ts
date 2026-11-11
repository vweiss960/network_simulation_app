import { create } from 'zustand';
import {
  Topology, NodeTemplate, NodeInstance, LinkData, FlowData, Customer,
  SimulationResult, MonteCarloResult, GlobalSetting, ColorScheme, FlowPath, PathEnd,
  ChatMessage, LinkColorConfig, SrlgGroup, MaintenanceResult, SrlgAnalysisResult,
  PrometheusConfig, LiveMetricsResult, UtilizationView, ActualUtilizationResult, SiteTemplate,
} from '../types';
import { api } from '../utils/api';
import { VIEW_LAYER_PALETTE } from '../utils/colors';

// Optional feature modules, gated server-side behind env vars and surfaced via
// /api/features. Loaded once at startup; the UI hides menus for disabled ones.
export type FeatureFlags = Awaited<ReturnType<typeof api.getFeatures>>;

let simTimer: ReturnType<typeof setTimeout> | null = null;
function debouncedAutoSim(fn: () => void, delay = 800) {
  if (simTimer) clearTimeout(simTimer);
  simTimer = setTimeout(fn, delay);
}

// Single global analysis lock. The backend is one uvicorn worker and the
// Monte Carlo / simulation work is CPU/GIL-bound, so overlapping runs don't
// parallelize — they just contend and crawl. We block a new run while any
// analysis (sim, Monte Carlo, maintenance window, or per-flow availability)
// is in flight, surfacing a clear "busy" message instead of piling up.
const ANALYSIS_BUSY_MSG = 'An analysis is already running — please wait for it to finish.';
function analysisBusy(s: any): boolean {
  return !!(s.isSimulating || s.isMonteCarlo || s.isMaintenanceRunning || s.isAnalyzingAvailability || s.isRunningFailures);
}

// Auto-simulate is a per-topology preference: each topology remembers
// whether you opted into automatic re-runs after edits. Stored in
// localStorage so it survives a refresh; keyed by topology id so switching
// topologies picks up that topology's setting.
const AUTO_SIM_KEY = (topoId: number) => `auto_simulate_topo_${topoId}`;
function readAutoSimulate(topoId: number | null): boolean {
  if (topoId == null) return false;
  try { return localStorage.getItem(AUTO_SIM_KEY(topoId)) === '1'; }
  catch { return false; }
}

// View mode (logical React Flow canvas vs geographic map) is a per-topology
// preference, persisted in localStorage like auto-simulate above.
type ViewMode = 'logical' | 'geographic';
const VIEW_MODE_KEY = (topoId: number) => `view_mode_topo_${topoId}`;
function readViewMode(topoId: number | null): ViewMode {
  if (topoId == null) return 'logical';
  try { return localStorage.getItem(VIEW_MODE_KEY(topoId)) === 'geographic' ? 'geographic' : 'logical'; }
  catch { return 'logical'; }
}

interface AppState {
  topologies: Topology[];
  currentTopologyId: number | null;
  // Protected-topology edit session (sudo-style). null = unknown/not loaded.
  editSession: { protected: boolean; active: boolean; heldByMe: boolean; username: string; expiresAt: string | null } | null;
  loadEditSession: (topologyId: number) => Promise<void>;
  beginEditSession: () => Promise<void>;
  commitEditSession: (message?: string) => Promise<void>;
  discardEditSession: () => Promise<void>;
  heartbeatEditSession: () => Promise<void>;
  canEditCurrentTopology: () => boolean;
  // Target view for new nodes: 'default' (normal) or a saved view id. When a
  // saved view is selected, created nodes are added to it and excluded from the
  // Default view.
  targetViewId: number | 'default';
  setTargetViewId: (id: number | 'default') => void;
  nodeTemplates: NodeTemplate[];
  siteTemplates: SiteTemplate[];
  /** Site template the Site Templates panel asked to add — the canvas picks
   *  it up, opens the variables form, and drops it at the viewport centre. */
  siteTemplateToPlace: number | null;
  setSiteTemplateToPlace: (id: number | null) => void;
  nodes: NodeInstance[];
  links: LinkData[];
  flows: FlowData[];
  customers: Customer[];
  globalSettings: GlobalSetting[];

  simulationResult: SimulationResult | null;
  monteCarloResult: MonteCarloResult | null;
  isSimulating: boolean;
  isMonteCarlo: boolean;
  isAnalyzingAvailability: boolean;
  // The SPOF/failure analysis is expensive, so it runs only on demand (Failures
  // tab button); util-only runs leave failureAnalysisRun false.
  isRunningFailures: boolean;
  failureAnalysisRun: boolean;

  colorScheme: ColorScheme;
  linkColors: LinkColorConfig;
  showInterfaceNames: boolean;
  linkWidthBySpeed: boolean;
  linkColorByUtilization: boolean;
  /** Customers panel: color the canvas by the load from only these
   *  customers' flows (others' links dimmed). Per topology. */
  customerUtilEnabled: boolean;
  customerUtilIds: number[];
  setCustomerUtilEnabled: (on: boolean) => void;
  setCustomerUtilIds: (ids: number[]) => void;
  /** Draw every satellite link (by default they're hidden unless their dish /
   *  PoP is selected or they're on a highlighted path). Persisted. */
  showSatelliteLinks: boolean;
  toggleShowSatelliteLinks: () => void;
  linkLabelFields: string[];
  groupByLocation: boolean;
  selectedNodeId: number | null;
  selectedLinkId: number | null;
  selectedLinkIds: number[];
  setSelectedLinkIds: (ids: number[]) => void;
  // Mirror of the canvas multi-node selection (one-way, written by
  // TopologyEditor) so panels can read it for bulk actions like coloring.
  multiSelectedNodeIds: number[];
  setMultiSelectedNodeIds: (ids: number[]) => void;
  selectedFlowId: number | null;
  highlightedFlowPaths: FlowPath | null;
  srlgs: SrlgGroup[];
  maintenanceResult: MaintenanceResult | null;
  srlgAnalysisResult: SrlgAnalysisResult[] | null;
  isMaintenanceRunning: boolean;
  maintenanceNodeIds: Set<number>;
  maintenanceLinkIds: Set<number>;
  includePlanned: boolean;
  hidePlanned: boolean;
  hideCustomers: boolean;
  hiddenCustomerIds: Set<number>;

  sidePanel: 'none' | 'nodes' | 'links' | 'flows' | 'simulation' | 'settings' | 'templates' | 'chat' | 'discovery' | 'users' | 'srlgs' | 'maintenance' | 'prometheus' | 'customers' | 'cost-analysis' | 'integrations' | 'lab' | 'views' | 'site-templates' | 'paths';

  // Lab mode: when on, the canvas swaps to a translated "lab view" of the
  // topology — same nodes/links, but templates substituted to deployable
  // images and interface names renamed per containerlab kind. The design
  // topology is never modified.
  labMode: boolean;
  labView: { nodes: any[]; links: any[]; skipped_nodes?: any[] } | null;
  // Persisted deploy-subset, shared between LabPanel and TopologyEditor so
  // canvas clicks and panel checkboxes stay in sync without a poll round-trip.
  // explicit=false ⇒ "no preference saved, deploy everything" (panel shows
  // all checkboxes checked, canvas dims nothing).
  labDeploySubset: number[];
  labDeploySubsetExplicit: boolean;
  // Transient — when true, single-clicking a node on the canvas toggles its
  // subset membership instead of opening the side panel. Toggled by a button
  // in the Lab panel.
  labSubsetSelectMode: boolean;
  loadLabDeploySubset: (topologyId: number) => Promise<void>;
  setLabDeploySubsetState: (ids: number[], explicit: boolean) => void;
  applyLabDeploySubset: (topologyId: number, ids: number[], explicit: boolean) => Promise<void>;
  toggleLabDeploySubsetNode: (topologyId: number, nodeId: number, allDeployableIds: number[]) => Promise<void>;
  setLabSubsetSelectMode: (on: boolean) => void;
  // Config Design mode (Phase 1) — when on, the side panel opens to the
  // template editor and the toolbar swaps in config-design-specific tools.
  // The canvas is unchanged in Phase 1; status badges land in Phase 2.
  configMode: boolean;
  setConfigMode: (on: boolean) => void;
  // Service mode — full-viewport service authoring + deploy. Mutually
  // exclusive with Lab and Config (all three swap the main view).
  serviceMode: boolean;
  setServiceMode: (on: boolean) => void;
  // Open console / log tabs in the bottom drawer (lab mode only).
  consoleTabs: Array<{ id: string; type: 'console' | 'logs'; nodeId: number; nodeName: string; cli?: string }>;
  activeConsoleTabId: string | null;

  prometheusConfig: PrometheusConfig | null;
  liveMetrics: LiveMetricsResult | null;
  isLiveMode: boolean;
  isLoadingMetrics: boolean;

  // Demand (simulated) vs actual (measured peak 30d/90d) link utilization on
  // the map. actualUtilization is the per-link view derived server-side from
  // imported interface metrics.
  utilizationView: UtilizationView;
  actualUtilization: ActualUtilizationResult | null;
  setUtilizationView: (v: UtilizationView) => void;
  loadActualUtilization: () => Promise<void>;
  importActualUtilizationCsv: (csv: string) => Promise<ActualUtilizationResult>;
  clearActualUtilization: () => Promise<void>;

  chatMessages: ChatMessage[];
  isChatLoading: boolean;

  loadTopologies: () => Promise<void>;
  selectTopology: (id: number) => Promise<void>;
  createTopology: (name: string, description: string, parentTopologyId?: number | null) => Promise<Topology>;
  deleteTopology: (id: number) => Promise<void>;
  duplicateTopology: (id: number) => Promise<void>;
  renameTopology: (id: number, name: string) => Promise<void>;

  loadNodeTemplates: () => Promise<void>;
  createNodeTemplate: (data: any) => Promise<void>;
  deleteNodeTemplate: (id: number) => Promise<void>;
  loadSiteTemplates: () => Promise<void>;
  /** Drop a site template into the current topology at (x, y). Returns the
   *  backend's warnings (e.g. patch panels skipped on a planning layer). */
  instantiateSiteTemplate: (templateId: number, variables: Record<string, string>, x: number, y: number) =>
    Promise<{ nodes: NodeInstance[]; patch_panels: number; warnings: string[] }>;

  addNode: (data: any) => Promise<NodeInstance>;
  updateNode: (nodeId: number, data: any) => Promise<void>;
  deleteNode: (nodeId: number) => Promise<void>;
  deleteNodes: (nodeIds: number[]) => Promise<void>;

  addLink: (data: any) => Promise<LinkData>;
  updateLink: (linkId: number, data: any) => Promise<void>;
  deleteLink: (linkId: number) => Promise<void>;

  updateNodeLocal: (nodeId: number, data: Partial<NodeInstance>) => void;

  loadCustomers: () => Promise<void>;
  addCustomer: (data: any) => Promise<Customer>;
  updateCustomer: (id: number, data: any) => Promise<void>;
  deleteCustomer: (id: number) => Promise<void>;

  addFlow: (data: any) => Promise<FlowData>;
  /** Returns the flows the server created or updated (skipped rows excluded). */
  bulkAddFlows: (flows: any[], updateExisting?: boolean) => Promise<FlowData[]>;
  updateFlow: (flowId: number, data: any) => Promise<void>;
  deleteFlow: (flowId: number) => Promise<void>;
  analyzeFlowAvailability: (flowId: number) => Promise<any>;
  deleteAllFlows: () => Promise<void>;

  features: FeatureFlags | null;
  loadFeatures: () => Promise<void>;

  runSimulation: () => Promise<void>;
  runFailureAnalysis: () => Promise<void>;
  autoRunSimulation: () => void;
  autoSimulate: boolean;
  setAutoSimulate: (on: boolean) => void;
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
  // Geographic-view overlay: TeleGeography submarine cables. Global display
  // preference (not per-topology).
  showSubmarineCables: boolean;
  toggleSubmarineCables: () => void;
  showDataCenters: boolean;
  toggleDataCenters: () => void;
  showIXPs: boolean;
  toggleIXPs: () => void;
  showCloudRegions: boolean;
  toggleCloudRegions: () => void;
  showGeoCoverage: boolean;
  toggleGeoCoverage: () => void;
  showWeatherAlerts: boolean;
  toggleWeatherAlerts: () => void;
  showActiveFires: boolean;
  toggleActiveFires: () => void;
  showEarthquakes: boolean;
  toggleEarthquakes: () => void;
  showCyclones: boolean;
  toggleCyclones: () => void;
  showInternetOutages: boolean;
  toggleInternetOutages: () => void;
  showCoverageStrength: boolean;
  toggleCoverageStrength: () => void;
  runMonteCarlo: (params: { a_location: string; z_location: string; a_node_ids?: number[]; z_node_ids?: number[]; iterations?: number; connection_type?: string }) => Promise<void>;

  loadSettings: () => Promise<void>;
  updateSetting: (key: string, value: number) => Promise<void>;

  loadSrlgs: () => Promise<void>;
  addSrlg: (data: any) => Promise<SrlgGroup>;
  updateSrlg: (id: number, data: any) => Promise<void>;
  deleteSrlg: (id: number) => Promise<void>;
  runMaintenanceWindow: (params: { failed_node_ids?: number[]; failed_link_ids?: number[]; srlg_ids?: number[] }) => Promise<void>;
  runSrlgAnalysis: () => Promise<void>;
  toggleMaintenanceNode: (nodeId: number) => void;
  toggleMaintenanceLink: (linkId: number) => void;
  clearMaintenance: () => void;
  applyMaintenance: () => Promise<void>;
  setIncludePlanned: (v: boolean) => void;
  setHidePlanned: (v: boolean) => void;
  setHideCustomers: (v: boolean) => void;
  toggleCustomerVisibility: (customerId: number) => void;

  loadPrometheusConfig: () => Promise<void>;
  savePrometheusConfig: (data: any) => Promise<void>;
  toggleLiveMode: (enabled: boolean) => Promise<void>;
  fetchLiveMetrics: () => Promise<void>;
  fetchHistoricalMetrics: (days?: number, mode?: string) => Promise<void>;

  sendChatMessage: (message: string) => Promise<void>;
  clearChat: () => void;

  pendingLink: { source_node_id: number; target_node_id: number } | null;
  setPendingLink: (link: { source_node_id: number; target_node_id: number } | null) => void;
  setColorScheme: (scheme: ColorScheme) => void;
  toggleInterfaceNames: () => void;
  toggleLinkWidthBySpeed: () => void;
  toggleLinkColorByUtilization: () => void;
  setLabMode: (on: boolean) => Promise<void>;
  openConsoleTab: (type: 'console' | 'logs', nodeId: number, nodeName: string, cli?: string) => void;
  closeConsoleTab: (id: string) => void;
  setActiveConsoleTab: (id: string) => void;
  setLinkLabelFields: (fields: string[]) => void;
  toggleGroupByLocation: () => void;
  setLinkColors: (colors: Partial<LinkColorConfig>) => void;
  editRequested: boolean;
  setSelectedNode: (id: number | null, edit?: boolean) => void;
  setSelectedLink: (id: number | null, edit?: boolean) => void;
  setSelectedFlow: (id: number | null, edit?: boolean) => void;
  setHighlightedFlowPaths: (paths: FlowPath | null) => void;
  // One-shot "pan the logical canvas to this node" request from a panel.
  // `seq` makes repeat requests for the same node re-trigger the effect.
  centerNodeRequest: { id: number; seq: number } | null;
  requestCenterNode: (id: number) => void;
  isolatedNodeIds: number[];
  setIsolatedNodeIds: (ids: number[]) => void;
  // ---- Sub-topology views ----
  // The canvas shows the UNION of the active views (stacked layers). View ids:
  //   'default' — every node MINUS defaultExcludedNodeIds (auto-includes new /
  //               inherited nodes); the user removes nodes from it.
  //   'adhoc'   — a transient selection-based focus (adHocView), not persisted.
  //   number    — a saved view (fixed inclusion list in savedViews).
  // editingViewId is the single view currently being edited (or null).
  savedViews: Array<{ id: number; name: string; description: string; node_ids: number[]; color: string }>;
  activeViewIds: Array<number | 'default' | 'adhoc'>;
  defaultExcludedNodeIds: number[];
  adHocView: number[] | null;
  editingViewId: number | 'default' | null;
  loadTopologyViews: (topologyId: number) => Promise<void>;
  toggleActiveView: (viewId: number | 'default' | 'adhoc') => void;
  // Drag-reorder the saved-views list; the list order IS the layer stack order
  // (the bottom-most active view's color wins where views overlap).
  reorderSavedView: (draggedId: number, targetId: number) => void;
  setEditingView: (viewId: number | 'default' | null) => void;
  resetToDefaultView: () => void;
  setAdHocView: (nodeIds: number[]) => void;
  addNodesToEditingView: (nodeIds: number[]) => Promise<void>;
  removeNodesFromEditingView: (nodeIds: number[]) => Promise<void>;
  saveTopologyView: (topologyId: number, name: string, nodeIds: number[], description?: string) => Promise<any>;
  updateTopologyView: (topologyId: number, viewId: number, data: { name?: string; description?: string; node_ids?: number[]; color?: string }) => Promise<void>;
  deleteTopologyView: (topologyId: number, viewId: number) => Promise<void>;
  agentHighlightNodeIds: number[];
  agentHighlightLinkIds: number[];
  setAgentHighlights: (nodeIds: number[], linkIds: number[]) => void;
  setSidePanel: (panel: AppState['sidePanel']) => void;

  // Path analysis A / Z ends (shared by the panel and the node right-click menu).
  pathEnds: { a: PathEnd | null; z: PathEnd | null };
  setPathEnd: (which: 'a' | 'z', end: PathEnd | null) => void;

  // Path builder
  pathBuilder: {
    active: boolean;
    sourceNodeId: number | null;
    destNodeId: number | null;
    nodeIds: number[];
    linkIds: number[];
  };
  startPathBuilder: (sourceNodeId: number, destNodeId: number) => void;
  pathBuilderAddLink: (linkId: number, nextNodeId: number) => void;
  pathBuilderUndo: () => void;
  cancelPathBuilder: () => void;
  finishPathBuilder: () => { nodeIds: number[]; linkIds: number[] } | null;
}

// Monotonic id for actual-utilization requests; only the latest may write.
let actualUtilSeq = 0;

type StoreSet = (partial: Partial<AppState> | ((s: AppState) => Partial<AppState>)) => void;

/** Target view: when a saved view is selected as the target, add new nodes
 *  to it and exclude them from the Default view (so they land only in the
 *  chosen view). Links follow automatically (membership is endpoint-derived). */
async function addToTargetView(get: () => AppState, set: StoreSet, topoId: number, nodeIds: number[]) {
  const { targetViewId, savedViews, defaultExcludedNodeIds } = get();
  if (typeof targetViewId !== 'number' || !nodeIds.length) return;
  const v = savedViews.find(x => x.id === targetViewId);
  if (!v) return;
  const merged = Array.from(new Set([...v.node_ids, ...nodeIds]));
  const excl = Array.from(new Set([...defaultExcludedNodeIds, ...nodeIds]));
  set(s => ({
    savedViews: s.savedViews.map(x => x.id === targetViewId ? { ...x, node_ids: merged } : x),
    defaultExcludedNodeIds: excl,
  }));
  // Persist sequentially (awaited) so rapid successive adds can't clobber
  // each other: these PUTs replace the whole array, so out-of-order writes
  // would drop nodes. Optimistic state above stands; resyncs on next load.
  try {
    await api.updateTopologyView(topoId, targetViewId, { node_ids: merged });
    await api.setDefaultView(topoId, excl);
  } catch { /* optimistic state stands; resyncs on next view load */ }
}

/** Re-fetch links (best effort) so their effective / estimated latencies are
 *  current; a failure just leaves them until the next topology load. */
function refreshLinksQuietly(topoId: number) {
  api.listLinks(topoId)
    .then(links => { if (useStore.getState().currentTopologyId === topoId) useStore.setState({ links }); })
    .catch(() => { /* stale estimates until next reload */ });
}

export const useStore = create<AppState>((set, get) => ({
  topologies: [],
  currentTopologyId: null,
  editSession: null,
  targetViewId: 'default',
  // Selecting a target view also displays it (like checking it in the
  // Sub-topology Views menu), so newly-added nodes are visible.
  setTargetViewId: (id) => set(s => ({
    targetViewId: id,
    activeViewIds: s.activeViewIds.includes(id) ? s.activeViewIds : [...s.activeViewIds, id],
  })),
  nodeTemplates: [],
  siteTemplates: [],
  siteTemplateToPlace: null,
  setSiteTemplateToPlace: (id) => set({ siteTemplateToPlace: id }),
  nodes: [],
  links: [],
  flows: [],
  customers: [],
  globalSettings: [],
  features: null,
  simulationResult: null,
  monteCarloResult: null,
  isSimulating: false,
  isRunningFailures: false,
  failureAnalysisRun: false,
  isMonteCarlo: false,
  isAnalyzingAvailability: false,
  srlgs: [],
  maintenanceResult: null,
  srlgAnalysisResult: null,
  isMaintenanceRunning: false,
  maintenanceNodeIds: new Set<number>(),
  maintenanceLinkIds: new Set<number>(),
  includePlanned: false,
  hidePlanned: false,
  hideCustomers: true,
  hiddenCustomerIds: new Set<number>(),
  colorScheme: 'utilization',
  showInterfaceNames: false,
  linkWidthBySpeed: false,
  linkColorByUtilization: false,
  customerUtilEnabled: false,
  customerUtilIds: [],
  showSatelliteLinks: (() => { try { return localStorage.getItem('show_satellite_links') === '1'; } catch { return false; } })(),
  labMode: false,
  labView: null,
  labDeploySubset: [],
  labDeploySubsetExplicit: false,
  labSubsetSelectMode: false,
  loadLabDeploySubset: async (topologyId: number) => {
    try {
      const r = await api.getLabSubset(topologyId);
      set({ labDeploySubset: r.node_ids || [], labDeploySubsetExplicit: !!r.explicit });
    } catch {
      set({ labDeploySubset: [], labDeploySubsetExplicit: false });
    }
  },
  setLabDeploySubsetState: (ids: number[], explicit: boolean) => {
    set({ labDeploySubset: ids, labDeploySubsetExplicit: explicit });
  },
  applyLabDeploySubset: async (topologyId: number, ids: number[], explicit: boolean) => {
    // Optimistic local update, then push to backend; the next 5s refetch (or
    // the next loadLabDeploySubset call) reconciles if the server disagrees.
    set({ labDeploySubset: ids, labDeploySubsetExplicit: explicit });
    try {
      await api.setLabSubset(topologyId, ids, explicit);
    } catch {
      // ignore — caller can re-fetch to recover
    }
  },
  toggleLabDeploySubsetNode: async (topologyId: number, nodeId: number, allDeployableIds: number[]) => {
    const s = get();
    // Seed from current state — implicit "all" becomes explicit with every
    // currently-deployable node, then we toggle the clicked one.
    const seed = new Set(s.labDeploySubsetExplicit ? s.labDeploySubset : allDeployableIds);
    if (seed.has(nodeId)) seed.delete(nodeId);
    else seed.add(nodeId);
    const ids = Array.from(seed);
    set({ labDeploySubset: ids, labDeploySubsetExplicit: true });
    try { await api.setLabSubset(topologyId, ids, true); } catch {}
  },
  setLabSubsetSelectMode: (on: boolean) => set({ labSubsetSelectMode: on }),
  configMode: false,
  setConfigMode: (on: boolean) => {
    // Config Design owns the main viewport (canvas is hidden), so we close
    // any open side panel on entry — the user is in dedicated-editor land.
    // On exit we leave the side panel alone since they're back to topology
    // editing where panels are useful.
    const updates: any = { configMode: on };
    if (on) {
      updates.sidePanel = 'none';
      // Config, Lab, and Service are mutually exclusive — they swap the
      // main view and would otherwise compete for the same toggle space.
      updates.labMode = false;
      updates.labView = null;
      updates.serviceMode = false;
    }
    set(updates);
  },
  serviceMode: false,
  setServiceMode: (on: boolean) => {
    // Service Mode owns the main viewport like Config Design — close any side
    // panel on entry and clear the other two exclusive modes.
    const updates: any = { serviceMode: on };
    if (on) {
      updates.sidePanel = 'none';
      updates.labMode = false;
      updates.labView = null;
      updates.configMode = false;
    }
    set(updates);
  },
  consoleTabs: [],
  activeConsoleTabId: null,
  // Auto-simulate is off by default and persists per-topology — see
  // selectTopology / setAutoSimulate. Initial value is false because no
  // topology is selected yet at store creation.
  autoSimulate: false,
  viewMode: 'logical',
  showSubmarineCables: (() => { try { return localStorage.getItem('show_submarine_cables') === '1'; } catch { return false; } })(),
  showDataCenters: (() => { try { return localStorage.getItem('show_data_centers') === '1'; } catch { return false; } })(),
  showIXPs: (() => { try { return localStorage.getItem('show_ixps') === '1'; } catch { return false; } })(),
  showCloudRegions: (() => { try { return localStorage.getItem('show_cloud_regions') === '1'; } catch { return false; } })(),
  showGeoCoverage: (() => { try { return localStorage.getItem('show_geo_coverage') === '1'; } catch { return false; } })(),
  showWeatherAlerts: (() => { try { return localStorage.getItem('show_weather_alerts') === '1'; } catch { return false; } })(),
  showActiveFires: (() => { try { return localStorage.getItem('show_active_fires') === '1'; } catch { return false; } })(),
  showEarthquakes: (() => { try { return localStorage.getItem('show_earthquakes') === '1'; } catch { return false; } })(),
  showCyclones: (() => { try { return localStorage.getItem('show_cyclones') === '1'; } catch { return false; } })(),
  showInternetOutages: (() => { try { return localStorage.getItem('show_internet_outages') === '1'; } catch { return false; } })(),
  showCoverageStrength: (() => { try { return localStorage.getItem('show_coverage_strength') === '1'; } catch { return false; } })(),
  linkLabelFields: ['interface'],
  groupByLocation: false,
  linkColors: {
    unused: '#3b82f6',
    low: '#14b8a6',
    mid: '#4ade80',
    high: '#eab308',
    critical: '#f97316',
    overloaded: '#ef4444',
  },
  selectedNodeId: null,
  selectedLinkId: null,
  selectedLinkIds: [],
  setSelectedLinkIds: (ids) => set({ selectedLinkIds: ids }),
  multiSelectedNodeIds: [],
  setMultiSelectedNodeIds: (ids) => set({ multiSelectedNodeIds: ids }),
  selectedFlowId: null,
  highlightedFlowPaths: null,
  isolatedNodeIds: [],
  savedViews: [],
  activeViewIds: ['default'],
  defaultExcludedNodeIds: [],
  adHocView: null,
  editingViewId: null,
  agentHighlightNodeIds: [],
  agentHighlightLinkIds: [],
  sidePanel: 'none',
  prometheusConfig: null,
  liveMetrics: null,
  isLiveMode: false,
  isLoadingMetrics: false,
  utilizationView: 'demand',
  actualUtilization: null,
  chatMessages: [],
  isChatLoading: false,

  loadTopologies: async () => {
    const topos = await api.listTopologies();
    set({ topologies: topos });
  },

  // ---- Protected-topology edit sessions ----
  canEditCurrentTopology: () => {
    const { currentTopologyId, topologies, editSession } = get();
    const topo = topologies.find(t => t.id === currentTopologyId);
    if (!topo?.is_protected) return true;       // not protected → always editable
    return !!editSession?.heldByMe;              // protected → only the session holder
  },
  loadEditSession: async (topologyId) => {
    try {
      const s = await api.getEditSession(topologyId);
      set({ editSession: { protected: s.protected, active: s.active, heldByMe: s.held_by_me, username: s.username, expiresAt: s.expires_at } });
    } catch { set({ editSession: null }); }
  },
  beginEditSession: async () => {
    const id = get().currentTopologyId; if (!id) return;
    const s = await api.beginEditSession(id);  // throws on 403 (not admin) / 409 (locked)
    set({ editSession: { protected: s.protected, active: s.active, heldByMe: s.held_by_me, username: s.username, expiresAt: s.expires_at } });
  },
  commitEditSession: async (message) => {
    const id = get().currentTopologyId; if (!id) return;
    await api.commitEditSession(id, message);
    await get().selectTopology(id);  // reload to reflect committed state + cleared session
  },
  discardEditSession: async () => {
    const id = get().currentTopologyId; if (!id) return;
    await api.discardEditSession(id);
    await get().selectTopology(id);  // reload to reflect reverted state
  },
  heartbeatEditSession: async () => {
    const id = get().currentTopologyId; if (!id) return;
    try {
      const s = await api.heartbeatEditSession(id);
      set({ editSession: { protected: s.protected, active: s.active, heldByMe: s.held_by_me, username: s.username, expiresAt: s.expires_at } });
    } catch { await get().loadEditSession(id); }  // session ended elsewhere
  },

  selectTopology: async (id: number) => {
    // Switching topologies invalidates lab mode (lab view is per-topology).
    // Auto-simulate is per-topology too — pick up this topology's saved
    // preference so each topology remembers its own setting independently.
    set({
      currentTopologyId: id,
      simulationResult: null, monteCarloResult: null, maintenanceResult: null,
      srlgAnalysisResult: null, liveMetrics: null, isLiveMode: false,
      actualUtilization: null,
      customerUtilEnabled: false, customerUtilIds: [],
      labMode: false, labView: null, serviceMode: false,
      // Sub-topology views are per-topology — open into the Default view and
      // reload the new topology's saved views + default exclusions below.
      activeViewIds: ['default'], defaultExcludedNodeIds: [], adHocView: null,
      editingViewId: null, savedViews: [],
      autoSimulate: readAutoSimulate(id),
      viewMode: readViewMode(id),
      editSession: null,
      targetViewId: 'default',
    });
    get().loadTopologyViews(id).catch(() => {});
    get().loadEditSession(id).catch(() => {});
    // Only nodes/links/flows are on the canvas-paint critical path.
    // srlgs + customers + prometheus config are needed for side panels and
    // for the auto-simulation but not for the first paint, so they fire
    // off the critical path. The canvas can render as soon as the three
    // critical fetches resolve, and the panel data fills in shortly after.
    const [nodes, links, flows] = await Promise.all([
      api.listNodes(id),
      api.listLinks(id),
      api.listFlows(id),
    ]);
    // Clear stale srlgs/customers from the previous topology so a panel
    // doesn't briefly display the wrong rows during the deferred refetch.
    set({ nodes, links, flows, srlgs: [], customers: [] });
    // The hidden-customer set is derived from the loaded nodes, so rebuild it
    // for this topology when the master toggle is on (it defaults to on).
    if (get().hideCustomers) get().setHideCustomers(true);
    // Deferred fetches — kick off after the critical state is set so React
    // can commit the canvas first. Errors here only affect side panels.
    api.listSrlgs(id).then(srlgs => {
      if (get().currentTopologyId === id) set({ srlgs });
    }).catch(() => {});
    api.listCustomers(id).then(customers => {
      if (get().currentTopologyId === id) set({ customers });
    }).catch(() => {});
    api.getPrometheusConfig(id).then(cfg => {
      if (get().currentTopologyId !== id) return;
      if (cfg) set({ prometheusConfig: cfg, isLiveMode: cfg.enabled });
      else set({ prometheusConfig: null, isLiveMode: false });
    }).catch(() => set({ prometheusConfig: null }));
    get().autoRunSimulation();
  },

  createTopology: async (name, description, parentTopologyId = null) => {
    const topo = await api.createTopology({ name, description, parent_topology_id: parentTopologyId });
    set(s => ({ topologies: [...s.topologies, topo] }));
    return topo;
  },

  deleteTopology: async (id) => {
    await api.deleteTopology(id);
    set(s => ({
      topologies: s.topologies.filter(t => t.id !== id),
      currentTopologyId: s.currentTopologyId === id ? null : s.currentTopologyId,
      nodes: s.currentTopologyId === id ? [] : s.nodes,
      links: s.currentTopologyId === id ? [] : s.links,
      flows: s.currentTopologyId === id ? [] : s.flows,
    }));
  },

  duplicateTopology: async (id) => {
    const topo = await api.duplicateTopology(id);
    set(s => ({ topologies: [...s.topologies, topo] }));
  },

  renameTopology: async (id, name) => {
    const topo = get().topologies.find(t => t.id === id);
    const updated = await api.updateTopology(id, { name, description: topo?.description || '' });
    set(s => ({ topologies: s.topologies.map(t => t.id === id ? { ...t, name: updated.name } : t) }));
  },

  loadNodeTemplates: async () => {
    const templates = await api.listNodeTemplates();
    set({ nodeTemplates: templates });
  },

  createNodeTemplate: async (data) => {
    await api.createNodeTemplate(data);
    const templates = await api.listNodeTemplates();
    set({ nodeTemplates: templates });
  },

  deleteNodeTemplate: async (id) => {
    await api.deleteNodeTemplate(id);
    set(s => ({ nodeTemplates: s.nodeTemplates.filter(t => t.id !== id) }));
  },

  loadSiteTemplates: async () => {
    set({ siteTemplates: await api.listSiteTemplates() });
  },

  instantiateSiteTemplate: async (templateId, variables, x, y) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) throw new Error('No topology selected');
    const res = await api.instantiateSiteTemplate(topoId, templateId, { variables, x, y });
    set(s => ({ nodes: [...s.nodes, ...res.nodes], links: [...s.links, ...res.links] }));
    await addToTargetView(get, set, topoId, res.nodes.map((n: NodeInstance) => n.id));
    if (res.links.length) get().autoRunSimulation();
    return { nodes: res.nodes, patch_panels: res.patch_panels, warnings: res.warnings };
  },

  addNode: async (data) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) throw new Error('No topology selected');
    const node = await api.createNode(topoId, data);
    set(s => ({ nodes: [...s.nodes, node] }));
    await addToTargetView(get, set, topoId, [node.id]);
    // Adding a node alone doesn't change routing — no auto-sim
    return node;
  },

  updateNode: async (nodeId, data) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const wasRR = get().nodes.find(n => n.id === nodeId)?.is_route_reflector;
    const updated = await api.updateNode(topoId, nodeId, data);
    if (wasRR && data.is_route_reflector === false) {
      // Demoting an RR strips it from every other device's rr_peer_ids on the
      // backend — refetch so peer lists/counts reflect that immediately.
      // (Saves of never-RR nodes also send is_route_reflector: false; the
      // wasRR gate keeps those on the cheap single-node update path.)
      const nodes = await api.listNodes(topoId);
      set({ nodes });
    } else {
      set(s => ({ nodes: s.nodes.map(n => n.id === nodeId ? updated : n) }));
    }
    // Estimated link latencies depend on node position and site.
    if ('geo_latitude' in data || 'geo_longitude' in data || 'location' in data) refreshLinksQuietly(topoId);
  },

  deleteNode: async (nodeId) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const hadLinks = get().links.some(l => l.source_node_id === nodeId || l.target_node_id === nodeId);
    await api.deleteNode(topoId, nodeId);
    set(s => ({
      // The backend also strips the deleted node from every device's
      // rr_peer_ids — mirror that locally so a later edit-save can't
      // re-persist the stale peer id from the store copy.
      nodes: s.nodes.filter(n => n.id !== nodeId).map(n =>
        n.rr_peer_ids?.includes(nodeId)
          ? { ...n, rr_peer_ids: n.rr_peer_ids.filter(id => id !== nodeId) }
          : n),
      links: s.links.filter(l => l.source_node_id !== nodeId && l.target_node_id !== nodeId),
    }));
    if (hadLinks) get().autoRunSimulation();
  },

  deleteNodes: async (nodeIds) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const idSet = new Set(nodeIds);
    const hadLinks = get().links.some(l => idSet.has(l.source_node_id) || idSet.has(l.target_node_id));
    // Sequential (not Promise.all): each backend delete rewrites OTHER nodes'
    // rr_peer_ids lists, and concurrent read-modify-writes of the same list
    // can lose one removal (resurrecting a reference to a deleted node).
    for (const id of nodeIds) {
      await api.deleteNode(topoId, id).catch(() => {});
    }
    set(s => ({
      nodes: s.nodes.filter(n => !idSet.has(n.id)).map(n =>
        n.rr_peer_ids?.some(id => idSet.has(id))
          ? { ...n, rr_peer_ids: n.rr_peer_ids.filter(id => !idSet.has(id)) }
          : n),
      links: s.links.filter(l => !idSet.has(l.source_node_id) && !idSet.has(l.target_node_id)),
    }));
    if (hadLinks) get().autoRunSimulation();
  },

  updateNodeLocal: (nodeId, data) => {
    set(s => ({
      nodes: s.nodes.map(n => n.id === nodeId ? { ...n, ...data } : n),
    }));
  },

  addLink: async (data) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) throw new Error('No topology selected');
    const link = await api.createLink(topoId, data);
    set(s => ({ links: [...s.links, link] }));
    get().autoRunSimulation(); // New link affects routing
    return link;
  },

  updateLink: async (linkId, data) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const original = get().links.find(l => l.id === linkId);
    const updated = await api.updateLink(topoId, linkId, data);
    set(s => ({ links: s.links.map(l => l.id === linkId ? updated : l) }));
    // Only auto-sim when routing-relevant fields actually changed
    // Add more fields here as needed
    const simTriggerFields: (keyof typeof data)[] = ['cost', 'bandwidth_gbps'];
    if (original && simTriggerFields.some(f => data[f] !== undefined && data[f] !== (original as any)[f])) {
      get().autoRunSimulation();
    }
  },

  deleteLink: async (linkId) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    await api.deleteLink(topoId, linkId);
    set(s => ({ links: s.links.filter(l => l.id !== linkId) }));
    get().autoRunSimulation();
  },

  loadCustomers: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const customers = await api.listCustomers(topoId);
    set({ customers });
  },

  addCustomer: async (data) => {
    const topoId = get().currentTopologyId;
    if (!topoId) throw new Error('No topology selected');
    const customer = await api.createCustomer(topoId, data);
    set(s => ({ customers: [...s.customers, customer] }));
    return customer;
  },

  updateCustomer: async (id, data) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const updated = await api.updateCustomer(topoId, id, data);
    set(s => ({ customers: s.customers.map(c => c.id === id ? updated : c) }));
  },

  deleteCustomer: async (id) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    await api.deleteCustomer(topoId, id);
    set(s => ({
      customers: s.customers.filter(c => c.id !== id),
      nodes: s.nodes.map(n => n.customer_id === id ? { ...n, customer_id: null } : n),
    }));
  },

  addFlow: async (data) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) throw new Error('No topology selected');
    const flow = await api.createFlow(topoId, data);
    set(s => ({ flows: [...s.flows, flow] }));
    get().autoRunSimulation(); // New demand affects utilization
    return flow;
  },

  bulkAddFlows: async (flows, updateExisting = false) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return [];
    // Created + updated flows; updated ones replace their store copies below.
    const created = await api.bulkCreateFlows(topoId, flows, updateExisting);
    set(s => {
      const updatedIds = new Set(created.map((f: any) => f.id));
      const kept = s.flows.filter(f => !updatedIds.has(f.id));
      return { flows: [...kept, ...created] };
    });
    get().autoRunSimulation(); // Bulk demands affect utilization
    return created;
  },

  updateFlow: async (flowId, data) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const original = get().flows.find(f => f.id === flowId);
    const updated = await api.updateFlow(topoId, flowId, data);
    set(s => ({ flows: s.flows.map(f => f.id === flowId ? updated : f) }));
    // Only auto-sim when routing-relevant fields actually changed
    // Add more fields here as needed
    const simTriggerFields = ['bandwidth_gbps', 'source_node_id', 'dest_node_id', 'ecmp_enabled', 'routing_behavior'];
    if (original && simTriggerFields.some(f => data[f] !== undefined && data[f] !== (original as any)[f])) {
      get().autoRunSimulation();
    }
  },

  deleteFlow: async (flowId) => {
    if (!get().canEditCurrentTopology()) throw new Error('This topology is protected — enter edit mode to make changes.');
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    await api.deleteFlow(topoId, flowId);
    set(s => ({ flows: s.flows.filter(f => f.id !== flowId) }));
    get().autoRunSimulation();
  },

  analyzeFlowAvailability: async (flowId) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return null;
    if (analysisBusy(get())) throw new Error(ANALYSIS_BUSY_MSG);
    set({ isAnalyzingAvailability: true });
    try {
      // Apply the active maintenance window (same exclusions the Monte Carlo
      // panel uses) so a per-flow report run during maintenance reflects the
      // degraded network. The backend stamps the exclusions into the report.
      const excludeNodes = Array.from(get().maintenanceNodeIds);
      const excludeLinks = Array.from(get().maintenanceLinkIds);
      const report = await api.analyzeFlowAvailability(topoId, flowId, {
        exclude_node_ids: excludeNodes.length ? excludeNodes : undefined,
        exclude_link_ids: excludeLinks.length ? excludeLinks : undefined,
        include_planned: get().includePlanned,
      });
      // Cache the report onto the flow in-place so the panel's indicator and
      // the report modal pick it up without a full reload.
      set(s => ({ flows: s.flows.map(f => f.id === flowId ? { ...f, availability_report: report } : f) }));
      return report;
    } finally {
      set({ isAnalyzingAvailability: false });
    }
  },

  deleteAllFlows: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    await api.deleteAllFlows(topoId);
    set({ flows: [], simulationResult: null, highlightedFlowPaths: null });
  },

  loadFeatures: async () => {
    try {
      const f = await api.getFeatures();
      set({ features: f });
    } catch {
      // On failure leave features null; consumers treat unknown flags as off.
    }
  },

  runSimulation: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    if (analysisBusy(get())) throw new Error(ANALYSIS_BUSY_MSG);
    set({ isSimulating: true, simulationResult: null, failureAnalysisRun: false });
    try {
      const nodeExcl: number[] = Array.from(get().maintenanceNodeIds);
      const linkExcl: number[] = Array.from(get().maintenanceLinkIds);
      const result = await api.runSimulation(topoId,
        nodeExcl.length ? nodeExcl : undefined,
        linkExcl.length ? linkExcl : undefined,
        get().includePlanned,
      );
      set({ simulationResult: result, isSimulating: false });
    } catch (err) {
      console.error('Simulation failed:', err);
      set({ isSimulating: false });
      throw err;
    }
  },

  // SPOF/failure analysis — runs the expensive failure scenarios on demand
  // (Failures tab). Re-fetches the simulation with failure_analysis=true and
  // keeps the same util/flow data.
  runFailureAnalysis: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    if (analysisBusy(get())) throw new Error(ANALYSIS_BUSY_MSG);
    set({ isRunningFailures: true });
    try {
      const nodeExcl: number[] = Array.from(get().maintenanceNodeIds);
      const linkExcl: number[] = Array.from(get().maintenanceLinkIds);
      const result = await api.runSimulation(topoId,
        nodeExcl.length ? nodeExcl : undefined,
        linkExcl.length ? linkExcl : undefined,
        get().includePlanned,
        true,
      );
      set({ simulationResult: result, failureAnalysisRun: true, isRunningFailures: false });
    } catch (err) {
      console.error('Failure analysis failed:', err);
      set({ isRunningFailures: false });
      throw err;
    }
  },

  setAutoSimulate: (on: boolean) => {
    set({ autoSimulate: on });
    const topoId = get().currentTopologyId;
    if (topoId != null) {
      try { localStorage.setItem(AUTO_SIM_KEY(topoId), on ? '1' : '0'); } catch {}
    }
    // Kick off a run immediately when enabling so the canvas catches up to
    // any edits the user made while auto-sim was off.
    if (on) get().autoRunSimulation();
  },

  toggleSubmarineCables: () => {
    const next = !get().showSubmarineCables;
    set({ showSubmarineCables: next });
    try { localStorage.setItem('show_submarine_cables', next ? '1' : '0'); } catch {}
  },

  toggleDataCenters: () => {
    const next = !get().showDataCenters;
    set({ showDataCenters: next });
    try { localStorage.setItem('show_data_centers', next ? '1' : '0'); } catch {}
  },

  toggleIXPs: () => {
    const next = !get().showIXPs;
    set({ showIXPs: next });
    try { localStorage.setItem('show_ixps', next ? '1' : '0'); } catch {}
  },

  toggleCloudRegions: () => {
    const next = !get().showCloudRegions;
    set({ showCloudRegions: next });
    try { localStorage.setItem('show_cloud_regions', next ? '1' : '0'); } catch {}
  },

  toggleGeoCoverage: () => {
    const next = !get().showGeoCoverage;
    set({ showGeoCoverage: next });
    try { localStorage.setItem('show_geo_coverage', next ? '1' : '0'); } catch {}
  },

  toggleWeatherAlerts: () => {
    const next = !get().showWeatherAlerts;
    set({ showWeatherAlerts: next });
    try { localStorage.setItem('show_weather_alerts', next ? '1' : '0'); } catch {}
  },

  toggleActiveFires: () => {
    const next = !get().showActiveFires;
    set({ showActiveFires: next });
    try { localStorage.setItem('show_active_fires', next ? '1' : '0'); } catch {}
  },

  toggleEarthquakes: () => {
    const next = !get().showEarthquakes;
    set({ showEarthquakes: next });
    try { localStorage.setItem('show_earthquakes', next ? '1' : '0'); } catch {}
  },

  toggleCyclones: () => {
    const next = !get().showCyclones;
    set({ showCyclones: next });
    try { localStorage.setItem('show_cyclones', next ? '1' : '0'); } catch {}
  },

  toggleInternetOutages: () => {
    const next = !get().showInternetOutages;
    set({ showInternetOutages: next });
    try { localStorage.setItem('show_internet_outages', next ? '1' : '0'); } catch {}
  },

  toggleCoverageStrength: () => {
    const next = !get().showCoverageStrength;
    set({ showCoverageStrength: next });
    try { localStorage.setItem('show_coverage_strength', next ? '1' : '0'); } catch {}
  },

  setViewMode: (mode) => {
    set({ viewMode: mode });
    const topoId = get().currentTopologyId;
    if (topoId != null) {
      try { localStorage.setItem(VIEW_MODE_KEY(topoId), mode); } catch {}
    }
    // Geographic view and lab mode are mutually exclusive — leaving lab mode
    // on under the map would show the wrong (deployable) data source.
    if (mode === 'geographic' && (get() as any).labMode) {
      (get() as any).setLabMode?.(false);
    }
  },

  autoRunSimulation: () => {
    const { currentTopologyId, nodes, links, autoSimulate } = get();
    // Opt-in: skip entirely when the user hasn't enabled the canvas toggle.
    if (!autoSimulate) return;
    if (!currentTopologyId || nodes.length === 0 || links.length === 0) return;
    // While the user is in lab mode the canvas shows the deployable view —
    // simulation-driven coloring (utilization etc.) shouldn't apply there.
    if ((get() as any).labMode) return;
    debouncedAutoSim(async () => {
      // Skip if ANY analysis is already running — auto-sim is background, so
      // it silently defers rather than surfacing the busy error.
      if (analysisBusy(get())) return;
      // Re-check the toggle in case the user flipped it off during the
      // debounce window — we don't want a queued run to fire after opt-out.
      if (!get().autoSimulate) return;
      set({ isSimulating: true, simulationResult: null, failureAnalysisRun: false });
      try {
        const nodeExcl: number[] = Array.from(get().maintenanceNodeIds);
        const linkExcl: number[] = Array.from(get().maintenanceLinkIds);
        const result = await api.runSimulation(get().currentTopologyId!,
          nodeExcl.length ? nodeExcl : undefined,
          linkExcl.length ? linkExcl : undefined,
          get().includePlanned,
        );
        set({ simulationResult: result, isSimulating: false });
      } catch {
        set({ isSimulating: false });
      }
    });
  },

  runMonteCarlo: async (params) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    if (analysisBusy(get())) throw new Error(ANALYSIS_BUSY_MSG);
    set({ isMonteCarlo: true });
    try {
      const nodeExcl: number[] = Array.from(get().maintenanceNodeIds);
      const linkExcl: number[] = Array.from(get().maintenanceLinkIds);
      const result = await api.runMonteCarlo(topoId, {
        ...params,
        exclude_node_ids: nodeExcl.length ? nodeExcl : undefined,
        exclude_link_ids: linkExcl.length ? linkExcl : undefined,
        include_planned: get().includePlanned,
      });
      set({ monteCarloResult: result, isMonteCarlo: false });
    } catch (err) {
      console.error('Monte Carlo failed:', err);
      set({ isMonteCarlo: false });
      throw err;
    }
  },

  loadSrlgs: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const srlgs = await api.listSrlgs(topoId);
    set({ srlgs });
  },

  addSrlg: async (data) => {
    const topoId = get().currentTopologyId;
    if (!topoId) throw new Error('No topology selected');
    const srlg = await api.createSrlg(topoId, data);
    set(s => ({ srlgs: [...s.srlgs, srlg] }));
    return srlg;
  },

  updateSrlg: async (id, data) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const updated = await api.updateSrlg(topoId, id, data);
    set(s => ({ srlgs: s.srlgs.map(g => g.id === id ? updated : g) }));
  },

  deleteSrlg: async (id) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    await api.deleteSrlg(topoId, id);
    set(s => ({ srlgs: s.srlgs.filter(g => g.id !== id) }));
  },

  runMaintenanceWindow: async (params) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    if (analysisBusy(get())) throw new Error(ANALYSIS_BUSY_MSG);
    set({ isMaintenanceRunning: true });
    try {
      // Same include_planned the canvas simulation uses, so the maintenance
      // baseline is the network the user is actually looking at.
      const result = await api.runMaintenanceWindow(topoId, { ...params, include_planned: get().includePlanned });
      set({ maintenanceResult: result, isMaintenanceRunning: false });
    } catch (err) {
      set({ isMaintenanceRunning: false });
      throw err;
    }
  },

  runSrlgAnalysis: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const result = await api.runSrlgAnalysis(topoId, get().includePlanned);
    set({ srlgAnalysisResult: result });
  },

  toggleMaintenanceNode: (nodeId: number) => {
    set(s => {
      const next = new Set(s.maintenanceNodeIds);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      // Clear results if nothing left in maintenance
      const noMaint = next.size === 0 && s.maintenanceLinkIds.size === 0;
      return {
        maintenanceNodeIds: next,
        ...(noMaint ? { maintenanceResult: null, isolatedNodeIds: [] } : {}),
      };
    });
  },

  toggleMaintenanceLink: (linkId: number) => {
    set(s => {
      const next = new Set(s.maintenanceLinkIds);
      if (next.has(linkId)) next.delete(linkId);
      else next.add(linkId);
      // Clear results if nothing left in maintenance
      const noMaint = s.maintenanceNodeIds.size === 0 && next.size === 0;
      return {
        maintenanceLinkIds: next,
        ...(noMaint ? { maintenanceResult: null, isolatedNodeIds: [] } : {}),
      };
    });
  },

  clearMaintenance: () => {
    set({
      maintenanceNodeIds: new Set<number>(),
      maintenanceLinkIds: new Set<number>(),
      maintenanceResult: null,
      isolatedNodeIds: [],
    });
  },

  setIncludePlanned: (v: boolean) => {
    set({ includePlanned: v });
    get().autoRunSimulation();
  },

  setHidePlanned: (v: boolean) => {
    if (v) {
      // Hiding planned → also turn off include planned
      set({ hidePlanned: true, includePlanned: false });
    } else {
      set({ hidePlanned: false });
    }
    get().autoRunSimulation();
  },

  setHideCustomers: (v: boolean) => {
    if (v) {
      // Hide all: collect all customer IDs that have nodes (skip unassigned nodes)
      const customerIds = new Set<number>();
      for (const n of get().nodes) {
        if (n.role === 'customer' && n.customer_id) customerIds.add(n.customer_id);
      }
      set({ hideCustomers: true, hiddenCustomerIds: customerIds });
    } else {
      set({ hideCustomers: false, hiddenCustomerIds: new Set<number>() });
    }
  },

  toggleCustomerVisibility: (customerId: number) => {
    set(s => {
      const next = new Set(s.hiddenCustomerIds);
      if (next.has(customerId)) {
        next.delete(customerId);
      } else {
        next.add(customerId);
      }
      // If all are cleared, turn off the master toggle
      const hideCustomers = next.size > 0;
      return { hiddenCustomerIds: next, hideCustomers };
    });
  },

  applyMaintenance: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const nodeIds: number[] = Array.from(get().maintenanceNodeIds);
    const linkIds: number[] = Array.from(get().maintenanceLinkIds);
    if (nodeIds.length === 0 && linkIds.length === 0) return;
    if (analysisBusy(get())) throw new Error(ANALYSIS_BUSY_MSG);
    set({ isMaintenanceRunning: true, simulationResult: null });
    try {
      const result = await api.runMaintenanceWindow(topoId, {
        failed_node_ids: nodeIds,
        failed_link_ids: linkIds,
      });
      // Use the failed-state simulation results as the active simulation
      set({
        maintenanceResult: result,
        isMaintenanceRunning: false,
        isolatedNodeIds: result.isolated_node_ids || [],
        simulationResult: {
          link_utilizations: result.failed_utilizations || [],
          flow_paths: result.failed_flow_paths || [],
          failure_scenarios: [],
          single_points_of_failure: [],
        },
      });
    } catch (err) {
      set({ isMaintenanceRunning: false });
      throw err;
    }
  },

  loadSettings: async () => {
    const settings = await api.listSettings();
    set({ globalSettings: settings });
  },

  updateSetting: async (key, value) => {
    await api.updateSetting(key, value);
    const settings = await api.listSettings();
    set({ globalSettings: settings });
    // Latency estimate settings change every estimated link latency.
    const topoId = get().currentTopologyId;
    if (key.startsWith('latency_') && topoId) refreshLinksQuietly(topoId);
  },

  loadPrometheusConfig: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    try {
      const cfg = await api.getPrometheusConfig(topoId);
      set({ prometheusConfig: cfg, isLiveMode: cfg?.enabled || false });
    } catch {
      set({ prometheusConfig: null, isLiveMode: false });
    }
  },

  savePrometheusConfig: async (data: any) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const cfg = await api.savePrometheusConfig(topoId, data);
    set({ prometheusConfig: cfg });
  },

  toggleLiveMode: async (enabled: boolean) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    await api.updatePrometheusConfig(topoId, { enabled });
    set(s => ({
      prometheusConfig: s.prometheusConfig ? { ...s.prometheusConfig, enabled } : null,
      isLiveMode: enabled,
      liveMetrics: enabled ? s.liveMetrics : null,
    }));
    if (enabled) {
      get().fetchLiveMetrics();
    }
  },

  fetchLiveMetrics: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    set({ isLoadingMetrics: true });
    try {
      const result = await api.getLiveMetrics(topoId);
      set({ liveMetrics: result, isLoadingMetrics: false });
    } catch (e: any) {
      set({ isLoadingMetrics: false });
      throw e;
    }
  },

  // TopologyEditor's effect (re)loads the data when an actual view turns on.
  setUtilizationView: (v) => set({ utilizationView: v }),

  loadActualUtilization: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const seq = ++actualUtilSeq;
    const result = await api.getActualUtilization(topoId);
    // Drop stale responses (topology switched, or a newer request went out).
    if (seq === actualUtilSeq && get().currentTopologyId === topoId) set({ actualUtilization: result });
  },

  importActualUtilizationCsv: async (csv) => {
    const topoId = get().currentTopologyId;
    if (!topoId) throw new Error('No topology selected');
    const seq = ++actualUtilSeq;
    const result = await api.importActualUtilizationCsv(topoId, csv);
    if (seq === actualUtilSeq && get().currentTopologyId === topoId) set({ actualUtilization: result });
    // The import writes measured link delay into link latency_ms.
    // The import itself already succeeded, so a failed refetch must not make
    // it look failed — the links reload on the next topology load.
    if (result.latency_updated_links) {
      try {
        const links = await api.listLinks(topoId);
        if (get().currentTopologyId === topoId) {
          set({ links });
          get().autoRunSimulation();
        }
      } catch { /* stale latency until next reload */ }
    }
    return result;
  },

  clearActualUtilization: async () => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    await api.clearActualUtilization(topoId);
    await get().loadActualUtilization();
  },

  fetchHistoricalMetrics: async (days = 10, mode = 'avg') => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    set({ isLoadingMetrics: true });
    try {
      const result = await api.getHistoricalMetrics(topoId, days, mode);
      set({ liveMetrics: result, isLoadingMetrics: false });
    } catch (e: any) {
      set({ isLoadingMetrics: false });
      throw e;
    }
  },

  sendChatMessage: async (message: string) => {
    const topoId = get().currentTopologyId;
    if (!topoId) return;
    const userMsg: ChatMessage = { role: 'user', content: message };
    set(s => ({ chatMessages: [...s.chatMessages, userMsg], isChatLoading: true }));

    // Add a placeholder assistant message that we'll stream into
    const placeholderMsg: ChatMessage = { role: 'assistant', content: '' };
    set(s => ({ chatMessages: [...s.chatMessages, placeholderMsg] }));

    try {
      const metadata = await api.chatStream(
        topoId,
        [...get().chatMessages].filter(m => m.content !== ''), // exclude empty placeholder
        // onToken: append to the last message (clear tool status if present)
        (token: string) => {
          set(s => {
            const msgs = [...s.chatMessages];
            const lastMsg = msgs[msgs.length - 1];
            if (lastMsg && lastMsg.role === 'assistant') {
              const wasToolStatus = (lastMsg as any)._toolStatus;
              const content = wasToolStatus ? token : lastMsg.content + token;
              msgs[msgs.length - 1] = { role: 'assistant', content };
            }
            return { chatMessages: msgs };
          });
        },
        // onToolStatus: show tool usage, will be cleared when streaming starts
        (tool: string) => {
          set(s => {
            const msgs = [...s.chatMessages];
            const lastMsg = msgs[msgs.length - 1];
            if (lastMsg && lastMsg.role === 'assistant') {
              // Mark as tool status (will be replaced when real tokens arrive)
              msgs[msgs.length - 1] = { ...lastMsg, content: `⏳ Running ${tool}...`, _toolStatus: true } as any;
            }
            return { chatMessages: msgs };
          });
        },
      );

      set({ isChatLoading: false });

      // Handle canvas actions from agent (always, regardless of actions_performed)
      if (metadata.highlight_nodes || metadata.highlight_links) {
        set({ agentHighlightNodeIds: metadata.highlight_nodes || [], agentHighlightLinkIds: metadata.highlight_links || [] });
      }
      if (metadata.focus_node_id) {
        set({ selectedNodeId: metadata.focus_node_id, selectedLinkId: null });
      }
      if (metadata.focus_link_id) {
        set({ selectedLinkId: metadata.focus_link_id, selectedNodeId: null });
      }
      if (metadata.set_color_scheme) {
        set({ colorScheme: metadata.set_color_scheme });
      }
      if (metadata.set_toggles) {
        const t = metadata.set_toggles;
        const updates: any = {};
        if (t.hide_planned !== undefined) updates.hidePlanned = t.hide_planned;
        if (t.include_planned !== undefined) updates.includePlanned = t.include_planned;
        // If hiding planned, force include off
        if (t.hide_planned === true) updates.includePlanned = false;
        set(updates);
      }
      if (metadata.report_download) {
        const rd = metadata.report_download;
        if (rd.type === 'simulation') {
          api.exportSimulationCsv(rd.topology_id, undefined, undefined, rd.include_planned).catch(() => {});
        } else if (rd.type === 'cost_allocation' && rd.csv) {
          const blob = new Blob([rd.csv], { type: 'text/csv' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'cost_allocation_report.csv';
          a.click();
          URL.revokeObjectURL(url);
        }
      }

      if (metadata.maintenance_node_ids || metadata.maintenance_link_ids) {
        set({
          maintenanceNodeIds: new Set(metadata.maintenance_node_ids || []),
          maintenanceLinkIds: new Set(metadata.maintenance_link_ids || []),
          ...(metadata.maintenance_result ? { maintenanceResult: metadata.maintenance_result } : {}),
        });
      }

      if (metadata.actions_performed && metadata.actions_performed.length > 0) {
        const [nodes, links, flows] = await Promise.all([
          api.listNodes(topoId), api.listLinks(topoId), api.listFlows(topoId),
        ]);
        const newState: any = { nodes, links, flows };
        if (metadata.simulation_result) newState.simulationResult = metadata.simulation_result;
        if (metadata.monte_carlo_result) newState.monteCarloResult = metadata.monte_carlo_result;
        set(newState);
        if (!metadata.simulation_result) get().autoRunSimulation();
      }
    } catch {
      set(s => {
        const msgs = [...s.chatMessages];
        const lastMsg = msgs[msgs.length - 1];
        if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content) {
          msgs[msgs.length - 1] = { ...lastMsg, content: 'Sorry, something went wrong. Please try again.' };
        } else {
          msgs.push({ role: 'assistant', content: 'Sorry, something went wrong. Please try again.' });
        }
        return { chatMessages: msgs, isChatLoading: false };
      });
    }
  },

  clearChat: () => set({ chatMessages: [] }),

  pendingLink: null,
  setPendingLink: (link) => set({ pendingLink: link }),
  setColorScheme: (scheme) => set({ colorScheme: scheme }),
  toggleInterfaceNames: () => set(s => ({ showInterfaceNames: !s.showInterfaceNames })),
  toggleLinkWidthBySpeed: () => set(s => ({ linkWidthBySpeed: !s.linkWidthBySpeed })),
  toggleLinkColorByUtilization: () => set(s => ({ linkColorByUtilization: !s.linkColorByUtilization })),
  setCustomerUtilEnabled: (on) => set({ customerUtilEnabled: on }),
  setCustomerUtilIds: (ids) => set({ customerUtilIds: ids }),
  toggleShowSatelliteLinks: () => {
    const next = !get().showSatelliteLinks;
    set({ showSatelliteLinks: next });
    try { localStorage.setItem('show_satellite_links', next ? '1' : '0'); } catch { /* ignore */ }
  },

  setLabMode: async (on: boolean) => {
    const topoId = get().currentTopologyId;
    if (!on || !topoId) {
      // Auto-close the lab side panel on exit (it auto-opens on entry).
      // Also close any open console tabs — they're lab-mode only.
      const closing: any = { labMode: false, labView: null, consoleTabs: [], activeConsoleTabId: null };
      if (get().sidePanel === 'lab') closing.sidePanel = 'none';
      set(closing);
      return;
    }
    // Lab, Config Design, and Service are mutually exclusive — they swap the
    // main view. Clicking one while another is on transparently switches.
    set({ labMode: true, configMode: false, serviceMode: false });
    try {
      const view = await api.getLabView(topoId);
      set({ labView: view });
    } catch (e) {
      set({ labMode: false, labView: null });
      throw e;
    }
  },

  openConsoleTab: (type, nodeId, nodeName, cli) => {
    const id = `${type}:${nodeId}`;
    set(s => {
      const exists = (s as any).consoleTabs.find((t: any) => t.id === id);
      if (exists) return { activeConsoleTabId: id } as any;
      return {
        consoleTabs: [...(s as any).consoleTabs, { id, type, nodeId, nodeName, cli }],
        activeConsoleTabId: id,
      } as any;
    });
  },
  closeConsoleTab: (id) => {
    set(s => {
      const tabs = (s as any).consoleTabs.filter((t: any) => t.id !== id);
      const active = (s as any).activeConsoleTabId === id
        ? (tabs.length > 0 ? tabs[tabs.length - 1].id : null)
        : (s as any).activeConsoleTabId;
      return { consoleTabs: tabs, activeConsoleTabId: active } as any;
    });
  },
  setActiveConsoleTab: (id) => set({ activeConsoleTabId: id } as any),
  setLinkLabelFields: (fields) => set({ linkLabelFields: fields }),
  toggleGroupByLocation: () => set(s => ({ groupByLocation: !s.groupByLocation })),
  setLinkColors: (colors) => set(s => ({ linkColors: { ...s.linkColors, ...colors } })),
  editRequested: false,
  setSelectedNode: (id, edit) => set({ selectedNodeId: id, selectedLinkId: null, selectedFlowId: null, editRequested: !!edit, isolatedNodeIds: [] }),
  setSelectedLink: (id, edit) => set({ selectedLinkId: id, selectedNodeId: null, selectedFlowId: null, editRequested: !!edit, isolatedNodeIds: [] }),
  setSelectedFlow: (id, edit) => set({ selectedFlowId: id, selectedNodeId: null, selectedLinkId: null, editRequested: !!edit, isolatedNodeIds: [] }),
  setHighlightedFlowPaths: (paths) => set({ highlightedFlowPaths: paths }),
  centerNodeRequest: null,
  requestCenterNode: (id) => set(s => ({ centerNodeRequest: { id, seq: (s.centerNodeRequest?.seq ?? 0) + 1 } })),
  setIsolatedNodeIds: (ids) => set({ isolatedNodeIds: ids }),

  // ---- Sub-topology views ----
  loadTopologyViews: async (topologyId) => {
    try {
      const [views, def] = await Promise.all([
        api.listTopologyViews(topologyId),
        api.getDefaultView(topologyId).catch(() => ({ excluded_node_ids: [] })),
      ]);
      // Preserve the user's in-session drag order (the layer stack) across
      // reloads: keep known ids in their current order, append any new ones.
      const prev = get().savedViews.map(v => v.id);
      const ordered = [...views].sort((a, b) => {
        const ia = prev.indexOf(a.id), ib = prev.indexOf(b.id);
        if (ia === -1 || ib === -1) return ia === ib ? 0 : ia === -1 ? 1 : -1;
        return ia - ib;
      });
      set({ savedViews: ordered, defaultExcludedNodeIds: def.excluded_node_ids || [] });
    } catch {
      set({ savedViews: [], defaultExcludedNodeIds: [] });
    }
  },
  // Show/hide a view in the stacked union. Never leave the canvas with zero
  // active views — fall back to the Default view.
  toggleActiveView: (viewId) => set(s => {
    const has = s.activeViewIds.includes(viewId);
    const next = has ? s.activeViewIds.filter(v => v !== viewId) : [...s.activeViewIds, viewId];
    return {
      activeViewIds: next.length ? next : ['default'],
      // Stop editing a view that's no longer displayed.
      editingViewId: has && s.editingViewId === viewId ? null : s.editingViewId,
    };
  }),
  // Move the dragged view to the target's position in the saved-views list.
  reorderSavedView: (draggedId, targetId) => set(s => {
    if (draggedId === targetId) return {};
    const arr = [...s.savedViews];
    const from = arr.findIndex(v => v.id === draggedId);
    const to = arr.findIndex(v => v.id === targetId);
    if (from < 0 || to < 0) return {};
    const [moved] = arr.splice(from, 1);
    arr.splice(to, 0, moved);
    return { savedViews: arr };
  }),
  // Editing implies the view is displayed. null exits edit mode.
  setEditingView: (viewId) => set(s => {
    if (viewId == null) return { editingViewId: null };
    const active = s.activeViewIds.includes(viewId) ? s.activeViewIds : [...s.activeViewIds, viewId];
    return { editingViewId: viewId, activeViewIds: active };
  }),
  resetToDefaultView: () => set({ activeViewIds: ['default'], editingViewId: null, adHocView: null }),
  // Transient focus on the current multi-selection (not persisted).
  setAdHocView: (nodeIds) => set({ adHocView: nodeIds, activeViewIds: ['adhoc'], editingViewId: null }),
  // Add/remove nodes for whichever view is being edited. For the Default view
  // this updates the exclusion set (add = un-exclude, remove = exclude); for a
  // saved view it updates its node_ids. Eagerly persisted to the backend.
  addNodesToEditingView: async (nodeIds) => {
    const { editingViewId, currentTopologyId } = get();
    if (editingViewId == null || currentTopologyId == null || !nodeIds.length) return;
    if (editingViewId === 'default') {
      const excl = get().defaultExcludedNodeIds.filter(id => !nodeIds.includes(id));
      set({ defaultExcludedNodeIds: excl });
      try { await api.setDefaultView(currentTopologyId, excl); } catch { /* resyncs on next load */ }
    } else {
      const v = get().savedViews.find(x => x.id === editingViewId);
      if (!v) return;
      const merged = Array.from(new Set([...v.node_ids, ...nodeIds]));
      set(s => ({ savedViews: s.savedViews.map(x => x.id === editingViewId ? { ...x, node_ids: merged } : x) }));
      try { await api.updateTopologyView(currentTopologyId, editingViewId as number, { node_ids: merged }); } catch { /* see above */ }
    }
  },
  removeNodesFromEditingView: async (nodeIds) => {
    const { editingViewId, currentTopologyId } = get();
    if (editingViewId == null || currentTopologyId == null || !nodeIds.length) return;
    if (editingViewId === 'default') {
      const excl = Array.from(new Set([...get().defaultExcludedNodeIds, ...nodeIds]));
      set({ defaultExcludedNodeIds: excl });
      try { await api.setDefaultView(currentTopologyId, excl); } catch { /* resyncs on next load */ }
    } else {
      const v = get().savedViews.find(x => x.id === editingViewId);
      if (!v) return;
      const drop = new Set(nodeIds);
      const next = v.node_ids.filter(id => !drop.has(id));
      set(s => ({ savedViews: s.savedViews.map(x => x.id === editingViewId ? { ...x, node_ids: next } : x) }));
      try { await api.updateTopologyView(currentTopologyId, editingViewId as number, { node_ids: next }); } catch { /* see above */ }
    }
  },
  saveTopologyView: async (topologyId, name, nodeIds, description = '') => {
    // Give each new view a distinct default layer color (cycling the palette by
    // how many views already exist); the user can recolor it from the Views menu.
    const color = VIEW_LAYER_PALETTE[get().savedViews.length % VIEW_LAYER_PALETTE.length];
    const created = await api.createTopologyView(topologyId, { name, description, node_ids: nodeIds, color });
    await get().loadTopologyViews(topologyId);
    // Stack the freshly-saved view alongside whatever's already displayed.
    set(s => ({ activeViewIds: s.activeViewIds.includes(created.id) ? s.activeViewIds : [...s.activeViewIds, created.id] }));
    return created;
  },
  updateTopologyView: async (topologyId, viewId, data) => {
    await api.updateTopologyView(topologyId, viewId, data);
    await get().loadTopologyViews(topologyId);
  },
  deleteTopologyView: async (topologyId, viewId) => {
    await api.deleteTopologyView(topologyId, viewId);
    set(s => {
      const next = s.activeViewIds.filter(v => v !== viewId);
      return {
        activeViewIds: next.length ? next : ['default'],
        editingViewId: s.editingViewId === viewId ? null : s.editingViewId,
        targetViewId: s.targetViewId === viewId ? 'default' : s.targetViewId,
      };
    });
    await get().loadTopologyViews(topologyId);
  },
  setAgentHighlights: (nodeIds, linkIds) => set({ agentHighlightNodeIds: nodeIds, agentHighlightLinkIds: linkIds }),
  setSidePanel: (panel) => {
    const prev = get().sidePanel;
    const updates: Partial<AppState> = { sidePanel: panel };
    // Clear highlights when leaving maintenance panel
    if (prev === 'maintenance' && panel !== 'maintenance') {
      updates.highlightedFlowPaths = null;
      updates.selectedLinkId = null;
      updates.selectedFlowId = null;
    }
    set(updates);
  },

  pathEnds: { a: null, z: null },
  setPathEnd: (which, end) => set(s => ({ pathEnds: { ...s.pathEnds, [which]: end } })),
  pathBuilder: { active: false, sourceNodeId: null, destNodeId: null, nodeIds: [], linkIds: [] },

  startPathBuilder: (sourceNodeId, destNodeId) => set({
    pathBuilder: { active: true, sourceNodeId, destNodeId, nodeIds: [sourceNodeId], linkIds: [] },
  }),

  pathBuilderAddLink: (linkId, nextNodeId) => set(s => {
    const pb = s.pathBuilder;
    if (!pb.active) return {};
    return {
      pathBuilder: {
        ...pb,
        nodeIds: [...pb.nodeIds, nextNodeId],
        linkIds: [...pb.linkIds, linkId],
      },
    };
  }),

  pathBuilderUndo: () => set(s => {
    const pb = s.pathBuilder;
    if (!pb.active || pb.nodeIds.length <= 1) return {};
    return {
      pathBuilder: {
        ...pb,
        nodeIds: pb.nodeIds.slice(0, -1),
        linkIds: pb.linkIds.slice(0, -1),
      },
    };
  }),

  cancelPathBuilder: () => set({
    pathBuilder: { active: false, sourceNodeId: null, destNodeId: null, nodeIds: [], linkIds: [] },
  }),

  finishPathBuilder: () => {
    const pb = get().pathBuilder;
    if (!pb.active || pb.nodeIds.length < 2) return null;
    const result = { nodeIds: [...pb.nodeIds], linkIds: [...pb.linkIds] };
    set({ pathBuilder: { active: false, sourceNodeId: null, destNodeId: null, nodeIds: [], linkIds: [] } });
    return result;
  },
}));

export interface InterfaceSpec {
  name: string;
  speed_gbps: number;
  description: string;
  status?: 'free' | 'reserved';
}

export interface NodeTemplate {
  id: number;
  name: string;
  vendor: string;
  model: string;
  platform: string;
  capacity_gbps: number;
  default_mtbf_hours: number;
  default_mttr_hours: number;
  default_interfaces: InterfaceSpec[];
  role?: string;
  clab_kind?: string;
  image_ref?: string;
  min_memory_mb?: number;
  min_cpu_cores?: number;
  requires_hugepages?: boolean;
  startup_config?: string;
}

/** A reusable small site (devices + links + patch panels) — see
 * backend services/site_templates.py. Text fields may hold {{variable}}
 * placeholders; nodes, links and panels reference each other by `key`. */
export interface SiteTemplateVariable { name: string; label: string; default: string; required: boolean }
export interface SiteTemplateNode {
  key: string; template_id: number | null; name: string; location: string; mgmt_ip: string;
  is_planned: boolean; dx: number; dy: number;
  /** Interfaces created with status 'reserved' (missing on older templates). */
  reserved_interfaces?: string[];
}
export interface SiteTemplateLink {
  key: string; source: string; source_interface: string; target: string; target_interface: string;
  latency_ms: number; carrier: string; circuit_id: string; link_id_label: string; is_planned: boolean;
}
export type PatchPortStatus = 'unused' | 'cabled_free' | 'cabled_reserved' | 'cabled_used';
export interface SiteTemplatePort {
  status: PatchPortStatus; label: string; node: string | null; interface: string;
  link: string | null; notes: string; rear_port: number | null;
}
export interface SiteTemplatePanel {
  key: string; name: string; location: string; rack: string; start_u: number | null; height_u: number;
  rows: number; ports_per_row: number; notes: string; in_front_of: string | null;
  ports: Record<string, SiteTemplatePort>;
}
export interface SiteTemplateDefinition {
  variables: SiteTemplateVariable[]; nodes: SiteTemplateNode[]; links: SiteTemplateLink[];
  patch_panels: SiteTemplatePanel[];
}
export interface SiteTemplate {
  id: number; name: string; description: string; definition: SiteTemplateDefinition; updated_at: string | null;
}

export interface Topology {
  id: number;
  name: string;
  description: string;
  parent_topology_id: number | null;
  is_protected?: boolean;
  device_count?: number;  // local devices (list endpoint only)
}

export interface NodeInstance {
  id: number;
  topology_id: number;
  template_id: number | null;
  name: string;
  vendor: string;
  model: string;
  platform: string;
  x: number;
  y: number;
  interfaces: InterfaceSpec[];
  latitude: number | null;   // logical (equirectangular projection of x/y)
  longitude: number | null;
  geo_latitude?: number | null;   // real-world map coords (independent of x/y)
  geo_longitude?: number | null;
  mtbf_hours: number | null;
  mttr_hours: number | null;
  capacity_gbps: number;
  location: string;
  prometheus_id: string;
  mgmt_ip: string;
  is_planned: boolean;
  role: string;
  customer_id: number | null;
  color: string;
  is_inherited?: boolean;
  is_route_reflector?: boolean;
  is_super_route_reflector?: boolean;
  rr_peer_ids?: number[];
}

export interface LinkData {
  id: number;
  topology_id: number;
  source_node_id: number;
  target_node_id: number;
  source_interface: string;
  target_interface: string;
  bandwidth_gbps: number;
  cost: number;
  /** Stored value; only meaningful when latency_source is "measured". */
  latency_ms: number;
  /** "measured" = latency_ms is known (entered or imported); "unknown" = estimated. */
  latency_source?: 'measured' | 'unknown';
  /** How the link is carried (picks the latency estimator). Satellite links
   *  connect a Satellite Dish to a Satellite PoP; the provider is `carrier`. */
  transport?: 'terrestrial' | 'satellite' | string;
  /** Satellite links only: "leo" | "meo" | "geo". */
  orbit?: string;
  /** Effective latency from the backend: measured, else estimated, else null. */
  latency_effective_ms?: number | null;
  latency_effective_source?: 'measured' | 'estimated' | 'unknown';
  latency_method?: string;
  carrier: string;
  mtbf_hours: number | null;
  mttr_hours: number | null;
  sr_attributes: Record<string, string>;
  circuit_cost: number;
  circuit_id: string;
  link_id_label: string;
  source_ip: string;
  target_ip: string;
  source_ipv6: string;
  target_ipv6: string;
  is_planned: boolean;
  color: string;
  // geo-view fiber route: a single line [[lng,lat],...] or multi-part
  // [[[lng,lat],...],...] (gaps between pieces preserved, no connector drawn).
  geo_path?: [number, number][] | [number, number][][] | null;
  is_inherited?: boolean;
}

export interface Customer {
  id: number;
  topology_id: number;
  name: string;
  contact_email: string;
  contact_phone: string;
  notes: string;
  color: string;
}

export interface TopologyVersion {
  id: number;
  topology_id: number;
  name: string;
  description: string;
  node_count: number;
  link_count: number;
  flow_count: number;
  created_by?: string;
  created_at: string;
}

export interface FlowData {
  id: number;
  topology_id: number;
  name: string;
  description: string;
  source_node_id: number;
  dest_node_id: number;
  source_interface: string;
  dest_interface: string;
  bandwidth_gbps: number;
  routing_behavior: 'shortest_path' | 'lowest_latency' | 'optimized' | 'explicit';
  explicit_path: number[];
  explicit_link_ids: number[];
  ecmp_enabled: boolean;
  is_inherited?: boolean;
  // Cached Monte Carlo availability report (null until the user runs
  // "Analyze availability" on the flow). See AvailabilityReport.
  availability_report?: AvailabilityReport | null;
}

export interface AvailabilityReport {
  flow_id: number;
  flow_name: string;
  source_node_name: string;
  dest_node_name: string;
  analyzed_at: string;
  iterations: number;
  availability: number;        // mean availability, 0..1
  downtime_min_yr: number;     // mean downtime minutes/year
  std_dev: number;             // availability std dev
  downtime_std_min_yr: number;
  disjoint_paths: number;
  outage_iterations: number;
  zero_downtime_iterations: number;
  downtime_percentiles_min: { p50: number; p90: number; p95: number; p99: number; max: number };
  availability_percentiles: { p1: number; p5: number; p10: number; p50: number; min: number };
  downtime_histogram_min: Array<{ bin_start: number; bin_end: number; count: number }>;
  a_devices?: Array<{ id: number; name: string }>;
  z_devices?: Array<{ id: number; name: string }>;
  // Maintenance-window exclusions that were applied when this report ran.
  excluded_nodes?: Array<{ id: number; name: string }>;
  excluded_links?: Array<{ id: number }>;
  error?: string;
}

export interface LinkUtilization {
  link_id: number;
  source_node_id: number;
  target_node_id: number;
  bandwidth_gbps: number;
  used_gbps: number;
  utilization_pct: number;
  flow_ids: number[];
  cost_per_gb: number;
}

// Which numbers drive link utilization on the map: simulated demand, or the
// measured (imported / live) peak over the trailing 30 or 90 days.
export type UtilizationView = 'demand' | 'actual_30d' | 'actual_90d';

export interface ActualLinkUtilization {
  link_id: number;
  source_node_id: number;
  target_node_id: number;
  bandwidth_gbps: number;
  peak_30d_bps: number | null;
  peak_90d_bps: number | null;
  link_delay_us: number | null;
  source_matched: boolean;
  target_matched: boolean;
  has_data: boolean;
}

export interface ActualUtilizationResult {
  links: ActualLinkUtilization[];
  metric_rows: number;
  links_with_data: number;
  total_links: number;
  unmatched_instances: string[];
  unmatched_interfaces: { instance: string; interface: string }[];
  unmatched_interface_count: number;
  updated_at: string | null;
  // Present on an import response only.
  imported_rows?: number;
  latency_updated_links?: number;
  latency_skipped_inherited?: number;
  errors?: string[];
  error_count?: number;
}

export interface FlowPath {
  flow_id: number;
  flow_name: string;
  paths: number[][];
  link_ids_per_path: number[][];
  bandwidth_per_path_gbps: number[];
  latency_per_path_ms?: number[];
  /** Per-path highlight colors (path analysis); default amber when absent. */
  path_colors?: string[];
}

/** One end of a path-analysis query: a node or a site (location). */
export interface PathEnd {
  node_id?: number;
  location?: string;
}

export interface AnalyzedPath {
  node_ids: number[];
  node_names: string[];
  link_ids: number[];
  hops: number;
  cost: number;
  /** Measured + estimated; links with unknown latency add nothing. */
  latency_ms: number;
  latency_measured_ms: number;
  latency_estimated_ms: number;
  latency_estimated_links: number;
  latency_unknown_links: number;
  bottleneck_gbps: number;
}

export interface PathAnalysisResult {
  mode: 'shortest' | 'disjoint';
  a_node_ids: number[];
  z_node_ids: number[];
  shortest?: { lowest_cost: AnalyzedPath; lowest_latency: AnalyzedPath | null; same_path: boolean };
  disjoint?: {
    independence: 'link' | 'node' | 'srlg';
    metric: 'cost' | 'latency';
    method: 'optimal' | 'greedy';
    requested: number;
    paths: AnalyzedPath[];
    shared_srlgs: { paths: [number, number]; srlgs: string[] }[];
  };
}

export interface FailureScenario {
  failed_element_type: 'node' | 'link' | 'srlg';
  failed_element_id: number;
  affected_flows: number[];
  link_utilizations: LinkUtilization[];
  spof: boolean;
  isolated_node_ids?: number[];
  isolated_node_names?: string[];
}

export interface SrlgMember {
  member_type: 'node' | 'link';
  member_id: number;
}

export interface SrlgGroup {
  id: number;
  topology_id: number;
  name: string;
  description: string;
  srlg_type: string;
  members: SrlgMember[];
}

export interface MaintenanceAffectedFlow {
  flow_id: number;
  flow_name: string;
  status: 'broken' | 'rerouted';
}

export interface MaintenanceResult {
  failed_node_ids: number[];
  failed_link_ids: number[];
  failed_node_names: string[];
  failed_link_names: string[];
  affected_flows: MaintenanceAffectedFlow[];
  total_flows: number;
  broken_count: number;
  rerouted_count: number;
  unaffected_count: number;
  capacity_exceeded: boolean;
  // Newly overloaded by the maintenance window (baseline <=100% -> failed >100%).
  overloaded_links: { link_id: number; name: string; utilization_pct: number; baseline_utilization_pct?: number; used_gbps: number; bandwidth_gbps: number }[];
  isolated_node_ids: number[];
  isolated_node_names: string[];
  baseline_utilizations: LinkUtilization[];
  failed_utilizations: LinkUtilization[];
  failed_flow_paths?: FlowPath[];
  baseline_flow_paths?: FlowPath[];
}

export interface SrlgAnalysisResult {
  srlg_id: number;
  srlg_name: string;
  srlg_type: string;
  member_count: number;
  affected_flow_ids: number[];
  affected_flow_count: number;
  max_utilization_pct: number;
  capacity_exceeded: boolean;
  isolated_node_ids: number[];
  isolated_node_names: string[];
  is_spof: boolean;
}

export interface SimulationResult {
  link_utilizations: LinkUtilization[];
  flow_paths: FlowPath[];
  failure_scenarios: FailureScenario[];
  single_points_of_failure: { type: string; id: number; name: string; affected_flows: number }[];
}

export interface MonteCarloPathResult {
  a_node: string;
  z_node: string;
  a_location: string;
  z_location: string;
  single_homed_availability: number | null;
  single_homed_downtime_min_yr: number | null;
  dual_homed_availability: number | null;
  dual_homed_downtime_min_yr: number | null;
  single_homed_paths: number;
  dual_homed_paths: number;
  failure_breakdown: { node_failures: number; link_failures: number };
}

export interface MonteCarloResult {
  iterations: number;
  a_location: string;
  z_location: string;
  connection_type: string;
  pair_results: MonteCarloPathResult[];
  overall_single_homed_availability: number | null;
  overall_dual_homed_availability: number | null;
  overall_single_downtime_min_yr: number | null;
  overall_dual_downtime_min_yr: number | null;
  error?: string;
}

export interface GlobalSetting {
  id: number;
  key: string;
  value: number;
  description: string;
}

export type ColorScheme = 'utilization' | 'cost_per_gb' | 'criticality' | 'circuit_cost' | 'latency';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatResponse {
  message: string;
  actions_performed: string[];
  simulation_result?: SimulationResult;
  monte_carlo_result?: MonteCarloResult;
}

export interface PrometheusConfig {
  id: number;
  topology_id: number;
  url: string;
  auth_type: string;
  username: string;
  password: string;
  bearer_token: string;
  enabled: boolean;
  in_octets_metric: string;
  out_octets_metric: string;
  speed_metric: string;
  interface_label: string;
  instance_label: string;
  rate_interval: string;
  mode: 'external' | 'builtin';
}

export interface SnmpCredential {
  id: number;
  topology_id: number;
  version: 2 | 3;
  community: string;
  security_level: string;
  username: string;
  auth_password: string;
  auth_protocol: string;
  priv_password: string;
  priv_protocol: string;
}

export interface LiveMetricsResult {
  link_utilizations: LinkUtilization[];
  errors: string[];
  timestamp: string;
  mapped_nodes?: number;
  total_nodes?: number;
  mode?: string;
  days?: number;
}

export interface LinkColorConfig {
  unused: string;
  low: string;
  mid: string;
  high: string;
  critical: string;
  overloaded: string;
}

export interface ExternalSystem {
  id: number;
  topology_id: number;
  name: string;
  base_url: string;
  auth_type: 'api_key' | 'bearer' | 'basic';
  auth_header_name: string;
  auth_value: string;
  enabled: boolean;
  verify_ssl: boolean;
}

export interface ExternalTable {
  name: string;
  primary_key?: string;
  description?: string;
}

export interface ExternalColumn {
  name: string;
  type?: string;
  nullable?: boolean;
  primary_key?: boolean;
}

export interface ExternalTableSchema {
  table: string;
  primary_key: string;
  columns: ExternalColumn[];
}

export interface ResourceMapping {
  id: number;
  external_system_id: number;
  phanero_resource: 'node' | 'link';
  target_table: string;
  primary_key_column: string;
  field_map: Record<string, string>;
  enabled: boolean;
}

export interface SyncQueueEntry {
  id: number;
  phanero_resource: string;
  phanero_id: number;
  operation: 'create' | 'update' | 'delete';
  status: 'pending' | 'success' | 'failed' | 'cancelled';
  retry_count: number;
  last_error: string;
  created_at: string | null;
  completed_at: string | null;
}

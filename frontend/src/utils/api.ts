import type { ActualUtilizationResult, SiteTemplate, SiteTemplateDefinition, PathEnd, PathAnalysisResult } from '../types';

const BASE = '/api';

/** Save a fetch Response body as a file download. */
async function downloadResponse(res: Response, filename: string) {
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('auth_token');
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

function handle401(status: number) {
  if (status === 401) {
    localStorage.removeItem('auth_token');
    window.location.href = '/';
  }
}

async function request<T>(path: string, options?: RequestInit, noAuthRedirect = false): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: authHeaders(),
    ...options,
  });
  if (!res.ok) {
    // Skip the global "session expired → logout" redirect for re-auth calls
    // where a 401 just means a wrong password, not an invalid token.
    if (!noAuthRedirect) handle401(res.status);
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }
  return res.json();
}

export const api = {
  // Feature flags (gated server-side behind env vars)
  getFeatures: () => request<{
    lab_enabled: boolean;
    lab_telemetry_enabled: boolean;
    service_enabled: boolean;
    config_design_enabled: boolean;
    geo_enabled: boolean;
    ai_enabled: boolean;
    discovery_enabled: boolean;
    prometheus_enabled: boolean;
    cost_enabled: boolean;
    integrations_enabled: boolean;
    patch_panels_enabled?: boolean;
    map?: { style_url: string; tiles_url: string; theme: string; api_key: string };
  }>('/features'),
  // Submarine cable routes + landing points (GeoJSON), proxied + cached by
  // the backend from TeleGeography. Used as an optional geographic overlay.
  getSubmarineCables: () => request<{
    cables: any; landing_points: any; attribution?: string;
  }>('/map/submarine-cables'),
  // Per-cable ownership (id → {owners, suppliers, rfs, provider}) + a provider
  // summary, so cables can be colored/filtered by owner (Google, Meta, etc.).
  getSubmarineCableOwners: () => request<{
    owners: Record<string, { owners: string; suppliers: string; rfs: string; rfs_year: number | null; length: string; length_km: number | null; provider: string }>;
    providers: { key: string; label: string; color: string; count: number }[];
    cable_count: number; note?: string; attribution?: string;
  }>('/map/submarine-cable-owners'),

  // Carrier lookup (PeeringDB): who you can interconnect with in a region.
  getFacilityCarriers: (facId: number) => request<{
    facility: { id: number; name: string; operator?: string; city?: string; country?: string };
    carriers: { net_id: number; asn: number; name: string; type: string; website: string; is_satellite?: boolean }[];
    types: { type: string; count: number }[]; count: number; attribution?: string;
  }>(`/map/facility-carriers?fac_id=${facId}`),
  // Networks peering at an IX (the IX counterpart of facility carriers).
  getIxCarriers: (ixId: number) => request<{
    ix: { id: number; name: string; city?: string; country?: string };
    carriers: { net_id: number; asn: number; name: string; type: string; website: string; is_satellite?: boolean }[];
    types: { type: string; count: number }[]; count: number; attribution?: string;
  }>(`/map/ix-carriers?ix_id=${ixId}`),
  getRegionCarriers: (country: string) => request<{
    country: string;
    carriers: { net_id: number; asn: number; name: string; type: string; website: string; site_count: number; is_satellite?: boolean }[];
    types: { type: string; count: number }[]; count: number;
    facilities_covered: number; facilities_total: number; attribution?: string;
  }>(`/map/region-carriers?country=${encodeURIComponent(country)}`),
  // Active severe-weather warnings (US NWS) as GeoJSON polygons — an outage
  // risk proxy for the geographic view.
  getWeatherAlerts: () => request<{
    alerts: any;
    severities: { severity: string; color: string; count: number }[];
    events: { event: string; count: number }[];
    activity: { active: number; possible: number };
    count: number; note?: string; attribution?: string;
  }>('/map/weather-alerts'),
  // Current US wildfires (NIFC WFIGS): perimeters + incident points — real
  // "active event" data, distinct from the weather alerts.
  getActiveFires: () => request<{
    perimeters: any; incidents: any;
    counts: { perimeters: number; incidents: number };
    note?: string; attribution?: string;
  }>('/map/active-fires'),
  // Recent earthquakes (USGS, M2.5+ past day) as GeoJSON points.
  getEarthquakes: () => request<{
    earthquakes: any; count: number; note?: string; attribution?: string;
  }>('/map/earthquakes'),
  // Active tropical cyclones (NOAA NHC) as point positions.
  getCyclones: () => request<{
    cyclones: any; count: number; note?: string; attribution?: string;
  }>('/map/cyclones'),
  // Countries with recent internet-outage activity (IODA) as centroid markers.
  getInternetOutages: () => request<{
    outages: any; count: number; note?: string; attribution?: string;
  }>('/map/internet-outages'),
  // Address/place lookup (OpenStreetMap Nominatim, proxied + cached) so the
  // map can re-orient to a typed location.
  geocode: (q: string, limit = 5) => request<{
    query: string;
    results: { display_name: string; lat: number; lon: number; type: string; bbox: [number, number, number, number] | null }[];
    attribution?: string;
  }>(`/map/geocode?q=${encodeURIComponent(q)}&limit=${limit}`),
  // Heuristic carrier-graph path(s) from an A-loc to a Z-loc.
  getPath: (aLat: number, aLon: number, zLat: number, zLon: number, alts = 2) => request<{
    a: { snap: { name: string; city?: string; country?: string; lonlat: [number, number] | null }; snap_km: number };
    z: { snap: { name: string; city?: string; country?: string; lonlat: [number, number] | null }; snap_km: number };
    paths: { hops: number; segments: { carrier: { asn: number; name: string; type: string }; from: { name: string; city?: string; country?: string; kind: string; lonlat: [number, number] | null }; to: { name: string; city?: string; country?: string; kind: string; lonlat: [number, number] | null } }[] }[];
    note?: string; attribution?: string;
  }>(`/map/path?a_lat=${aLat}&a_lon=${aLon}&z_lat=${zLat}&z_lon=${zLon}&alts=${alts}`),
  getNearestFacilities: (lat: number, lon: number, limit = 8) => request<{
    point: { lat: number; lon: number };
    facilities: { id: number; name: string; operator?: string; city?: string; country?: string; net_count?: number; distance_km: number }[];
    attribution?: string;
  }>(`/map/nearest-facilities?lat=${lat}&lon=${lon}&limit=${limit}`),
  // A satellite (LEO) operator's interconnection PoPs (facilities + IXPs).
  getOperatorPops: (key: string) => request<{
    operator: string; key: string; color: string;
    pops: any; counts: { facilities: number; ixps: number };
    note?: string; attribution?: string;
  }>(`/map/operator-pops?key=${encodeURIComponent(key)}`),
  getCarrier: (netId: number) => request<{
    net_id: number; asn: number; name: string; aka: string; type: string; website: string;
    policy_general: string; policy_url: string; irr_as_set: string; looking_glass: string;
    notes: string; peeringdb_url: string;
    contacts: { role: string; name: string; email: string; phone: string; url: string }[];
    attribution?: string;
  }>(`/map/carrier?net_id=${netId}`),
  // Colocation / data-center facilities (PeeringDB), as a GeoJSON point
  // FeatureCollection with an `operator` property per feature for vendor
  // coloring, plus an operators list for the legend.
  getDataCenters: () => request<{
    datacenters: any; operators: { name: string; count: number }[]; attribution?: string;
  }>('/map/datacenters'),
  // Internet Exchange Points (PeeringDB), GeoJSON points placed at facility
  // centroids, with peer (net_count) + facility counts per feature.
  getIXPs: () => request<{ ixps: any; attribution?: string }>('/map/ixps'),
  // Cloud provider regions (AWS/Azure/GCP) from a bundled curated dataset,
  // GeoJSON points tagged by provider, with a providers list for the legend.
  getCloudRegions: () => request<{
    cloud_regions: any; providers: { key: string; label: string; color: string; count: number }[]; note?: string;
  }>('/map/cloud-regions'),
  // Geostationary coverage: nested footprint-cap rings per operator (fade
  // from nadir to the low-elevation edge), derived from CelesTrak GEO data.
  getGeoCoverage: () => request<{
    geo_coverage: any; operators: { key: string; label: string; color: string; count: number }[];
    ring_levels: number; note?: string;
  }>('/map/geo-coverage'),
  // Expected satellites-in-view vs latitude per constellation — the metric for
  // "who covers here better" anywhere on the globe (longitude-averaged).
  getCoverageStrength: () => request<{
    constellations: {
      key: string; vendor: string; color: string; regime: string;
      satellite_count: number; peak_in_view: number;
      by_latitude: { lat: number; in_view: number }[];
    }[];
    min_elevation_deg: number; latitude_step: number; note?: string; attribution?: string;
  }>('/map/coverage-strength'),

  // Config Design (Phase 1)
  listConfigTemplates: () => request<any[]>('/config-templates/'),
  createConfigTemplate: (data: { name: string; description?: string; body?: string; platform_default?: string; match_attr?: string }) =>
    request<any>('/config-templates/', { method: 'POST', body: JSON.stringify(data) }),
  updateConfigTemplate: (id: number, data: any) =>
    request<any>(`/config-templates/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteConfigTemplate: (id: number) =>
    request<any>(`/config-templates/${id}`, { method: 'DELETE' }),
  validateConfigTemplate: (body: string) =>
    request<{ ok: boolean; error: string; line: number | null }>('/config-templates/validate', { method: 'POST', body: JSON.stringify({ body }) }),
  renderConfigTemplate: (body: string, node_id: number) =>
    request<{ ok: boolean; rendered: string; error: string; line: number | null }>('/config-templates/render', { method: 'POST', body: JSON.stringify({ body, node_id }) }),
  renderConfigTemplateGroup: (group_id: number, node_id: number) =>
    request<{ ok: boolean; rendered: string; error: string; line: number | null; group_id: number; group_name: string; member_count: number; failed_template_name?: string }>(
      '/config-templates/render-group',
      { method: 'POST', body: JSON.stringify({ group_id, node_id }) },
    ),
  getConfigContext: (node_id: number) =>
    request<any>(`/config-templates/context/${node_id}`),
  assignConfigTemplate: (node_id: number, config_template_id: number | null) =>
    request<any>(`/config-templates/assign/${node_id}`, { method: 'PUT', body: JSON.stringify({ config_template_id }) }),
  renderAllConfigs: (topology_id: number) =>
    request<any>(`/config-templates/render-all/${topology_id}`, { method: 'POST' }),
  getGeneratedConfig: (node_id: number) =>
    request<any>(`/config-templates/generated/${node_id}`),

  // Service module (config deployment) — mirrors the config block above.
  listServices: () => request<any[]>('/services'),
  createService: (data: { name: string; description?: string; target_mode?: string }) =>
    request<any>('/services', { method: 'POST', body: JSON.stringify(data) }),
  getService: (sid: number) => request<any>(`/services/${sid}`),
  updateService: (sid: number, data: any) =>
    request<any>(`/services/${sid}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteService: (sid: number) =>
    request<any>(`/services/${sid}`, { method: 'DELETE' }),
  // Templates
  listServiceTemplates: (sid: number) => request<any[]>(`/services/${sid}/templates`),
  createServiceTemplate: (sid: number, data: { name: string; body?: string; position?: number }) =>
    request<any>(`/services/${sid}/templates`, { method: 'POST', body: JSON.stringify(data) }),
  updateServiceTemplate: (sid: number, tid: number, data: any) =>
    request<any>(`/services/${sid}/templates/${tid}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteServiceTemplate: (sid: number, tid: number) =>
    request<any>(`/services/${sid}/templates/${tid}`, { method: 'DELETE' }),
  reorderServiceTemplates: (sid: number, template_ids: number[]) =>
    request<any[]>(`/services/${sid}/templates/reorder`, { method: 'PUT', body: JSON.stringify({ template_ids }) }),
  validateServiceTemplate: (sid: number, body: string) =>
    request<{ ok: boolean; error: string; line: number | null }>(
      `/services/${sid}/templates/validate`, { method: 'POST', body: JSON.stringify({ body }) }),
  // Variables (declarations)
  listServiceVariables: (sid: number) => request<any[]>(`/services/${sid}/variables`),
  createServiceVariable: (sid: number, data: { name: string; required?: boolean; default?: any; description?: string }) =>
    request<any>(`/services/${sid}/variables`, { method: 'POST', body: JSON.stringify(data) }),
  updateServiceVariable: (sid: number, vid: number, data: any) =>
    request<any>(`/services/${sid}/variables/${vid}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteServiceVariable: (sid: number, vid: number) =>
    request<any>(`/services/${sid}/variables/${vid}`, { method: 'DELETE' }),
  // Render (dry-run) + deploy (push)
  renderService: (sid: number, data: { topology_id: number; target_mode?: string; deployments: any[] }) =>
    request<{ target_mode: string; results: any[] }>(
      `/services/${sid}/render`, { method: 'POST', body: JSON.stringify(data) }),
  deployService: (sid: number, data: { topology_id: number; target_mode?: string; credentials?: { username: string; password: string }; dry_run?: boolean; deployments: any[] }) =>
    request<{ target_mode: string; dry_run: boolean; results: any[] }>(
      `/services/${sid}/deploy`, { method: 'POST', body: JSON.stringify(data) }),
  exportService: (sid: number) => request<any>(`/services/${sid}/export`),
  importService: (payload: any) =>
    request<any>('/services/import', { method: 'POST', body: JSON.stringify(payload) }),

  // Template groups (ordered collections of templates → composite render)
  listConfigTemplateGroups: () => request<any[]>('/config-template-groups/'),
  createConfigTemplateGroup: (data: { name: string; description?: string; platform_default?: string; match_attr?: string }) =>
    request<any>('/config-template-groups/', { method: 'POST', body: JSON.stringify(data) }),
  updateConfigTemplateGroup: (id: number, data: any) =>
    request<any>(`/config-template-groups/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteConfigTemplateGroup: (id: number) =>
    request<any>(`/config-template-groups/${id}`, { method: 'DELETE' }),
  addTemplateToGroup: (group_id: number, template_id: number, position?: number) =>
    request<any>(`/config-template-groups/${group_id}/members`, {
      method: 'POST', body: JSON.stringify({ template_id, position }),
    }),
  removeTemplateFromGroup: (group_id: number, member_id: number) =>
    request<any>(`/config-template-groups/${group_id}/members/${member_id}`, { method: 'DELETE' }),
  reorderGroupMembers: (group_id: number, member_ids: number[]) =>
    request<any>(`/config-template-groups/${group_id}/reorder`, {
      method: 'PUT', body: JSON.stringify({ member_ids }),
    }),
  assignGroupToNode: (node_id: number, config_template_group_id: number | null) =>
    request<any>(`/config-template-groups/assign/${node_id}`, {
      method: 'PUT', body: JSON.stringify({ config_template_group_id }),
    }),

  // Attribute definitions (global, scoped to Config Design)
  listDeviceAttrDefs: () => request<any[]>('/attribute-definitions/device'),
  createDeviceAttrDef: (data: any) =>
    request<any>('/attribute-definitions/device', { method: 'POST', body: JSON.stringify(data) }),
  updateDeviceAttrDef: (id: number, data: any) =>
    request<any>(`/attribute-definitions/device/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteDeviceAttrDef: (id: number) =>
    request<any>(`/attribute-definitions/device/${id}`, { method: 'DELETE' }),
  nextUniqueIntegerValue: (defId: number, topology_id: number) =>
    request<{ next_value: number; start: number; used_count: number }>(`/attribute-definitions/device/${defId}/next-value?topology_id=${topology_id}`),
  listIfaceAttrDefs: () => request<any[]>('/attribute-definitions/interface'),
  createIfaceAttrDef: (data: any) =>
    request<any>('/attribute-definitions/interface', { method: 'POST', body: JSON.stringify(data) }),
  updateIfaceAttrDef: (id: number, data: any) =>
    request<any>(`/attribute-definitions/interface/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteIfaceAttrDef: (id: number) =>
    request<any>(`/attribute-definitions/interface/${id}`, { method: 'DELETE' }),

  // Topologies
  listTopologies: () => request<any[]>('/topologies/'),
  createTopology: (data: { name: string; description: string; parent_topology_id?: number | null }) =>
    request<any>('/topologies/', { method: 'POST', body: JSON.stringify(data) }),
  getTopology: (id: number) => request<any>(`/topologies/${id}`),
  updateTopology: (id: number, data: { name: string; description?: string; parent_topology_id?: number | null }) =>
    request<any>(`/topologies/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  // Admin-only protection toggle (dedicated endpoint; no edit session required).
  setTopologyProtection: (id: number, isProtected: boolean) =>
    request<any>(`/topologies/${id}/protection`, { method: 'PUT', body: JSON.stringify({ is_protected: isProtected }) }),
  deleteTopology: (id: number) =>
    request<any>(`/topologies/${id}`, { method: 'DELETE' }),
  duplicateTopology: (id: number) =>
    request<any>(`/topologies/${id}/duplicate`, { method: 'POST' }),

  // Node Templates
  listNodeTemplates: () => request<any[]>('/node-templates/'),
  createNodeTemplate: (data: any) =>
    request<any>('/node-templates/', { method: 'POST', body: JSON.stringify(data) }),
  deleteNodeTemplate: (id: number) =>
    request<any>(`/node-templates/${id}`, { method: 'DELETE' }),

  // Site templates (global) + dropping one into a topology
  listSiteTemplates: () => request<SiteTemplate[]>('/site-templates/'),
  createSiteTemplate: (data: { name: string; description: string; definition: SiteTemplateDefinition }) =>
    request<SiteTemplate>('/site-templates/', { method: 'POST', body: JSON.stringify(data) }),
  updateSiteTemplate: (id: number, data: { name: string; description: string; definition: SiteTemplateDefinition }) =>
    request<SiteTemplate>(`/site-templates/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteSiteTemplate: (id: number) =>
    request<any>(`/site-templates/${id}`, { method: 'DELETE' }),
  instantiateSiteTemplate: (topoId: number, id: number, data: { variables: Record<string, string>; x: number; y: number }) =>
    request<{ nodes: any[]; links: any[]; patch_panels: number; warnings: string[] }>(
      `/topologies/${topoId}/site-templates/${id}/instantiate`, { method: 'POST', body: JSON.stringify(data) }),

  // Nodes
  listNodes: (topoId: number) => request<any[]>(`/topologies/${topoId}/nodes/`),
  createNode: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/nodes/`, { method: 'POST', body: JSON.stringify(data) }),
  updateNode: (topoId: number, nodeId: number, data: any) =>
    request<any>(`/topologies/${topoId}/nodes/${nodeId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteNode: (topoId: number, nodeId: number) =>
    request<any>(`/topologies/${topoId}/nodes/${nodeId}`, { method: 'DELETE' }),

  // Links
  listLinks: (topoId: number) => request<any[]>(`/topologies/${topoId}/links/`),
  getAvailableInterfaces: (topoId: number, nodeId: number) =>
    request<any>(`/topologies/${topoId}/links/available-interfaces/${nodeId}`),
  createLink: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/links/`, { method: 'POST', body: JSON.stringify(data) }),
  updateLink: (topoId: number, linkId: number, data: any) =>
    request<any>(`/topologies/${topoId}/links/${linkId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteLink: (topoId: number, linkId: number) =>
    request<any>(`/topologies/${topoId}/links/${linkId}`, { method: 'DELETE' }),

  // Flows
  listFlows: (topoId: number) => request<any[]>(`/topologies/${topoId}/flows/`),
  createFlow: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/flows/`, { method: 'POST', body: JSON.stringify(data) }),
  // Rows whose name already exists are skipped, or update that flow when
  // updateExisting is set. Returns the flows created or updated.
  bulkCreateFlows: (topoId: number, flows: any[], updateExisting = false) =>
    request<any[]>(`/topologies/${topoId}/flows/bulk`, { method: 'POST', body: JSON.stringify({ flows, update_existing: updateExisting }) }),
  updateFlow: (topoId: number, flowId: number, data: any) =>
    request<any>(`/topologies/${topoId}/flows/${flowId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteFlow: (topoId: number, flowId: number) =>
    request<any>(`/topologies/${topoId}/flows/${flowId}`, { method: 'DELETE' }),
  deleteAllFlows: (topoId: number) =>
    request<any>(`/topologies/${topoId}/flows/`, { method: 'DELETE' }),
  // Compute the path for one flow without running a full simulation.
  // Used by the canvas/Flows panel to preview a flow's path on click when
  // no simulation result is available. Returns the same shape as one
  // entry in simulationResult.flow_paths.
  computeFlowPath: (topoId: number, flowId: number) =>
    request<{
      flow_id: number; flow_name: string;
      paths: number[][]; link_ids_per_path: number[][]; bandwidth_per_path_gbps: number[];
    }>(`/topologies/${topoId}/flows/${flowId}/compute-path`, { method: 'POST' }),
  // Run a Monte Carlo availability analysis between a flow's source/dest
  // nodes (default iterations from global settings). Optional exclude lists
  // apply the active maintenance window. Result is cached on the flow row.
  analyzeFlowAvailability: (
    topoId: number, flowId: number,
    opts?: { exclude_node_ids?: number[]; exclude_link_ids?: number[]; include_planned?: boolean },
  ) =>
    request<any>(`/topologies/${topoId}/flows/${flowId}/analyze-availability`, {
      method: 'POST',
      body: JSON.stringify(opts || {}),
    }),

  // Actual (measured) link utilization
  getActualUtilization: (topoId: number) =>
    request<ActualUtilizationResult>(`/topologies/${topoId}/actual-utilization/`),
  importActualUtilizationCsv: (topoId: number, csv: string, replace = true) =>
    request<ActualUtilizationResult>(`/topologies/${topoId}/actual-utilization/import-csv`, {
      method: 'POST', body: JSON.stringify({ csv, replace }),
    }),
  clearActualUtilization: (topoId: number) =>
    request<{ deleted: number }>(`/topologies/${topoId}/actual-utilization/`, { method: 'DELETE' }),
  exportLinkUtilizationCsv: async (topoId: number) => {
    const token = localStorage.getItem('auth_token');
    const res = await fetch(`${BASE}/topologies/${topoId}/actual-utilization/export-csv`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) { handle401(res.status); throw new Error('Export failed'); }
    const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '')?.[1] || 'link_utilization.csv';
    await downloadResponse(res, name);
  },

  // Simulation
  exportSimulationCsv: async (topoId: number, excludeNodeIds?: number[], excludeLinkIds?: number[], includePlanned?: boolean) => {
    const token = localStorage.getItem('auth_token');
    const res = await fetch(`/api/topologies/${topoId}/simulate/export-csv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ exclude_node_ids: excludeNodeIds || [], exclude_link_ids: excludeLinkIds || [], include_planned: includePlanned || false }),
    });
    if (!res.ok) throw new Error('Export failed');
    await downloadResponse(res, 'simulation_report.csv');
  },
  runSimulation: (topoId: number, excludeNodeIds?: number[], excludeLinkIds?: number[], includePlanned?: boolean, failureAnalysis?: boolean) =>
    request<any>(`/topologies/${topoId}/simulate/`, {
      method: 'POST',
      body: JSON.stringify({ exclude_node_ids: excludeNodeIds || [], exclude_link_ids: excludeLinkIds || [], include_planned: includePlanned || false, failure_analysis: failureAnalysis ?? false }),
    }),
  getLabSubset: (topoId: number) =>
    request<{ node_ids: number[]; explicit: boolean }>(`/topologies/${topoId}/lab/subset`),
  setLabSubset: (topoId: number, node_ids: number[], explicit: boolean) =>
    request<{ ok: boolean; node_ids: number[]; explicit: boolean }>(`/topologies/${topoId}/lab/subset`, {
      method: 'PUT', body: JSON.stringify({ node_ids, explicit }),
    }),
  redeployLab: (topoId: number) =>
    request<any>(`/topologies/${topoId}/lab/redeploy`, { method: 'POST' }),
  getLabRunningSummary: () =>
    request<Record<string, number>>(`/lab/running-summary`),

  computeShortestPaths: (topoId: number, source_node_id: number, target_node_ids: number[]) =>
    request<any>(`/topologies/${topoId}/simulate/shortest-paths`, {
      method: 'POST',
      body: JSON.stringify({ source_node_id, target_node_ids }),
    }),

  // A-loc → Z-loc paths without a flow: lowest cost / latency, or disjoint sets.
  analyzePaths: (topoId: number, body: {
    a: PathEnd; z: PathEnd; mode: 'shortest' | 'disjoint';
    metric?: 'cost' | 'latency'; disjoint?: 'link' | 'node' | 'srlg'; max_paths?: number; include_planned?: boolean;
  }) =>
    request<PathAnalysisResult>(`/topologies/${topoId}/simulate/path-analysis`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  previewRouting: (topoId: number, routingBehavior: string) =>
    request<any>(`/topologies/${topoId}/simulate/preview/${routingBehavior}`, { method: 'POST' }),
  resolveLocation: (topoId: number, location: string, includePlanned?: boolean) =>
    request<{ id: number; name: string; location: string }[]>(
      `/topologies/${topoId}/simulate/resolve-location?location=${encodeURIComponent(location)}&include_planned=${includePlanned || false}`
    ),
  runMonteCarlo: (topoId: number, params: {
    a_location: string; z_location: string;
    a_node_ids?: number[]; z_node_ids?: number[];
    iterations?: number; connection_type?: string;
    exclude_node_ids?: number[]; exclude_link_ids?: number[];
    include_planned?: boolean;
  }) =>
    request<any>(`/topologies/${topoId}/simulate/monte-carlo`, {
      method: 'POST',
      body: JSON.stringify({
        a_location: params.a_location,
        z_location: params.z_location,
        a_node_ids: params.a_node_ids || null,
        z_node_ids: params.z_node_ids || null,
        iterations: params.iterations || 10000,
        connection_type: params.connection_type || 'both',
        exclude_node_ids: params.exclude_node_ids || null,
        exclude_link_ids: params.exclude_link_ids || null,
        include_planned: params.include_planned || false,
      }),
    }),

  // Settings
  listSettings: () => request<any[]>('/settings/'),
  updateSetting: (key: string, value: number) =>
    request<any>(`/settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) }),

  // User Management (admin)
  listUsers: () => request<any[]>('/auth/users'),
  createUser: (data: { username: string; password: string; display_name?: string; is_admin?: boolean }) =>
    request<any>('/auth/users', { method: 'POST', body: JSON.stringify(data) }),
  updateUser: (id: number, data: { password?: string; display_name?: string; is_admin?: boolean }) =>
    request<any>(`/auth/users/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteUser: (id: number) =>
    request<any>(`/auth/users/${id}`, { method: 'DELETE' }),

  // SRLGs
  listSrlgs: (topoId: number) => request<any[]>(`/topologies/${topoId}/srlgs/`),
  createSrlg: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/srlgs/`, { method: 'POST', body: JSON.stringify(data) }),
  updateSrlg: (topoId: number, srlgId: number, data: any) =>
    request<any>(`/topologies/${topoId}/srlgs/${srlgId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteSrlg: (topoId: number, srlgId: number) =>
    request<any>(`/topologies/${topoId}/srlgs/${srlgId}`, { method: 'DELETE' }),

  // Maintenance Window
  runMaintenanceWindow: (topoId: number, params: { failed_node_ids?: number[]; failed_link_ids?: number[]; srlg_ids?: number[]; include_planned?: boolean }) =>
    request<any>(`/topologies/${topoId}/simulate/maintenance-window`, { method: 'POST', body: JSON.stringify(params) }),
  runSrlgAnalysis: (topoId: number, includePlanned?: boolean) =>
    request<any>(`/topologies/${topoId}/simulate/srlg-analysis?include_planned=${includePlanned || false}`, { method: 'POST' }),

  // Customers
  listCustomers: (topoId: number) => request<any[]>(`/topologies/${topoId}/customers/`),
  createCustomer: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/customers/`, { method: 'POST', body: JSON.stringify(data) }),
  updateCustomer: (topoId: number, custId: number, data: any) =>
    request<any>(`/topologies/${topoId}/customers/${custId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteCustomer: (topoId: number, custId: number) =>
    request<any>(`/topologies/${topoId}/customers/${custId}`, { method: 'DELETE' }),

  // Cost Allocation
  runCostAllocation: (topoId: number, model: string) =>
    request<any>(`/topologies/${topoId}/cost-allocation/`, { method: 'POST', body: JSON.stringify({ allocation_model: model }) }),

  bulkUpdateLinks: (topoId: number, linkIds: number[], updates: Record<string, any>) =>
    request<any>(`/topologies/${topoId}/links/bulk-update`, { method: 'PUT', body: JSON.stringify({ link_ids: linkIds, updates }) }),
  bulkUpdateNodes: (topoId: number, nodeIds: number[], updates: Record<string, any>) =>
    request<any>(`/topologies/${topoId}/nodes/bulk-update`, { method: 'PUT', body: JSON.stringify({ node_ids: nodeIds, updates }) }),

  // Topology Versions
  listVersions: (topoId: number) => request<any[]>(`/topologies/${topoId}/versions/`),
  saveVersion: (topoId: number, data: { name: string; description?: string }) =>
    request<any>(`/topologies/${topoId}/versions/`, { method: 'POST', body: JSON.stringify(data) }),
  restoreVersion: (topoId: number, versionId: number) =>
    request<any>(`/topologies/${topoId}/versions/${versionId}/restore`, { method: 'POST' }),
  deleteVersion: (topoId: number, versionId: number) =>
    request<any>(`/topologies/${topoId}/versions/${versionId}`, { method: 'DELETE' }),
  // Unified diff between two versions (or a version vs 'current').
  diffVersions: (topoId: number, from: string, to: string) =>
    request<{ from_label: string; to_label: string; diff: string; identical: boolean }>(
      `/topologies/${topoId}/versions/diff?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),

  // Protected-topology edit sessions (sudo-style)
  getEditSession: (topoId: number) =>
    request<{ protected: boolean; active: boolean; held_by_me: boolean; username: string; expires_at: string | null }>(
      `/topologies/${topoId}/edit-session/`),
  beginEditSession: (topoId: number) =>
    request<any>(`/topologies/${topoId}/edit-session/begin`, { method: 'POST' }),
  heartbeatEditSession: (topoId: number) =>
    request<any>(`/topologies/${topoId}/edit-session/heartbeat`, { method: 'POST' }),
  commitEditSession: (topoId: number, message?: string) =>
    request<any>(`/topologies/${topoId}/edit-session/commit`, { method: 'POST', body: JSON.stringify({ message: message || '' }) }),
  discardEditSession: (topoId: number) =>
    request<any>(`/topologies/${topoId}/edit-session/discard`, { method: 'POST' }),

  // Maintenance Presets
  listMaintenancePresets: (topoId: number) => request<any[]>(`/topologies/${topoId}/maintenance-presets/`),
  createMaintenancePreset: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/maintenance-presets/`, { method: 'POST', body: JSON.stringify(data) }),
  updateMaintenancePreset: (topoId: number, presetId: number, data: any) =>
    request<any>(`/topologies/${topoId}/maintenance-presets/${presetId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteMaintenancePreset: (topoId: number, presetId: number) =>
    request<any>(`/topologies/${topoId}/maintenance-presets/${presetId}`, { method: 'DELETE' }),

  // Saved sub-topology views — a named subset of node ids. Links between the
  // selected nodes are derived client-side, so views store node ids only.
  // Patch panels (documentation only — ports reference existing links/nodes).
  listPatchPanels: (topoId: number, location: string) =>
    request<any[]>(`/topologies/${topoId}/patch-panels/?location=${encodeURIComponent(location)}`),
  createPatchPanel: (topoId: number, data: {
    location: string; name: string; rack: string; start_u: number | null; height_u: number;
    rows: number; ports_per_row: number; notes: string; in_front_of_id?: number | null;
  }) => request<any>(`/topologies/${topoId}/patch-panels/`, { method: 'POST', body: JSON.stringify(data) }),
  updatePatchPanel: (topoId: number, panelId: number, data: Record<string, any>, force = false) =>
    request<any>(`/topologies/${topoId}/patch-panels/${panelId}${force ? '?force=true' : ''}`,
      { method: 'PUT', body: JSON.stringify(data) }),
  updatePatchPort: (topoId: number, panelId: number, port: number, data: {
    status: string; label: string; node_id: number | null; interface: string; link_id: number | null; notes: string;
  }) => request<any>(`/topologies/${topoId}/patch-panels/${panelId}/ports/${port}`,
      { method: 'PUT', body: JSON.stringify(data) }),
  // Jumper `port` on a panel to `rear_port` on the panel directly behind it.
  connectPatchPorts: (topoId: number, panelId: number, port: number, rear_port: number) =>
    request<any>(`/topologies/${topoId}/patch-panels/${panelId}/connections`,
      { method: 'PUT', body: JSON.stringify({ port, rear_port }) }),
  disconnectPatchPort: (topoId: number, panelId: number, port: number) =>
    request<any>(`/topologies/${topoId}/patch-panels/${panelId}/connections/${port}`, { method: 'DELETE' }),
  deletePatchPanel: (topoId: number, panelId: number) =>
    request<any>(`/topologies/${topoId}/patch-panels/${panelId}`, { method: 'DELETE' }),

  listTopologyViews: (topoId: number) =>
    request<Array<{ id: number; topology_id: number; name: string; description: string; node_ids: number[]; color: string; created_at: string | null; updated_at: string | null }>>(
      `/topologies/${topoId}/views/`,
    ),
  createTopologyView: (topoId: number, data: { name: string; description?: string; node_ids: number[]; color?: string }) =>
    request<any>(`/topologies/${topoId}/views/`, { method: 'POST', body: JSON.stringify(data) }),
  updateTopologyView: (topoId: number, viewId: number, data: { name?: string; description?: string; node_ids?: number[]; color?: string }) =>
    request<any>(`/topologies/${topoId}/views/${viewId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteTopologyView: (topoId: number, viewId: number) =>
    request<any>(`/topologies/${topoId}/views/${viewId}`, { method: 'DELETE' }),
  // Default view = all nodes MINUS these excluded ids (auto-includes new/inherited).
  getDefaultView: (topoId: number) =>
    request<{ excluded_node_ids: number[] }>(`/topologies/${topoId}/views/default`),
  setDefaultView: (topoId: number, excludedNodeIds: number[]) =>
    request<{ excluded_node_ids: number[] }>(`/topologies/${topoId}/views/default`, {
      method: 'PUT', body: JSON.stringify({ excluded_node_ids: excludedNodeIds }),
    }),

  // Prometheus
  getPrometheusConfig: (topoId: number) => request<any>(`/topologies/${topoId}/prometheus/config`),
  savePrometheusConfig: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/prometheus/config`, { method: 'POST', body: JSON.stringify(data) }),
  updatePrometheusConfig: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/prometheus/config`, { method: 'PATCH', body: JSON.stringify(data) }),
  deletePrometheusConfig: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/config`, { method: 'DELETE' }),
  testPrometheus: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/test`, { method: 'POST' }),
  discoverPrometheusDevices: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/discover`, { method: 'POST' }),
  importPrometheusDevices: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/prometheus/import-devices`, { method: 'POST', body: JSON.stringify(data) }),
  enrichFromPrometheus: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/enrich`, { method: 'POST' }),
  getSnmpCredentials: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/snmp-credentials`),
  saveSnmpCredentials: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/prometheus/snmp-credentials`, { method: 'POST', body: JSON.stringify(data) }),
  deleteSnmpCredentials: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/snmp-credentials`, { method: 'DELETE' }),
  regeneratePrometheusConfig: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/regenerate`, { method: 'POST' }),
  getLiveMetrics: (topoId: number) =>
    request<any>(`/topologies/${topoId}/prometheus/live`),
  getHistoricalMetrics: (topoId: number, days?: number, mode?: string) =>
    request<any>(`/topologies/${topoId}/prometheus/historical?days=${days || 10}&mode=${mode || 'avg'}`),

  // LDAP
  ldapStatus: () => request<{ enabled: boolean; server: string }>('/auth/ldap-status'),
  ldapTest: () => request<any>('/auth/ldap-test'),

  // Lab orchestration (containerlab)
  getLabView: (topoId: number) =>
    request<{ nodes: any[]; links: any[]; skipped_nodes: any[] }>(`/topologies/${topoId}/lab/view`),
  getLabYaml: (topoId: number) =>
    request<any>(`/topologies/${topoId}/lab/yaml`),
  getLabEstimate: (topoId: number) =>
    request<any>(`/topologies/${topoId}/lab/estimate`),
  getLabPrereqs: (topoId: number) =>
    request<{
      xrd_node_count: number;
      all_ok: boolean;
      checks: Array<{ id: string; label: string; value: number | null; required_min: number; ok: boolean; fix_command: string; reason: string }>;
      fix_snippet_transient: string;
      fix_snippet_persistent: string;
    }>(`/topologies/${topoId}/lab/prereqs`),
  getLabStatus: (topoId: number) =>
    request<any>(`/topologies/${topoId}/lab/status`),
  deployLab: (topoId: number) =>
    request<any>(`/topologies/${topoId}/lab/deploy`, { method: 'POST' }),
  destroyLab: (topoId: number) =>
    request<any>(`/topologies/${topoId}/lab/destroy`, { method: 'POST' }),
  startLabNode: (topoId: number, nodeId: number) =>
    request<any>(`/topologies/${topoId}/lab/node/${nodeId}/start`, { method: 'POST' }),
  stopLabNode: (topoId: number, nodeId: number) =>
    request<any>(`/topologies/${topoId}/lab/node/${nodeId}/stop`, { method: 'POST' }),
  restartLabNode: (topoId: number, nodeId: number) =>
    request<any>(`/topologies/${topoId}/lab/node/${nodeId}/restart`, { method: 'POST' }),
  // Custom gNMI subscriptions (lab telemetry add-on)
  getLabSubscriptions: (topoId: number) =>
    request<{ subscriptions: any[] }>(`/topologies/${topoId}/lab/subscriptions`),
  createLabSubscription: (topoId: number, data: { name: string; path: string; mode: string; sample_interval: string }) =>
    request<any>(`/topologies/${topoId}/lab/subscriptions`, { method: 'POST', body: JSON.stringify(data) }),
  deleteLabSubscription: (topoId: number, subId: number) =>
    request<any>(`/topologies/${topoId}/lab/subscriptions/${subId}`, { method: 'DELETE' }),
  applyLabSubscriptions: (topoId: number) =>
    request<any>(`/topologies/${topoId}/lab/subscriptions/apply`, { method: 'POST' }),
  testLabSubscription: (topoId: number, path: string, target?: string) =>
    request<{ ok: boolean; target: string; address: string; path: string; output: string; error: string }>(
      `/topologies/${topoId}/lab/subscriptions/test`, { method: 'POST', body: JSON.stringify({ path, target: target ?? null }) }),
  // Link degradation (netem)
  getLinkDegradation: (topoId: number, linkId: number) =>
    request<any>(`/topologies/${topoId}/lab/link/${linkId}/netem`),
  setLinkDegradation: (topoId: number, linkId: number, data: { delay: string; jitter: string; loss: number; rate: number; corruption: number }) =>
    request<any>(`/topologies/${topoId}/lab/link/${linkId}/netem`, { method: 'POST', body: JSON.stringify(data) }),
  resetLinkDegradation: (topoId: number, linkId: number) =>
    request<any>(`/topologies/${topoId}/lab/link/${linkId}/netem`, { method: 'DELETE' }),
  listLabImages: () =>
    request<{ images: any[] }>(`/lab/images`),
  loadLabImage: (filename: string) =>
    request<any>(`/lab/images/load`, { method: 'POST', body: JSON.stringify({ filename }) }),
  uploadLabImage: async (file: File, onProgress?: (pct: number) => void) => {
    // Multipart upload — uses XHR for progress events (fetch doesn't expose them yet)
    const token = localStorage.getItem('auth_token');
    return await new Promise<any>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `/api/lab/images/upload`);
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      if (onProgress && xhr.upload) {
        xhr.upload.onprogress = e => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({}); }
        } else {
          if (xhr.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
          }
          reject(new Error(`API error ${xhr.status}: ${xhr.responseText}`));
        }
      };
      xhr.onerror = () => reject(new Error('Network error during upload'));
      const fd = new FormData();
      fd.append('file', file);
      xhr.send(fd);
    });
  },
  // ---- QCOW VM image build flow (vrnetlab) ----
  // Parallel to the .tgz `docker load` flow above. Returns image rows with
  // build state + the supported platform registry for the upload form.
  listQcowImages: () =>
    request<{
      images: Array<{
        id: number; filename: string; size_mb: number;
        vendor: string; platform: string; clab_kind: string;
        status: 'uploaded' | 'building' | 'built' | 'failed';
        built_image_tag: string; tag_still_loaded: boolean;
        error_message: string; uploaded_at: string | null; built_at: string | null;
      }>;
      platforms: Array<{ key: string; vendor: string; label: string; clab_kind: string; qcow_hint: string }>;
      active_build_id: number | null;
    }>(`/lab/qcow-images`),
  uploadQcowImage: async (file: File, platform: string, onProgress?: (pct: number) => void) => {
    // Multipart with both file + platform field. Same XHR-for-progress
    // pattern as uploadLabImage above — fetch can't expose upload progress.
    const token = localStorage.getItem('auth_token');
    return await new Promise<any>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `/api/lab/qcow-images/upload`);
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      if (onProgress && xhr.upload) {
        xhr.upload.onprogress = e => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({}); }
        } else {
          if (xhr.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
          }
          reject(new Error(`API error ${xhr.status}: ${xhr.responseText}`));
        }
      };
      xhr.onerror = () => reject(new Error('Network error during upload'));
      const fd = new FormData();
      fd.append('file', file);
      fd.append('platform', platform);
      xhr.send(fd);
    });
  },
  buildQcowImage: (id: number) =>
    request<{ id: number; status: string }>(`/lab/qcow-images/${id}/build`, { method: 'POST' }),
  getQcowImageStatus: (id: number) =>
    request<{ id: number; status: string; built_image_tag: string; error_message: string; log: string }>(
      `/lab/qcow-images/${id}/status`,
    ),
  deleteQcowImage: (id: number, alsoDockerRmi = true) =>
    request<any>(`/lab/qcow-images/${id}?also_docker_rmi=${alsoDockerRmi ? 'true' : 'false'}`, { method: 'DELETE' }),

  getLabHostCapacity: () =>
    request<{ memory_mb: number; cpu_cores: number; hugepages_count: number }>(`/lab/host-capacity`),
  getLabKindDefaults: () =>
    request<{ kinds: Record<string, { min_memory_mb: number; min_cpu_cores: number; requires_hugepages: boolean; description: string }> }>(`/lab/kind-defaults`),
  patchNodeTemplate: (id: number, data: any) =>
    request<any>(`/node-templates/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  // External Integrations
  listIntegrations: (topoId: number) =>
    request<any[]>(`/topologies/${topoId}/integrations/`),
  createIntegration: (topoId: number, data: any) =>
    request<any>(`/topologies/${topoId}/integrations/`, { method: 'POST', body: JSON.stringify(data) }),
  updateIntegration: (topoId: number, systemId: number, data: any) =>
    request<any>(`/topologies/${topoId}/integrations/${systemId}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteIntegration: (topoId: number, systemId: number) =>
    request<any>(`/topologies/${topoId}/integrations/${systemId}`, { method: 'DELETE' }),
  testIntegration: (topoId: number, systemId: number) =>
    request<{ connected: boolean; tables_count: number }>(
      `/topologies/${topoId}/integrations/${systemId}/test`, { method: 'POST' }
    ),
  listIntegrationTables: (topoId: number, systemId: number) =>
    request<{ tables: any[] }>(`/topologies/${topoId}/integrations/${systemId}/tables`),
  getIntegrationTableSchema: (topoId: number, systemId: number, tableName: string) =>
    request<any>(`/topologies/${topoId}/integrations/${systemId}/tables/${encodeURIComponent(tableName)}/schema`),
  listIntegrationMappings: (topoId: number, systemId: number) =>
    request<any[]>(`/topologies/${topoId}/integrations/${systemId}/mappings`),
  saveIntegrationMapping: (topoId: number, systemId: number, data: any) =>
    request<any>(`/topologies/${topoId}/integrations/${systemId}/mappings`, { method: 'POST', body: JSON.stringify(data) }),
  deleteIntegrationMapping: (topoId: number, systemId: number, mappingId: number) =>
    request<any>(`/topologies/${topoId}/integrations/${systemId}/mappings/${mappingId}`, { method: 'DELETE' }),
  listIntegrationQueue: (topoId: number, systemId: number, status?: string, limit?: number) =>
    request<any[]>(
      `/topologies/${topoId}/integrations/${systemId}/queue?status=${status || 'all'}&limit=${limit || 100}`
    ),
  retryIntegrationQueueItem: (topoId: number, systemId: number, itemId: number) =>
    request<any>(`/topologies/${topoId}/integrations/${systemId}/queue/${itemId}/retry`, { method: 'POST' }),
  clearIntegrationQueue: (topoId: number, systemId: number, status?: string) =>
    request<{ ok: boolean; deleted: number }>(
      `/topologies/${topoId}/integrations/${systemId}/queue?status=${status || 'success'}`,
      { method: 'DELETE' }
    ),
  syncAllToIntegration: (topoId: number, systemId: number) =>
    request<{ ok: boolean; queued: number; enabled: boolean }>(
      `/topologies/${topoId}/integrations/${systemId}/sync-all`, { method: 'POST' }
    ),

  // Chat
  chat: (topoId: number, messages: { role: string; content: string }[]) =>
    request<any>(`/topologies/${topoId}/chat`, { method: 'POST', body: JSON.stringify({ messages }) }),

  // Discovery
  discoverTopology: async (
    topoId: number,
    params: {
      seed_ip: string; os_type: string; username: string; password: string;
      max_depth?: number; max_devices?: number; timeout_per_device?: number;
    },
    onEvent: (data: any) => void,
  ): Promise<{ nodes_created: number; links_created: number } | null> => {
    const res = await fetch(`${BASE}/topologies/${topoId}/discover`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(params),
    });
    if (!res.ok) {
      handle401(res.status);
      const err = await res.text();
      throw new Error(`API error ${res.status}: ${err}`);
    }
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result: any = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          onEvent(data);
          if (data.type === 'done') {
            result = data;
          }
        } catch { /* skip malformed */ }
      }
    }
    return result;
  },

  chatStream: async (
    topoId: number,
    messages: { role: string; content: string }[],
    onToken: (text: string) => void,
    onToolStatus?: (tool: string) => void,
  ): Promise<{ actions_performed: string[]; [key: string]: any }> => {
    const res = await fetch(`${BASE}/topologies/${topoId}/chat`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ messages }),
    });
    if (!res.ok) {
      handle401(res.status);
      const err = await res.text();
      throw new Error(`API error ${res.status}: ${err}`);
    }
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let metadata: any = {};

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE lines
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === 'token') {
            onToken(data.text);
          } else if (data.type === 'tool_status' && onToolStatus) {
            onToolStatus(data.tool);
          } else if (data.type === 'done') {
            metadata = data;
          }
        } catch { /* skip malformed lines */ }
      }
    }
    return metadata;
  },
};

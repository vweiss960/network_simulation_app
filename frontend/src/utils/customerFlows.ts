import type { FlowData, NodeInstance, LinkData, LinkUtilization, SimulationResult } from '../types';

// A flow belongs to a customer when either end terminates on a customer-role
// node assigned to that customer (a stale customer_id left on a router is
// ignored). Membership is derived from the nodes, never stored, so a flow
// between two customers' nodes belongs to both. Mirrors the backend's
// services/customer_flows.py.

export function nodeCustomerMap(nodes: NodeInstance[]): Map<number, number> {
  const m = new Map<number, number>();
  for (const n of nodes) if (n.customer_id && n.role === 'customer') m.set(n.id, n.customer_id);
  return m;
}

/** Distinct customers owning the flow's endpoints, A-end first. */
export function flowCustomerIds(flow: FlowData, nodeCustomer: Map<number, number>): number[] {
  const ids: number[] = [];
  for (const nid of [flow.source_node_id, flow.dest_node_id]) {
    const cid = nodeCustomer.get(nid);
    if (cid && !ids.includes(cid)) ids.push(cid);
  }
  return ids;
}

/** Link utilization from only the selected customers' flows, built from the
 *  simulation's routed paths (same ECMP split as services/simulation.py). A
 *  flow shared by two selected customers counts once; links carrying none of
 *  their traffic are absent. */
export function customerLinkUtilization(
  sim: SimulationResult | null,
  flows: FlowData[],
  nodes: NodeInstance[],
  links: LinkData[],
  customerIds: number[],
): Record<number, LinkUtilization> {
  const out: Record<number, LinkUtilization> = {};
  if (!sim || customerIds.length === 0) return out;
  const wanted = new Set(customerIds);
  const nodeCustomer = nodeCustomerMap(nodes);
  const flowIds = new Set(flows
    .filter(f => flowCustomerIds(f, nodeCustomer).some(c => wanted.has(c)))
    .map(f => f.id));
  const linkById = new Map(links.map(l => [l.id, l]));
  const add = (lid: number, gbps: number, flowId: number) => {
    const l = linkById.get(lid);
    if (!l) return;
    const u = out[lid] ??= {
      link_id: lid, source_node_id: l.source_node_id, target_node_id: l.target_node_id,
      bandwidth_gbps: l.bandwidth_gbps, used_gbps: 0, utilization_pct: 0, flow_ids: [], cost_per_gb: 0,
    };
    u.used_gbps += gbps;
    if (!u.flow_ids.includes(flowId)) u.flow_ids.push(flowId);
  };
  for (const fp of sim.flow_paths) {
    if (!flowIds.has(fp.flow_id)) continue;
    fp.paths.forEach((path, i) => {
      const gbps = fp.bandwidth_per_path_gbps[i] ?? 0;
      const lids = fp.link_ids_per_path[i] || [];
      if (lids.length) { for (const lid of lids) add(lid, gbps, fp.flow_id); return; }
      // Explicit paths without link ids: first link between each node pair.
      for (let k = 0; k < path.length - 1; k++) {
        const [a, b] = [path[k], path[k + 1]];
        const l = links.find(x => (x.source_node_id === a && x.target_node_id === b) || (x.source_node_id === b && x.target_node_id === a));
        if (l) add(l.id, gbps, fp.flow_id);
      }
    });
  }
  for (const u of Object.values(out)) {
    u.utilization_pct = u.bandwidth_gbps > 0 ? Math.round(u.used_gbps / u.bandwidth_gbps * 10000) / 100 : 0;
    const cc = linkById.get(u.link_id)?.circuit_cost || 0;
    u.cost_per_gb = cc > 0 && u.used_gbps > 0 ? Math.round(cc / u.used_gbps * 10000) / 10000 : 0;
    u.used_gbps = Math.round(u.used_gbps * 10000) / 10000;
  }
  return out;
}

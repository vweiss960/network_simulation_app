import type { Customer, FlowData, MaintenanceAffectedFlow, MaintenanceResult, NodeInstance } from '../types';
import { nodeCustomerMap, flowCustomerIds } from './customerFlows';

// Maintenance-window impact grouped by customer. A flow between two customers'
// nodes is listed under both; flows touching no customer node go under an
// "Unassigned" group (customer = null), which always sorts last.

export interface ImpactedFlow extends MaintenanceAffectedFlow {
  aLoc: string;
  zLoc: string;
  bandwidth_gbps: number | null;
}

export interface CustomerImpact {
  key: string;                 // customer id, or 'unassigned'
  customer: Customer | null;
  flows: ImpactedFlow[];
  brokenCount: number;
  reroutedCount: number;
}

/** "Name (Location)" for a flow endpoint. */
function locLabel(node: NodeInstance | undefined): string {
  if (!node) return '?';
  return node.location ? `${node.name} (${node.location})` : node.name;
}

export function groupImpactByCustomer(
  affected: MaintenanceAffectedFlow[], flows: FlowData[], nodes: NodeInstance[], customers: Customer[],
): CustomerImpact[] {
  const nodeById = new Map(nodes.map(n => [n.id, n]));
  const flowById = new Map(flows.map(f => [f.id, f]));
  const customerById = new Map(customers.map(c => [c.id, c]));
  const nodeCustomer = nodeCustomerMap(nodes);
  const groups = new Map<string, CustomerImpact>();

  const groupFor = (customer: Customer | null) => {
    const key = customer ? String(customer.id) : 'unassigned';
    let g = groups.get(key);
    if (!g) {
      g = { key, customer, flows: [], brokenCount: 0, reroutedCount: 0 };
      groups.set(key, g);
    }
    return g;
  };

  for (const af of affected) {
    const flow = flowById.get(af.flow_id);
    const item: ImpactedFlow = {
      ...af,
      aLoc: locLabel(flow && nodeById.get(flow.source_node_id)),
      zLoc: locLabel(flow && nodeById.get(flow.dest_node_id)),
      bandwidth_gbps: flow?.bandwidth_gbps ?? null,
    };
    const owners = (flow ? flowCustomerIds(flow, nodeCustomer) : [])
      .map(cid => customerById.get(cid))
      .filter((c): c is Customer => !!c);
    for (const owner of owners.length ? owners : [null]) {
      const g = groupFor(owner);
      g.flows.push(item);
      if (af.status === 'broken') g.brokenCount++; else g.reroutedCount++;
    }
  }

  const statusRank = (f: ImpactedFlow) => (f.status === 'broken' ? 0 : 1);
  for (const g of groups.values()) {
    g.flows.sort((a, b) => statusRank(a) - statusRank(b) || a.flow_name.localeCompare(b.flow_name));
  }
  return [...groups.values()].sort((a, b) => {
    if (!a.customer) return 1;
    if (!b.customer) return -1;
    return a.customer.name.localeCompare(b.customer.name);
  });
}

// ---- Reports ----

export interface ReportContext {
  topologyName: string;
  result: MaintenanceResult;
  groups: CustomerImpact[];
}

const esc = (s: unknown) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

// Quote every cell; prefix a leading formula trigger (= + - @ tab CR) with an
// apostrophe so spreadsheet apps don't execute user-entered names/notes.
const csvCell = (s: unknown) => {
  let v = String(s ?? '');
  if (/^[=+\-@\t\r]/.test(v)) v = `'${v}`;
  return `"${v.replace(/"/g, '""')}"`;
};

const fmtBw = (gbps: number | null) => (gbps == null ? '' : `${gbps} Gbps`);

function download(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking synchronously can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fileStem(topologyName: string) {
  const safe = topologyName.replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'topology';
  return `maintenance_impact_${safe}_${new Date().toISOString().slice(0, 10)}`;
}

export function downloadImpactCsv({ topologyName, result, groups }: ReportContext) {
  const rows: string[] = [];
  rows.push(`Maintenance Impact Report,${csvCell(topologyName)}`);
  rows.push(`Generated,${csvCell(new Date().toLocaleString())}`);
  rows.push(`Nodes in maintenance,${csvCell(result.failed_node_names.join('; ') || 'None')}`);
  rows.push(`Links in maintenance,${csvCell(result.failed_link_names.join('; ') || 'None')}`);
  rows.push(`Broken flows,${result.broken_count}`);
  rows.push(`Rerouted flows,${result.rerouted_count}`);
  rows.push('');
  rows.push(['Customer', 'Email', 'Phone', 'Notes', 'Flow', 'A-loc', 'Z-loc', 'Bandwidth', 'Status'].join(','));
  for (const g of groups) {
    const c = g.customer;
    for (const f of g.flows) {
      rows.push([
        c ? c.name : 'Unassigned', c?.contact_email, c?.contact_phone, c?.notes,
        f.flow_name, f.aLoc, f.zLoc, fmtBw(f.bandwidth_gbps), f.status,
      ].map(csvCell).join(','));
    }
  }
  // BOM so Excel decodes UTF-8 (non-ASCII customer/site names) correctly.
  download('\uFEFF' + rows.join('\r\n'), `${fileStem(topologyName)}.csv`, 'text/csv;charset=utf-8');
}

export function downloadImpactHtml({ topologyName, result, groups }: ReportContext) {
  const list = (items: string[]) => items.length
    ? `<ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>`
    : '<p class="muted">None</p>';
  const impactedCustomers = groups.filter(g => g.customer).length;

  const customerSections = groups.map(g => {
    const c = g.customer;
    const contact = c ? `
      <dl class="contact">
        <dt>Email</dt><dd>${c.contact_email ? `<a href="mailto:${esc(c.contact_email)}">${esc(c.contact_email)}</a>` : '<span class="muted">—</span>'}</dd>
        <dt>Phone</dt><dd>${esc(c.contact_phone) || '<span class="muted">—</span>'}</dd>
        <dt>Notes</dt><dd class="notes">${esc(c.notes) || '<span class="muted">—</span>'}</dd>
      </dl>` : '<p class="muted">Flows that do not start or end on a customer node.</p>';
    const rows = g.flows.map(f => `
        <tr>
          <td>${esc(f.flow_name)}</td><td>${esc(f.aLoc)}</td><td>${esc(f.zLoc)}</td>
          <td class="num">${esc(fmtBw(f.bandwidth_gbps))}</td>
          <td><span class="status ${f.status}">${f.status === 'broken' ? 'Broken' : 'Rerouted'}</span></td>
        </tr>`).join('');
    return `
    <section class="customer">
      <h3>${esc(c ? c.name : 'Unassigned')}
        <span class="counts">${g.brokenCount} broken · ${g.reroutedCount} rerouted</span></h3>
      ${contact}
      <table>
        <thead><tr><th>Flow</th><th>A-loc</th><th>Z-loc</th><th class="num">Bandwidth</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
  }).join('');

  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maintenance Impact — ${esc(topologyName)}</title>
<style>
  body { font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; color: #1f2937; background: #fff; max-width: 960px; margin: 24px auto; padding: 0 16px; }
  h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 17px; margin: 28px 0 8px; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
  h3 { font-size: 15px; margin: 0 0 6px; display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .muted { color: #6b7280; } .meta { color: #6b7280; font-size: 12px; }
  .summary { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
  .card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px 14px; min-width: 110px; }
  .card b { display: block; font-size: 20px; } .card.broken b { color: #b91c1c; } .card.rerouted b { color: #b45309; }
  .scope { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0 24px; }
  .scope h4 { margin: 8px 0 4px; font-size: 13px; } ul { margin: 0; padding-left: 18px; }
  .customer { border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; break-inside: avoid; }
  .counts { font-weight: normal; font-size: 12px; color: #6b7280; }
  .contact { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0 0 10px; font-size: 13px; }
  .contact dt { color: #6b7280; } .contact dd { margin: 0; } .notes { white-space: pre-wrap; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }
  th { background: #f9fafb; font-weight: 600; } .num { text-align: right; white-space: nowrap; }
  .status { font-weight: 600; } .status.broken { color: #b91c1c; } .status.rerouted { color: #b45309; }
  @media print { body { margin: 0; } a { color: inherit; text-decoration: none; } }
</style></head>
<body>
  <h1>Maintenance Impact Report</h1>
  <div class="meta">Topology: ${esc(topologyName)} · Generated ${esc(new Date().toLocaleString())}</div>
  <div class="summary">
    <div class="card broken"><b>${result.broken_count}</b>Broken flows</div>
    <div class="card rerouted"><b>${result.rerouted_count}</b>Rerouted flows</div>
    <div class="card"><b>${impactedCustomers}</b>Impacted customers</div>
  </div>

  <h2>Maintenance scope</h2>
  <div class="scope">
    <div><h4>Nodes (${result.failed_node_names.length})</h4>${list(result.failed_node_names)}</div>
    <div><h4>Links (${result.failed_link_names.length})</h4>${list(result.failed_link_names)}</div>
  </div>

  <h2>Impacted customers</h2>
  ${customerSections || '<p class="muted">No flows are affected by this maintenance window.</p>'}
</body></html>`;
  download(html, `${fileStem(topologyName)}.html`, 'text/html');
}

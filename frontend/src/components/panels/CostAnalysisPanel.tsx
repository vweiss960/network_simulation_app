import { useState } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import { nodeCustomerMap, flowCustomerIds } from '../../utils/customerFlows';
import toast from 'react-hot-toast';
import { DollarSign, Play, ChevronDown, ChevronRight, Loader2, Info, Download } from 'lucide-react';

export default function CostAnalysisPanel() {
  const { currentTopologyId, nodes, links, customers, flows } = useStore();
  const [allocationModel, setAllocationModel] = useState<'proportional_used' | 'proportional_total'>('proportional_used');
  const [result, setResult] = useState<any>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [activeTab, setActiveTab] = useState<'summary' | 'customers' | 'links'>('summary');
  const [expandedLink, setExpandedLink] = useState<number | null>(null);
  const [expandedCustomer, setExpandedCustomer] = useState<number | null>(null);
  const [showModelInfo, setShowModelInfo] = useState(false);
  const [custSearch, setCustSearch] = useState('');
  const [linkSearch, setLinkSearch] = useState('');

  const nodeMap = Object.fromEntries(nodes.map(n => [n.id, n.name]));

  const handleRun = async () => {
    if (!currentTopologyId) return;
    setIsRunning(true);
    try {
      const r = await api.runCostAllocation(currentTopologyId, allocationModel);
      setResult(r);
      toast.success('Cost allocation analysis complete');
    } catch (e: any) {
      toast.error(`Analysis failed: ${e.message}`);
    }
    setIsRunning(false);
  };

  const handleExportCsv = () => {
    if (!result) return;
    const lines: string[] = [];

    // Customer summary
    lines.push('=== CUSTOMER COST SUMMARY ===');
    lines.push('Customer,Total Cost,Link Count');
    for (const c of result.customers) {
      lines.push(`"${c.customer_name}","$${c.total_cost.toFixed(2)}",${c.link_count}`);
    }
    lines.push('');

    // Customer detail
    lines.push('=== CUSTOMER DETAIL (PER LINK) ===');
    lines.push('Customer,Link,Bandwidth (Gbps),Share (%),Allocated Cost');
    for (const c of result.customers) {
      for (const l of c.links) {
        lines.push(`"${c.customer_name}","${l.link_name}",${l.bandwidth_gbps},${l.share_pct}%,"$${l.allocated_cost.toFixed(2)}"`);
      }
    }
    lines.push('');

    // Link breakdown
    lines.push('=== LINK BREAKDOWN ===');
    lines.push('Link,Circuit Cost,Capacity (Gbps),Used (Gbps),Utilization (%),Unallocated Cost');
    for (const lb of result.link_breakdowns) {
      lines.push(`"${lb.link_name}","$${lb.circuit_cost.toFixed(2)}",${lb.bandwidth_gbps},${lb.used_gbps},${lb.utilization_pct}%,"$${lb.unallocated_cost.toFixed(2)}"`);
    }
    lines.push('');

    // Link allocations detail
    lines.push('=== LINK ALLOCATION DETAIL ===');
    lines.push('Link,Customer,Bandwidth (Gbps),Share (%),Allocated Cost,Formula');
    for (const lb of result.link_breakdowns) {
      for (const a of lb.allocations) {
        lines.push(`"${lb.link_name}","${a.customer_name}",${a.bandwidth_gbps},${a.share_pct}%,"$${a.allocated_cost.toFixed(2)}","${a.formula}"`);
      }
    }

    // Model info
    lines.push('');
    lines.push(`Allocation Model: ${result.allocation_model}`);
    lines.push(`"${result.allocation_model_description}"`);

    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'cost_allocation_report.csv';
    a.click();
    URL.revokeObjectURL(url);
    toast.success('Report exported');
  };

  const linksWithCost = links.filter(l => (l.circuit_cost || 0) > 0);
  const nodeCustomer = nodeCustomerMap(nodes);
  const assignedFlows = flows.filter(f => flowCustomerIds(f, nodeCustomer).length > 0);

  const r = result;

  return (
    <div className="space-y-3">
      {/* Prerequisites check */}
      {(linksWithCost.length === 0 || assignedFlows.length === 0) && (
        <div className="bg-amber-900/20 border border-amber-700/40 rounded-lg p-3 text-xs space-y-1">
          <div className="text-amber-400 font-medium flex items-center gap-1"><Info size={12} /> Setup required</div>
          {linksWithCost.length === 0 && (
            <div className="text-amber-300">No links have circuit costs. Edit links and set the "Circuit Cost" field.</div>
          )}
          {assignedFlows.length === 0 && (
            <div className="text-amber-300">No flows belong to customers. Assign customer nodes to customers in the Nodes panel — flows starting or ending on them belong to that customer.</div>
          )}
        </div>
      )}

      {/* Allocation model selector */}
      <div className="bg-surface-800 rounded-lg p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-[10px] text-surface-400 font-medium">Allocation Model</div>
          <button className="text-primary-400 hover:text-primary-300 transition-colors"
            onClick={() => setShowModelInfo(!showModelInfo)} title="Learn about allocation models">
            <Info size={14} />
          </button>
        </div>

        {showModelInfo && (
          <div className="bg-surface-900 rounded-lg p-3 text-[10px] space-y-2 border border-surface-600 fade-in">
            <div className="font-medium text-surface-200">How cost allocation models work</div>
            <table className="w-full text-[9px]">
              <thead>
                <tr className="text-surface-400">
                  <td className="pb-1"></td>
                  <td className="pb-1 text-center font-medium">Used BW</td>
                  <td className="pb-1 text-center font-medium">Total Capacity</td>
                </tr>
              </thead>
              <tbody className="text-surface-300">
                <tr><td className="text-surface-500 pr-2">Customers pay for idle capacity?</td><td className="text-center text-amber-400">Yes</td><td className="text-center text-green-400">No</td></tr>
                <tr><td className="text-surface-500 pr-2">Unallocated cost?</td><td className="text-center">Unassigned flows only</td><td className="text-center">Idle + unassigned</td></tr>
                <tr><td className="text-surface-500 pr-2">Full cost recovery?</td><td className="text-center text-green-400">Yes</td><td className="text-center text-amber-400">Partial</td></tr>
                <tr><td className="text-surface-500 pr-2">Better for customers?</td><td className="text-center text-amber-400">No</td><td className="text-center text-green-400">Yes</td></tr>
                <tr><td className="text-surface-500 pr-2">Better for provider?</td><td className="text-center text-green-400">Yes</td><td className="text-center text-amber-400">No</td></tr>
              </tbody>
            </table>
          </div>
        )}

        <div className="space-y-1">
          <label className="flex items-start gap-2 cursor-pointer hover:bg-surface-700 p-1.5 rounded transition-colors">
            <input type="radio" name="model" value="proportional_used"
              checked={allocationModel === 'proportional_used'}
              onChange={() => setAllocationModel('proportional_used')}
              className="accent-primary-500 mt-0.5" />
            <div>
              <div className="text-xs font-medium">Proportional to Used Bandwidth</div>
              <div className="text-[9px] text-surface-500">Full circuit cost divided among customers by their share of bandwidth in use. Customers absorb unused capacity cost proportionally. Provider recovers full cost.</div>
            </div>
          </label>
          <label className="flex items-start gap-2 cursor-pointer hover:bg-surface-700 p-1.5 rounded transition-colors">
            <input type="radio" name="model" value="proportional_total"
              checked={allocationModel === 'proportional_total'}
              onChange={() => setAllocationModel('proportional_total')}
              className="accent-primary-500 mt-0.5" />
            <div>
              <div className="text-xs font-medium">Proportional to Total Capacity</div>
              <div className="text-[9px] text-surface-500">Customers pay only for their share of total link capacity. Unused capacity cost remains with the provider. Customers pay less per Gbps.</div>
            </div>
          </label>
        </div>
      </div>

      {/* Run button */}
      <button className="btn btn-primary btn-sm w-full" onClick={handleRun}
        disabled={isRunning}>
        {isRunning
          ? <><Loader2 size={14} className="animate-spin" /> Analyzing...</>
          : <><DollarSign size={14} /> Run Cost Allocation</>
        }
      </button>

      {/* Results */}
      {r && (
        <div className="space-y-3 fade-in">
          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-surface-800 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-primary-400">${r.summary.total_network_cost.toLocaleString()}</div>
              <div className="text-[10px] text-surface-500">Total Network Cost</div>
            </div>
            <div className="bg-surface-800 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-green-400">${r.summary.total_allocated_cost.toLocaleString()}</div>
              <div className="text-[10px] text-surface-500">Allocated</div>
            </div>
            <div className="bg-surface-800 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-amber-400">${r.summary.total_unallocated_cost.toLocaleString()}</div>
              <div className="text-[10px] text-surface-500">Unallocated</div>
            </div>
            <div className="bg-surface-800 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-surface-300">{r.summary.customer_count}</div>
              <div className="text-[10px] text-surface-500">Customers</div>
            </div>
          </div>

          <div className="text-[9px] text-surface-500 bg-surface-800 rounded p-2">
            <Info size={10} className="inline mr-1" />
            {r.allocation_model_description}
          </div>

          {/* Tabs */}
          <div className="flex border-b border-surface-700">
            {(['summary', 'customers', 'links'] as const).map(tab => (
              <button key={tab}
                className={`flex-1 py-1.5 text-[11px] font-medium border-b-2 transition-colors ${
                  activeTab === tab ? 'border-primary-500 text-primary-400' : 'border-transparent text-surface-400 hover:text-surface-200'
                }`}
                onClick={() => setActiveTab(tab)}
              >
                {tab === 'summary' ? 'Overview' : tab === 'customers' ? 'By Customer' : 'By Link'}
              </button>
            ))}
          </div>

          {/* Summary tab */}
          {activeTab === 'summary' && (
            <div className="space-y-2">
              <div className="bg-surface-800 rounded-lg p-2 text-xs">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  <span className="text-surface-500">Links with cost:</span>
                  <span>{r.summary.links_with_cost} / {r.summary.total_links}</span>
                  <span className="text-surface-500">Flows assigned:</span>
                  <span>{r.summary.flows_assigned} / {r.summary.total_flows}</span>
                  <span className="text-surface-500">Allocation model:</span>
                  <span className="capitalize">{r.allocation_model.replace('_', ' ')}</span>
                </div>
              </div>

              {/* Customer cost ranking */}
              <div className="text-[10px] text-surface-400 font-medium">Customer Cost Ranking</div>
              {r.customers.map((c: any) => (
                <div key={c.customer_id} className="flex justify-between items-center bg-surface-800 rounded px-2 py-1.5 text-xs">
                  <span className="font-medium">{c.customer_name}</span>
                  <span className="text-primary-400 font-bold">${c.total_cost.toLocaleString()}</span>
                </div>
              ))}

              <button className="btn btn-secondary btn-sm w-full" onClick={handleExportCsv}>
                <Download size={12} /> Export Full Report (CSV)
              </button>
            </div>
          )}

          {/* Customers tab */}
          {activeTab === 'customers' && (
            <div className="space-y-2">
              <input className="w-full text-xs" placeholder="Search customers..."
                value={custSearch} onChange={e => setCustSearch(e.target.value)} />
              <div className="space-y-1 max-h-[400px] overflow-y-auto">
              {r.customers.filter((c: any) => !custSearch || c.customer_name.toLowerCase().includes(custSearch.toLowerCase())).map((c: any) => {
                const isExp = expandedCustomer === c.customer_id;
                return (
                  <div key={c.customer_id} className="bg-surface-800 rounded-lg">
                    <div className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-surface-700 rounded-lg transition-colors"
                      onClick={() => setExpandedCustomer(isExp ? null : c.customer_id)}>
                      <div className="flex items-center gap-1">
                        {isExp ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                        <span className="text-xs font-medium">{c.customer_name}</span>
                        <span className="text-[10px] text-surface-500">({c.link_count} links)</span>
                      </div>
                      <span className="text-xs text-primary-400 font-bold">${c.total_cost.toLocaleString()}</span>
                    </div>
                    {isExp && (
                      <div className="px-3 pb-2 border-t border-surface-700 pt-2">
                        <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-2 gap-y-0.5 text-[10px]">
                          <div className="text-surface-500 font-medium">Link</div>
                          <div className="text-surface-500 font-medium text-right">BW</div>
                          <div className="text-surface-500 font-medium text-right">Share</div>
                          <div className="text-surface-500 font-medium text-right">Cost</div>
                          {c.links.map((l: any) => (
                            <div key={l.link_id} className="contents text-[11px]">
                              <div className="text-surface-300 truncate">{l.link_name}</div>
                              <div className="text-right text-surface-400">{l.bandwidth_gbps}G</div>
                              <div className="text-right text-surface-400">{l.share_pct}%</div>
                              <div className="text-right text-primary-400">${l.allocated_cost.toFixed(2)}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
              </div>
            </div>
          )}

          {/* Links tab */}
          {activeTab === 'links' && (
            <div className="space-y-2">
              <input className="w-full text-xs" placeholder="Search links..."
                value={linkSearch} onChange={e => setLinkSearch(e.target.value)} />
              <div className="space-y-1 max-h-[400px] overflow-y-auto">
              {r.link_breakdowns.filter((lb: any) => lb.circuit_cost > 0 && (!linkSearch || lb.link_name.toLowerCase().includes(linkSearch.toLowerCase()))).map((lb: any) => {
                const isExp = expandedLink === lb.link_id;
                return (
                  <div key={lb.link_id} className="bg-surface-800 rounded-lg">
                    <div className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-surface-700 rounded-lg transition-colors"
                      onClick={() => setExpandedLink(isExp ? null : lb.link_id)}>
                      <div className="flex items-center gap-1">
                        {isExp ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                        <span className="text-xs font-medium truncate">{lb.link_name}</span>
                      </div>
                      <span className="text-xs text-primary-400 font-bold ml-2">${lb.circuit_cost.toLocaleString()}</span>
                    </div>
                    {isExp && (
                      <div className="px-3 pb-2 border-t border-surface-700 pt-2 space-y-2">
                        {/* Link info */}
                        <div className="text-[10px] text-surface-400 grid grid-cols-2 gap-x-4 gap-y-0.5">
                          <span>Capacity:</span><span>{lb.bandwidth_gbps}G</span>
                          <span>Used:</span><span>{lb.used_gbps}G ({lb.utilization_pct}%)</span>
                          <span>Unallocated:</span><span className="text-amber-400">${lb.unallocated_cost.toFixed(2)}</span>
                        </div>

                        {/* Explanation */}
                        <div className="text-[9px] text-surface-500 bg-surface-900 rounded p-1.5 italic">
                          {lb.explanation}
                        </div>

                        {/* Per-customer breakdown */}
                        {lb.allocations.length > 0 && (
                          <div>
                            <div className="text-[10px] text-surface-400 font-medium mb-1">Customer Allocations</div>
                            {lb.allocations.map((a: any) => (
                              <div key={a.customer_id} className="bg-surface-900 rounded p-2 mb-1">
                                <div className="flex justify-between text-[11px]">
                                  <span className="font-medium">{a.customer_name}</span>
                                  <span className="text-primary-400 font-bold">${a.allocated_cost.toFixed(2)}</span>
                                </div>
                                <div className="text-[9px] text-surface-500 mt-0.5">
                                  {a.bandwidth_gbps}G ({a.share_pct}% share)
                                </div>
                                {/* Formula - full transparency */}
                                <div className="text-[8px] text-surface-600 mt-1 font-mono bg-surface-800 rounded px-1.5 py-0.5">
                                  {a.formula}
                                </div>
                                {/* Flows contributing */}
                                {a.flows.length > 0 && (
                                  <div className="mt-1 text-[9px] text-surface-500">
                                    Flows: {a.flows.map((f: any) => `${f.flow_name} (${f.bandwidth_gbps}G)`).join(', ')}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}

                        {lb.allocations.length === 0 && (
                          <div className="text-[10px] text-surface-500">No customer flows on this link. Full cost unallocated.</div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

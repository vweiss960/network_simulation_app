import { useState, useEffect, useCallback } from 'react';
import { useStore } from '../../stores/useStore';
import toast from 'react-hot-toast';
import { Play, Activity, AlertTriangle, Shield, RefreshCw, Download } from 'lucide-react';
import { api } from '../../utils/api';

export default function SimulationPanel() {
  const {
    runSimulation, runMonteCarlo, simulationResult, monteCarloResult,
    isSimulating, isMonteCarlo, nodes, flows, setHighlightedFlowPaths,
    selectedNodeId, selectedLinkId, setSelectedLink, links,
    isolatedNodeIds, setIsolatedNodeIds, includePlanned, setIncludePlanned, hidePlanned,
    isMaintenanceRunning, isAnalyzingAvailability,
    runFailureAnalysis, isRunningFailures, failureAnalysisRun,
  } = useStore();
  // Global analysis lock — any in-flight analysis disables every run button
  // so the user can't pile up work on the single backend worker.
  const analysisBusy = isSimulating || isMonteCarlo || isMaintenanceRunning || isAnalyzingAvailability;
  const [activeTab, setActiveTab] = useState<'overview' | 'utilization' | 'failures' | 'montecarlo'>('overview');
  // Utilization view: by default ignore links into customer nodes (access/
  // last-mile links the operator usually doesn't tune for utilization).
  const [showCustomerLinks, setShowCustomerLinks] = useState(false);

  // Monte Carlo form
  const [mcForm, setMcForm] = useState({
    a_location: '',
    z_location: '',
    iterations: '1000',
    connection_type: 'both',
  });
  const [aDevices, setADevices] = useState<{ id: number; name: string; location: string }[]>([]);
  const [zDevices, setZDevices] = useState<{ id: number; name: string; location: string }[]>([]);
  const [selectedAIds, setSelectedAIds] = useState<Set<number>>(new Set());
  const [selectedZIds, setSelectedZIds] = useState<Set<number>>(new Set());

  const { currentTopologyId } = useStore();

  // Resolve location to devices when input changes
  const resolveA = useCallback(async (loc: string) => {
    if (!loc || !currentTopologyId) { setADevices([]); setSelectedAIds(new Set()); return; }
    try {
      const devs = await api.resolveLocation(currentTopologyId, loc, includePlanned);
      setADevices(devs);
      setSelectedAIds(new Set(devs.map(d => d.id)));
    } catch { setADevices([]); }
  }, [currentTopologyId, includePlanned]);

  const resolveZ = useCallback(async (loc: string) => {
    if (!loc || !currentTopologyId) { setZDevices([]); setSelectedZIds(new Set()); return; }
    try {
      const devs = await api.resolveLocation(currentTopologyId, loc, includePlanned);
      setZDevices(devs);
      setSelectedZIds(new Set(devs.map(d => d.id)));
    } catch { setZDevices([]); }
  }, [currentTopologyId, includePlanned]);

  // Debounced resolve on typing
  useEffect(() => {
    const t = setTimeout(() => resolveA(mcForm.a_location), 300);
    return () => clearTimeout(t);
  }, [mcForm.a_location, resolveA]);

  useEffect(() => {
    const t = setTimeout(() => resolveZ(mcForm.z_location), 300);
    return () => clearTimeout(t);
  }, [mcForm.z_location, resolveZ]);

  const toggleADevice = (id: number) => {
    setSelectedAIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleZDevice = (id: number) => {
    setSelectedZIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleSimulate = async () => {
    try {
      await runSimulation();
      toast.success('Simulation complete');
    } catch (err: any) {
      toast.error(`Simulation failed: ${err?.message || 'unknown error'}`);
    }
  };

  const handleMonteCarlo = async () => {
    if (!mcForm.a_location || !mcForm.z_location) {
      toast.error('A-location and Z-location are required');
      return;
    }
    if (selectedAIds.size === 0 || selectedZIds.size === 0) {
      toast.error('Select at least one device at each location');
      return;
    }
    try {
      await runMonteCarlo({
        a_location: mcForm.a_location,
        z_location: mcForm.z_location,
        a_node_ids: [...selectedAIds],
        z_node_ids: [...selectedZIds],
        iterations: parseInt(mcForm.iterations) || 10000,
      });
      toast.success('Monte Carlo complete');
      setActiveTab('montecarlo');
    } catch (err: any) {
      toast.error(`Monte Carlo failed: ${err?.message || 'unknown error'}`);
    }
  };

  const nodeName = (id: number) => nodes.find(n => n.id === id)?.name ?? `Node ${id}`;
  const linkName = (id: number) => {
    const link = links.find(l => l.id === id);
    if (!link) return `Link #${id}`;
    return `${nodeName(link.source_node_id)} → ${nodeName(link.target_node_id)}`;
  };

  // Unique locations for dropdowns
  const locations = [...new Set(nodes.map(n => n.location).filter(Boolean))];
  const mcNodes = includePlanned ? nodes : nodes.filter(n => !n.is_planned);
  const nodeOptions = mcNodes.map(n => ({ id: n.id, label: n.name, location: n.location }));

  return (
    <div className="space-y-3">
      {/* Run Simulation */}
      <button
        className="btn btn-primary btn-sm w-full"
        onClick={handleSimulate}
        disabled={analysisBusy}
        title={analysisBusy && !isSimulating ? 'Another analysis is running — wait for it to finish' : undefined}
      >
        {isSimulating ? <><RefreshCw size={14} className="animate-spin" /> Running...</> : <><Play size={14} /> Re-run Simulation</>}
      </button>
      <p className="text-[10px] text-surface-500 px-1">Simulation auto-runs when you add/remove nodes, links, or flows.</p>

      {/* Tabs */}
      <div className="flex border-b border-surface-700">
        {(['overview', 'utilization', 'failures', 'montecarlo'] as const).map(tab => (
          <button
            key={tab}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? 'border-primary-500 text-primary-400'
                : 'border-transparent text-surface-500 hover:text-surface-300'
            }`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === 'montecarlo' ? 'Monte Carlo' : tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Overview */}
      {activeTab === 'overview' && simulationResult && (
        <div className="space-y-3 fade-in">
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-surface-800 rounded-lg p-3 text-center">
              <div className="text-2xl font-bold text-primary-400">{simulationResult.flow_paths.length}</div>
              <div className="text-[10px] text-surface-500 mt-1">Routed Flows</div>
            </div>
            <button
              onClick={() => setActiveTab('failures')}
              className="bg-surface-800 rounded-lg p-3 text-center hover:bg-surface-700 transition-colors"
              title={failureAnalysisRun ? 'View single points of failure' : 'Run failure analysis in the Failures tab'}
            >
              <div className="text-2xl font-bold text-amber-400">
                {failureAnalysisRun ? simulationResult.single_points_of_failure.length : '—'}
              </div>
              <div className="text-[10px] text-surface-500 mt-1">{failureAnalysisRun ? 'SPOFs Found' : 'SPOFs · not run'}</div>
            </button>
          </div>
          <div>
            <h4 className="text-xs font-medium text-surface-400 mb-2">Flow Paths</h4>
            {simulationResult.flow_paths.map(fp => (
              <div
                key={fp.flow_id}
                className="text-xs mb-1 p-2 bg-surface-800 rounded cursor-pointer hover:bg-surface-700"
                onClick={() => setHighlightedFlowPaths(fp)}
              >
                <span className="font-medium">{fp.flow_name}</span>
                {fp.paths.length > 0 ? (
                  <div className="text-surface-500 mt-0.5">
                    {fp.paths.map((path, i) => (
                      <div key={i}>
                        Path {i + 1}: {path.map(nid => nodeName(nid)).join(' → ')}
                        <span className="text-surface-600 ml-1">
                          ({fp.bandwidth_per_path_gbps[i]}G
                          {fp.latency_per_path_ms?.[i] !== undefined && `, ${fp.latency_per_path_ms[i]}ms`})
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="text-red-400 ml-2">No path found</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Utilization */}
      {activeTab === 'utilization' && simulationResult && (() => {
        const customerIds = new Set(nodes.filter(n => n.role === 'customer').map(n => n.id));
        const isCustomerLink = (lu: any) => customerIds.has(lu.source_node_id) || customerIds.has(lu.target_node_id);
        const customerLinkCount = simulationResult.link_utilizations.filter(isCustomerLink).length;
        const utilRows = simulationResult.link_utilizations
          .filter(lu => showCustomerLinks || !isCustomerLink(lu))
          .sort((a, b) => b.utilization_pct - a.utilization_pct);
        return (
        <div className="space-y-2 fade-in">
          <button className="btn btn-secondary btn-sm w-full" onClick={async () => {
            try {
              const nodeExcl = Array.from(useStore.getState().maintenanceNodeIds);
              const linkExcl = Array.from(useStore.getState().maintenanceLinkIds);
              await api.exportSimulationCsv(
                useStore.getState().currentTopologyId!,
                nodeExcl.length ? nodeExcl : undefined,
                linkExcl.length ? linkExcl : undefined,
                useStore.getState().includePlanned,
              );
              toast.success('Report exported');
            } catch { toast.error('Export failed'); }
          }}>
            <Download size={12} /> Export Simulation Report (CSV)
          </button>
          {customerLinkCount > 0 && (
            <label className="flex items-center gap-2 text-[11px] text-surface-400 px-1 cursor-pointer">
              <input type="checkbox" checked={showCustomerLinks} onChange={e => setShowCustomerLinks(e.target.checked)} className="accent-blue-500" />
              Include customer links
              <span className="text-surface-600">({customerLinkCount} hidden)</span>
            </label>
          )}
          {utilRows.map(lu => (
              <div
                key={lu.link_id}
                className={`rounded-lg p-2 cursor-pointer transition-colors ${
                  selectedLinkId === lu.link_id
                    ? 'bg-blue-900/30 ring-1 ring-blue-500/50'
                    : 'bg-surface-800 hover:bg-surface-700'
                }`}
                onClick={() => setSelectedLink(selectedLinkId === lu.link_id ? null : lu.link_id)}
              >
                <div className="flex justify-between text-xs mb-1">
                  <span>{nodeName(lu.source_node_id)} → {nodeName(lu.target_node_id)}</span>
                  <span className={lu.utilization_pct > 80 ? 'text-red-400 font-bold' : ''}>
                    {lu.utilization_pct}%
                  </span>
                </div>
                <div className="w-full h-1.5 bg-surface-700 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.min(lu.utilization_pct, 100)}%`,
                      background: lu.utilization_pct > 90 ? '#ef4444' :
                        lu.utilization_pct > 75 ? '#f97316' :
                          lu.utilization_pct > 50 ? '#eab308' : '#22c55e',
                    }}
                  />
                </div>
                <div className="text-[10px] text-surface-500 mt-0.5">
                  {lu.used_gbps}G / {lu.bandwidth_gbps}G | Cost/GB: ${lu.cost_per_gb}
                </div>
              </div>
            ))}
        </div>
        );
      })()}

      {/* Failures */}
      {activeTab === 'failures' && simulationResult && (() => {
        // SPOF/failure analysis is the expensive part — run it on demand.
        if (!failureAnalysisRun) {
          return (
            <div className="space-y-2 fade-in">
              <div className="bg-surface-800 border border-surface-700 rounded-lg p-4 text-center">
                <Shield size={22} className="mx-auto mb-2 text-surface-400" />
                <p className="text-xs text-surface-300 mb-1">Failure analysis runs separately</p>
                <p className="text-[10px] text-surface-500 mb-3 leading-relaxed">
                  Testing every node and link failure is the slow part of a simulation, so it isn't run automatically. Click below to find single points of failure.
                </p>
                <button
                  className="btn btn-primary btn-sm w-full"
                  disabled={analysisBusy}
                  onClick={async () => { try { await runFailureAnalysis(); } catch (e: any) { toast.error(e?.message || 'Failure analysis failed'); } }}
                >
                  {isRunningFailures
                    ? (<><RefreshCw size={12} className="animate-spin" /> Analyzing failures…</>)
                    : (<><AlertTriangle size={12} /> Run Failure Analysis</>)}
                </button>
              </div>
            </div>
          );
        }
        // Most impactful first: nodes/links that break the most flows, then
        // those that isolate the most nodes.
        const spofs = simulationResult.failure_scenarios.filter(fs => fs.spof).slice().sort(
          (a, b) => (b.affected_flows?.length || 0) - (a.affected_flows?.length || 0)
            || (b.isolated_node_ids?.length || 0) - (a.isolated_node_ids?.length || 0)
        );
        // Reuse the same simulation report as the Utilization tab — its SPOF
        // section is ranked by impact and includes isolated nodes.
        const downloadReport = async () => {
          try {
            const nodeExcl = Array.from(useStore.getState().maintenanceNodeIds);
            const linkExcl = Array.from(useStore.getState().maintenanceLinkIds);
            await api.exportSimulationCsv(
              useStore.getState().currentTopologyId!,
              nodeExcl.length ? nodeExcl : undefined,
              linkExcl.length ? linkExcl : undefined,
              useStore.getState().includePlanned,
            );
            toast.success('Report exported');
          } catch { toast.error('Export failed'); }
        };
        const isActive = (fs: any) => {
          if (isolatedNodeIds.length === 0) return false;
          if (fs.failed_element_type === 'node') return selectedNodeId === fs.failed_element_id;
          return selectedLinkId === fs.failed_element_id;
        };
        const handleClick = (fs: any) => {
          const isoIds: number[] = fs.isolated_node_ids || [];
          const wasActive = isActive(fs);
          if (wasActive) {
            useStore.setState({ isolatedNodeIds: [], selectedNodeId: null, selectedLinkId: null });
          } else {
            useStore.setState({
              isolatedNodeIds: isoIds,
              selectedNodeId: fs.failed_element_type === 'node' ? fs.failed_element_id : null,
              selectedLinkId: fs.failed_element_type === 'link' ? fs.failed_element_id : null,
              selectedFlowId: null,
            });
          }
        };
        return (
          <div className="space-y-2 fade-in">
            {spofs.length > 0 ? (
              <>
                <div className="bg-red-900/20 border border-red-800 rounded-lg p-3">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <div className="flex items-center gap-2 text-red-400 text-xs font-medium">
                      <AlertTriangle size={14} /> {spofs.length} Single Point{spofs.length > 1 ? 's' : ''} of Failure
                    </div>
                    <button
                      onClick={downloadReport}
                      className="flex items-center gap-1 text-[10px] text-red-300 hover:text-red-100 underline shrink-0"
                      title="Download a CSV report of the single points of failure (ranked by impact)"
                    >
                      <Download size={11} /> Download report
                    </button>
                  </div>
                  <p className="text-[10px] text-surface-500">
                    Ranked by flows broken. Click a SPOF to highlight the nodes it would isolate in red on the map.
                  </p>
                </div>
                {spofs.map((fs, i) => {
                  const isoNames: string[] = fs.isolated_node_names || [];
                  const isoIds: number[] = fs.isolated_node_ids || [];
                  const active = isActive(fs);
                  return (
                    <div
                      key={`spof-${i}`}
                      className={`text-xs p-2 rounded-lg cursor-pointer transition-all ${
                        active
                          ? 'bg-red-900/30 ring-1 ring-red-500/60'
                          : 'bg-red-900/10 border border-red-800/50 hover:bg-red-900/20'
                      }`}
                      onClick={() => handleClick(fs)}
                    >
                      <div className="flex justify-between items-center">
                        <span className="font-medium text-red-300">
                          {fs.failed_element_type === 'node'
                            ? `Node: ${nodeName(fs.failed_element_id)}`
                            : `Link: ${linkName(fs.failed_element_id)}`}
                        </span>
                        <span className="text-red-400 text-[10px] font-bold">SPOF</span>
                      </div>
                      <div className="text-surface-500 mt-1">
                        Isolates {isoIds.length} node{isoIds.length > 1 ? 's' : ''}:
                        <span className="text-red-400 ml-1">{isoNames.join(', ')}</span>
                      </div>
                      {fs.affected_flows.length > 0 && (
                        <div className="text-surface-600 mt-0.5">
                          Breaks {fs.affected_flows.length} flow{fs.affected_flows.length > 1 ? 's' : ''}:
                          {' '}{fs.affected_flows.map((fid: number) => flows.find(f => f.id === fid)?.name || `Flow ${fid}`).join(', ')}
                        </div>
                      )}
                    </div>
                  );
                })}
              </>
            ) : (
              <div className="text-center py-4 text-green-400 text-sm">
                <Shield size={24} className="mx-auto mb-2" />
                No single points of failure detected
                <p className="text-[10px] text-surface-500 mt-1">No single node or link failure would isolate any part of the network.</p>
              </div>
            )}
          </div>
        );
      })()}

      {/* Monte Carlo */}
      {activeTab === 'montecarlo' && (
        <div className="space-y-3 fade-in">
          <div className="bg-surface-800 rounded-lg p-3 space-y-2">
            <h4 className="text-xs font-medium text-surface-300">Availability Simulation</h4>
            <p className="text-[10px] text-surface-500">
              Time-based simulation: each iteration simulates 1 year of operation. Components fail
              and are repaired based on their MTBF/MTTR using exponential distributions. Measures
              actual downtime between temporary user nodes connected to your selected devices.
            </p>

            {/* Include planned toggle */}
            <div
              className={`flex items-center justify-between bg-surface-900/60 rounded px-2 py-1.5 cursor-pointer select-none transition-colors ${hidePlanned ? 'opacity-50' : 'hover:bg-surface-700/60'}`}
              onClick={() => { if (hidePlanned) { toast('Turn off "Hide Planned" in the Nodes panel first', { icon: 'i' }); } else setIncludePlanned(!includePlanned); }}
              title={hidePlanned ? 'Turn off "Hide Planned" in the Nodes panel to enable this toggle' : ''}
            >
              <span className="text-[10px] text-surface-300">Include Planned Nodes/Links {hidePlanned && <span className="text-[9px] text-surface-500">(hidden)</span>}</span>
              <div className={`relative w-8 h-[18px] rounded-full transition-colors ${includePlanned ? 'bg-indigo-600' : 'bg-surface-600'}`}>
                <div className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow transition-transform ${includePlanned ? 'translate-x-[16px]' : 'translate-x-[2px]'}`} />
              </div>
            </div>

            {/* A-Location */}
            <div>
              <label className="text-[10px] text-surface-500">A-Location (source)</label>
              <input
                className="w-full" placeholder="Node name or location"
                value={mcForm.a_location}
                onChange={e => setMcForm({ ...mcForm, a_location: e.target.value })}
                list="a-loc-options"
              />
              <datalist id="a-loc-options">
                {nodeOptions.map(n => <option key={n.id} value={n.label} />)}
                {locations.map(l => <option key={l} value={l} />)}
              </datalist>
              {aDevices.length > 0 && (
                <div className="mt-1 space-y-0.5">
                  <div className="text-[10px] text-surface-400">
                    {aDevices.length} device{aDevices.length > 1 ? 's' : ''} found — select which to connect:
                  </div>
                  {aDevices.map(d => (
                    <label key={d.id} className="flex items-center gap-1.5 text-[10px] cursor-pointer hover:text-surface-200">
                      <input
                        type="checkbox" className="w-3 h-3 rounded"
                        checked={selectedAIds.has(d.id)}
                        onChange={() => toggleADevice(d.id)}
                      />
                      <span>{d.name}</span>
                      {d.location && <span className="text-surface-600">({d.location})</span>}
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* Z-Location */}
            <div>
              <label className="text-[10px] text-surface-500">Z-Location (dest)</label>
              <input
                className="w-full" placeholder="Node name or location"
                value={mcForm.z_location}
                onChange={e => setMcForm({ ...mcForm, z_location: e.target.value })}
                list="z-loc-options"
              />
              <datalist id="z-loc-options">
                {nodeOptions.map(n => <option key={n.id} value={n.label} />)}
                {locations.map(l => <option key={l} value={l} />)}
              </datalist>
              {zDevices.length > 0 && (
                <div className="mt-1 space-y-0.5">
                  <div className="text-[10px] text-surface-400">
                    {zDevices.length} device{zDevices.length > 1 ? 's' : ''} found — select which to connect:
                  </div>
                  {zDevices.map(d => (
                    <label key={d.id} className="flex items-center gap-1.5 text-[10px] cursor-pointer hover:text-surface-200">
                      <input
                        type="checkbox" className="w-3 h-3 rounded"
                        checked={selectedZIds.has(d.id)}
                        onChange={() => toggleZDevice(d.id)}
                      />
                      <span>{d.name}</span>
                      {d.location && <span className="text-surface-600">({d.location})</span>}
                    </label>
                  ))}
                </div>
              )}
            </div>

            <div>
              <label className="text-[10px] text-surface-500">Iterations</label>
              <input className="w-full" type="number" value={mcForm.iterations}
                onChange={e => setMcForm({ ...mcForm, iterations: e.target.value })} />
            </div>

            {/* Connection summary */}
            <div className="text-[10px] text-surface-500 bg-surface-900 rounded p-2">
              <div>A-end: temp user → {selectedAIds.size} device{selectedAIds.size !== 1 ? 's' : ''}</div>
              <div>Z-end: temp user → {selectedZIds.size} device{selectedZIds.size !== 1 ? 's' : ''}</div>
            </div>

            <button
              className="btn btn-primary btn-sm w-full"
              onClick={handleMonteCarlo}
              disabled={analysisBusy}
              title={analysisBusy && !isMonteCarlo ? 'Another analysis is running — wait for it to finish' : undefined}
            >
              {isMonteCarlo
                ? <><RefreshCw size={14} className="animate-spin" /> Running...</>
                : <><Activity size={14} /> Run Monte Carlo</>}
            </button>
          </div>

          {/* Results */}
          {monteCarloResult && (
            <div className="space-y-3">
              {monteCarloResult.error && (
                <div className="bg-red-900/20 border border-red-800 rounded-lg p-3 text-xs text-red-400">
                  {monteCarloResult.error}
                </div>
              )}

              {!monteCarloResult.error && (
                <>
                  {/* Availability result */}
                  <div className="bg-surface-800 rounded-lg p-4 text-center">
                    <div className={`text-2xl font-bold ${
                      monteCarloResult.availability > 0.9999 ? 'text-green-400'
                        : monteCarloResult.availability > 0.99 ? 'text-amber-400' : 'text-red-400'
                    }`}>
                      {(monteCarloResult.availability * 100).toFixed(4)}%
                    </div>
                    <div className="text-[10px] text-surface-500 mt-1">Availability</div>
                    <div className="text-[9px] text-surface-600 mt-1">
                      {monteCarloResult.downtime_min_yr} min/yr downtime
                    </div>
                    <div className="text-[9px] text-surface-600">
                      &sigma; {(monteCarloResult.std_dev * 100).toFixed(4)}% ({monteCarloResult.downtime_std_min_yr} min/yr)
                    </div>
                  </div>

                  {/* Details */}
                  <div className="bg-surface-800 rounded-lg p-3 text-xs space-y-1">
                    <div className="grid grid-cols-2 gap-x-4 text-[10px]">
                      <span className="text-surface-500">Iterations:</span>
                      <span>{monteCarloResult.iterations.toLocaleString()}</span>
                      <span className="text-surface-500">Disjoint paths:</span>
                      <span>{monteCarloResult.disjoint_paths}</span>
                      <span className="text-surface-500">Outage iterations:</span>
                      <span>{monteCarloResult.outage_iterations}</span>
                    </div>
                  </div>

                  {/* Connected devices */}
                  {monteCarloResult.a_devices && (
                    <div className="text-[10px] text-surface-500 bg-surface-900 rounded p-2">
                      <div>A-end: {monteCarloResult.a_devices.map((d: any) => d.name).join(', ')}</div>
                      <div>Z-end: {monteCarloResult.z_devices.map((d: any) => d.name).join(', ')}</div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {!monteCarloResult && (
            <div className="text-center py-4 text-surface-500 text-sm">
              Enter A and Z locations, select devices, and run Monte Carlo.
            </div>
          )}
        </div>
      )}

      {!simulationResult && activeTab !== 'montecarlo' && (
        <div className="text-center py-8 text-surface-500 text-sm">
          Add nodes, links, and flows — simulation runs automatically.
        </div>
      )}
    </div>
  );
}

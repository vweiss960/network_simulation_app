import { useState, useEffect } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import { getUtilizationColor } from '../../utils/colors';
import toast from 'react-hot-toast';
import { Activity, Wifi, WifiOff, RefreshCw, Search, Download, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';

export default function PrometheusPanel() {
  const {
    currentTopologyId, prometheusConfig, isLiveMode, liveMetrics, isLoadingMetrics,
    loadPrometheusConfig, savePrometheusConfig, toggleLiveMode, fetchLiveMetrics,
    fetchHistoricalMetrics, nodes, links,
  } = useStore();
  const linkColors = useStore(s => s.linkColors);

  const [activeTab, setActiveTab] = useState<'config' | 'live' | 'discover'>('config');
  const [form, setForm] = useState({
    url: '', auth_type: 'none', username: '', password: '', bearer_token: '',
    in_octets_metric: 'ifHCInOctets', out_octets_metric: 'ifHCOutOctets',
    speed_metric: 'ifHighSpeed', interface_label: 'ifName',
    instance_label: 'instance', rate_interval: '5m',
  });
  const [testResult, setTestResult] = useState<any>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [discoveredDevices, setDiscoveredDevices] = useState<any[]>([]);
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [histMode, setHistMode] = useState<'avg' | 'max'>('avg');
  const [histDays, setHistDays] = useState(10);
  const [mode, setMode] = useState<'external' | 'builtin'>('external');
  const [snmpForm, setSnmpForm] = useState({
    version: 3, community: 'public', security_level: 'authPriv',
    username: '', auth_password: '', auth_protocol: 'SHA',
    priv_password: '', priv_protocol: 'AES',
  });

  // Load config into form when it changes
  useEffect(() => {
    if (prometheusConfig) {
      setMode(prometheusConfig.mode || 'external');
      setForm({
        url: prometheusConfig.url || '',
        auth_type: prometheusConfig.auth_type || 'none',
        username: prometheusConfig.username || '',
        password: '',
        bearer_token: '',
        in_octets_metric: prometheusConfig.in_octets_metric || 'ifHCInOctets',
        out_octets_metric: prometheusConfig.out_octets_metric || 'ifHCOutOctets',
        speed_metric: prometheusConfig.speed_metric || 'ifHighSpeed',
        interface_label: prometheusConfig.interface_label || 'ifName',
        instance_label: prometheusConfig.instance_label || 'instance',
        rate_interval: prometheusConfig.rate_interval || '5m',
      });
      // Load SNMP credentials
      if (currentTopologyId) {
        api.getSnmpCredentials(currentTopologyId).then(cred => {
          if (cred) setSnmpForm({
            version: cred.version || 3, community: cred.community || 'public',
            security_level: cred.security_level || 'authPriv',
            username: cred.username || '', auth_password: '', auth_protocol: cred.auth_protocol || 'SHA',
            priv_password: '', priv_protocol: cred.priv_protocol || 'AES',
          });
        }).catch(() => {});
      }
    }
  }, [prometheusConfig, currentTopologyId]);

  const handleSave = async () => {
    if (mode === 'external' && !form.url) { toast.error('Prometheus URL is required'); return; }
    if (mode === 'builtin' && !snmpForm.username && snmpForm.version === 3) {
      toast.error('SNMP username is required for SNMPv3'); return;
    }
    try {
      await savePrometheusConfig({ ...form, mode, url: mode === 'builtin' ? 'http://prometheus:9090' : form.url });
      if (mode === 'builtin' && currentTopologyId) {
        await api.saveSnmpCredentials(currentTopologyId, snmpForm);
      }
      toast.success('Configuration saved' + (mode === 'builtin' ? ' — Prometheus configs regenerated' : ''));
    } catch (e: any) { toast.error(`Save failed: ${e.message}`); }
  };

  const handleTest = async () => {
    if (!currentTopologyId) return;
    setIsTesting(true);
    setTestResult(null);
    try {
      const result = await api.testPrometheus(currentTopologyId);
      setTestResult(result);
      toast.success(result.message);
    } catch (e: any) {
      const detail = JSON.parse(e.message.split(': ').slice(1).join(': ') || '{}');
      setTestResult({ status: 'error', error: detail?.detail?.error || e.message, help: detail?.detail?.help || '' });
      toast.error('Connection test failed');
    }
    setIsTesting(false);
  };

  const handleDiscover = async () => {
    if (!currentTopologyId) return;
    setIsDiscovering(true);
    try {
      const result = await api.discoverPrometheusDevices(currentTopologyId);
      setDiscoveredDevices(result.devices || []);
      toast.success(`Found ${result.count} device(s), ${result.healthy} healthy`);
    } catch (e: any) {
      try {
        const detail = JSON.parse(e.message.split(': ').slice(1).join(': '));
        toast.error(detail?.detail?.error || e.message);
      } catch { toast.error(e.message); }
    }
    setIsDiscovering(false);
  };

  const handleImport = async (clearExisting: boolean) => {
    if (!currentTopologyId) return;
    const devices = discoveredDevices.filter(d => d.health === 'up');
    try {
      const result = await api.importPrometheusDevices(currentTopologyId, {
        devices, clear_existing: clearExisting,
      });
      toast.success(`Imported ${result.created} device(s) as nodes`);
      // Reload topology data
      await useStore.getState().selectTopology(currentTopologyId);
      setDiscoveredDevices([]);
    } catch (e: any) { toast.error(`Import failed: ${e.message}`); }
  };

  const handleToggleLive = async () => {
    try {
      await toggleLiveMode(!isLiveMode);
      if (!isLiveMode) toast.success('Live metrics enabled');
      else toast('Live metrics disabled');
    } catch (e: any) {
      try {
        const detail = JSON.parse(e.message.split(': ').slice(1).join(': '));
        toast.error(detail?.detail?.error || e.message);
      } catch { toast.error(e.message); }
    }
  };

  const handleRefresh = async () => {
    try {
      await fetchLiveMetrics();
      toast.success(`Updated at ${new Date().toLocaleTimeString()}`);
    } catch (e: any) {
      try {
        const detail = JSON.parse(e.message.split(': ').slice(1).join(': '));
        toast.error(detail?.detail?.error || e.message);
        if (detail?.detail?.help) toast(detail.detail.help, { duration: 8000, icon: 'i' });
      } catch { toast.error(e.message); }
    }
  };

  const handleHistorical = async () => {
    try {
      await fetchHistoricalMetrics(histDays, histMode);
      toast.success(`Loaded ${histMode} utilization over ${histDays} days`);
    } catch (e: any) {
      try {
        const detail = JSON.parse(e.message.split(': ').slice(1).join(': '));
        toast.error(detail?.detail?.error || e.message);
      } catch { toast.error(e.message); }
    }
  };

  const mappedNodeCount = nodes.filter(n => n.prometheus_id).length;

  return (
    <div className="space-y-3">
      {/* Tabs */}
      <div className="flex border-b border-surface-700">
        {(['config', 'live', 'discover'] as const).map(tab => (
          <button key={tab}
            className={`flex-1 py-1.5 text-[11px] font-medium border-b-2 transition-colors ${
              activeTab === tab ? 'border-primary-500 text-primary-400' : 'border-transparent text-surface-400 hover:text-surface-200'
            }`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === 'config' ? 'Configuration' : tab === 'live' ? 'Live Metrics' : 'Discovery'}
          </button>
        ))}
      </div>

      {/* --- CONFIG TAB --- */}
      {activeTab === 'config' && (
        <div className="space-y-3">
          {/* Mode selector */}
          <div>
            <label className="text-[10px] text-surface-400 block mb-1">Mode</label>
            <div className="flex gap-1">
              {(['builtin', 'external'] as const).map(m => (
                <button key={m}
                  className={`flex-1 text-[11px] py-1.5 rounded border transition-colors ${
                    mode === m ? 'bg-primary-600/30 border-primary-500 text-primary-300' : 'bg-surface-800 border-surface-600 text-surface-400'
                  }`}
                  onClick={() => setMode(m)}
                >
                  {m === 'builtin' ? 'Built-in (Managed)' : 'External Server'}
                </button>
              ))}
            </div>
            <p className="text-[9px] text-surface-500 mt-0.5">
              {mode === 'builtin'
                ? 'Uses the built-in Prometheus & SNMP exporter. Configure SNMP credentials below — the app manages everything else.'
                : 'Point to an existing Prometheus server with SNMP data already being collected.'}
            </p>
          </div>

          {/* Built-in mode: SNMP credentials */}
          {mode === 'builtin' && (
            <div className="space-y-2 bg-surface-800 rounded-lg p-3">
              <div className="text-[10px] text-surface-400 font-medium">SNMP Credentials</div>
              <div>
                <label className="text-[9px] text-surface-500">SNMP Version</label>
                <select className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                  value={snmpForm.version} onChange={e => setSnmpForm({ ...snmpForm, version: Number(e.target.value) as 2 | 3 })}>
                  <option value={2}>v2c</option>
                  <option value={3}>v3</option>
                </select>
              </div>
              {snmpForm.version === 2 ? (
                <div>
                  <label className="text-[9px] text-surface-500">Community String</label>
                  <input className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                    value={snmpForm.community} onChange={e => setSnmpForm({ ...snmpForm, community: e.target.value })} />
                </div>
              ) : (
                <>
                  <div>
                    <label className="text-[9px] text-surface-500">Security Level</label>
                    <select className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                      value={snmpForm.security_level} onChange={e => setSnmpForm({ ...snmpForm, security_level: e.target.value })}>
                      <option value="authPriv">authPriv</option>
                      <option value="authNoPriv">authNoPriv</option>
                      <option value="noAuthNoPriv">noAuthNoPriv</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-[9px] text-surface-500">Username</label>
                    <input className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                      value={snmpForm.username} onChange={e => setSnmpForm({ ...snmpForm, username: e.target.value })} />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[9px] text-surface-500">Auth Protocol</label>
                      <select className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                        value={snmpForm.auth_protocol} onChange={e => setSnmpForm({ ...snmpForm, auth_protocol: e.target.value })}>
                        <option value="SHA">SHA</option>
                        <option value="MD5">MD5</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[9px] text-surface-500">Auth Password</label>
                      <input className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                        type="password" value={snmpForm.auth_password}
                        onChange={e => setSnmpForm({ ...snmpForm, auth_password: e.target.value })} />
                    </div>
                  </div>
                  {snmpForm.security_level === 'authPriv' && (
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[9px] text-surface-500">Privacy Protocol</label>
                        <select className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                          value={snmpForm.priv_protocol} onChange={e => setSnmpForm({ ...snmpForm, priv_protocol: e.target.value })}>
                          <option value="AES">AES</option>
                          <option value="DES">DES</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-[9px] text-surface-500">Privacy Password</label>
                        <input className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-xs"
                          type="password" value={snmpForm.priv_password}
                          onChange={e => setSnmpForm({ ...snmpForm, priv_password: e.target.value })} />
                      </div>
                    </div>
                  )}
                </>
              )}
              <p className="text-[8px] text-surface-600">
                Devices are auto-added as SNMP targets when they have a Management IP set. The app generates Prometheus configs and reloads automatically.
              </p>
            </div>
          )}

          {/* External mode: URL + Auth */}
          {mode === 'external' && (
            <>
              <div>
                <label className="text-[10px] text-surface-400 block mb-1">Prometheus URL</label>
                <input className="w-full bg-surface-800 border border-surface-600 rounded px-2 py-1.5 text-xs"
                  placeholder="http://prometheus:9090"
                  value={form.url}
                  onChange={e => setForm({ ...form, url: e.target.value })}
                />
              </div>

              <div>
                <label className="text-[10px] text-surface-400 block mb-1">Authentication</label>
                <select className="w-full bg-surface-800 border border-surface-600 rounded px-2 py-1.5 text-xs"
                  value={form.auth_type}
                  onChange={e => setForm({ ...form, auth_type: e.target.value })}
            >
              <option value="none">None</option>
              <option value="basic">Basic Auth</option>
              <option value="bearer">Bearer Token</option>
            </select>
          </div>

          {form.auth_type === 'basic' && (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-surface-400 block mb-1">Username</label>
                <input className="w-full bg-surface-800 border border-surface-600 rounded px-2 py-1.5 text-xs"
                  value={form.username} onChange={e => setForm({ ...form, username: e.target.value })}
                />
              </div>
              <div>
                <label className="text-[10px] text-surface-400 block mb-1">Password</label>
                <input className="w-full bg-surface-800 border border-surface-600 rounded px-2 py-1.5 text-xs"
                  type="password" value={form.password}
                  onChange={e => setForm({ ...form, password: e.target.value })}
                />
              </div>
            </div>
          )}
          {form.auth_type === 'bearer' && (
            <div>
              <label className="text-[10px] text-surface-400 block mb-1">Bearer Token</label>
              <input className="w-full bg-surface-800 border border-surface-600 rounded px-2 py-1.5 text-xs"
                type="password" value={form.bearer_token}
                onChange={e => setForm({ ...form, bearer_token: e.target.value })}
              />
            </div>
          )}
            </>
          )}

          <div>
            <label className="text-[10px] text-surface-400 block mb-1">Rate Interval</label>
            <input className="w-full bg-surface-800 border border-surface-600 rounded px-2 py-1.5 text-xs"
              value={form.rate_interval} onChange={e => setForm({ ...form, rate_interval: e.target.value })}
            />
            <p className="text-[9px] text-surface-500 mt-0.5">Should match or exceed your Prometheus scrape interval</p>
          </div>

          {/* Advanced metric config */}
          <button className="text-[10px] text-primary-400 hover:underline"
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            {showAdvanced ? 'Hide' : 'Show'} Advanced Metric Configuration
          </button>

          {showAdvanced && (
            <div className="space-y-2 bg-surface-800 rounded-lg p-2">
              {[
                { key: 'in_octets_metric', label: 'Inbound Octets Metric', hint: 'SNMP: ifHCInOctets' },
                { key: 'out_octets_metric', label: 'Outbound Octets Metric', hint: 'SNMP: ifHCOutOctets' },
                { key: 'speed_metric', label: 'Interface Speed Metric', hint: 'SNMP: ifHighSpeed (Mbps)' },
                { key: 'interface_label', label: 'Interface Name Label', hint: 'Usually: ifName' },
                { key: 'instance_label', label: 'Instance Label', hint: 'Usually: instance' },
              ].map(({ key, label, hint }) => (
                <div key={key}>
                  <label className="text-[9px] text-surface-400 block">{label}</label>
                  <input className="w-full bg-surface-900 border border-surface-600 rounded px-2 py-1 text-[11px] font-mono"
                    value={(form as any)[key]}
                    onChange={e => setForm({ ...form, [key]: e.target.value })}
                  />
                  <p className="text-[8px] text-surface-600">{hint}</p>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm flex-1" onClick={handleSave}>
              Save Configuration
            </button>
            <button className="btn btn-secondary btn-sm flex-1" onClick={handleTest} disabled={isTesting || !prometheusConfig}>
              {isTesting ? 'Testing...' : 'Test Connection'}
            </button>
          </div>

          {/* Test result */}
          {testResult && (
            <div className={`rounded-lg p-3 text-xs ${testResult.status === 'connected' ? 'bg-green-900/30 border border-green-700' : 'bg-red-900/30 border border-red-700'}`}>
              {testResult.status === 'connected' ? (
                <div className="space-y-1">
                  <div className="flex items-center gap-1.5 text-green-400 font-medium">
                    <CheckCircle size={12} /> Connected
                  </div>
                  <div className="text-surface-300">{testResult.message}</div>
                </div>
              ) : (
                <div className="space-y-1">
                  <div className="flex items-center gap-1.5 text-red-400 font-medium">
                    <XCircle size={12} /> Connection Failed
                  </div>
                  <div className="text-surface-300">{testResult.error}</div>
                  {testResult.help && (
                    <div className="text-amber-400 text-[10px] mt-1 bg-amber-900/20 rounded p-1.5">
                      <AlertTriangle size={10} className="inline mr-1" />
                      {testResult.help}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Node mapping status + Enrich button */}
          {prometheusConfig && (
            <div className="bg-surface-800 rounded-lg p-3">
              <div className="text-[10px] text-surface-400 font-medium mb-1">Node Mapping Status</div>
              <div className="text-xs">
                <span className={mappedNodeCount > 0 ? 'text-green-400' : 'text-amber-400'}>
                  {mappedNodeCount}/{nodes.length} nodes
                </span>
                <span className="text-surface-500"> have Prometheus IDs configured</span>
              </div>
              {mappedNodeCount === 0 && nodes.length > 0 && (
                <p className="text-[9px] text-amber-400 mt-1">
                  Set the "Prometheus ID" field in each node's edit form to the device IP/hostname
                  matching the Prometheus "instance" label, or click "Enrich Topology" to auto-match.
                </p>
              )}
              {nodes.length > 0 && (
                <button className="btn btn-secondary btn-sm w-full mt-2 text-[10px]"
                  onClick={async () => {
                    if (!currentTopologyId) return;
                    try {
                      const result = await api.enrichFromPrometheus(currentTopologyId);
                      if (result.enriched) {
                        toast.success(
                          `Enriched: ${result.matched_nodes}/${result.total_nodes} nodes matched, ` +
                          `${result.interfaces_updated || 0} speeds updated, ` +
                          `${result.interfaces_added || 0} interfaces added, ` +
                          `${result.links_updated || 0} link bandwidths corrected`
                        );
                        await useStore.getState().selectTopology(currentTopologyId);
                      } else {
                        toast.error(result.reason || 'Enrichment failed');
                      }
                    } catch (e: any) {
                      try {
                        const detail = JSON.parse(e.message.split(': ').slice(1).join(': '));
                        toast.error(detail?.detail?.error || e.message);
                      } catch { toast.error(e.message); }
                    }
                  }}
                >
                  Enrich Topology from Prometheus
                </button>
              )}
              <p className="text-[8px] text-surface-600 mt-1">
                Auto-matches nodes to SNMP targets by hostname, updates interface names/speeds, fills link interfaces via LLDP and IP subnets, and sets link IPs.
              </p>
            </div>
          )}
        </div>
      )}

      {/* --- LIVE TAB --- */}
      {activeTab === 'live' && (
        <div className="space-y-3">
          {!prometheusConfig ? (
            <div className="bg-surface-800 rounded-lg p-4 text-center">
              <WifiOff size={24} className="mx-auto text-surface-500 mb-2" />
              <div className="text-xs text-surface-400">No Prometheus configuration found</div>
              <div className="text-[10px] text-surface-500 mt-1">
                Go to the Configuration tab to set up Prometheus first.
              </div>
            </div>
          ) : (
            <>
              {/* Live toggle */}
              <div className="flex items-center justify-between bg-surface-800 rounded-lg p-3">
                <div className="flex items-center gap-2">
                  {isLiveMode ? <Wifi size={14} className="text-green-400" /> : <WifiOff size={14} className="text-surface-500" />}
                  <span className="text-xs font-medium">{isLiveMode ? 'Live' : 'Offline'}</span>
                </div>
                <button
                  className={`relative w-10 h-5 rounded-full transition-colors ${isLiveMode ? 'bg-green-600' : 'bg-surface-600'}`}
                  onClick={handleToggleLive}
                >
                  <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${isLiveMode ? 'left-5.5 translate-x-0.5' : 'left-0.5'}`} />
                </button>
              </div>

              {isLiveMode && (
                <>
                  {/* Refresh + Historical */}
                  <div className="flex gap-2">
                    <button className="btn btn-primary btn-sm flex-1 flex items-center justify-center gap-1"
                      onClick={handleRefresh} disabled={isLoadingMetrics}>
                      <RefreshCw size={12} className={isLoadingMetrics ? 'animate-spin' : ''} />
                      {isLoadingMetrics ? 'Loading...' : 'Refresh Now'}
                    </button>
                  </div>

                  <div className="bg-surface-800 rounded-lg p-2">
                    <div className="text-[10px] text-surface-400 font-medium mb-1.5">Historical View</div>
                    <div className="flex gap-2 items-end">
                      <div className="flex-1">
                        <label className="text-[9px] text-surface-500 block">Period</label>
                        <select className="w-full bg-surface-900 border border-surface-600 rounded px-1.5 py-1 text-[11px]"
                          value={histDays} onChange={e => setHistDays(Number(e.target.value))}>
                          <option value={1}>Last 24h</option>
                          <option value={3}>Last 3 days</option>
                          <option value={7}>Last 7 days</option>
                          <option value={10}>Last 10 days</option>
                          <option value={30}>Last 30 days</option>
                        </select>
                      </div>
                      <div className="flex-1">
                        <label className="text-[9px] text-surface-500 block">Aggregation</label>
                        <select className="w-full bg-surface-900 border border-surface-600 rounded px-1.5 py-1 text-[11px]"
                          value={histMode} onChange={e => setHistMode(e.target.value as 'avg' | 'max')}>
                          <option value="avg">Average</option>
                          <option value="max">Max</option>
                        </select>
                      </div>
                      <button className="btn btn-secondary btn-sm text-[10px] px-2" onClick={handleHistorical}
                        disabled={isLoadingMetrics}>
                        Load
                      </button>
                    </div>
                  </div>

                  {/* Results */}
                  {liveMetrics && (
                    <div className="space-y-2">
                      <div className="text-[10px] text-surface-400">
                        {liveMetrics.mode ? `${liveMetrics.mode === 'avg' ? 'Average' : 'Max'} over ${liveMetrics.days}d` : 'Current'} — {liveMetrics.timestamp ? new Date(liveMetrics.timestamp).toLocaleString() : ''}
                        {liveMetrics.mapped_nodes !== undefined && (
                          <span className="ml-1 text-surface-500">({liveMetrics.mapped_nodes}/{liveMetrics.total_nodes} nodes mapped)</span>
                        )}
                      </div>

                      {/* Errors */}
                      {liveMetrics.errors?.length > 0 && (
                        <div className="bg-red-900/20 border border-red-700/50 rounded-lg p-2">
                          <div className="text-[10px] text-red-400 font-medium mb-1">Warnings</div>
                          {liveMetrics.errors.map((err, i) => (
                            <div key={i} className="text-[9px] text-red-300">{err}</div>
                          ))}
                        </div>
                      )}

                      {/* Utilization list */}
                      <div className="space-y-1 max-h-[400px] overflow-y-auto">
                        {[...liveMetrics.link_utilizations]
                          .sort((a, b) => b.utilization_pct - a.utilization_pct)
                          .map(lu => {
                            const srcNode = nodes.find(n => n.id === lu.source_node_id);
                            const tgtNode = nodes.find(n => n.id === lu.target_node_id);
                            const pct = lu.utilization_pct;
                            const color = getUtilizationColor(pct, linkColors);
                            return (
                              <div key={lu.link_id} className="bg-surface-800 rounded p-2">
                                <div className="flex justify-between text-[11px] mb-1">
                                  <span className="truncate">{srcNode?.name || '?'} — {tgtNode?.name || '?'}</span>
                                  <span style={{ color }} className="font-mono font-bold ml-2">{pct}%</span>
                                </div>
                                <div className="w-full h-1.5 bg-surface-700 rounded-full overflow-hidden">
                                  <div className="h-full rounded-full transition-all" style={{
                                    width: `${Math.min(pct, 100)}%`, background: color,
                                  }} />
                                </div>
                                <div className="text-[9px] text-surface-500 mt-0.5">
                                  {lu.used_gbps} / {lu.bandwidth_gbps} Gbps
                                  {!lu.has_data && <span className="text-amber-400 ml-1">(no Prometheus data)</span>}
                                </div>
                              </div>
                            );
                          })}
                      </div>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* --- DISCOVERY TAB --- */}
      {activeTab === 'discover' && (
        <div className="space-y-3">
          {!prometheusConfig ? (
            <div className="bg-surface-800 rounded-lg p-4 text-center">
              <Search size={24} className="mx-auto text-surface-500 mb-2" />
              <div className="text-xs text-surface-400">Configure Prometheus first</div>
              <div className="text-[10px] text-surface-500 mt-1">
                Set up the connection in the Configuration tab, then come back here to discover devices.
              </div>
            </div>
          ) : (
            <>
              <button className="btn btn-primary btn-sm w-full flex items-center justify-center gap-1"
                onClick={handleDiscover} disabled={isDiscovering}>
                <Search size={12} />
                {isDiscovering ? 'Discovering...' : 'Discover Devices from Prometheus'}
              </button>

              <p className="text-[9px] text-surface-500">
                Queries Prometheus for SNMP targets and retrieves hostname, interfaces, and speeds.
              </p>

              {discoveredDevices.length > 0 && (
                <>
                  <div className="text-xs text-surface-400 font-medium">
                    Discovered {discoveredDevices.length} device(s)
                  </div>

                  <div className="space-y-2 max-h-[300px] overflow-y-auto">
                    {discoveredDevices.map((dev, i) => (
                      <div key={i} className={`bg-surface-800 rounded-lg p-2 border ${dev.health === 'up' ? 'border-green-700/30' : 'border-red-700/30'}`}>
                        <div className="flex items-center justify-between">
                          <div>
                            <span className="text-xs font-medium">{dev.hostname || dev.instance}</span>
                            <span className="text-[9px] text-surface-500 ml-1.5">{dev.instance}</span>
                          </div>
                          <span className={`text-[9px] px-1.5 py-0.5 rounded ${dev.health === 'up' ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'}`}>
                            {dev.health}
                          </span>
                        </div>
                        {dev.error && <div className="text-[9px] text-red-400 mt-1">{dev.error}</div>}
                        {dev.interfaces?.length > 0 && (
                          <div className="mt-1.5">
                            <div className="text-[9px] text-surface-500">{dev.interfaces.length} interface(s):</div>
                            <div className="flex flex-wrap gap-1 mt-0.5">
                              {dev.interfaces.map((iface: any, j: number) => (
                                <span key={j} className={`text-[8px] px-1 py-0.5 rounded ${
                                  iface.oper_status === 'up' ? 'bg-green-900/30 text-green-400' : 'bg-surface-700 text-surface-400'
                                }`}>
                                  {iface.name} {iface.speed_gbps > 0 ? `(${iface.speed_gbps}G)` : ''}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>

                  <div className="flex gap-2">
                    <button className="btn btn-primary btn-sm flex-1"
                      onClick={() => handleImport(false)}>
                      <Download size={12} className="mr-1" /> Add to Topology
                    </button>
                    <button className="btn btn-secondary btn-sm flex-1"
                      onClick={() => {
                        if (confirm('This will remove all existing nodes and links. Continue?')) {
                          handleImport(true);
                        }
                      }}>
                      Replace All
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

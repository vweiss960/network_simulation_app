import { useState, useRef, useEffect } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import { toast } from 'react-hot-toast';
import { Loader2, Play, Square, ChevronDown, ChevronRight, CheckCircle2, XCircle, Radio } from 'lucide-react';

interface DiscoveryEvent {
  type: 'progress' | 'device' | 'error' | 'done';
  message: string;
  hostname?: string;
  ip?: string;
  os_type?: string;
  vendor?: string;
  neighbor_count?: number;
  interface_count?: number;
  nodes_created?: number;
  links_created?: number;
}

type Step = 'config' | 'running' | 'done';

export default function DiscoveryPanel() {
  const { currentTopologyId, loadNodeTemplates } = useStore();
  const topoId = currentTopologyId;

  // Form state
  const [seedIp, setSeedIp] = useState('');
  const [osType, setOsType] = useState('iosxe');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [discoveryMethod, setDiscoveryMethod] = useState('igp');
  const [igpProtocol, setIgpProtocol] = useState('isis');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [clearExisting, setClearExisting] = useState(true);
  const [maxDepth, setMaxDepth] = useState(5);
  const [maxDevices, setMaxDevices] = useState(50);
  const [timeoutPerDevice, setTimeoutPerDevice] = useState(30);

  // Discovery state
  const [step, setStep] = useState<Step>('config');
  const [events, setEvents] = useState<DiscoveryEvent[]>([]);
  const [devices, setDevices] = useState<DiscoveryEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<{ nodes_created: number; links_created: number } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // Reload store data after discovery
  const reloadTopology = useStore(s => s.selectTopology);

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [events]);

  const handleStart = async () => {
    if (!topoId) {
      toast.error('No topology selected');
      return;
    }
    if (!seedIp.trim() || !username.trim() || !password.trim()) {
      toast.error('IP, username, and password are required');
      return;
    }

    setStep('running');
    setIsRunning(true);
    setEvents([]);
    setDevices([]);
    setResult(null);

    try {
      const res = await api.discoverTopology(
        topoId,
        {
          seed_ip: seedIp.trim(),
          os_type: osType,
          username: username.trim(),
          password: password.trim(),
          discovery_method: discoveryMethod,
          igp_protocol: igpProtocol,
          clear_existing: clearExisting,
          max_depth: maxDepth,
          max_devices: maxDevices,
          timeout_per_device: timeoutPerDevice,
        },
        (event: DiscoveryEvent) => {
          setEvents(prev => [...prev, event]);
          if (event.type === 'device') {
            setDevices(prev => [...prev, event]);
          }
        },
      );

      if (res) {
        setResult(res);
        // Reload topology data to show discovered nodes/links
        await reloadTopology(topoId);
        toast.success(`Discovered ${res.nodes_created} nodes and ${res.links_created} links`);
      }
    } catch (err: any) {
      toast.error(`Discovery failed: ${err?.message || 'unknown error'}`);
      setEvents(prev => [...prev, { type: 'error', message: err?.message || 'Unknown error' }]);
    } finally {
      setIsRunning(false);
      setStep('done');
    }
  };

  const handleReset = () => {
    setStep('config');
    setEvents([]);
    setDevices([]);
    setResult(null);
  };

  return (
    <div className="space-y-4 text-xs">
      {/* Step 1: Configuration */}
      {step === 'config' && (
        <>
          <div className="bg-surface-800/50 border border-surface-700 rounded-lg p-3 space-y-3">
            <h4 className="text-[11px] font-medium text-surface-300 uppercase tracking-wider">Seed Device</h4>

            <div>
              <label className="text-[10px] text-surface-500 mb-1 block">Management IP</label>
              <input
                className="w-full"
                placeholder="10.0.0.1"
                value={seedIp}
                onChange={e => setSeedIp(e.target.value)}
              />
            </div>

            <div>
              <label className="text-[10px] text-surface-500 mb-1 block">OS Type</label>
              <select
                className="w-full"
                value={osType}
                onChange={e => setOsType(e.target.value)}
              >
                <option value="iosxe">IOS-XE</option>
                <option value="iosxr">IOS-XR</option>
                <option value="nxos">NX-OS</option>
                <option value="junos">JUNOS</option>
              </select>
            </div>

            <div>
              <label className="text-[10px] text-surface-500 mb-1 block">Discovery Method</label>
              <div className="flex gap-1">
                <button
                  className={`flex-1 px-2 py-1.5 rounded text-[10px] font-medium transition-colors ${
                    discoveryMethod === 'igp'
                      ? 'bg-primary-600 text-white'
                      : 'bg-surface-700 text-surface-400 hover:text-surface-200'
                  }`}
                  onClick={() => setDiscoveryMethod('igp')}
                >
                  IGP Database
                </button>
                <button
                  className={`flex-1 px-2 py-1.5 rounded text-[10px] font-medium transition-colors ${
                    discoveryMethod === 'lldp'
                      ? 'bg-primary-600 text-white'
                      : 'bg-surface-700 text-surface-400 hover:text-surface-200'
                  }`}
                  onClick={() => setDiscoveryMethod('lldp')}
                >
                  LLDP/CDP Crawl
                </button>
              </div>
              <p className="text-[9px] text-surface-600 mt-1">
                {discoveryMethod === 'igp'
                  ? 'Pulls full topology from the IGP link-state database (single SSH session)'
                  : 'Recursively crawls network via LLDP/CDP neighbors (SSH to each device)'}
              </p>
            </div>

            {discoveryMethod === 'igp' && (
              <div>
                <label className="text-[10px] text-surface-500 mb-1 block">IGP Protocol</label>
                <select
                  className="w-full"
                  value={igpProtocol}
                  onChange={e => setIgpProtocol(e.target.value)}
                >
                  <option value="isis">IS-IS</option>
                  <option value="ospf">OSPF</option>
                </select>
              </div>
            )}

            <div>
              <label className="text-[10px] text-surface-500 mb-1 block">Username</label>
              <input
                className="w-full"
                placeholder="admin"
                value={username}
                onChange={e => setUsername(e.target.value)}
              />
            </div>

            <div>
              <label className="text-[10px] text-surface-500 mb-1 block">Password</label>
              <input
                className="w-full"
                type="password"
                placeholder="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
              />
            </div>
          </div>

          {/* Advanced options */}
          <div className="bg-surface-800/50 border border-surface-700 rounded-lg">
            <button
              className="w-full flex items-center gap-2 p-3 text-[11px] text-surface-400 hover:text-surface-200"
              onClick={() => setShowAdvanced(!showAdvanced)}
            >
              {showAdvanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Advanced Options
            </button>
            {showAdvanced && (
              <div className="px-3 pb-3 space-y-3 border-t border-surface-700 pt-3">
                {discoveryMethod === 'lldp' && (
                  <div>
                    <label className="text-[10px] text-surface-500 mb-1 block">Max Depth (hops from seed)</label>
                    <input
                      className="w-full"
                      type="number"
                      min={1}
                      max={20}
                      value={maxDepth}
                      onChange={e => setMaxDepth(parseInt(e.target.value) || 5)}
                    />
                  </div>
                )}
                <div>
                  <label className="text-[10px] text-surface-500 mb-1 block">Max Devices</label>
                  <input
                    className="w-full"
                    type="number"
                    min={1}
                    max={200}
                    value={maxDevices}
                    onChange={e => setMaxDevices(parseInt(e.target.value) || 50)}
                  />
                </div>
                <div>
                  <label className="text-[10px] text-surface-500 mb-1 block">Timeout per Device (seconds)</label>
                  <input
                    className="w-full"
                    type="number"
                    min={5}
                    max={120}
                    value={timeoutPerDevice}
                    onChange={e => setTimeoutPerDevice(parseInt(e.target.value) || 30)}
                  />
                </div>
                <p className="text-[9px] text-surface-600">
                  Credentials are used for all discovered devices. They are never stored.
                </p>
              </div>
            )}
          </div>

          <label className="flex items-center gap-2 text-[10px] text-surface-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={clearExisting}
              onChange={e => setClearExisting(e.target.checked)}
              className="rounded border-surface-600"
            />
            Clear existing nodes and links before discovery
          </label>

          <button
            className="btn btn-primary btn-sm w-full"
            onClick={handleStart}
            disabled={!seedIp.trim() || !username.trim() || !password.trim()}
          >
            <Radio size={14} /> Start Discovery
          </button>

          <p className="text-[10px] text-surface-600 leading-relaxed">
            {discoveryMethod === 'igp'
              ? `Connects to the seed device via SSH and pulls the ${igpProtocol.toUpperCase()} link-state database to discover the full routing domain topology. Only requires SSH access to the seed device.`
              : 'Connects to the seed device via SSH, runs LLDP/CDP neighbor commands, and recursively crawls the network. Requires SSH access to all devices.'}
          </p>
        </>
      )}

      {/* Step 2: Running / Step 3: Done */}
      {(step === 'running' || step === 'done') && (
        <>
          {/* Summary bar */}
          <div className="flex items-center gap-3 bg-surface-800/50 border border-surface-700 rounded-lg p-3">
            {isRunning ? (
              <Loader2 size={16} className="text-primary-400 animate-spin shrink-0" />
            ) : result ? (
              <CheckCircle2 size={16} className="text-green-400 shrink-0" />
            ) : (
              <XCircle size={16} className="text-red-400 shrink-0" />
            )}
            <div className="flex-1 min-w-0">
              {isRunning && (
                <div className="text-[11px] text-surface-200">
                  Discovering... ({devices.length} device{devices.length !== 1 ? 's' : ''} found)
                </div>
              )}
              {!isRunning && result && (
                <div className="text-[11px] text-green-400">
                  Complete: {result.nodes_created} nodes, {result.links_created} links created
                </div>
              )}
              {!isRunning && !result && (
                <div className="text-[11px] text-red-400">Discovery failed</div>
              )}
            </div>
          </div>

          {/* Discovered devices */}
          {devices.length > 0 && (
            <div className="bg-surface-800/50 border border-surface-700 rounded-lg p-3">
              <h4 className="text-[11px] font-medium text-surface-300 mb-2">
                Discovered Devices ({devices.length})
              </h4>
              <div className="space-y-1.5 max-h-[200px] overflow-y-auto">
                {devices.map((d, i) => (
                  <div key={i} className="flex items-center gap-2 text-[10px]">
                    <CheckCircle2 size={10} className="text-green-500 shrink-0" />
                    <span className="text-surface-200 font-medium">{d.hostname}</span>
                    <span className="text-surface-500">{d.ip}</span>
                    <span className="text-surface-600 ml-auto">{d.neighbor_count} nbr</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Event log */}
          <div className="bg-surface-800/50 border border-surface-700 rounded-lg p-3">
            <h4 className="text-[11px] font-medium text-surface-300 mb-2">Log</h4>
            <div
              ref={logRef}
              className="max-h-[250px] overflow-y-auto space-y-0.5 font-mono text-[10px]"
            >
              {events.map((ev, i) => (
                <div
                  key={i}
                  className={
                    ev.type === 'error' ? 'text-red-400' :
                    ev.type === 'device' ? 'text-green-400' :
                    ev.type === 'done' ? 'text-primary-400' :
                    'text-surface-500'
                  }
                >
                  {ev.message}
                </div>
              ))}
              {isRunning && (
                <div className="text-surface-600 animate-pulse">...</div>
              )}
            </div>
          </div>

          {/* Actions */}
          {!isRunning && (
            <button className="btn btn-secondary btn-sm w-full" onClick={handleReset}>
              New Discovery
            </button>
          )}
        </>
      )}
    </div>
  );
}

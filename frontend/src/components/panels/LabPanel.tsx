import { useEffect, useMemo, useRef, useState } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import toast from 'react-hot-toast';
import {
  Play, Square, RefreshCw, AlertTriangle, CheckCircle, XCircle, Download,
  ChevronDown, ChevronRight, Server, HardDrive, Upload,
} from 'lucide-react';
import QcowImageManager from './QcowImageManager';
import GnmiSubscriptions from './GnmiSubscriptions';

type PrereqsResponse = {
  xrd_node_count: number;
  all_ok: boolean;
  checks: Array<{ id: string; label: string; value: number | null; required_min: number; ok: boolean; fix_command: string; revert_command?: string; reason: string }>;
  fix_snippet_transient: string;
  fix_snippet_persistent: string;
  revert_snippet_transient: string;
  revert_snippet_persistent: string;
};

type EstimateResponse = {
  topology_estimate: {
    memory_mb: number;
    cpu_cores: number;
    requires_hugepages: boolean;
    node_count: number;
    link_count: number;
  };
  host_capacity: { memory_mb: number; cpu_cores: number; hugepages_count: number };
  fits_memory: boolean | null;
  fits_cpu: boolean | null;
  hugepages_ok: boolean;
  skipped_nodes: Array<{ id: number; name: string; reason: string }>;
  skipped_links: Array<{ id: number; reason: string }>;
};

type LabStatus = {
  status: 'idle' | 'deploying' | 'running' | 'destroying' | 'failed';
  lab_name: string;
  lab_dir: string;
  last_error: string;
  started_at: string | null;
  finished_at: string | null;
  containers: any[];
};

type DeviceImage = {
  filename: string;
  path: string;
  size_mb: number;
  host_loaded_refs?: string[];
};

export default function LabPanel() {
  const { currentTopologyId, nodes } = useStore();
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [prereqs, setPrereqs] = useState<PrereqsResponse | null>(null);
  const [status, setStatus] = useState<LabStatus | null>(null);
  const [otherLabTopoId, setOtherLabTopoId] = useState<number | null>(null);
  const [yaml, setYaml] = useState('');
  const [images, setImages] = useState<DeviceImage[]>([]);
  const [hostLoadedRefs, setHostLoadedRefs] = useState<string[]>([]);
  const [showYaml, setShowYaml] = useState(false);
  const [showImages, setShowImages] = useState(false);
  const [showRevert, setShowRevert] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Subset state lives in the store so canvas clicks + panel checkboxes
  // share one source of truth and stay in sync without polling.
  const labDeploySubset = useStore(s => (s as any).labDeploySubset) as number[];
  const labDeploySubsetExplicit = useStore(s => (s as any).labDeploySubsetExplicit) as boolean;
  const labSubsetSelectMode = useStore(s => (s as any).labSubsetSelectMode) as boolean;
  const loadLabDeploySubset = useStore(s => (s as any).loadLabDeploySubset) as (id: number) => Promise<void>;
  const applyLabDeploySubset = useStore(s => (s as any).applyLabDeploySubset) as (id: number, ids: number[], explicit: boolean) => Promise<void>;
  const setLabSubsetSelectMode = useStore(s => (s as any).setLabSubsetSelectMode) as (on: boolean) => void;
  const subset = useMemo(() => new Set(labDeploySubset), [labDeploySubset]);
  const subsetExplicit = labDeploySubsetExplicit;
  const [showSubset, setShowSubset] = useState(false);

  useEffect(() => {
    if (!currentTopologyId) return;
    loadLabDeploySubset(currentTopologyId).catch(() => {});
  }, [currentTopologyId, loadLabDeploySubset]);

  const saveSubset = async (next: Set<number>, explicit: boolean) => {
    if (!currentTopologyId) return;
    try {
      await applyLabDeploySubset(currentTopologyId, Array.from(next), explicit);
    } catch (e: any) {
      toast.error(`Saving subset failed: ${e.message || e}`);
    }
  };

  const deployableNodes = useMemo(
    () => nodes.filter(n => (n as any).role !== 'customer'),
    [nodes],
  );

  const refresh = async () => {
    if (!currentTopologyId) return;
    try {
      const [est, st, imgs, pre] = await Promise.all([
        api.getLabEstimate(currentTopologyId).catch(() => null),
        api.getLabStatus(currentTopologyId).catch(() => null),
        api.listLabImages().catch(() => ({ images: [] })),
        api.getLabPrereqs(currentTopologyId).catch(() => null),
      ]);
      if (est) setEstimate(est);
      if (st) setStatus(st);
      if (pre) setPrereqs(pre);
      const list = imgs.images || [];
      setImages(list);
      const firstRefs = list.length > 0 ? (list[0].host_loaded_refs || []) : [];
      setHostLoadedRefs(firstRefs);
    } catch (e: any) {
      toast.error(`Lab status: ${e.message}`);
    }
  };

  useEffect(() => { refresh(); }, [currentTopologyId]);

  // Auto-poll while a deploy/destroy is in flight
  useEffect(() => {
    if (!status || (status.status !== 'deploying' && status.status !== 'destroying')) return;
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [status?.status, currentTopologyId]);

  // Only one lab may run globally — track whether ANOTHER topology holds it.
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const summary = await api.getLabRunningSummary();
        const other = Object.keys(summary || {}).map(Number)
          .find(tid => tid !== currentTopologyId && (summary as Record<string, number>)[tid] > 0);
        if (alive) setOtherLabTopoId(other ?? null);
      } catch { /* ignore */ }
    };
    check();
    const id = setInterval(check, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [currentTopologyId]);

  const loadYaml = async () => {
    if (!currentTopologyId) return;
    try {
      const r = await api.getLabYaml(currentTopologyId);
      setYaml(r.yaml || '');
      setShowYaml(true);
    } catch (e: any) {
      toast.error(`YAML preview failed: ${e.message}`);
    }
  };

  const deploy = async () => {
    if (!currentTopologyId) return;
    if (!confirm('Deploy this topology as a containerlab? This will spin up vendor containers on the host.')) return;
    setBusy(true);
    try {
      const r = await api.deployLab(currentTopologyId);
      toast.success(`Deployed${r.skipped_nodes?.length ? ` (${r.skipped_nodes.length} node(s) skipped)` : ''}`);
      refresh();
    } catch (e: any) {
      const msg = parseErrorDetail(e) || e.message;
      toast.error(msg, { duration: 10000 });
      refresh();
    }
    setBusy(false);
  };

  const redeploy = async () => {
    if (!currentTopologyId) return;
    if (!confirm(
      'Redeploy will Destroy the current lab and Deploy with the selected subset.\n\n' +
      'Any in-container running-config that isn\'t in the YAML\'s startup-config will be lost. Continue?'
    )) return;
    setBusy(true);
    try {
      const r = await api.redeployLab(currentTopologyId);
      toast.success(`Redeployed — ${r?.lab_name || 'lab'} running`);
      refresh();
    } catch (e: any) {
      toast.error(parseErrorDetail(e) || e.message, { duration: 10000 });
      refresh();
    }
    setBusy(false);
  };

  const destroy = async () => {
    if (!currentTopologyId) return;
    if (!confirm('Destroy the running lab? All containers spawned for this topology will be removed.')) return;
    setBusy(true);
    try {
      await api.destroyLab(currentTopologyId);
      toast.success('Lab destroyed');
      refresh();
    } catch (e: any) {
      const msg = parseErrorDetail(e) || e.message;
      toast.error(msg, { duration: 10000 });
      refresh();
    }
    setBusy(false);
  };

  const uploadImage = async (file: File) => {
    setUploadPct(0);
    try {
      const r = await api.uploadLabImage(file, p => setUploadPct(p));
      toast.success(`Uploaded ${r.filename} (${r.size_mb} MB) — click Load to import into Docker.`, { duration: 8000 });
      refresh();
    } catch (e: any) {
      toast.error(`Upload failed: ${parseErrorDetail(e) || e.message}`, { duration: 10000 });
    }
    setUploadPct(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const loadImage = async (filename: string) => {
    setBusy(true);
    try {
      const r = await api.loadLabImage(filename);
      const refs = (r.loaded_refs || []).join(', ');
      const notes = (r.notes || []).join(' ');
      const msg = [refs && `Loaded: ${refs}`, notes].filter(Boolean).join(' — ') || filename;
      toast.success(msg, { duration: 10000 });
      refresh();
    } catch (e: any) {
      toast.error(`Load failed: ${parseErrorDetail(e) || e.message}`, { duration: 10000 });
    }
    setBusy(false);
  };

  const statusBadge = useMemo(() => {
    if (!status) return null;
    const map: Record<string, string> = {
      idle: 'bg-surface-700 text-surface-400',
      deploying: 'bg-blue-900/40 text-blue-300',
      running: 'bg-green-900/40 text-green-300',
      destroying: 'bg-amber-900/40 text-amber-300',
      failed: 'bg-red-900/40 text-red-300',
    };
    return (
      <span className={`text-[10px] px-1.5 py-0.5 rounded ${map[status.status] || 'bg-surface-700'}`}>
        {status.status}
      </span>
    );
  }, [status?.status]);

  const canDeploy = status && (status.status === 'idle' || status.status === 'failed') && otherLabTopoId == null;
  // Destroy is allowed whenever a lab_name has ever been set — even if we
  // currently think the lab is 'idle', leftover containers from a partial
  // success or crash may still need cleaning. The sweep is idempotent, so a
  // redundant click just confirms there's nothing left to remove. Only block
  // while a destroy is already mid-flight.
  const canDestroy = status && !!status.lab_name && status.status !== 'destroying';

  return (
    <div className="space-y-3">
      {/* Status header */}
      <div className="border border-surface-700 rounded p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Server size={14} className="text-surface-400" />
            <span className="text-sm font-medium">Lab</span>
            {statusBadge}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={refresh} disabled={busy}>
            <RefreshCw size={12} className={busy ? 'animate-spin' : ''} />
          </button>
        </div>
        {status?.lab_name && (
          <div className="text-[10px] text-surface-500 font-mono truncate" title={status.lab_dir}>
            {status.lab_name}
          </div>
        )}
        {status?.last_error && (
          <div className="text-[10px] text-red-300 bg-red-900/20 border border-red-800/40 rounded p-2 whitespace-pre-wrap break-all max-h-[120px] overflow-y-auto">
            {status.last_error}
          </div>
        )}
        {otherLabTopoId != null && otherLabTopoId !== currentTopologyId && (
          <div className="text-[10px] text-amber-300 bg-amber-900/20 border border-amber-800/40 rounded p-2">
            Another topology (id {otherLabTopoId}) has a running lab — only one lab runs at a time. Destroy it first to deploy this one.
          </div>
        )}
        <div className="flex gap-2">
          <button
            className="btn btn-primary btn-sm flex-1"
            onClick={deploy}
            disabled={busy || !canDeploy}
            title={subset.size > 0 ? `Deploy ${subset.size} selected node(s)` : 'Deploy all nodes'}
          >
            <Play size={12} /> Deploy
          </button>
          <button
            className="btn btn-secondary btn-sm flex-1"
            onClick={redeploy}
            disabled={busy || !status?.lab_name}
            title="Destroy the current lab and Deploy with the latest subset / template state"
          >
            <RefreshCw size={12} /> Redeploy
          </button>
          <button
            className="btn btn-ghost btn-sm flex-1 text-red-400"
            onClick={destroy}
            disabled={busy || !canDestroy}
          >
            <Square size={12} /> Destroy
          </button>
        </div>
      </div>

      {/* Subset selector — choose which nodes the next Deploy/Redeploy brings up.
          "Select all" stores no explicit preference (future-proof — new nodes
          auto-join). "Clear" stores an explicit empty list and Deploy refuses
          until something is picked. Also editable by clicking nodes on the
          canvas while in Lab mode. */}
      <div className="border border-surface-700 rounded p-2 space-y-1.5">
        <button
          className="w-full flex items-center justify-between text-xs font-medium hover:bg-surface-800/50 rounded px-1 py-0.5"
          onClick={() => setShowSubset(v => !v)}
        >
          <span className="flex items-center gap-1.5">
            {showSubset ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            Nodes to deploy
          </span>
          <span className="text-[10px] text-surface-500">
            {!subsetExplicit
              ? `all (${deployableNodes.length})`
              : `${subset.size} / ${deployableNodes.length}`}
          </span>
        </button>
        {showSubset && (
          <div className="space-y-1">
            <button
              className={`btn btn-sm text-[11px] w-full ${
                labSubsetSelectMode
                  ? 'bg-primary-600 hover:bg-primary-500 text-white'
                  : 'btn-secondary'
              }`}
              onClick={() => setLabSubsetSelectMode(!labSubsetSelectMode)}
              title={labSubsetSelectMode
                ? 'Stop selecting — clicks return to default (select node)'
                : 'Enable: single-click any node on the canvas to toggle it in the subset'}
            >
              {labSubsetSelectMode ? '✓ Selecting on canvas — click to stop' : 'Select Nodes to Deploy'}
            </button>
            <div className="flex gap-1.5">
              <button
                className="btn btn-ghost btn-sm text-[10px] flex-1"
                onClick={() => saveSubset(new Set(), false)}
                title="Deploy every node (and any added later)"
              >
                Select all
              </button>
              <button
                className="btn btn-ghost btn-sm text-[10px] flex-1"
                onClick={() => saveSubset(new Set(), true)}
                title="Unselect every node — Deploy will refuse until you pick some"
              >
                Clear
              </button>
            </div>
            <div className="text-[10px] text-surface-500 px-1">
              {subsetExplicit && subset.size === 0
                ? <span className="text-amber-300">No nodes selected — Deploy will refuse until at least one is picked.</span>
                : labSubsetSelectMode
                  ? <span className="text-primary-300">Click a node on the canvas to toggle it. The checkboxes below update live.</span>
                  : 'Toggle "Select Nodes to Deploy" to pick via the canvas, or tick the checkboxes here.'}
            </div>
            <div className="max-h-[160px] overflow-y-auto border border-surface-700/60 rounded">
              {deployableNodes.map(n => {
                // While explicit=false, all checkboxes show "checked".
                const checked = !subsetExplicit || subset.has(n.id);
                return (
                  <label
                    key={n.id}
                    className="flex items-center gap-2 px-2 py-1 text-[11px] hover:bg-surface-800/50 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => {
                        // First toggle while implicit "all" → seed from all
                        // current ids so subsequent toggles operate on the
                        // visible state.
                        const seed = subsetExplicit
                          ? new Set(subset)
                          : new Set(deployableNodes.map(x => x.id));
                        if (seed.has(n.id)) seed.delete(n.id);
                        else seed.add(n.id);
                        saveSubset(seed, true);
                      }}
                    />
                    <span className="flex-1 truncate">{n.name}</span>
                    <span className="text-[9px] text-surface-600 font-mono">{n.platform || '—'}</span>
                  </label>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Host prerequisites */}
      {prereqs && prereqs.checks.length > 0 && (
        <div className={`border rounded p-3 space-y-1.5 ${prereqs.all_ok ? 'border-surface-700' : 'border-amber-700/60 bg-amber-900/10'}`}>
          <div className="text-xs font-medium mb-1 flex items-center gap-1.5">
            {prereqs.all_ok ? <CheckCircle size={12} className="text-green-400" /> : <AlertTriangle size={12} className="text-amber-400" />}
            Host Prerequisites
            {prereqs.xrd_node_count > 0 && (
              <span className="text-[10px] text-surface-500 ml-1">({prereqs.xrd_node_count} XRd node{prereqs.xrd_node_count > 1 ? 's' : ''})</span>
            )}
          </div>
          {prereqs.checks.map(c => (
            <div key={c.id} className="text-[11px] flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 min-w-0">
                {c.ok ? <CheckCircle size={11} className="text-green-400 shrink-0" /> : <XCircle size={11} className="text-red-400 shrink-0" />}
                <span className="font-mono text-surface-300 truncate" title={c.reason}>{c.label}</span>
              </div>
              <span className="text-surface-400 text-[10px] whitespace-nowrap">
                <span className={c.ok ? 'text-surface-300' : 'text-amber-300'}>{c.value ?? '?'}</span>
                {c.required_min > 0 && <> / <span className="text-surface-400">≥ {c.required_min}</span></>}
              </span>
            </div>
          ))}
          {!prereqs.all_ok && (
            <div className="pt-2 space-y-1.5">
              <div className="text-[10px] text-amber-300">
                These settings must be applied on the host (not from inside Phanero).
                Run as root:
              </div>
              <pre className="text-[10px] font-mono bg-surface-900/60 border border-surface-700 rounded p-2 whitespace-pre-wrap break-all max-h-[120px] overflow-y-auto">
                {prereqs.fix_snippet_transient}
              </pre>
              <div className="flex gap-1.5">
                <button
                  className="btn btn-ghost btn-sm text-[10px] flex-1"
                  onClick={() => navigator.clipboard.writeText(prereqs.fix_snippet_transient).then(() => toast.success('Copied transient fix'))}
                >
                  Copy fix
                </button>
                {prereqs.fix_snippet_persistent && (
                  <button
                    className="btn btn-ghost btn-sm text-[10px] flex-1"
                    onClick={() => navigator.clipboard.writeText(prereqs.fix_snippet_persistent).then(() => toast.success('Copied persistent fix'))}
                    title="Writes /etc/sysctl.d/99-xrd.conf so settings survive reboot"
                  >
                    Copy persistent fix
                  </button>
                )}
                <button
                  className="btn btn-ghost btn-sm text-[10px]"
                  onClick={refresh}
                  title="Re-check after applying"
                >
                  <RefreshCw size={11} />
                </button>
              </div>
            </div>
          )}
          {/* Revert / reset-to-defaults — always shown, independent of all_ok,
              so the user can roll back after applying. Collapsed by default. */}
          {(prereqs.revert_snippet_transient || prereqs.revert_snippet_persistent) && (
            <div className="pt-2 border-t border-surface-700/60">
              <button
                className="text-[10px] text-surface-400 hover:text-surface-200 flex items-center gap-1 w-full"
                onClick={() => setShowRevert(v => !v)}
              >
                {showRevert ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                Reset host to defaults
              </button>
              {showRevert && (
                <div className="mt-1.5 space-y-1.5">
                  <div className="text-[10px] text-surface-500">
                    Undo the changes the apply step makes. Run as root on the host —
                    safe to re-run, and won't break the box if a setting was already
                    at its default.
                  </div>
                  <pre className="text-[10px] font-mono bg-surface-900/60 border border-surface-700 rounded p-2 whitespace-pre-wrap break-all max-h-[140px] overflow-y-auto">
                    {prereqs.revert_snippet_transient}
                  </pre>
                  <div className="flex gap-1.5">
                    <button
                      className="btn btn-ghost btn-sm text-[10px] flex-1"
                      onClick={() => navigator.clipboard.writeText(prereqs.revert_snippet_transient).then(() => toast.success('Copied revert commands'))}
                    >
                      Copy revert
                    </button>
                    {prereqs.revert_snippet_persistent && (
                      <button
                        className="btn btn-ghost btn-sm text-[10px] flex-1"
                        onClick={() => navigator.clipboard.writeText(prereqs.revert_snippet_persistent).then(() => toast.success('Copied persistent revert'))}
                        title="Remove the persistent drop-in files created by the persistent fix"
                      >
                        Copy persistent revert
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Resource estimate */}
      {estimate && (
        <div className="border border-surface-700 rounded p-3 space-y-1.5">
          <div className="text-xs font-medium mb-1 flex items-center gap-1.5">
            <HardDrive size={12} className="text-surface-400" />
            Resource Estimate
          </div>
          <ResourceRow
            label="Memory"
            need={`${estimate.topology_estimate.memory_mb} MB`}
            have={`${estimate.host_capacity.memory_mb} MB`}
            fits={estimate.fits_memory}
          />
          <ResourceRow
            label="CPU cores"
            need={String(estimate.topology_estimate.cpu_cores)}
            have={String(estimate.host_capacity.cpu_cores)}
            fits={estimate.fits_cpu}
          />
          <ResourceRow
            label="Hugepages"
            need={estimate.topology_estimate.requires_hugepages ? 'required' : 'none'}
            have={String(estimate.host_capacity.hugepages_count || 0)}
            fits={estimate.hugepages_ok}
          />
          <div className="text-[10px] text-surface-500 pt-1">
            {estimate.topology_estimate.node_count} node(s), {estimate.topology_estimate.link_count} link(s) will be deployed
          </div>
          {estimate.skipped_nodes.length > 0 && (
            <div className="text-[10px] text-amber-300 mt-1 space-y-0.5">
              <div className="flex items-center gap-1"><AlertTriangle size={10} />{estimate.skipped_nodes.length} node(s) skipped:</div>
              {estimate.skipped_nodes.slice(0, 5).map(n => (
                <div key={n.id} className="ml-3 truncate">• {n.name}: {n.reason}</div>
              ))}
              {estimate.skipped_nodes.length > 5 && (
                <div className="ml-3 text-surface-500">…and {estimate.skipped_nodes.length - 5} more</div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Running container status */}
      {status && status.containers.length > 0 && (
        <div className="border border-surface-700 rounded p-3 space-y-1">
          <div className="text-xs font-medium mb-1">Live Containers ({status.containers.length})</div>
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead className="text-surface-500">
                <tr>
                  <th className="text-left py-0.5">Name</th>
                  <th className="text-left py-0.5">Kind</th>
                  <th className="text-left py-0.5">State</th>
                  <th className="text-left py-0.5">IPv4</th>
                </tr>
              </thead>
              <tbody>
                {status.containers.map((c, i) => (
                  <tr key={i} className="border-t border-surface-700/40">
                    <td className="py-0.5 truncate">{c.name || c.lab_name || '?'}</td>
                    <td className="py-0.5 text-surface-400">{c.kind || '?'}</td>
                    <td className="py-0.5">
                      <span className={c.state === 'running' || c.status === 'running' ? 'text-green-400' : 'text-amber-400'}>
                        {c.state || c.status || '?'}
                      </span>
                    </td>
                    <td className="py-0.5 font-mono text-surface-400">{c.ipv4_address || c.ipv4 || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* YAML preview */}
      <div className="border border-surface-700 rounded">
        <button
          className="w-full px-3 py-2 flex items-center justify-between hover:bg-surface-800 transition-colors"
          onClick={() => { if (!yaml) loadYaml(); else setShowYaml(!showYaml); }}
        >
          <span className="text-xs font-medium">Generated YAML</span>
          {showYaml ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        {showYaml && (
          <div className="border-t border-surface-700">
            <div className="flex items-center justify-end gap-2 px-3 py-1.5 border-b border-surface-700">
              <button
                className="btn btn-ghost btn-sm text-[10px]"
                onClick={() => navigator.clipboard.writeText(yaml).then(() => toast.success('Copied YAML'))}
              >
                Copy
              </button>
              <button
                className="btn btn-ghost btn-sm text-[10px]"
                onClick={() => {
                  const blob = new Blob([yaml], { type: 'text/yaml' });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url; a.download = `${status?.lab_name || 'topology'}.clab.yml`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                <Download size={10} /> Download
              </button>
            </div>
            <pre className="text-[10px] font-mono p-3 max-h-[280px] overflow-y-auto whitespace-pre-wrap break-all bg-surface-900/40">
              {yaml || '(loading…)'}
            </pre>
          </div>
        )}
      </div>

      {/* Device images */}
      <div className="border border-surface-700 rounded">
        <button
          className="w-full px-3 py-2 flex items-center justify-between hover:bg-surface-800 transition-colors"
          onClick={() => setShowImages(!showImages)}
        >
          <span className="text-xs font-medium">Device Images ({images.length})</span>
          {showImages ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        {showImages && (
          <div className="border-t border-surface-700 p-3 space-y-2">
            <div className="text-[10px] text-surface-500">
              Files under <code className="text-surface-400">device_images/</code> on the host.
              Click <em>Load</em> to import a .tgz into the host Docker, then paste the resulting
              ref into a node template's <code>image_ref</code>.
            </div>

            {/* Upload control */}
            <div className="flex items-center gap-2 p-2 bg-surface-900/50 border border-dashed border-surface-700 rounded">
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={e => {
                  const f = e.target.files?.[0];
                  if (f) uploadImage(f);
                }}
              />
              <button
                className="btn btn-ghost btn-sm text-[10px]"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadPct !== null}
              >
                <Upload size={11} /> Upload Image
              </button>
              {uploadPct !== null ? (
                <div className="flex-1 flex items-center gap-2">
                  <div className="flex-1 h-1.5 bg-surface-700 rounded overflow-hidden">
                    <div className="h-full bg-primary-500 transition-all" style={{ width: `${uploadPct}%` }} />
                  </div>
                  <span className="text-[10px] text-surface-400 w-10 text-right">{uploadPct}%</span>
                </div>
              ) : (
                <span className="text-[10px] text-surface-500 flex-1">
                  Pick a vendor .tgz / .tar (XRd signed bundle, cEOS-lab, etc.)
                </span>
              )}
            </div>

            {images.length === 0 && (
              <div className="text-[10px] text-surface-500">No image files found in device_images/.</div>
            )}
            {images.map(img => (
              <div key={img.filename} className="flex items-center justify-between gap-2 text-[10px]">
                <div className="flex-1 min-w-0">
                  <div className="font-mono truncate text-surface-300" title={img.path}>{img.filename}</div>
                  <div className="text-surface-500">{img.size_mb} MB</div>
                </div>
                <button
                  className="btn btn-ghost btn-sm text-[10px]"
                  onClick={() => loadImage(img.filename)}
                  disabled={busy}
                >
                  Load
                </button>
              </div>
            ))}
            {hostLoadedRefs.length > 0 && (
              <details className="text-[10px] mt-2">
                <summary className="cursor-pointer text-surface-500 hover:text-surface-300">
                  Currently loaded on host ({hostLoadedRefs.length})
                </summary>
                <div className="mt-1 max-h-[160px] overflow-y-auto space-y-0.5 font-mono">
                  {hostLoadedRefs.map(r => (
                    <div key={r} className="text-surface-400 truncate" title={r}>
                      {r}
                      {r !== '<none>:<none>' && (
                        <button
                          className="ml-2 text-primary-400 hover:text-primary-300"
                          onClick={() => navigator.clipboard.writeText(r).then(() => toast.success('Copied'))}
                        >
                          copy
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>

      {/* Lab telemetry add-on: custom gNMI subscriptions → Prometheus. Renders
          nothing unless PHANERO_LAB_TELEMETRY_ENABLED. */}
      <GnmiSubscriptions />

      {/* QCOW VM image build flow (vrnetlab). Separate from the .tgz `docker
          load` section above since it has its own lifecycle (upload -> build
          -> built tag) and its own backend resource. */}
      <QcowImageManager />
    </div>
  );
}

function ResourceRow({ label, need, have, fits }: { label: string; need: string; have: string; fits: boolean | null }) {
  return (
    <div className="flex items-center justify-between text-[11px]">
      <span className="text-surface-400">{label}</span>
      <span className="flex items-center gap-1.5">
        <span className="text-surface-300">{need}</span>
        <span className="text-surface-600">/</span>
        <span className="text-surface-400">{have}</span>
        {fits === true && <CheckCircle size={11} className="text-green-400" />}
        {fits === false && <XCircle size={11} className="text-red-400" />}
      </span>
    </div>
  );
}

function parseErrorDetail(e: any): string | null {
  try {
    const body = e?.message?.split(': ').slice(1).join(': ');
    const parsed = body ? JSON.parse(body) : null;
    if (parsed?.detail && typeof parsed.detail === 'string') return parsed.detail;
  } catch { /* fall through */ }
  return null;
}

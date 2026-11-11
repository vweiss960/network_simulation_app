/**
 * ServiceDeployPanel — the right column of ServiceDesignView.
 *
 * Lets the operator pick a target mode (lab node vs management IP), build the
 * per-device deploy input via a hybrid Form|JSON editor (one shared
 * `deployments` source of truth), Preview the rendered config (dry-run), and
 * Deploy it live over SSH — showing per-device results and the XRd transcript.
 */
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import {
  Server, Globe, Eye, Send, Loader2, CheckCircle2, XCircle, ChevronDown, ChevronRight, Copy,
} from 'lucide-react';
import { useStore } from '../stores/useStore';
import { api } from '../utils/api';

type VarDecl = { id: number; name: string; required: boolean; default: any; description: string };
type Deployment = { device: string; variables: Record<string, any> };
type ResultRow = { device: string; ok: boolean; rendered?: string; output?: string; error?: string; pushed?: boolean };

interface Props {
  serviceId: number;
  defaultTargetMode: string;
  variables: VarDecl[];
  topologyId: number | null;
}

/** Best-effort coercion of a form string into a JSON scalar so `{{ asn }}`
 *  becomes a number when it looks like one. Non-matching input stays a string. */
function coerce(s: string): any {
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (/^-?\d+$/.test(s)) return parseInt(s, 10);
  if (/^-?\d+\.\d+$/.test(s)) return parseFloat(s);
  return s;
}
function forInput(v: any): string {
  if (v === undefined || v === null) return '';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

export default function ServiceDeployPanel({ serviceId, defaultTargetMode, variables, topologyId }: Props) {
  const storeNodes = useStore(s => s.nodes);
  const [targetMode, setTargetMode] = useState<'lab' | 'mgmt_ip'>(defaultTargetMode === 'mgmt_ip' ? 'mgmt_ip' : 'lab');
  const [creds, setCreds] = useState({ username: '', password: '' });
  const [inputMode, setInputMode] = useState<'form' | 'json'>('form');
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [jsonText, setJsonText] = useState('[]');
  const [jsonError, setJsonError] = useState('');
  const [labNodes, setLabNodes] = useState<{ name: string; kind: string }[]>([]);
  const [labErr, setLabErr] = useState('');
  const [busy, setBusy] = useState<false | 'preview' | 'deploy'>(false);
  const [results, setResults] = useState<{ kind: 'preview' | 'deploy'; rows: ResultRow[] } | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Candidate devices per mode: lab view nodes (lab) or topology nodes with a
  // mgmt IP (mgmt_ip).
  useEffect(() => {
    if (targetMode !== 'lab' || !topologyId) { setLabNodes([]); setLabErr(''); return; }
    let alive = true;
    api.getLabView(topologyId)
      .then(v => { if (alive) { setLabNodes((v.nodes || []).map((n: any) => ({ name: n.name, kind: n.lab_kind || '' }))); setLabErr(''); } })
      .catch(() => { if (alive) { setLabNodes([]); setLabErr('Could not load lab nodes — is the lab deployed?'); } });
    return () => { alive = false; };
  }, [targetMode, topologyId]);

  const candidates = useMemo(() => {
    if (targetMode === 'lab') return labNodes.map(n => n.name);
    return storeNodes.filter(n => (n.mgmt_ip || '').trim()).map(n => n.name);
  }, [targetMode, labNodes, storeNodes]);

  // Keep the JSON textarea in sync when entering JSON mode.
  useEffect(() => {
    if (inputMode === 'json') { setJsonText(JSON.stringify(deployments, null, 2)); setJsonError(''); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputMode]);

  const selected = useMemo(() => new Set(deployments.map(d => d.device)), [deployments]);

  const toggleDevice = (name: string) => {
    setDeployments(ds => selected.has(name)
      ? ds.filter(d => d.device !== name)
      : [...ds, { device: name, variables: {} }]);
  };
  const setVar = (device: string, key: string, raw: string) => {
    setDeployments(ds => ds.map(d => d.device === device
      ? { ...d, variables: { ...d.variables, [key]: coerce(raw) } }
      : d));
  };

  const onJsonChange = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed)) { setJsonError('Top level must be an array of deployments.'); return; }
      setJsonError('');
      setDeployments(parsed);
    } catch (e: any) {
      setJsonError(e.message || 'Invalid JSON');
    }
  };

  const payload = () => ({
    topology_id: topologyId as number,
    target_mode: targetMode,
    deployments,
    ...(targetMode === 'mgmt_ip' ? { credentials: creds } : {}),
  });

  const canRun = topologyId != null && deployments.length > 0 && !jsonError && !busy
    && (targetMode !== 'mgmt_ip' || creds.username.trim() !== '');

  const onPreview = async () => {
    if (!canRun) return;
    setBusy('preview'); setResults(null);
    try {
      const res = await api.renderService(serviceId, payload());
      setResults({ kind: 'preview', rows: res.results });
    } catch (e: any) {
      toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Preview failed');
    }
    setBusy(false);
  };

  const onDeploy = async () => {
    if (!canRun) return;
    if (!confirm(`Push config to ${deployments.length} device(s) via ${targetMode}? This changes real devices.`)) return;
    setBusy('deploy'); setResults(null);
    try {
      const res = await api.deployService(serviceId, { ...payload(), dry_run: false });
      setResults({ kind: 'deploy', rows: res.results });
      const failed = res.results.filter((r: ResultRow) => !r.ok).length;
      failed ? toast.error(`${failed} of ${res.results.length} device(s) failed`) : toast.success('Deployed to all devices');
    } catch (e: any) {
      toast.error(e?.message?.replace(/^API error \d+: /, '') || 'Deploy failed');
    }
    setBusy(false);
  };

  return (
    <div className="w-[420px] shrink-0 border-l border-surface-700 bg-surface-900 flex flex-col h-full">
      <div className="px-3 py-2 border-b border-surface-700 text-xs font-medium flex items-center gap-1.5">
        <Send size={12} className="text-sky-400" /> Deploy
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3 text-xs">
        {/* API reference — service id + endpoints for scripting this deploy */}
        <div className="border border-surface-700 rounded p-2 bg-surface-950/50 space-y-1">
          <div className="text-[10px] text-surface-400">
            API — service id <span className="font-mono text-sky-300">{serviceId}</span>
          </div>
          {[
            { label: 'dry-run', path: `/api/services/${serviceId}/render` },
            { label: 'deploy', path: `/api/services/${serviceId}/deploy` },
          ].map(ep => (
            <button key={ep.label}
              className="w-full flex items-center gap-1.5 text-[10px] font-mono px-1 py-0.5 rounded hover:bg-surface-800 group"
              title={`Copy: POST ${ep.path}`}
              onClick={() => navigator.clipboard?.writeText(`POST ${ep.path}`).then(() => toast.success('Endpoint copied'))}>
              <span className="text-surface-500">POST</span>
              <span className="text-surface-300 truncate">{ep.path}</span>
              <span className="text-surface-600 ml-auto shrink-0">{ep.label}</span>
              <Copy size={10} className="text-surface-600 group-hover:text-surface-300 shrink-0" />
            </button>
          ))}
        </div>

        {/* Target mode toggle */}
        <div>
          <div className="text-[10px] text-surface-400 mb-1">Target</div>
          <div className="flex rounded overflow-hidden border border-surface-700 w-full">
            {(['lab', 'mgmt_ip'] as const).map(m => (
              <button key={m}
                className={`flex-1 px-2 py-1.5 flex items-center justify-center gap-1.5 ${
                  targetMode === m ? 'bg-sky-700/40 text-sky-100' : 'bg-surface-800 text-surface-400 hover:text-surface-200'}`}
                onClick={() => { setTargetMode(m); setDeployments([]); setResults(null); }}>
                {m === 'lab' ? <Server size={12} /> : <Globe size={12} />}
                {m === 'lab' ? 'Lab node' : 'Mgmt IP'}
              </button>
            ))}
          </div>
        </div>

        {targetMode === 'mgmt_ip' && (
          <div className="grid grid-cols-2 gap-2">
            <label className="text-[10px] text-surface-400">Username
              <input className="input input-sm w-full text-xs mt-0.5" value={creds.username}
                onChange={e => setCreds(c => ({ ...c, username: e.target.value }))} />
            </label>
            <label className="text-[10px] text-surface-400">Password
              <input type="password" className="input input-sm w-full text-xs mt-0.5" value={creds.password}
                onChange={e => setCreds(c => ({ ...c, password: e.target.value }))} />
            </label>
          </div>
        )}

        {/* Input mode toggle */}
        <div className="flex items-center justify-between">
          <div className="text-[10px] text-surface-400">Deployments ({deployments.length})</div>
          <div className="flex rounded overflow-hidden border border-surface-700 text-[10px]">
            {(['form', 'json'] as const).map(m => (
              <button key={m} className={`px-2 py-0.5 ${inputMode === m ? 'bg-surface-700 text-surface-100' : 'text-surface-400'}`}
                onClick={() => setInputMode(m)}>{m === 'form' ? 'Form' : 'JSON'}</button>
            ))}
          </div>
        </div>

        {inputMode === 'form' ? (
          <div className="space-y-2">
            {labErr && <div className="text-[10px] text-amber-300">{labErr}</div>}
            {candidates.length === 0 && !labErr && (
              <div className="text-[10px] text-surface-500">
                {targetMode === 'lab' ? 'No lab nodes found.' : 'No topology nodes have a management IP.'}
              </div>
            )}
            {/* Device checklist */}
            <div className="flex flex-wrap gap-1">
              {candidates.map(name => (
                <button key={name}
                  className={`text-[10px] px-1.5 py-0.5 rounded border ${
                    selected.has(name) ? 'bg-sky-700/30 border-sky-600/50 text-sky-100' : 'border-surface-700 text-surface-400 hover:text-surface-200'}`}
                  onClick={() => toggleDevice(name)}>{name}</button>
              ))}
            </div>
            {/* Per-device variable fields */}
            {deployments.map(d => (
              <div key={d.device} className="border border-surface-700 rounded p-2 space-y-1">
                <div className="text-[11px] font-medium text-sky-200">{d.device}</div>
                {variables.length === 0 && <div className="text-[10px] text-surface-500">No declared variables — use JSON mode to add ad-hoc values.</div>}
                {variables.map(v => (
                  <label key={v.id} className="block text-[10px] text-surface-400">
                    {v.name}{v.required && <span className="text-red-400"> *</span>}
                    <input className="input input-sm w-full text-xs mt-0.5"
                      placeholder={v.default != null ? `default: ${forInput(v.default)}` : ''}
                      value={forInput(d.variables[v.name])}
                      onChange={e => setVar(d.device, v.name, e.target.value)} />
                  </label>
                ))}
              </div>
            ))}
          </div>
        ) : (
          <div>
            <textarea className="input w-full text-[11px] font-mono h-56 resize-none"
              value={jsonText} onChange={e => onJsonChange(e.target.value)} spellCheck={false} />
            {jsonError && <div className="text-[10px] text-red-300 mt-1">{jsonError}</div>}
            <div className="text-[9px] text-surface-500 mt-1">
              Array of {'{ device, templates?, variables }'}. Edits sync to the form.
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="border-t border-surface-700 p-2 flex gap-2">
        <button className="btn btn-ghost btn-sm text-xs flex-1" onClick={onPreview} disabled={!canRun}>
          {busy === 'preview' ? <Loader2 size={12} className="animate-spin" /> : <Eye size={12} />} Preview
        </button>
        <button className="btn btn-primary btn-sm text-xs flex-1" onClick={onDeploy} disabled={!canRun}>
          {busy === 'deploy' ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} Deploy
        </button>
      </div>

      {/* Results */}
      {results && (
        <div className="border-t border-surface-700 max-h-[45%] overflow-y-auto p-2 space-y-2">
          <div className="text-[10px] text-surface-400">{results.kind === 'preview' ? 'Preview' : 'Deploy result'}</div>
          {results.rows.map((r, i) => {
            const key = `${r.device}-${i}`;
            const open = expanded[key];
            return (
              <div key={key} className="border border-surface-700 rounded">
                <button className="w-full flex items-center gap-1.5 px-2 py-1 text-[11px]"
                  onClick={() => setExpanded(e => ({ ...e, [key]: !open }))}>
                  {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  {r.ok ? <CheckCircle2 size={12} className="text-green-400" /> : <XCircle size={12} className="text-red-400" />}
                  <span className="font-medium truncate">{r.device}</span>
                  {results.kind === 'deploy' && <span className="ml-auto text-[9px] text-surface-500">{r.pushed ? 'pushed' : 'not pushed'}</span>}
                </button>
                {open && (
                  <div className="px-2 pb-2 space-y-1">
                    {r.error && <div className="text-[10px] text-red-300 bg-red-900/20 border border-red-700/40 rounded p-1.5">{r.error}</div>}
                    {r.rendered && (
                      <pre className="text-[10px] font-mono bg-surface-950 border border-surface-700 rounded p-2 whitespace-pre-wrap break-all max-h-40 overflow-y-auto">{r.rendered}</pre>
                    )}
                    {r.output && (
                      <>
                        <div className="text-[9px] text-surface-500">device transcript</div>
                        <pre className="text-[10px] font-mono bg-surface-950 border border-surface-700 rounded p-2 whitespace-pre-wrap break-all max-h-40 overflow-y-auto">{r.output}</pre>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

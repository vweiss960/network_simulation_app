import { useEffect, useState } from 'react';
import { AlertTriangle, Camera, CheckCircle2, FileCode2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';

/** Inspector content when nothing is selected: lab health and resources. */
export default function LabSummary() {
  const { currentTopologyId, lab, nodes, links, kinds } = useLabStore();
  const [est, setEst] = useState<any>(null);
  const [prereqs, setPrereqs] = useState<any>(null);
  const [yaml, setYaml] = useState<string | null>(null);

  useEffect(() => {
    if (!currentTopologyId) return;
    api.getLabEstimate(currentTopologyId).then(setEst).catch(() => {});
    api.getLabPrereqs(currentTopologyId).then(setPrereqs).catch(() => {});
  }, [currentTopologyId, nodes.length, links.length]);

  const vmCount = nodes.filter(n => kinds[n.kind]?.is_vm).length;
  const failing = (prereqs?.checks ?? []).filter((c: any) => !c.ok);
  const mem = est?.topology_estimate?.memory_mb ?? 0;
  const hostMem = est?.host_capacity?.memory_mb ?? 0;

  return (
    <div className="h-full overflow-y-auto text-sm">
      <div className="p-3 border-b border-surface-700">
        <div className="text-xs font-semibold text-surface-300 mb-2">This lab</div>
        <div className="grid grid-cols-2 gap-y-1 text-xs">
          <span className="text-surface-500">Devices</span><span>{nodes.length}{vmCount ? ` (${vmCount} VM)` : ''}</span>
          <span className="text-surface-500">Cables</span><span>{links.length}</span>
          <span className="text-surface-500">Memory</span>
          <span>{(mem / 1024).toFixed(1)} GB{hostMem ? <span className="text-surface-500"> of {(hostMem / 1024).toFixed(0)} GB</span> : null}</span>
          <span className="text-surface-500">vCPU</span>
          <span>{est?.topology_estimate?.cpu_cores ?? 0}{est?.host_capacity?.cpu_cores ? <span className="text-surface-500"> of {est.host_capacity.cpu_cores}</span> : null}</span>
        </div>
        <button onClick={() => currentTopologyId && api.getLabYaml(currentTopologyId).then(r => setYaml(r.yaml))}
          className="mt-3 flex items-center gap-1 text-xs text-primary-300 hover:text-primary-200">
          <FileCode2 size={13} /> View containerlab YAML
        </button>
        {lab && lab.status !== 'idle' && (
          <button className="mt-2 flex items-center gap-1 text-xs text-primary-300 hover:text-primary-200"
            onClick={async () => {
              if (!currentTopologyId) return;
              const label = prompt('Label for this lab snapshot (optional)');
              if (label === null) return;
              const t = toast.loading('Saving configs…');
              try {
                const r = await api.snapshotLab(currentTopologyId, label);
                toast.success(`Saved ${r.taken.length} device config(s)${r.skipped.length ? `, skipped ${r.skipped.length}` : ''}`, { id: t });
              } catch (e) { toast.error((e as Error).message, { id: t }); }
            }}>
            <Camera size={13} /> Snapshot all device configs
          </button>
        )}
      </div>

      {lab?.last_error && (
        <div className="p-3 border-b border-surface-700">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-red-300 mb-1"><AlertTriangle size={13} /> Last lab error</div>
          <pre className="text-[11px] whitespace-pre-wrap text-red-200/80 max-h-48 overflow-y-auto">{lab.last_error}</pre>
        </div>
      )}

      {!!lab?.skipped_nodes?.filter(s => !s.off).length && (
        <div className="p-3 border-b border-surface-700">
          <div className="text-xs font-semibold text-amber-300 mb-1">Won't deploy</div>
          {lab.skipped_nodes.filter(s => !s.off).map(s => <div key={s.id} className="text-xs text-surface-300"><b>{s.name}</b>: {s.reason}</div>)}
        </div>
      )}
      {!!lab?.skipped_nodes?.filter(s => s.off).length && (
        <div className="p-3 border-b border-surface-700 text-xs text-surface-300">
          <b>Off:</b> {lab.skipped_nodes.filter(s => s.off).map(s => s.name).join(', ')} — right-click a device → Start.
        </div>
      )}

      <div className="p-3 border-b border-surface-700">
        <div className="text-xs font-semibold text-surface-300 mb-2">Host checks</div>
        {failing.length === 0
          ? <div className="flex items-center gap-1.5 text-xs text-emerald-300"><CheckCircle2 size={13} /> All prerequisites met</div>
          : failing.map((c: any) => (
            <details key={c.id} className="mb-2">
              <summary className="text-xs text-amber-300 cursor-pointer">{c.label}: {String(c.value)} (needs {c.required_min})</summary>
              <div className="text-[11px] text-surface-400 mt-1">{c.reason}</div>
              <pre className="mt-1 p-2 rounded bg-surface-900 text-[11px] whitespace-pre-wrap select-all">{c.fix_command}</pre>
            </details>
          ))}
      </div>

      <div className="p-3 text-xs text-surface-400 space-y-1">
        <div className="font-semibold text-surface-300">Tips</div>
        <div>Right-click the canvas → Add device; drag from a device's blue dot to another device to cable them.</div>
        <div>Start devices one at a time (right-click → Start) or all at once with Deploy; cables to devices that are off connect when they start.</div>
        <div>Changes to a running lab apply live: new devices and cables are added, removed ones are torn down.</div>
        <div>Double-click a running device to open its console (SSH for network OSes, so config mode works).</div>
        <div>Cabling is live: plugging or unplugging a cable never restarts a device.</div>
        <div>Use Group box, Text and Note (top right of the canvas) to annotate the lab; Ctrl/⌘ K asks the AI.</div>
      </div>

      {yaml !== null && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={() => setYaml(null)}>
          <div className="w-full max-w-3xl max-h-full flex flex-col bg-surface-800 rounded-lg border border-surface-600" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-2 border-b border-surface-700">
              <span className="text-sm font-semibold">topology.clab.yml</span>
              <button onClick={() => setYaml(null)}><X size={16} /></button>
            </div>
            <pre className="p-4 overflow-auto text-xs font-mono text-surface-200">{yaml}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

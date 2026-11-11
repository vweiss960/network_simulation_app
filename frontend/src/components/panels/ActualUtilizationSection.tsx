import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Upload, Download, Trash2, ChevronDown, ChevronRight, Info } from 'lucide-react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';

/**
 * Link metrics: import per-interface measurements as CSV (instance,
 * interface, peak_30d_bps, peak_90d_bps, link_delay_us), export the circuit
 * report CSV, and show how well the import matched the topology.
 */
export default function ActualUtilizationSection() {
  const currentTopologyId = useStore(s => s.currentTopologyId);
  const actual = useStore(s => s.actualUtilization);
  const utilizationView = useStore(s => s.utilizationView);
  const setUtilizationView = useStore(s => s.setUtilizationView);
  const loadActualUtilization = useStore(s => s.loadActualUtilization);
  const importCsv = useStore(s => s.importActualUtilizationCsv);
  const clearActual = useStore(s => s.clearActualUtilization);
  // The import writes link latency, so it needs an edit session on protected topologies.
  const canEdit = useStore(s => s.canEditCurrentTopology());
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [showUnmatched, setShowUnmatched] = useState(false);

  useEffect(() => {
    if (currentTopologyId) loadActualUtilization().catch(() => {});
  }, [currentTopologyId, loadActualUtilization]);

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setBusy(true);
    try {
      const r = await importCsv(await file.text());
      const skipped = r.error_count ? `, ${r.error_count} row(s) skipped` : '';
      const delays = r.latency_updated_links ? `, latency updated on ${r.latency_updated_links} link(s)` : '';
      toast.success(`Imported ${r.imported_rows} interface rows — ${r.links_with_data}/${r.total_links} links matched${delays}${skipped}`);
      if (r.latency_skipped_inherited) {
        toast(`${r.latency_skipped_inherited} inherited link(s) kept their latency — import delays on the parent topology to update them.`);
      }
      if (utilizationView === 'demand') setUtilizationView('actual_30d');
    } catch (err: any) {
      toast.error(parseDetail(err) || 'Import failed');
    } finally {
      setBusy(false);
    }
  };

  const handleExport = async () => {
    if (!currentTopologyId) return;
    try { await api.exportLinkUtilizationCsv(currentTopologyId); }
    catch { toast.error('Export failed'); }
  };

  const handleClear = async () => {
    if (!confirm('Remove all imported link metrics for this topology? Link latencies already set from the import are kept.')) return;
    try { await clearActual(); toast.success('Imported link metrics cleared'); }
    catch { toast.error('Clear failed'); }
  };

  const hasData = !!actual && actual.metric_rows > 0;
  const unmatchedCount = (actual?.unmatched_instances.length || 0) + (actual?.unmatched_interface_count || 0);

  return (
    <div className="relative bg-surface-800/60 rounded-lg px-3 py-2 space-y-2">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1 text-[11px] text-surface-300">
          Link Metrics
          <CsvSchemaHint />
        </span>
        {hasData && actual?.updated_at && (
          <span className="text-[10px] text-surface-500" title="Last import">
            {new Date(actual.updated_at + (actual.updated_at.endsWith('Z') ? '' : 'Z')).toLocaleString()}
          </span>
        )}
      </div>
      <div className="flex gap-1.5">
        <button className="btn btn-ghost btn-sm flex-1 text-[11px]" disabled={busy || !canEdit} onClick={() => fileInput.current?.click()}
          title={canEdit
            ? 'CSV columns: instance, interface, peak_30d_bps, peak_90d_bps, link_delay_us. Replaces any previous import.'
            : 'Enter edit mode to import — the import updates link latency'}>
          <Upload size={12} /> {busy ? 'Importing…' : 'Import CSV'}
        </button>
        <button className="btn btn-ghost btn-sm flex-1 text-[11px]" onClick={handleExport}
          title="Circuit ID, speed, A/Z location, 30/90-day max BW (Mbps), latency">
          <Download size={12} /> Export CSV
        </button>
        {hasData && (
          <button className="btn btn-ghost btn-sm text-[11px] text-red-400" onClick={handleClear} title="Clear imported data">
            <Trash2 size={12} />
          </button>
        )}
        <input ref={fileInput} type="file" accept=".csv,text/csv" className="hidden" onChange={handleFile} />
      </div>
      {hasData ? (
        <div className="text-[10px] text-surface-400 space-y-1">
          <div>
            {actual!.metric_rows} interface rows · {actual!.links_with_data}/{actual!.total_links} links with data
          </div>
          {unmatchedCount > 0 && (
            <div>
              <button className="flex items-center gap-1 text-amber-400 hover:text-amber-300" onClick={() => setShowUnmatched(v => !v)}>
                {showUnmatched ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                {unmatchedCount} row(s) not matched to a link
              </button>
              {showUnmatched && (
                <div className="mt-1 max-h-40 overflow-y-auto font-mono bg-surface-900 rounded p-1.5 space-y-0.5">
                  {actual!.unmatched_instances.map(i => (
                    <div key={`i-${i}`} title="No node with this name / Prometheus ID / mgmt IP">{i} <span className="text-surface-500">(unknown device)</span></div>
                  ))}
                  {actual!.unmatched_interfaces.map(u => (
                    <div key={`f-${u.instance}-${u.interface}`} title="Device matched, but no link uses this interface">
                      {u.instance} {u.interface} <span className="text-surface-500">(no link)</span>
                    </div>
                  ))}
                  {actual!.unmatched_interface_count > actual!.unmatched_interfaces.length && (
                    <div className="text-surface-500">…and {actual!.unmatched_interface_count - actual!.unmatched_interfaces.length} more</div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="text-[10px] text-surface-500">
          No measured data. Import a CSV with columns <span className="font-mono">instance, interface, peak_30d_bps, peak_90d_bps, link_delay_us</span>.
        </div>
      )}
    </div>
  );
}

const SCHEMA_COLUMNS: [string, string, string][] = [
  ['instance', 'required', 'Device: node name, Prometheus ID or mgmt IP (":port" and domain suffix are stripped)'],
  ['interface', 'required', 'Link endpoint interface, e.g. Gi0/0/1 or GigabitEthernet0/0/1'],
  ['peak_30d_bps', 'number', '30-day peak in bits/sec (blank = no data)'],
  ['peak_90d_bps', 'number', '90-day peak in bits/sec (blank = no data)'],
  ['link_delay_us', 'optional', 'One-way link delay in microseconds; sets the link\'s latency. Blank or 0 keeps the existing latency'],
];

const SCHEMA_EXAMPLE = `instance,interface,peak_30d_bps,peak_90d_bps,link_delay_us
core-rtr1,Gi0/0/1,4200000000,5100000000,1250
10.0.0.2:9116,TenGigabitEthernet0/1/0,812000000,950000000,`;

/** Hover/focus popover describing the import CSV schema, with a template
 * download. Positioned against the (relative) section so it fits the panel. */
function CsvSchemaHint() {
  const downloadTemplate = () => {
    const url = URL.createObjectURL(new Blob([SCHEMA_EXAMPLE + '\n'], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'actual_utilization_template.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <span className="group">
      <button type="button" className="text-surface-500 hover:text-surface-300 focus:text-surface-300 outline-none" aria-label="CSV import format">
        <Info size={11} />
      </button>
      <div className="hidden group-hover:block group-focus-within:block absolute left-2 right-2 top-5 z-50 pt-1.5">
        <div className="bg-surface-900 border border-surface-700 rounded-md shadow-lg p-2.5 space-y-2 text-[10px] text-surface-300">
          <div className="font-medium text-surface-200">Import CSV format</div>
          <table className="w-full">
            <tbody>
              {SCHEMA_COLUMNS.map(([col, kind, desc]) => (
                <tr key={col} className="align-top">
                  <td className="font-mono text-primary-400 pr-2 py-0.5 whitespace-nowrap">{col}</td>
                  <td className="text-surface-400 py-0.5">
                    <span className="text-surface-500">{kind}</span> — {desc}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <pre className="font-mono text-[9px] bg-surface-800 rounded p-1.5 overflow-x-auto whitespace-pre">{SCHEMA_EXAMPLE}</pre>
          <div className="text-surface-500">
            Header row required; names are case-insensitive and extra columns are ignored. A link uses the higher
            of its two endpoints for each value. Importing replaces previous data and updates link latency
            (local links only).
          </div>
          <button type="button" className="btn btn-ghost btn-sm w-full text-[10px]" onClick={downloadTemplate}>
            <Download size={11} /> Download template
          </button>
        </div>
      </div>
    </span>
  );
}

function parseDetail(e: any): string | null {
  try {
    const body = e?.message?.split(': ').slice(1).join(': ');
    const parsed = body ? JSON.parse(body) : null;
    if (parsed?.detail && typeof parsed.detail === 'string') return parsed.detail;
  } catch { /* fall through */ }
  return null;
}

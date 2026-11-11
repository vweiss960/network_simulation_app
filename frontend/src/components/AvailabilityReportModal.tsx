/**
 * AvailabilityReportModal — shows the cached Monte Carlo availability report
 * for a single flow/demand: headline availability + downtime, distribution
 * statistics, and a histogram of per-iteration yearly downtime.
 *
 * The report is produced by POST /flows/{id}/analyze-availability (same
 * Monte Carlo engine as the Simulation panel) and cached on the flow.
 */
import { X, Activity, RefreshCw, AlertTriangle } from 'lucide-react';
import { AvailabilityReport } from '../types';

interface Props {
  report: AvailabilityReport | null;
  open: boolean;
  onClose: () => void;
  onRerun?: () => void;
  rerunning?: boolean;
}

// "Nines" notation — 99.99% -> "4 nines". A quick reliability shorthand.
function ninesOf(avail: number): string {
  if (avail >= 1) return '∞';
  const unavail = 1 - avail;
  if (unavail <= 0) return '∞';
  const nines = -Math.log10(unavail);
  return nines.toFixed(1);
}

function availColor(avail: number): string {
  if (avail > 0.9999) return 'text-green-400';
  if (avail > 0.999) return 'text-emerald-400';
  if (avail > 0.99) return 'text-amber-400';
  return 'text-red-400';
}

function fmtMin(min: number): string {
  if (min >= 60) return `${(min / 60).toFixed(1)} hr/yr`;
  return `${min.toFixed(1)} min/yr`;
}

export default function AvailabilityReportModal({ report, open, onClose, onRerun, rerunning }: Props) {
  if (!open || !report) return null;

  const hasError = !!report.error;
  const maxCount = report.downtime_histogram_min?.reduce((m, b) => Math.max(m, b.count), 0) || 1;
  const analyzedAt = report.analyzed_at ? new Date(report.analyzed_at).toLocaleString() : '';
  const exclNodes = report.excluded_nodes || [];
  const exclLinks = report.excluded_links || [];
  const hasMaintenance = exclNodes.length > 0 || exclLinks.length > 0;

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={onClose}>
      <div
        className="bg-surface-800 border border-surface-600 rounded-lg w-[760px] max-w-full max-h-[88vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700">
          <div className="flex items-center gap-2 min-w-0">
            <Activity size={15} className="text-primary-400 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">Availability report — {report.flow_name}</div>
              <div className="text-[10px] text-surface-500 truncate">
                {report.source_node_name} → {report.dest_node_name}
                {analyzedAt && <span className="ml-2">· {analyzedAt}</span>}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {onRerun && (
              <button className="btn btn-ghost btn-sm text-[11px]" onClick={onRerun} disabled={rerunning} title="Re-run the Monte Carlo analysis">
                <RefreshCw size={12} className={rerunning ? 'animate-spin' : ''} /> {rerunning ? 'Running…' : 'Re-run'}
              </button>
            )}
            <button className="btn btn-ghost btn-sm p-1" onClick={onClose}><X size={14} /></button>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
          {hasMaintenance && (
            <div className="flex items-start gap-2 text-[11px] bg-amber-900/20 border border-amber-700/40 rounded p-2">
              <AlertTriangle size={13} className="text-amber-400 mt-0.5 shrink-0" />
              <div>
                <div className="text-amber-300 font-medium">Analyzed with a maintenance window applied.</div>
                <div className="text-surface-400 mt-0.5">
                  Excluded:{' '}
                  {exclNodes.length > 0 && (
                    <span>{exclNodes.length} node{exclNodes.length === 1 ? '' : 's'} ({exclNodes.map(n => n.name).join(', ')})</span>
                  )}
                  {exclNodes.length > 0 && exclLinks.length > 0 && ', '}
                  {exclLinks.length > 0 && (
                    <span>{exclLinks.length} link{exclLinks.length === 1 ? '' : 's'}</span>
                  )}
                  . These numbers reflect the degraded network, not the full as-built topology.
                </div>
              </div>
            </div>
          )}
          {hasError ? (
            <div className="text-[12px] text-red-300 bg-red-900/20 border border-red-700/40 rounded p-3">
              {report.error}
            </div>
          ) : (
            <>
              {/* Headline */}
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-surface-900/60 border border-surface-700 rounded p-3 text-center">
                  <div className={`text-2xl font-bold ${availColor(report.availability)}`}>
                    {(report.availability * 100).toFixed(4)}%
                  </div>
                  <div className="text-[10px] text-surface-500 mt-0.5">Typical availability ({ninesOf(report.availability)} nines)</div>
                </div>
                <div className="bg-surface-900/60 border border-surface-700 rounded p-3 text-center">
                  <div className="text-2xl font-bold text-surface-100">{fmtMin(report.downtime_min_yr)}</div>
                  <div className="text-[10px] text-surface-500 mt-0.5">Anticipated downtime</div>
                </div>
                <div className="bg-surface-900/60 border border-surface-700 rounded p-3 text-center">
                  <div className="text-2xl font-bold text-surface-100">{report.disjoint_paths}</div>
                  <div className="text-[10px] text-surface-500 mt-0.5">Disjoint paths (healthy net)</div>
                </div>
              </div>

              {/* Statistical detail */}
              <div>
                <div className="text-[10px] uppercase tracking-wide text-surface-500 mb-1">Statistics</div>
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px]">
                  <Row label="Iterations" value={report.iterations.toLocaleString()} />
                  <Row label="Iterations with any outage" value={`${report.outage_iterations.toLocaleString()} (${((report.outage_iterations / report.iterations) * 100).toFixed(2)}%)`} />
                  <Row label="Perfect-year iterations" value={`${report.zero_downtime_iterations.toLocaleString()} (${((report.zero_downtime_iterations / report.iterations) * 100).toFixed(2)}%)`} />
                  <Row label="Availability σ" value={`${(report.std_dev * 100).toFixed(4)}%`} />
                  <Row label="Downtime σ" value={fmtMin(report.downtime_std_min_yr)} />
                  <Row label="Worst-case downtime" value={fmtMin(report.downtime_percentiles_min.max)} />
                </div>
              </div>

              {/* Downtime percentiles */}
              <div>
                <div className="text-[10px] uppercase tracking-wide text-surface-500 mb-1">Downtime percentiles (min/yr)</div>
                <div className="grid grid-cols-5 gap-2 text-center">
                  {([['p50', 'Median'], ['p90', 'p90'], ['p95', 'p95'], ['p99', 'p99'], ['max', 'Max']] as const).map(([k, lbl]) => (
                    <div key={k} className="bg-surface-900/60 border border-surface-700 rounded py-1.5">
                      <div className="text-[12px] font-semibold text-surface-100">{(report.downtime_percentiles_min as any)[k].toFixed(1)}</div>
                      <div className="text-[9px] text-surface-500">{lbl}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Histogram */}
              <div>
                <div className="text-[10px] uppercase tracking-wide text-surface-500 mb-1">
                  Downtime distribution (min/yr across {report.iterations.toLocaleString()} simulated years)
                </div>
                {report.downtime_histogram_min && report.downtime_histogram_min.length > 0 ? (
                  <div className="bg-surface-900/60 border border-surface-700 rounded p-3">
                    <div className="flex items-end gap-[2px] h-[140px]">
                      {report.downtime_histogram_min.map((b, i) => {
                        const h = maxCount > 0 ? (b.count / maxCount) * 100 : 0;
                        return (
                          <div
                            key={i}
                            className="flex-1 bg-primary-500/70 hover:bg-primary-400 rounded-t transition-colors"
                            style={{ height: `${Math.max(h, b.count > 0 ? 1.5 : 0)}%` }}
                            title={`${b.bin_start.toFixed(1)}–${b.bin_end.toFixed(1)} min/yr: ${b.count.toLocaleString()} iterations`}
                          />
                        );
                      })}
                    </div>
                    <div className="flex justify-between text-[9px] text-surface-500 mt-1">
                      <span>0 min/yr</span>
                      <span>{report.downtime_percentiles_min.max.toFixed(0)} min/yr</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-[11px] text-surface-500">No distribution data.</div>
                )}
                <div className="text-[9px] text-surface-500 mt-1">
                  Tall left-most bar = simulated years with little/no downtime. A long right tail means rare but severe outages.
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-surface-800 py-0.5">
      <span className="text-surface-500">{label}</span>
      <span className="text-surface-200 font-mono">{value}</span>
    </div>
  );
}

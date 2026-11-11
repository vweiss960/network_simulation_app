import type { ActualUtilizationResult, LinkData, LinkUtilization, UtilizationView } from '../types';

/** Peak bps for the chosen period (null when the link has no data). */
export function actualPeakBps(
  a: { peak_30d_bps: number | null; peak_90d_bps: number | null },
  view: UtilizationView,
): number | null {
  return view === 'actual_90d' ? a.peak_90d_bps : a.peak_30d_bps;
}

/**
 * Map link_id → LinkUtilization built from measured peaks, shaped like the
 * simulation's so the canvas colors/labels work unchanged. Uses the link's
 * current bandwidth (so an edited speed re-scales immediately). Links with no
 * measured data are omitted — "unknown" stays distinct from 0%.
 */
export function buildActualUtilMap(
  actual: ActualUtilizationResult | null,
  links: LinkData[],
  view: UtilizationView,
): Record<number, LinkUtilization> {
  const map: Record<number, LinkUtilization> = {};
  if (!actual || view === 'demand') return map;
  const bwById = new Map(links.map(l => [l.id, l.bandwidth_gbps]));
  for (const a of actual.links) {
    const bps = actualPeakBps(a, view);
    if (bps == null) continue;
    const bw = bwById.get(a.link_id) ?? a.bandwidth_gbps;
    const usedGbps = bps / 1e9;
    map[a.link_id] = {
      link_id: a.link_id,
      source_node_id: a.source_node_id,
      target_node_id: a.target_node_id,
      bandwidth_gbps: bw,
      used_gbps: Math.round(usedGbps * 1e4) / 1e4,
      utilization_pct: bw > 0 ? Math.round((usedGbps / bw) * 1000) / 10 : 0,
      flow_ids: [],
      cost_per_gb: 0,
    };
  }
  return map;
}

/** Human-readable rate, e.g. 1.25 Gbps / 830 Mbps. */
export function formatBps(bps: number | null | undefined): string {
  if (bps == null) return '-';
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbps`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`;
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(1)} Kbps`;
  return `${Math.round(bps)} bps`;
}

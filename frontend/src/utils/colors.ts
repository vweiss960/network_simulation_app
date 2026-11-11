import { ColorScheme, LinkColorConfig } from '../types';
import { effectiveLatency } from './latency';

const DEFAULT_LINK_COLORS: LinkColorConfig = {
  unused: '#3b82f6',
  low: '#14b8a6',
  mid: '#4ade80',
  high: '#eab308',
  critical: '#f97316',
  overloaded: '#ef4444',
};

export function getUtilizationColor(pct: number, colors?: LinkColorConfig): string {
  const c = colors || DEFAULT_LINK_COLORS;
  if (pct <= 0) return c.unused;
  if (pct < 25) return c.low;
  if (pct < 50) return c.mid;
  if (pct < 75) return c.high;
  if (pct < 90) return c.critical;
  return c.overloaded;
}

// Each scheme's color steps, shared by the link coloring below and the
// canvas legend meter so the two can't drift apart.
export const COST_STEPS: { max: number; color: string; label: string }[] = [
  { max: 0.01, color: '#fde047', label: '≤.01' },
  { max: 0.05, color: '#facc15', label: '≤.05' },
  { max: 0.1, color: '#f59e0b', label: '≤.10' },
  { max: 0.5, color: '#d946ef', label: '≤.50' },
  { max: Infinity, color: '#a855f7', label: '.50+' },
];
export function getCostColor(costPerGb: number): string {
  return COST_STEPS.find(s => costPerGb <= s.max)!.color;
}

// One-way link latency (ms). Unset (0) links get the slate "no data" color.
export const LATENCY_NONE_COLOR = '#475569';
export const LATENCY_STEPS: { max: number; color: string; label: string }[] = [
  { max: 5, color: '#14b8a6', label: '<5' },
  { max: 10, color: '#4ade80', label: '<10' },
  { max: 25, color: '#eab308', label: '<25' },
  { max: 50, color: '#f97316', label: '<50' },
  { max: Infinity, color: '#ef4444', label: '50+' },
];
export function getLatencyColor(latencyMs: number): string {
  if (!(latencyMs > 0)) return LATENCY_NONE_COLOR;
  return LATENCY_STEPS.find(s => latencyMs < s.max)!.color;
}

// Circuit cost relative to the topology's average circuit cost: cheap is
// yellow, around average purple, expensive deep purple; no cost light blue.
export const CIRCUIT_COST_NONE_COLOR = '#60a5fa';
export const CIRCUIT_COST_STEPS: { max: number; color: string; label: string }[] = [
  { max: 0.25, color: '#fde047', label: '<¼' },
  { max: 0.5, color: '#facc15', label: '<½' },
  { max: 0.75, color: '#f59e0b', label: '<¾' },
  { max: 1.25, color: '#d946ef', label: '~1' },
  { max: 2.0, color: '#c026d3', label: '<2' },
  { max: Infinity, color: '#a855f7', label: '2+' },
];
export function getCircuitCostColor(circuitCost: number, avgCost: number): string {
  if (circuitCost <= 0) return CIRCUIT_COST_NONE_COLOR;
  if (avgCost <= 0) return '#facc15';
  const ratio = circuitCost / avgCost;
  return CIRCUIT_COST_STEPS.find(s => ratio < s.max)!.color;
}

export function getCriticalityColor(affectedFlows: number, totalFlows: number): string {
  if (totalFlows === 0) return '#22c55e';
  const ratio = affectedFlows / totalFlows;
  if (ratio <= 0) return '#22c55e';
  if (ratio < 0.25) return '#84cc16';
  if (ratio < 0.5) return '#eab308';
  if (ratio < 0.75) return '#f97316';
  return '#ef4444';
}

export function getLinkColor(
  scheme: ColorScheme,
  util?: { utilization_pct: number; cost_per_gb: number; flow_ids?: number[] },
  totalFlows?: number,
  colors?: LinkColorConfig,
  linkData?: { circuit_cost?: number; bandwidth_gbps?: number; latency_ms?: number; latency_source?: string; latency_effective_ms?: number | null; latency_effective_source?: string },
  avgCircuitCost?: number,
): string {
  if (scheme === 'latency') return getLatencyColor((linkData && effectiveLatency(linkData)) || 0);
  if (scheme === 'circuit_cost') {
    const cc = linkData?.circuit_cost || 0;
    return getCircuitCostColor(cc, avgCircuitCost || 0);
  }
  if (!util) return colors?.unused || DEFAULT_LINK_COLORS.unused;
  switch (scheme) {
    case 'utilization':
      return getUtilizationColor(util.utilization_pct, colors);
    case 'cost_per_gb':
      return getCostColor(util.cost_per_gb);
    case 'criticality':
      return getCriticalityColor((util.flow_ids || []).length, totalFlows || 1);
    default:
      return getUtilizationColor(util.utilization_pct, colors);
  }
}

export function getLinkWidth(bandwidthGbps: number, byBandwidth: boolean = false): number {
  if (!byBandwidth) return 1;
  // Step up width with bandwidth tier so faster links look thicker.
  // <10G → 1, 10G → 2, 100G → 5, 400G → 6, 1T+ → 7
  if (bandwidthGbps >= 1000) return 7;
  if (bandwidthGbps >= 400) return 6;
  if (bandwidthGbps >= 100) return 5;
  if (bandwidthGbps >= 10) return 2;
  return 1;
}

// Layer colors for stacked sub-topology views. The Default view is neutral
// (no tint); each additional active view gets the next palette color in the
// order it appears in activeViewIds, so TopologyEditor (node rings) and
// ViewsPanel (legend/swatches) agree on which color is which view.
export const VIEW_LAYER_PALETTE = [
  '#22d3ee', '#f59e0b', '#a855f7', '#10b981', '#ef4444', '#3b82f6', '#ec4899', '#84cc16',
];

// The "default grey" — an unset color (empty) OR this explicit grey both mean
// "no manual color: defer to the active view's color, or the default styling."
// Matches the grey swatch at the end of the node/link color palettes.
export const VIEW_DEFER_COLOR = '#b1b1b7';

// True when a node/link carries a real user-chosen color (so it should win over
// a view's layer color). Empty or the defer-grey count as "not manual".
export function isManualColor(c?: string | null): boolean {
  return !!c && c !== VIEW_DEFER_COLOR;
}

// Resolve a STABLE color for every view: a stored color wins; uncolored views
// get a palette color assigned by view id (not list position) that skips colors
// already taken by other views. Because it keys off id, reordering the layer
// stack never changes a view's color, and two views never share one by accident.
export function resolveViewColors(views: Array<{ id: number; color?: string | null }>): Map<number, string> {
  const used = new Set<string>();
  views.forEach(v => { if (v.color && v.color.trim()) used.add(v.color); });
  let palIdx = 0;
  const nextColor = () => {
    while (palIdx < VIEW_LAYER_PALETTE.length && used.has(VIEW_LAYER_PALETTE[palIdx])) palIdx++;
    const c = VIEW_LAYER_PALETTE[palIdx % VIEW_LAYER_PALETTE.length];
    palIdx++; used.add(c);
    return c;
  };
  const m = new Map<number, string>();
  [...views].sort((a, b) => a.id - b.id).forEach(v => {
    m.set(v.id, (v.color && v.color.trim()) ? v.color : nextColor());
  });
  return m;
}

// A distinct color for the transient ad-hoc selection layer — the first palette
// color not already taken by a saved view.
export function adHocLayerColor(usedColors: Iterable<string>): string {
  const used = new Set(usedColors);
  return VIEW_LAYER_PALETTE.find(c => !used.has(c)) || VIEW_LAYER_PALETTE[VIEW_LAYER_PALETTE.length - 1];
}

// Inherited (parent-layer) items in a planning layer render "ghosted":
// translucent in their true color so local additions and the simulation
// overlay stand out. Planned inherited items stay a bit stronger.
export function inheritedGhostOpacity(isPlanned: boolean): number {
  return isPlanned ? 0.6 : 0.45;
}

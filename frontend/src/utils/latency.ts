// Display helpers for effective link latency (see backend services/latency.py):
// a measured value, else an estimate, else unknown.

type LatencyFields = {
  latency_ms?: number;
  latency_source?: string;
  latency_effective_ms?: number | null;
  latency_effective_source?: string;
  latency_method?: string;
};

/** Effective latency in ms, or null when unknown. */
export function effectiveLatency(l: LatencyFields): number | null {
  if (l.latency_effective_source) return l.latency_effective_ms ?? null;
  // Links not yet refreshed from the API: fall back to the stored value if known.
  return l.latency_source === 'measured' ? (l.latency_ms ?? null) : null;
}

const fmt = (ms: number) => (ms >= 10 ? ms.toFixed(1) : ms.toFixed(2)).replace(/\.?0+$/, '');

/** "12 ms", "~14.2 ms" (estimated) or "unknown"; optionally with the method. */
export function latencyLabel(l: LatencyFields, withMethod = false): string {
  const ms = effectiveLatency(l);
  if (ms == null) return 'unknown';
  if (l.latency_effective_source === 'estimated') {
    return `~${fmt(ms)} ms${withMethod && l.latency_method ? ` (estimated · ${l.latency_method})` : ''}`;
  }
  return `${fmt(ms)} ms`;
}

/** Value for the latency input: the known value, or blank (= estimate it). */
export function latencyFormValue(l: LatencyFields): string {
  return l.latency_source === 'measured' ? String(l.latency_ms ?? '') : '';
}

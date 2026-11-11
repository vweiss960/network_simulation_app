/**
 * Format bandwidth in Gbps to a human-readable string with smart units.
 * e.g. 0.001 -> "1 Mbps", 0.1 -> "100 Mbps", 0.5 -> "500 Mbps",
 *      1 -> "1 Gbps", 10 -> "10 Gbps", 100 -> "100 Gbps"
 */
export function formatBandwidth(gbps: number): string {
  if (gbps >= 1) {
    return gbps % 1 === 0 ? `${gbps}G` : `${gbps.toFixed(2)}G`;
  }
  const mbps = gbps * 1000;
  if (mbps >= 1) {
    return mbps % 1 === 0 ? `${mbps}M` : `${mbps.toFixed(1)}M`;
  }
  const kbps = gbps * 1000000;
  return kbps % 1 === 0 ? `${kbps}K` : `${kbps.toFixed(0)}K`;
}

/**
 * Convert a value + unit to Gbps.
 */
export function toGbps(value: number, unit: 'gbps' | 'mbps'): number {
  return unit === 'mbps' ? value / 1000 : value;
}

/**
 * Convert Gbps to a value + best unit for editing.
 */
export function fromGbps(gbps: number): { value: number; unit: 'gbps' | 'mbps' } {
  if (gbps >= 1) return { value: gbps, unit: 'gbps' };
  return { value: gbps * 1000, unit: 'mbps' };
}

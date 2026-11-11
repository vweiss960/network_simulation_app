// Satellite dishes / PoPs and the satellite links between them (see backend
// services/satellite.py). Satellite links are hidden on the canvases unless
// shown explicitly (selection, highlighted path, or the "Show satellite
// links" switch).

export const SAT_DISH = 'sat_dish';
export const SAT_POP = 'sat_pop';
export const ORBITS = ['leo', 'meo', 'geo'] as const;
export type Orbit = typeof ORBITS[number];

/** Orbit colors for drawn satellite links and badges. */
export const ORBIT_COLORS: Record<string, string> = { leo: '#22d3ee', meo: '#a78bfa', geo: '#fbbf24' };

export const isSatRole = (role?: string) => role === SAT_DISH || role === SAT_POP;
export const isSatelliteLink = (l: { transport?: string }) => l.transport === 'satellite';

/** "LEO · Starlink" */
export function satLinkLabel(l: { orbit?: string; carrier?: string }): string {
  const orbit = (l.orbit || '').toUpperCase();
  return l.carrier ? `${orbit} · ${l.carrier}` : orbit;
}

/** Whether a satellite link should be drawn right now: everything shown
 *  (switch or Path Builder), the link itself is in focus (highlighted path,
 *  selected, AI-highlighted, in maintenance), or its dish / PoP is selected. */
export function satLinkVisible(
  l: { id: number; source_node_id: number; target_node_id: number },
  opts: { showAll: boolean; selectedNodeIds: Set<number>; focusedLinkIds: Set<number> },
): boolean {
  return opts.showAll
    || opts.focusedLinkIds.has(l.id)
    || opts.selectedNodeIds.has(l.source_node_id)
    || opts.selectedNodeIds.has(l.target_node_id);
}

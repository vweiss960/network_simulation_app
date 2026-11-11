import { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { useStore } from '../stores/useStore';
import { isManualColor, inheritedGhostOpacity } from '../utils/colors';
import { Link2 } from 'lucide-react';

/** Hover button at a node's top-right (edit mode, logical canvas): click to
 *  start drawing a link from this node, then click the target node. */
export function LinkStartButton({ nodeId, onStart }: { nodeId: number; onStart: (id: number) => void }) {
  return (
    <button
      className="nodrag nopan absolute z-20 w-5 h-5 rounded-full bg-primary-600 hover:bg-primary-500 text-white flex items-center justify-center shadow-md opacity-0 group-hover:opacity-100 transition-opacity"
      style={{ top: -4, left: 'calc(50% + 14px)' }}
      onMouseDown={e => e.stopPropagation()}
      onClick={e => { e.stopPropagation(); onStart(nodeId); }}
      title="Create a link from this node, then click the target node"
    >
      <Link2 size={11} />
    </button>
  );
}

export function LabReadyBadge({ ready, uptime, mgmtIp, threshold }: { ready: string; uptime?: number; mgmtIp?: string; threshold?: number }) {
  // Lab mode runs against a light grey canvas, so the badge text uses darker
  // 700-level Tailwind shades for legibility.
  const palette: Record<string, { dot: string; text: string; label: string }> = {
    up: { dot: 'bg-green-600', text: 'text-green-800', label: 'UP' },
    booting: { dot: 'bg-amber-500 animate-pulse', text: 'text-amber-800', label: 'BOOTING' },
    starting: { dot: 'bg-amber-500 animate-pulse', text: 'text-amber-800', label: 'STARTING' },
    down: { dot: 'bg-red-600', text: 'text-red-800', label: 'DOWN' },
    unknown: { dot: 'bg-gray-500', text: 'text-gray-700', label: '?' },
    not_deployed: { dot: 'bg-gray-500', text: 'text-gray-700', label: 'NOT DEPLOYED' },
  };
  const p = palette[ready] || palette.unknown;
  let tooltip = `Lab state: ${ready}`;
  if (uptime != null) tooltip += `\nUptime: ${uptime}s`;
  if (threshold != null && ready === 'booting') tooltip += `\nReady when uptime ≥ ${threshold}s`;
  if (mgmtIp) tooltip += `\nMgmt IP: ${mgmtIp}`;
  return (
    <div className={`text-[7px] font-bold tracking-wide flex items-center gap-1 ${p.text}`} title={tooltip}>
      <span className={`w-1.5 h-1.5 rounded-full ${p.dot}`} />
      {p.label}
    </div>
  );
}

const PB_COLORS: Record<string, { fill: string; stroke: string; arrow: string; glow: string }> = {
  source: { fill: '#064e3b', stroke: '#10b981', arrow: '#6ee7b7', glow: 'rgba(16,185,129,0.6)' },
  dest:   { fill: '#4c1d95', stroke: '#8b5cf6', arrow: '#c4b5fd', glow: 'rgba(139,92,246,0.6)' },
  head:   { fill: '#0c4a6e', stroke: '#38bdf8', arrow: '#7dd3fc', glow: 'rgba(56,189,248,0.6)' },
  onpath: { fill: '#064e3b', stroke: '#10b981', arrow: '#6ee7b7', glow: 'rgba(16,185,129,0.4)' },
};

// L2 switches render as a rounded-rectangle with stacked bidirectional
// arrows (the standard switching symbol) instead of the router's cross-
// arrow circle. Detected from the model name — vIOS-L2, "Cat 9300 L2",
// or any template explicitly tagged "switch". Anything else stays a
// router. Word-bounded to avoid false matches like "iOS-XR2" or "XYL2".
const SWITCH_MODEL_RE = /\b(l2|switch)\b/i;

function RouterNode({ data, selected }: NodeProps) {
  const selectedNodeId = useStore(s => s.selectedNodeId);
  const isolatedNodeIds = useStore(s => s.isolatedNodeIds);
  const agentHighlightNodeIds = useStore(s => s.agentHighlightNodeIds);
  const isHighlighted = selectedNodeId === data.nodeId;
  const isIsolated = isolatedNodeIds.includes(data.nodeId);
  const isAgentHighlighted = agentHighlightNodeIds.includes(data.nodeId);
  const isMultiSelected = !!data.multiSelected;
  const isSwitch = SWITCH_MODEL_RE.test((data.model as string) || '');
  const isInMaintenance = !!data.inMaintenance;
  const isPlanned = !!data.isPlanned;
  const isInherited = !!data.isInherited;
  const isPlanningLocal = !!data.isPlanningLocal;
  const labMode = !!data.labMode;
  const labStatus: any = data.labStatus;
  const labReady: string = labStatus?.ready_state || (labMode ? 'not_deployed' : '');
  // Lab mode: node isn't in the chosen deploy subset → dim it on the canvas
  // so the user can see the full topology context but knows it won't be
  // brought up by the next Deploy.
  const labOutOfSubset = !!data.labOutOfSubset;
  // Sub-topology edit mode: this node isn't a member of the currently-active
  // view; ghost it so the user can see + right-click to add it.
  const outOfView = !!data.outOfView;
  // On the highlighted flow path but hidden by the active views / filters —
  // temporarily shown translucent so the path is complete (see TopologyEditor).
  const pathGhost = !!data.pathGhost;
  // Resting node color: a real user color wins; an empty or default-grey color
  // defers to the active sub-topology view's layer color (data.viewColor).
  const customColor: string = (data.nodeColor as string) || '';
  const viewColor: string = (data.viewColor as string) || '';
  const effectiveColor: string = isManualColor(customColor) ? customColor : viewColor;
  const active = selected || isHighlighted || isMultiSelected || isAgentHighlighted;
  const pbRole: string | undefined = data.pbRole;

  let fill: string, stroke: string, arrowColor: string, glow: string;

  if (isInMaintenance) {
    fill = '#7f1d1d';
    stroke = '#ef4444';
    arrowColor = '#fca5a5';
    glow = 'rgba(239,68,68,0.5)';
  } else if (isPlanned) {
    fill = '#1e1b4b';
    stroke = '#818cf8';
    arrowColor = '#a5b4fc';
    glow = 'rgba(129,140,248,0.4)';
  } else if (isIsolated) {
    fill = '#7f1d1d';
    stroke = '#ef4444';
    arrowColor = '#fca5a5';
    glow = 'rgba(239,68,68,0.7)';
  } else if (isAgentHighlighted) {
    // Super RRs get an amber tier-1 highlight; regular RRs keep the violet
    // tier-2 highlight. The split applies in both "Show Route Reflectors"
    // and any other agent highlight pass — anyone flagged super_rr stands
    // out visually even when mixed in with non-super RRs.
    if ((data as any).isSuperRR) {
      fill = '#78350f';
      stroke = '#f59e0b';
      arrowColor = '#fde68a';
      glow = 'rgba(245,158,11,0.65)';
    } else {
      fill = '#4c1d95';
      stroke = '#a855f7';
      arrowColor = '#c4b5fd';
      glow = 'rgba(168,85,247,0.6)';
    }
  } else if (pbRole && PB_COLORS[pbRole]) {
    const c = PB_COLORS[pbRole];
    fill = c.fill;
    stroke = c.stroke;
    arrowColor = c.arrow;
    glow = c.glow;
  } else if (active) {
    fill = '#1e3a8a';
    stroke = '#3b82f6';
    arrowColor = '#93c5fd';
    glow = 'rgba(59,130,246,0.6)';
  } else if (isPlanningLocal) {
    // Non-planned items added in a planning layer get a yellow tint so the
    // user's planning-layer additions pop against inherited items.
    fill = '#422006';
    stroke = '#eab308';
    arrowColor = '#fde047';
    glow = '';
  } else if (labMode) {
    // Lab mode: light grey node body with a softer mid-grey outline so the
    // node reads against the lighter canvas without feeling heavy.
    fill = '#d1d5db';
    stroke = '#6b7280';
    arrowColor = '#374151';
    glow = '';
  } else {
    fill = '#1e293b';
    stroke = '#475569';
    arrowColor = '#94a3b8';
    glow = '';
  }

  // User-set or view-layer color overrides the default stroke (but never
  // overrides maintenance/isolation/highlight-state cues).
  if (effectiveColor && !isInMaintenance && !isIsolated && !isAgentHighlighted && !pbRole && !active) {
    stroke = effectiveColor;
    // Derive a darker fill from the stroke for contrast
    try {
      const r = parseInt(effectiveColor.slice(1, 3), 16);
      const g = parseInt(effectiveColor.slice(3, 5), 16);
      const b = parseInt(effectiveColor.slice(5, 7), 16);
      fill = `rgb(${Math.round(r * 0.2)}, ${Math.round(g * 0.2)}, ${Math.round(b * 0.2)})`;
      arrowColor = `rgb(${Math.min(255, r + 60)}, ${Math.min(255, g + 60)}, ${Math.min(255, b + 60)})`;
    } catch { /* keep defaults */ }
  }

  const showGlow = active || !!pbRole || isIsolated || isInMaintenance || isPlanned;

  return (
    <div className={`relative group ${showGlow ? 'z-10' : ''} ${isPlanned ? 'planned-node' : ''}`}>
      {data.onStartLink && <LinkStartButton nodeId={data.nodeId} onStart={data.onStartLink} />}
      <Handle type="source" position={Position.Top} id="main"
        style={{ opacity: 0, width: 1, height: 1, pointerEvents: 'none', top: 22, left: '50%' }} />
      <Handle type="target" position={Position.Top} id="main-target"
        style={{ opacity: 0, width: 1, height: 1, pointerEvents: 'none', top: 22, left: '50%' }} />

      <div
        className="flex flex-col items-center transition-all"
        style={{
          ...(showGlow ? { filter: `drop-shadow(0 0 10px ${glow})` } : {}),
          ...(isPlanned ? { opacity: 0.7 } : {}),
          // Inherited nodes are ghosted (no halo) so local additions stand
          // out. Full strength whenever a state cue is showing (selected /
          // highlighted, path-builder role, isolated, in maintenance).
          ...(isInherited && !active && !pbRole && !isIsolated && !isInMaintenance ? { opacity: inheritedGhostOpacity(isPlanned) } : {}),
          ...(labOutOfSubset ? { opacity: 0.3, filter: 'grayscale(60%)' } : {}),
          ...(outOfView ? { opacity: 0.25, filter: 'grayscale(70%)' } : {}),
          ...(pathGhost ? { opacity: 0.4, filter: `grayscale(60%)${showGlow ? ` drop-shadow(0 0 10px ${glow})` : ''}` } : {}),
        }}
      >
        <svg width="44" height="44" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
          {isSwitch ? (
            // Cisco-style L2 switch glyph: tall rounded rectangle with four
            // stacked horizontal arrows alternating left / right / left /
            // right, each biased toward the side it points to. Filled
            // triangle arrowheads (vs chevrons) match the standard
            // networking-diagram switch symbol.
            <>
              <rect x="3" y="6" width="42" height="36" rx="3" fill={fill} stroke={stroke} strokeWidth="2"
                strokeDasharray={isPlanned ? '6 3' : undefined} />
              {/* Each arrow = a flat-ended shaft that stops at the triangle's
                  back edge + a filled triangle for the head. No overlap, no
                  rounded caps poking past the tip — gives clean isoceles
                  arrowheads instead of the smudged dot+chevron look. */}
              {/* Row 1: ←  biased left */}
              <line x1="30" y1="13" x2="16" y2="13" stroke={arrowColor} strokeWidth="2.5" />
              <polygon points="8,13 16,9 16,17" fill={arrowColor} />
              {/* Row 2: →  biased right */}
              <line x1="18" y1="21" x2="32" y2="21" stroke={arrowColor} strokeWidth="2.5" />
              <polygon points="40,21 32,17 32,25" fill={arrowColor} />
              {/* Row 3: ←  biased left */}
              <line x1="30" y1="29" x2="16" y2="29" stroke={arrowColor} strokeWidth="2.5" />
              <polygon points="8,29 16,25 16,33" fill={arrowColor} />
              {/* Row 4: →  biased right */}
              <line x1="18" y1="37" x2="32" y2="37" stroke={arrowColor} strokeWidth="2.5" />
              <polygon points="40,37 32,33 32,41" fill={arrowColor} />
            </>
          ) : (
            <>
              <circle cx="24" cy="24" r="20" fill={fill} stroke={stroke} strokeWidth="2"
                strokeDasharray={isPlanned ? '6 3' : undefined} />
              <line x1="24" y1="8" x2="24" y2="40" stroke={arrowColor} strokeWidth="1.5" />
              <line x1="8" y1="24" x2="40" y2="24" stroke={arrowColor} strokeWidth="1.5" />
              <polyline points="20,14 24,8 28,14" fill="none" stroke={arrowColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              <polyline points="20,34 24,40 28,34" fill="none" stroke={arrowColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              <polyline points="14,20 8,24 14,28" fill="none" stroke={arrowColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              <polyline points="34,20 40,24 34,28" fill="none" stroke={arrowColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </>
          )}
        </svg>

        <div className={`mt-1 text-[10px] font-semibold whitespace-nowrap ${
          labMode ? 'text-gray-900' : 'text-surface-300 bg-surface-900/80 px-1.5 py-0.5 rounded font-medium'
        }`}>
          {data.label}
        </div>
        {pathGhost && <div className="text-[7px] text-amber-400 font-bold tracking-wide">NOT IN VIEW</div>}
        {isInherited && <div className="text-[7px] text-surface-400 font-bold tracking-wide">INHERITED</div>}
        {isPlanned && <div className="text-[7px] text-indigo-400 font-bold tracking-wide">PLANNED</div>}
        {labMode && labReady && <LabReadyBadge ready={labReady} uptime={labStatus?.uptime_sec} mgmtIp={labStatus?.mgmt_ip} threshold={labStatus?.boot_threshold_sec} />}
        {pbRole === 'source' && <div className="text-[8px] text-emerald-400 font-bold">A-END</div>}
        {pbRole === 'dest' && <div className="text-[8px] text-violet-400 font-bold">Z-END</div>}
        {!pbRole && !isPlanned && data.vendor && (
          <div className={`text-[8px] whitespace-nowrap ${labMode ? 'text-gray-700' : 'text-surface-500'}`}>
            {data.vendor} {data.model}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(RouterNode);

import { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { useStore } from '../stores/useStore';
import { LabReadyBadge, LinkStartButton } from './RouterNode';
import { isManualColor, inheritedGhostOpacity } from '../utils/colors';

function CustomerNode({ data, selected }: NodeProps) {
  const selectedNodeId = useStore(s => s.selectedNodeId);
  const isolatedNodeIds = useStore(s => s.isolatedNodeIds);
  const agentHighlightNodeIds = useStore(s => s.agentHighlightNodeIds);
  const isHighlighted = selectedNodeId === data.nodeId;
  const isIsolated = isolatedNodeIds.includes(data.nodeId);
  const isAgentHighlighted = agentHighlightNodeIds.includes(data.nodeId);
  const isMultiSelected = !!data.multiSelected;
  const isInMaintenance = !!data.inMaintenance;
  const isPlanned = !!data.isPlanned;
  const isInherited = !!data.isInherited;
  const isPlanningLocal = !!data.isPlanningLocal;
  // On the highlighted flow path but hidden (customer toggled off / out of
  // view) — temporarily shown translucent so the path is complete.
  const pathGhost = !!data.pathGhost;
  const labMode = !!data.labMode;
  const labStatus: any = data.labStatus;
  const labReady: string = labStatus?.ready_state || (labMode ? 'not_deployed' : '');
  // User-set color wins. Planned items get yellow (set in isPlanned branch);
  // planning-local non-planned items fall back to an emerald tint.
  const customColor: string = (data.nodeColor as string) || '';
  const active = selected || isHighlighted || isMultiSelected || isAgentHighlighted;
  const pbRole: string | undefined = data.pbRole;
  // Resting color: a real user color wins; empty / default-grey defers to the
  // active sub-topology view's layer color.
  const viewColor: string = (data.viewColor as string) || '';
  const effectiveColor: string = isManualColor(customColor) ? customColor : viewColor;

  let fill: string, stroke: string, iconColor: string, glow: string;

  if (isInMaintenance) {
    fill = '#7f1d1d'; stroke = '#ef4444'; iconColor = '#fca5a5'; glow = 'rgba(239,68,68,0.5)';
  } else if (isPlanned) {
    fill = '#1e1b4b'; stroke = '#818cf8'; iconColor = '#a5b4fc'; glow = 'rgba(129,140,248,0.4)';
  } else if (isIsolated) {
    fill = '#7f1d1d'; stroke = '#ef4444'; iconColor = '#fca5a5'; glow = 'rgba(239,68,68,0.7)';
  } else if (isAgentHighlighted) {
    fill = '#4c1d95'; stroke = '#a855f7'; iconColor = '#c4b5fd'; glow = 'rgba(168,85,247,0.6)';
  } else if (pbRole === 'source') {
    fill = '#064e3b'; stroke = '#10b981'; iconColor = '#6ee7b7'; glow = 'rgba(16,185,129,0.6)';
  } else if (pbRole === 'dest') {
    fill = '#4c1d95'; stroke = '#8b5cf6'; iconColor = '#c4b5fd'; glow = 'rgba(139,92,246,0.6)';
  } else if (active) {
    fill = '#1e3a8a'; stroke = '#3b82f6'; iconColor = '#93c5fd'; glow = 'rgba(59,130,246,0.6)';
  } else if (data.customerColor) {
    const cc = data.customerColor;
    stroke = cc;
    // Derive a lighter version for the icon by parsing hex and blending toward white
    const r = parseInt(cc.slice(1,3), 16), g = parseInt(cc.slice(3,5), 16), b = parseInt(cc.slice(5,7), 16);
    iconColor = `rgb(${Math.min(255, r + 80)}, ${Math.min(255, g + 80)}, ${Math.min(255, b + 80)})`;
    fill = `rgb(${Math.round(r * 0.15)}, ${Math.round(g * 0.15)}, ${Math.round(b * 0.15)})`;
    glow = '';
  } else if (isPlanningLocal) {
    fill = '#422006'; stroke = '#eab308'; iconColor = '#fde047'; glow = '';
  } else if (labMode) {
    fill = '#d1d5db'; stroke = '#6b7280'; iconColor = '#374151'; glow = '';
  } else {
    fill = '#1a2332'; stroke = '#f59e0b'; iconColor = '#fbbf24'; glow = '';
  }

  // User-set or planning-default color overrides the default stroke
  if (effectiveColor && !isInMaintenance && !isIsolated && !isAgentHighlighted && !pbRole && !active) {
    stroke = effectiveColor;
    try {
      const r = parseInt(effectiveColor.slice(1, 3), 16);
      const g = parseInt(effectiveColor.slice(3, 5), 16);
      const b = parseInt(effectiveColor.slice(5, 7), 16);
      fill = `rgb(${Math.round(r * 0.2)}, ${Math.round(g * 0.2)}, ${Math.round(b * 0.2)})`;
      iconColor = `rgb(${Math.min(255, r + 60)}, ${Math.min(255, g + 60)}, ${Math.min(255, b + 60)})`;
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
          ...(pathGhost ? { opacity: 0.4, filter: `grayscale(60%)${showGlow ? ` drop-shadow(0 0 10px ${glow})` : ''}` } : {}),
        }}
      >
        <svg width="44" height="44" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
          {/* Diamond shape */}
          <polygon points="24,4 44,24 24,44 4,24" fill={fill} stroke={stroke} strokeWidth="2"
            strokeDasharray={isPlanned ? '6 3' : undefined} />
          {/* Person icon inside */}
          <circle cx="24" cy="18" r="4.5" fill="none" stroke={iconColor} strokeWidth="1.5" />
          <path d="M16 33 C16 27 20 24 24 24 C28 24 32 27 32 33" fill="none" stroke={iconColor} strokeWidth="1.5" strokeLinecap="round" />
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
        {!pbRole && !isPlanned && (
          <div className={`text-[8px] whitespace-nowrap ${labMode ? 'text-amber-800' : 'text-amber-500/70'}`}>CUSTOMER</div>
        )}
      </div>
    </div>
  );
}

export default memo(CustomerNode);

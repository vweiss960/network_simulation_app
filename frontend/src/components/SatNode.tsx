import { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { useStore } from '../stores/useStore';
import { LinkStartButton } from './RouterNode';
import { isManualColor, inheritedGhostOpacity } from '../utils/colors';
import { ORBIT_COLORS } from '../utils/satellite';

/** Satellite Dish ("satdish") and Satellite PoP ("satpop") nodes. Their
 *  satellite links are hidden by default, so a badge always says what each is
 *  connected to: a dish lists its PoP(s), a PoP counts its dishes. */
function SatNode({ data, selected, type }: NodeProps) {
  const selectedNodeId = useStore(s => s.selectedNodeId);
  const isolatedNodeIds = useStore(s => s.isolatedNodeIds);
  const agentHighlightNodeIds = useStore(s => s.agentHighlightNodeIds);
  const isPop = type === 'satpop';
  const isIsolated = isolatedNodeIds.includes(data.nodeId);
  const isAgentHighlighted = agentHighlightNodeIds.includes(data.nodeId);
  const isInMaintenance = !!data.inMaintenance;
  const isPlanned = !!data.isPlanned;
  const isInherited = !!data.isInherited;
  const pathGhost = !!data.pathGhost;
  const pbRole: string | undefined = data.pbRole;
  const active = selected || selectedNodeId === data.nodeId || !!data.multiSelected || isAgentHighlighted;
  const customColor: string = (data.nodeColor as string) || '';
  const effectiveColor = isManualColor(customColor) ? customColor : ((data.viewColor as string) || '');
  // Satellite summary: dish → [{orbit, carrier, peer}], PoP → dish count.
  const sat: { links?: { orbit: string; carrier: string; peer: string }[]; dishes?: number } = data.satSummary || {};

  let fill = '#0c1a2b', stroke = '#38bdf8', icon = '#7dd3fc', glow = '';
  if (isInMaintenance || isIsolated) { fill = '#7f1d1d'; stroke = '#ef4444'; icon = '#fca5a5'; glow = 'rgba(239,68,68,0.6)'; }
  else if (isPlanned) { fill = '#1e1b4b'; stroke = '#818cf8'; icon = '#a5b4fc'; glow = 'rgba(129,140,248,0.4)'; }
  else if (isAgentHighlighted) { fill = '#4c1d95'; stroke = '#a855f7'; icon = '#c4b5fd'; glow = 'rgba(168,85,247,0.6)'; }
  else if (pbRole === 'source') { fill = '#064e3b'; stroke = '#10b981'; icon = '#6ee7b7'; glow = 'rgba(16,185,129,0.6)'; }
  else if (pbRole === 'dest') { fill = '#4c1d95'; stroke = '#8b5cf6'; icon = '#c4b5fd'; glow = 'rgba(139,92,246,0.6)'; }
  else if (active) { fill = '#1e3a8a'; stroke = '#3b82f6'; icon = '#93c5fd'; glow = 'rgba(59,130,246,0.6)'; }
  else if (effectiveColor) { stroke = effectiveColor; }
  const showGlow = !!glow;

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
          ...(isInherited && !active && !pbRole && !isIsolated && !isInMaintenance ? { opacity: inheritedGhostOpacity(isPlanned) } : {}),
          ...(pathGhost ? { opacity: 0.4, filter: 'grayscale(60%)' } : {}),
        }}
      >
        <svg width="44" height="44" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
          {isPop ? (
            // PoP: rounded square with an uplink antenna mast.
            <>
              <rect x="6" y="6" width="36" height="36" rx="8" fill={fill} stroke={stroke} strokeWidth="2"
                strokeDasharray={isPlanned ? '6 3' : undefined} />
              <path d="M24 36 V20" stroke={icon} strokeWidth="1.8" strokeLinecap="round" />
              <path d="M18 36 L24 22 L30 36" stroke={icon} strokeWidth="1.4" strokeLinejoin="round" fill="none" />
              <path d="M17.5 15.5 A9 9 0 0 1 30.5 15.5" stroke={icon} strokeWidth="1.4" strokeLinecap="round" fill="none" />
              <path d="M20.5 18.5 A5 5 0 0 1 27.5 18.5" stroke={icon} strokeWidth="1.4" strokeLinecap="round" fill="none" />
              <circle cx="24" cy="20" r="1.6" fill={icon} />
            </>
          ) : (
            // Dish: circle with a parabolic dish aimed at the sky.
            <>
              <circle cx="24" cy="24" r="20" fill={fill} stroke={stroke} strokeWidth="2"
                strokeDasharray={isPlanned ? '6 3' : undefined} />
              <path d="M13 27 A13 13 0 0 0 31 15" stroke={icon} strokeWidth="1.8" strokeLinecap="round" fill="none" />
              <path d="M13 27 L31 15" stroke={icon} strokeWidth="1" strokeOpacity="0.6" />
              <path d="M22 21 L29 13" stroke={icon} strokeWidth="1.4" strokeLinecap="round" />
              <circle cx="30" cy="12" r="1.8" fill={icon} />
              <path d="M20 29 L18 35 H28" stroke={icon} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" fill="none" />
            </>
          )}
        </svg>
        <div className="mt-1 text-[10px] whitespace-nowrap text-surface-300 bg-surface-900/80 px-1.5 py-0.5 rounded font-medium">
          {data.label}
        </div>
        {pathGhost && <div className="text-[7px] text-amber-400 font-bold tracking-wide">NOT IN VIEW</div>}
        {isInherited && <div className="text-[7px] text-surface-400 font-bold tracking-wide">INHERITED</div>}
        {isPlanned && <div className="text-[7px] text-indigo-400 font-bold tracking-wide">PLANNED</div>}
        {pbRole === 'source' && <div className="text-[8px] text-emerald-400 font-bold">A-END</div>}
        {pbRole === 'dest' && <div className="text-[8px] text-violet-400 font-bold">Z-END</div>}
        {!pbRole && (isPop ? (
          <div className="text-[8px] whitespace-nowrap text-sky-300/80">🛰 {sat.dishes || 0} dish{sat.dishes === 1 ? '' : 'es'}</div>
        ) : sat.links && sat.links.length > 0 ? (
          sat.links.length === 1 ? (
            <div className="text-[8px] whitespace-nowrap" style={{ color: ORBIT_COLORS[sat.links[0].orbit] || '#7dd3fc' }}>
              🛰 {sat.links[0].orbit.toUpperCase()}{sat.links[0].carrier ? ` · ${sat.links[0].carrier}` : ''} → {sat.links[0].peer}
            </div>
          ) : (
            <div className="text-[8px] whitespace-nowrap text-sky-300/80">🛰 {sat.links.length} PoPs</div>
          )
        ) : (
          <div className="text-[8px] whitespace-nowrap text-surface-500">🛰 not connected</div>
        ))}
      </div>
    </div>
  );
}

export default memo(SatNode);

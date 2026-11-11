import { memo } from 'react';
import { EdgeProps, EdgeLabelRenderer } from 'reactflow';
import { latencyLabel } from '../utils/latency';
import { inheritedGhostOpacity } from '../utils/colors';

function getOffsetPath(
  sx: number, sy: number, tx: number, ty: number, offset: number
): { path: string; labelX: number; labelY: number } {
  if (offset === 0) {
    // Straight line (or very slight curve for aesthetics)
    const mx = (sx + tx) / 2;
    const my = (sy + ty) / 2;
    return { path: `M ${sx} ${sy} L ${tx} ${ty}`, labelX: mx, labelY: my };
  }

  // Compute perpendicular offset for the control point
  const dx = tx - sx;
  const dy = ty - sy;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  // Perpendicular unit vector
  const px = -dy / len;
  const py = dx / len;

  // Control point at midpoint + perpendicular offset
  const mx = (sx + tx) / 2 + px * offset;
  const my = (sy + ty) / 2 + py * offset;

  // Quadratic bezier
  const path = `M ${sx} ${sy} Q ${mx} ${my} ${tx} ${ty}`;

  // Label position at the curve midpoint (t=0.5 of quadratic bezier)
  const labelX = 0.25 * sx + 0.5 * mx + 0.25 * tx;
  const labelY = 0.25 * sy + 0.5 * my + 0.25 * ty;

  return { path, labelX, labelY };
}

function CustomEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  label,
  animated,
}: EdgeProps) {
  const color = data?.edgeColor || '#4b5563';
  const width = data?.width || 2;
  // Sub-topology edit mode: links touching a non-member node get heavily
  // dimmed so the in-view skeleton stays prominent while non-members ghost
  // around them for context + add-by-right-click.
  const outOfView = data?.outOfView === true;
  const opacity = outOfView ? 0.15 : (data?.opacity ?? 1);
  const isPending = data?.pending === true;
  const isSelected = data?.selected === true;
  const isPlanned = data?.isPlanned === true;
  const isInherited = data?.isInherited === true;
  const isPlanningLocal = data?.isPlanningLocal === true;
  const isAgentHighlighted = data?.isAgentHighlighted === true;
  const isMultiSelected = data?.multiSelected === true;
  const isOnBuiltPath = data?.onBuiltPath === true;
  const schemeColor = data?.schemeColor || color;
  // Color-by-utilization styling (pill labels, over-capacity glow).
  const utilStyle = data?.utilStyle === true;
  const utilPct: number = data?.utilPct ?? 0;
  const utilColor: string = data?.utilColor || color;
  const utilLabel = data?.utilLabel === true;
  // Inherited links are ghosted (translucent, true color, no halo) so
  // utilization coloring still reads through. Selected / multi-selected /
  // agent- or flow-highlighted and path-builder links render at full strength.
  const lineOpacity = isInherited && !isSelected && !animated && !isMultiSelected && !isAgentHighlighted && !isOnBuiltPath
    ? Math.min(opacity, inheritedGhostOpacity(isPlanned)) : opacity;
  // Satellite links draw as a dashed arc so they never read as fiber (the
  // arc bend is set in rfEdges).
  const isSatellite = data?.satellite === true;
  const curveOffset = data?.curveOffset ?? 0;
  const linkLabelFields: string[] = data?.linkLabelFields || [];
  const linkData: any = data?.linkData;

  const { path: edgePath, labelX, labelY } = getOffsetPath(sourceX, sourceY, targetX, targetY, curveOffset);

  // Compute positions near source and target for interface labels (30% from each end)
  const t = 0.30;
  const len = Math.sqrt((targetX - sourceX) ** 2 + (targetY - sourceY) ** 2) || 1;
  const px = -(targetY - sourceY) / len;
  const py = (targetX - sourceX) / len;
  const srcLabelX = sourceX + (targetX - sourceX) * t + px * curveOffset * t;
  const srcLabelY = sourceY + (targetY - sourceY) * t + py * curveOffset * t;
  const tgtLabelX = targetX + (sourceX - targetX) * t + px * curveOffset * t;
  const tgtLabelY = targetY + (sourceY - targetY) * t + py * curveOffset * t;

  if (isPending) {
    return (
      <>
        <path
          id={`${id}-hitarea`}
          d={edgePath}
          fill="none"
          stroke="transparent"
          strokeWidth={20}
          className="react-flow__edge-interaction"
        />
        {/* Glow */}
        <path d={edgePath} fill="none" stroke={color} strokeWidth={width + 4} opacity={0.2} strokeLinecap="round" />
        {/* Dashed line */}
        <path
          d={edgePath}
          fill="none"
          stroke={color}
          strokeWidth={width}
          opacity={opacity}
          strokeDasharray="8 6"
          strokeLinecap="round"
          className="phanero-edge-path"
        >
          <animate attributeName="stroke-dashoffset" from="14" to="0" dur="0.6s" repeatCount="indefinite" />
        </path>
        {label && (
          <EdgeLabelRenderer>
            <div
              style={{
                position: 'absolute',
                transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
                pointerEvents: 'none',
              }}
              className="nodrag nopan"
            >
              <div style={{
                background: '#78350f', color: '#fbbf24',
                padding: '2px 8px', borderRadius: '10px',
                fontSize: '10px', fontWeight: 600,
                border: '1px solid #f59e0b',
              }}>
                {label}
              </div>
            </div>
          </EdgeLabelRenderer>
        )}
      </>
    );
  }

  return (
    <>
      <path
        id={`${id}-hitarea`}
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        className="react-flow__edge-interaction"
      />
      {/* Selected glow — matches node selection blue glow */}
      {isSelected && (
        <>
          <path d={edgePath} fill="none" stroke="#3b82f6" strokeWidth={width + 12} opacity={0.12} strokeLinecap="round" />
          <path d={edgePath} fill="none" stroke="#3b82f6" strokeWidth={width + 8} opacity={0.2} strokeLinecap="round" />
          <path d={edgePath} fill="none" stroke="#3b82f6" strokeWidth={width + 4} opacity={0.35} strokeLinecap="round" />
        </>
      )}
      {/* Multi-select glow — sky blue */}
      {isMultiSelected && !isSelected && (
        <>
          <path d={edgePath} fill="none" stroke="#38bdf8" strokeWidth={width + 10} opacity={0.12} strokeLinecap="round" />
          <path d={edgePath} fill="none" stroke="#38bdf8" strokeWidth={width + 6} opacity={0.25} strokeLinecap="round" />
        </>
      )}
      {/* Agent highlight glow — purple */}
      {isAgentHighlighted && !isSelected && (
        <>
          <path d={edgePath} fill="none" stroke="#a855f7" strokeWidth={width + 12} opacity={0.15} strokeLinecap="round" />
          <path d={edgePath} fill="none" stroke="#a855f7" strokeWidth={width + 8} opacity={0.25} strokeLinecap="round" />
          <path d={edgePath} fill="none" stroke="#a855f7" strokeWidth={width + 4} opacity={0.4} strokeLinecap="round" />
        </>
      )}
      {/* Over-capacity glow (color-by-utilization only). Shown only while the
          line itself is drawn in its utilization color at full strength, so it
          never contradicts a maintenance / highlight / selection / ghost state. */}
      {utilStyle && utilPct >= 100 && color === utilColor && lineOpacity >= 1 && (
        <path d={edgePath} fill="none" stroke={utilColor} strokeWidth={width + 7} opacity={0.25} strokeLinecap="round" />
      )}
      {/* Main edge (ghosting via lineOpacity). The dash pattern is reserved
          for planned links so inherited planned links stay identifiable. */}
      <path
        id={id}
        d={edgePath}
        fill="none"
        stroke={color}
        strokeWidth={isSelected ? width + 1 : width}
        opacity={lineOpacity}
        className="phanero-edge-path"
        strokeLinecap="round"
        strokeDasharray={isSatellite ? '2 5' : isPlanned ? '8 4' : undefined}
      />
      {/* Animated dash overlay */}
      {animated && (
        <path
          d={edgePath}
          fill="none"
          stroke={color}
          strokeWidth={width}
          opacity={opacity}
          strokeDasharray="5 5"
          className="phanero-edge-path"
          strokeLinecap="round"
        >
          <animate attributeName="stroke-dashoffset" from="10" to="0" dur="0.5s" repeatCount="indefinite" />
        </path>
      )}
      {/* Label */}
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'all',
            }}
            className="nodrag nopan"
          >
            {utilLabel ? (
              // Utilization label: same shape as the plain label, tinted in
              // the link color; solid red at 90%+. Fades with the line.
              <div style={{
                opacity: lineOpacity,
                padding: '2px 6px', borderRadius: '4px',
                fontSize: '11px', fontWeight: 600, border: `1px solid ${utilColor}`,
                ...(utilPct >= 90
                  ? { background: utilColor, color: '#1a0505' }
                  : { background: `color-mix(in srgb, ${utilColor} 15%, #0b1220)`, color: utilColor }),
              }}>
                {label}
              </div>
            ) : (
              <div style={{
                background: '#0f172a', color: '#e2e8f0',
                padding: '2px 6px', borderRadius: '4px',
                fontSize: '11px', fontWeight: 600,
                border: `1px ${data?.labelDashed ? 'dashed' : 'solid'} ${schemeColor}`, opacity: 0.95,
              }}>
                {label}
              </div>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
      {/* Attribute labels near source and target */}
      {linkData && linkLabelFields.length > 0 && (
        <EdgeLabelRenderer>
          {(() => {
            const srcLines: string[] = [];
            const tgtLines: string[] = [];
            for (const field of linkLabelFields) {
              if (field === 'interface') {
                if (linkData.source_interface) srcLines.push(linkData.source_interface);
                if (linkData.target_interface) tgtLines.push(linkData.target_interface);
              } else if (field === 'ip') {
                if (linkData.source_ip) srcLines.push(linkData.source_ip);
                if (linkData.target_ip) tgtLines.push(linkData.target_ip);
              } else if (field === 'bandwidth') {
                srcLines.push(linkData.bandwidth_gbps + 'G');
                tgtLines.push(linkData.bandwidth_gbps + 'G');
              } else if (field === 'cost') {
                srcLines.push('cost:' + linkData.cost);
                tgtLines.push('cost:' + linkData.cost);
              } else if (field === 'latency') {
                // Effective latency: "12 ms", "~14.2 ms" (estimated) or "unknown".
                srcLines.push(latencyLabel(linkData));
                tgtLines.push(latencyLabel(linkData));
              } else if (field === 'carrier') {
                if (linkData.carrier) { srcLines.push(linkData.carrier); tgtLines.push(linkData.carrier); }
              } else if (field === 'circuit_cost') {
                if (linkData.circuit_cost) { const cc = '$' + linkData.circuit_cost.toLocaleString(); srcLines.push(cc); tgtLines.push(cc); }
              } else if (field === 'circuit_id') {
                if (linkData.circuit_id) { srcLines.push(linkData.circuit_id); tgtLines.push(linkData.circuit_id); }
              } else if (field === 'link_id_label') {
                if (linkData.link_id_label) { srcLines.push(linkData.link_id_label); tgtLines.push(linkData.link_id_label); }
              }
            }
            return (
              <>
                {srcLines.length > 0 && (
                  <div
                    style={{
                      position: 'absolute',
                      transform: `translate(-50%, -50%) translate(${srcLabelX}px,${srcLabelY}px)`,
                      pointerEvents: 'none',
                    }}
                    className="nodrag nopan"
                  >
                    <div style={{
                      background: '#1e293b', color: '#94a3b8',
                      padding: '1px 4px', borderRadius: '3px',
                      fontSize: '8px', fontWeight: 500,
                      whiteSpace: 'nowrap', opacity: 0.9,
                      lineHeight: '1.3',
                    }}>
                      {srcLines.map((line, i) => <div key={i}>{line}</div>)}
                    </div>
                  </div>
                )}
                {tgtLines.length > 0 && (
                  <div
                    style={{
                      position: 'absolute',
                      transform: `translate(-50%, -50%) translate(${tgtLabelX}px,${tgtLabelY}px)`,
                      pointerEvents: 'none',
                    }}
                    className="nodrag nopan"
                  >
                    <div style={{
                      background: '#1e293b', color: '#94a3b8',
                      padding: '1px 4px', borderRadius: '3px',
                      fontSize: '8px', fontWeight: 500,
                      whiteSpace: 'nowrap', opacity: 0.9,
                      lineHeight: '1.3',
                    }}>
                      {tgtLines.map((line, i) => <div key={i}>{line}</div>)}
                    </div>
                  </div>
                )}
              </>
            );
          })()}
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export default memo(CustomEdge);

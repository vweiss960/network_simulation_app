import { memo } from 'react';
import { EdgeLabelRenderer, useStore, type EdgeProps, type ReactFlowState } from 'reactflow';

export interface CableEdgeData {
  sourceIf: string;
  targetIf: string;
  impaired?: boolean;
}

const ICON_CENTER_Y = 28; // half of the 56px device icon

/** Short interface label: GigabitEthernet0/0/0/1 → Gi0/0/0/1, Ethernet3 → Et3. */
export function shortIf(name: string): string {
  const m = name.match(/^([A-Za-z]+)(.*)$/);
  if (!m) return name;
  const [, word, rest] = m;
  if (/^(eth|e)$/i.test(word) || word.length <= 3) return name;
  return word.slice(0, 2) + rest;
}

/** Straight cable between device-icon centres, with interface names near each end. */
function CableEdge({ id, source, target, data, selected }: EdgeProps<CableEdgeData>) {
  const sn = useStore((s: ReactFlowState) => s.nodeInternals.get(source));
  const tn = useStore((s: ReactFlowState) => s.nodeInternals.get(target));
  if (!sn || !tn) return null;
  const cx = (n: typeof sn) => (n.positionAbsolute?.x ?? n.position.x) + (n.width ?? 56) / 2;
  const cy = (n: typeof sn) => (n.positionAbsolute?.y ?? n.position.y) + ICON_CENTER_Y;
  const [x1, y1, x2, y2] = [cx(sn), cy(sn), cx(tn), cy(tn)];
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  const off = Math.min(52, len / 3);
  const lx1 = x1 + ((x2 - x1) / len) * off, ly1 = y1 + ((y2 - y1) / len) * off;
  const lx2 = x2 - ((x2 - x1) / len) * off, ly2 = y2 - ((y2 - y1) / len) * off;
  const stroke = selected ? '#60a5fa' : data?.impaired ? '#f59e0b' : '#94a3b8';
  return (
    <>
      <path id={id} d={`M${x1},${y1} L${x2},${y2}`} stroke="transparent" strokeWidth={14} fill="none"
        className="react-flow__edge-interaction" />
      <path d={`M${x1},${y1} L${x2},${y2}`} stroke={stroke} strokeWidth={selected ? 3 : 2} fill="none"
        strokeDasharray={data?.impaired ? '6 4' : undefined} />
      <EdgeLabelRenderer>
        {[[lx1, ly1, data?.sourceIf], [lx2, ly2, data?.targetIf]].map(([x, y, label], i) => (
          <div key={i}
            style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}
            className="absolute pointer-events-none px-1 rounded bg-surface-900/90 text-[9px] font-mono text-surface-300 border border-surface-700">
            {shortIf(String(label ?? ''))}
          </div>
        ))}
      </EdgeLabelRenderer>
    </>
  );
}

export default memo(CableEdge);

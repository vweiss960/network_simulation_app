import { memo } from 'react';
import { EdgeLabelRenderer, useStore, type EdgeProps, type ReactFlowState } from 'reactflow';
import { SYMBOL_SIZE } from './DeviceNode';

export interface CableEdgeData {
  sourceIf: string;
  targetIf: string;
  impaired?: boolean;
  mgmt?: boolean;          // management attachment (dashed, not editable)
  inactive?: boolean;      // an end isn't deployed yet (dashed)
  capturing?: boolean;     // a packet capture is running on it (red marker mid-cable)
}

const ICON_CENTER_Y = SYMBOL_SIZE / 2;

/** Short interface label: GigabitEthernet0/0/0/1 → Gi0/0/0/1, Ethernet3 → Et3. */
export function shortIf(name: string): string {
  const m = name.match(/^([A-Za-z]+)(.*)$/);
  if (!m) return name;
  const [, word, rest] = m;
  if (/^(eth|e|port)$/i.test(word) || word.length <= 3) return name;
  return word.slice(0, 2) + rest;
}

/** Straight cable between device-symbol centres, with interface names near each end. */
function CableEdge({ id, source, target, data, selected }: EdgeProps<CableEdgeData>) {
  const sn = useStore((s: ReactFlowState) => s.nodeInternals.get(source));
  const tn = useStore((s: ReactFlowState) => s.nodeInternals.get(target));
  if (!sn || !tn) return null;
  const cx = (n: typeof sn) => (n.positionAbsolute?.x ?? n.position.x) + (n.width ?? SYMBOL_SIZE) / 2;
  const cy = (n: typeof sn) => (n.positionAbsolute?.y ?? n.position.y) + ICON_CENTER_Y;
  const [x1, y1, x2, y2] = [cx(sn), cy(sn), cx(tn), cy(tn)];
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  const ux = (x2 - x1) / len, uy = (y2 - y1) / len;
  // Chip distance from a device's centre: further when the cable leaves
  // downward, where the device's name / kind / IP labels are.
  const offFor = (down: number) => Math.min(down > 0.45 ? 96 : 60, len / 2 - 14);
  const o1 = offFor(uy), o2 = offFor(-uy);
  const lx1 = x1 + ux * o1, ly1 = y1 + uy * o1;
  const lx2 = x2 - ux * o2, ly2 = y2 - uy * o2;
  const stroke = selected ? '#2563eb' : data?.mgmt ? '#a3adbd' : data?.impaired ? '#d97706'
    : data?.inactive ? '#94a3b8' : '#64748b';
  const dash = data?.mgmt ? '3 5' : data?.impaired ? '7 4' : data?.inactive ? '6 5' : undefined;
  return (
    <>
      <path id={id} d={`M${x1},${y1} L${x2},${y2}`} stroke="transparent" strokeWidth={14} fill="none"
        className="react-flow__edge-interaction" />
      <path d={`M${x1},${y1} L${x2},${y2}`} stroke={stroke} strokeWidth={data?.mgmt ? 1.25 : selected ? 3 : 2}
        fill="none" strokeDasharray={dash} />
      <EdgeLabelRenderer>
        {(data?.mgmt ? [[lx1, ly1, data?.sourceIf]] : [[lx1, ly1, data?.sourceIf], [lx2, ly2, data?.targetIf]]).map(([x, y, label], i) => (
          <div key={i}
            style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}
            className={`absolute pointer-events-none px-1.5 py-px rounded-md bg-white text-[10px] font-mono shadow-sm border
              ${selected ? 'border-blue-300 text-blue-700' : 'border-slate-300 text-slate-700'}`}>
            {shortIf(String(label ?? ''))}
          </div>
        ))}
        {data?.capturing && (
          <div title="Packet capture running"
            style={{ transform: `translate(-50%, -50%) translate(${(x1 + x2) / 2}px, ${(y1 + y2) / 2}px)` }}
            className="absolute pointer-events-none flex items-center gap-1 px-1.5 py-px rounded-full bg-white border border-red-300 shadow-sm text-[9px] font-semibold text-red-600">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />REC
          </div>
        )}
      </EdgeLabelRenderer>
    </>
  );
}

export default memo(CableEdge);

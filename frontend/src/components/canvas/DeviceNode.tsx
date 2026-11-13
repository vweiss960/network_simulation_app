import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { deviceIcon, SYMBOL_SHAPE } from './icons';
import type { ReadyState } from '../../types';

export interface DeviceNodeData {
  name: string;
  icon: string;
  kindLabel: string;
  mgmtIp: string;
  ready: ReadyState;
  noImage: boolean;
  deployed: boolean;
  networkLabel?: string;   // Network nodes: "eno2 · VLAN 120" / "Lab switch"
  pending?: boolean;       // changes saved for the next restart
  off?: boolean;           // powered off (started one at a time)
}

/** Status dot (top-right of the symbol) per runtime state; none when absent. */
const DOT: Partial<Record<ReadyState, string>> = {
  up: 'bg-emerald-500',
  booting: 'bg-amber-400 animate-pulse',
  starting: 'bg-sky-400 animate-pulse',
  down: 'bg-red-500',
  unknown: 'bg-slate-400',
};
const DOT_TITLE: Record<ReadyState, string> = {
  absent: 'Not deployed', booting: 'Booting', starting: 'Starting', up: 'Up', down: 'Stopped', unknown: 'Unknown',
};

export const SYMBOL_SIZE = 56;

function DeviceNode({ data, selected }: NodeProps<DeviceNodeData>) {
  const Symbol = deviceIcon(data.icon);
  const isNetwork = data.networkLabel !== undefined;
  // In a deployed lab, a device without a container is "not deployed" yet.
  const absent = data.deployed && data.ready === 'absent' && !isNetwork;
  const ghost = absent || data.noImage;
  const shape = SYMBOL_SHAPE[data.icon] === 'circle' ? 'rounded-full' : 'rounded-xl';
  return (
    <div className="group flex flex-col items-center select-none" title={`${data.name} — ${data.kindLabel}`}>
      <div className={`relative flex items-center justify-center ${shape}
          ${selected ? 'ring-2 ring-blue-500 ring-offset-4 ring-offset-[#f4f6f9]' : ''}`}
        style={{ width: SYMBOL_SIZE, height: SYMBOL_SIZE }}>
        <Symbol size={SYMBOL_SIZE} fill="#ffffff" dashed={ghost}
          className={ghost ? 'text-slate-400' : 'text-slate-700'} />
        {data.deployed && !isNetwork && DOT[data.ready] && (
          <span title={DOT_TITLE[data.ready]}
            className={`absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full border-2 border-white ${DOT[data.ready]}`} />
        )}
        {/* One loose handle covering the device: drag from it onto another device to cable them. */}
        <Handle type="source" position={Position.Right} id="h"
          className="!w-3 !h-3 !bg-blue-500 !border-2 !border-white !-right-1.5 opacity-0 group-hover:opacity-100 plabs-handle" />
      </div>
      <div className="plabs-halo mt-1.5 text-[13px] font-semibold text-slate-800 leading-tight">{data.name}</div>
      <div className="plabs-halo text-[11px] text-slate-500 leading-tight">{data.networkLabel ?? data.kindLabel}</div>
      {data.deployed && !isNetwork && !absent && data.mgmtIp && (
        <div className="plabs-halo text-[11px] font-mono leading-tight text-slate-600 mt-0.5">{data.mgmtIp}</div>
      )}
      {absent && <Chip className="bg-slate-200 text-slate-600">{data.off ? 'Off' : 'Not deployed'}</Chip>}
      {data.pending && <Chip className="bg-amber-100 text-amber-800" title="Changes saved for the next restart">Restart pending</Chip>}
      {data.noImage && <Chip className="bg-amber-100 text-amber-800">{isNetwork ? 'Not connected' : 'No image'}</Chip>}
    </div>
  );
}

function Chip({ children, className, title }: { children: React.ReactNode; className: string; title?: string }) {
  return <div title={title} className={`mt-1 px-1.5 py-px rounded text-[10px] font-medium leading-tight ${className}`}>{children}</div>;
}

export default memo(DeviceNode);

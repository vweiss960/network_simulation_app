import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { deviceIcon } from './icons';
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
}

const RING: Record<ReadyState, string> = {
  absent: 'ring-surface-600',
  booting: 'ring-amber-400 animate-pulse',
  starting: 'ring-sky-400 animate-pulse',
  up: 'ring-emerald-400',
  down: 'ring-red-500',
  unknown: 'ring-surface-500',
};

const STATE_LABEL: Record<ReadyState, string> = {
  absent: 'not deployed', booting: 'booting', starting: 'starting', up: 'up', down: 'stopped', unknown: 'unknown',
};

function DeviceNode({ data, selected }: NodeProps<DeviceNodeData>) {
  const Icon = deviceIcon(data.icon);
  const ring = data.deployed ? RING[data.ready] : 'ring-surface-600';
  return (
    <div className="group flex flex-col items-center select-none" title={`${data.name} — ${data.kindLabel}`}>
      <div
        className={`relative w-14 h-14 rounded-xl bg-surface-800 ring-2 ${ring} flex items-center justify-center
          ${selected ? 'outline outline-2 outline-offset-4 outline-primary-400' : ''}
          ${data.noImage ? 'opacity-50' : ''}`}
      >
        <Icon size={28} className="text-surface-100" />
        {/* One loose handle covering the device: drag from it onto another device to cable them. */}
        <Handle type="source" position={Position.Right} id="h"
          className="!w-3 !h-3 !bg-primary-400 !border-0 !-right-1.5 opacity-0 group-hover:opacity-100 plabs-handle" />
      </div>
      <div className="mt-1 text-xs font-semibold text-surface-100 leading-tight">{data.name}</div>
      <div className="text-[10px] text-surface-400 leading-tight">{data.networkLabel ?? data.kindLabel}</div>
      {data.deployed && data.networkLabel === undefined && (
        <div className="text-[10px] font-mono leading-tight text-surface-300">
          {data.mgmtIp || STATE_LABEL[data.ready]}
        </div>
      )}
      {data.noImage && <div className="text-[10px] text-amber-400 leading-tight">{data.networkLabel !== undefined ? 'not connected' : 'no image'}</div>}
    </div>
  );
}

export default memo(DeviceNode);

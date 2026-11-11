import { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { useStore } from '../stores/useStore';

function LocationNode({ data, selected }: NodeProps) {
  const selectedNodeId = useStore(s => s.selectedNodeId);
  const isHighlighted = selectedNodeId === data.nodeId;
  const active = selected || isHighlighted || data.multiSelected;

  const count: number = data.count || 0;
  const fill = active ? '#1e3a5a' : '#1e293b';
  const stroke = active ? '#3b82f6' : '#475569';
  const glow = active ? 'rgba(59,130,246,0.5)' : '';

  return (
    <div className={`relative ${active ? 'z-10' : ''}`}>
      <Handle type="source" position={Position.Top} id="main"
        style={{ opacity: 0, width: 1, height: 1, pointerEvents: 'none', top: 22, left: '50%' }} />
      <Handle type="target" position={Position.Top} id="main-target"
        style={{ opacity: 0, width: 1, height: 1, pointerEvents: 'none', top: 22, left: '50%' }} />

      <div
        className="flex flex-col items-center transition-all"
        style={active ? { filter: `drop-shadow(0 0 12px ${glow})` } : undefined}
      >
        {/* Cloud/building icon for location */}
        <svg width="56" height="44" viewBox="0 0 56 44" fill="none" xmlns="http://www.w3.org/2000/svg">
          {/* Rounded rectangle representing a site/location */}
          <rect x="4" y="4" width="48" height="36" rx="8" fill={fill} stroke={stroke} strokeWidth="2" />
          {/* Building icon */}
          <rect x="16" y="14" width="8" height="16" fill={stroke} opacity="0.6" rx="1" />
          <rect x="26" y="10" width="8" height="20" fill={stroke} opacity="0.6" rx="1" />
          <rect x="36" y="16" width="6" height="14" fill={stroke} opacity="0.4" rx="1" />
          {/* Count badge */}
          <circle cx="46" cy="10" r="8" fill="#3b82f6" stroke="#1e293b" strokeWidth="1.5" />
          <text x="46" y="13" textAnchor="middle" fill="white" fontSize="9" fontWeight="bold">{count}</text>
        </svg>

        <div className="mt-1 text-[10px] font-medium text-surface-300 bg-surface-900/80 px-1.5 py-0.5 rounded whitespace-nowrap">
          {data.label}
        </div>
        <div className="text-[8px] text-surface-500">
          {count} device{count !== 1 ? 's' : ''}
        </div>
      </div>
    </div>
  );
}

export default memo(LocationNode);

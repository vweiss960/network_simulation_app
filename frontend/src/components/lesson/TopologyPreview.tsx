/**
 * Read-only picture of a lesson's starting topology: the canvas's own device
 * symbols, cables and annotations in a non-interactive React Flow.
 */
import { useMemo } from 'react';
import ReactFlow, { Background, BackgroundVariant, ConnectionMode, ReactFlowProvider, type Edge, type Node } from 'reactflow';
import 'reactflow/dist/style.css';
import DeviceNode, { type DeviceNodeData } from '../canvas/DeviceNode';
import CableEdge, { type CableEdgeData } from '../canvas/CableEdge';
import { GroupNode, NoteNode, TextNode } from '../canvas/AnnotationNodes';
import { useLabStore } from '../../stores/useLabStore';
import type { LessonTopology } from '../../types';

const nodeTypes = { device: DeviceNode, group: GroupNode, text: TextNode, note: NoteNode };
const edgeTypes = { cable: CableEdge };

export default function TopologyPreview({ topology, height = 340 }: { topology: LessonTopology; height?: number }) {
  const kinds = useLabStore(s => s.kinds);
  const { nodes, edges } = useMemo(() => {
    const ann: Node[] = topology.annotations.map(a => ({
      id: 'a:' + a.id, type: a.type, position: { x: a.x, y: a.y }, data: { ann: { ...a, locked: true } },
      draggable: false, selectable: false, zIndex: a.type === 'group' ? -1 : 0,
      ...(a.type !== 'text' ? { style: { width: a.w, height: a.h } } : {}),
    }));
    const dev: Node<DeviceNodeData>[] = topology.nodes.map(n => ({
      id: n.name, type: 'device', position: { x: n.position?.[0] ?? 0, y: n.position?.[1] ?? 0 },
      draggable: false, selectable: false,
      data: {
        name: n.name, icon: n.icon || (n.kind === 'bridge' ? 'switch' : 'router'),
        kindLabel: kinds[n.kind]?.label ?? n.kind, mgmtIp: '', ready: 'absent', noImage: false, deployed: false,
        networkLabel: n.kind === 'bridge' ? (n.network?.mode === 'internal' ? 'Lab switch' : n.network?.nic ?? 'Network') : undefined,
        off: n.enabled === false,
      },
    }));
    const e: Edge<CableEdgeData>[] = topology.links.map((l, i) => ({
      id: `l${i}`, type: 'cable', source: l.a, target: l.b, selectable: false,
      data: { sourceIf: l.a_interface, targetIf: l.b_interface },
    }));
    return { nodes: [...ann, ...dev], edges: e };
  }, [topology, kinds]);

  if (!topology.nodes.length) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-500"
        style={{ height }}>
        Blank canvas — the learner builds the topology.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-slate-200 bg-[#f6f7fa] overflow-hidden" style={{ height }}>
      <ReactFlowProvider>
        {/* Loose: a device's single handle serves both cable ends, as on the canvas. */}
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} connectionMode={ConnectionMode.Loose}
          fitView fitViewOptions={{ padding: 0.12, maxZoom: 1 }} proOptions={{ hideAttribution: true }}
          nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
          panOnDrag zoomOnScroll={false} zoomOnPinch preventScrolling={false}>
          <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} color="#cbd5e1" />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}

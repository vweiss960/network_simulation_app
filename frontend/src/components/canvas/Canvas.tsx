import { useCallback, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background, ConnectionMode, Controls, MiniMap, type Connection, type Edge, type Node,
  type NodeDragHandler, type ReactFlowInstance,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useLabStore } from '../../stores/useLabStore';
import DeviceNode, { type DeviceNodeData } from './DeviceNode';
import CableEdge, { type CableEdgeData } from './CableEdge';
import ContextMenu, { type MenuItem } from './ContextMenu';
import LinkNetemModal from '../LinkNetemModal';

const nodeTypes = { device: DeviceNode };
const edgeTypes = { cable: CableEdge };
export const TEMPLATE_DND = 'application/x-plabs-template';

type Menu = { x: number; y: number; items: MenuItem[] } | null;

export default function Canvas() {
  const {
    nodes, links, lab, kinds, selection, select, addNode, moveNodes, connect, deleteNode, deleteLink,
    openConsole, nodeAction, currentTopologyId,
  } = useLabStore();
  const rf = useRef<ReactFlowInstance | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<Menu>(null);
  const [netemLink, setNetemLink] = useState<number | null>(null);

  const deployed = !!lab && lab.status !== 'idle';

  const rfNodes: Node<DeviceNodeData>[] = useMemo(() => nodes.map(n => {
    const rt = lab?.nodes?.[String(n.id)];
    return {
      id: String(n.id),
      type: 'device',
      position: { x: n.x, y: n.y },
      selected: selection?.type === 'node' && selection.id === n.id,
      data: {
        name: n.name,
        icon: n.icon,
        kindLabel: kinds[n.kind]?.label ?? n.kind,
        mgmtIp: rt?.mgmt_ip || n.mgmt_ipv4,
        ready: rt?.ready_state ?? 'absent',
        noImage: !n.image_ref,
        deployed,
      },
    };
  }), [nodes, lab, kinds, selection, deployed]);

  const rfEdges: Edge<CableEdgeData>[] = useMemo(() => links.map(l => ({
    id: String(l.id),
    type: 'cable',
    source: String(l.source_node_id),
    target: String(l.target_node_id),
    selected: selection?.type === 'link' && selection.id === l.id,
    data: { sourceIf: l.source_interface, targetIf: l.target_interface },
  })), [links, selection]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes(TEMPLATE_DND)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    }
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    const raw = e.dataTransfer.getData(TEMPLATE_DND);
    if (!raw || !rf.current) return;
    e.preventDefault();
    const p = rf.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    addNode(Number(raw), Math.round(p.x - 28), Math.round(p.y - 28));
  }, [addNode]);

  const onNodeDragStop: NodeDragHandler = useCallback((_e, _n, dragged) => {
    const pos: Record<number, [number, number]> = {};
    for (const d of dragged) pos[Number(d.id)] = [Math.round(d.position.x), Math.round(d.position.y)];
    moveNodes(pos);
  }, [moveNodes]);

  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target && c.source !== c.target) connect(Number(c.source), Number(c.target));
  }, [connect]);

  const onNodeContextMenu = useCallback((e: React.MouseEvent, n: Node) => {
    e.preventDefault();
    const id = Number(n.id);
    const node = nodes.find(x => x.id === id);
    const rt = lab?.nodes?.[n.id];
    const live = rt && rt.state !== 'absent';
    const running = rt?.state === 'running';
    select({ type: 'node', id });
    setMenu({
      x: e.clientX, y: e.clientY, items: [
        { label: 'Open console', onClick: () => openConsole(id), disabled: !running },
        { label: 'Open shell (sh)', onClick: () => openConsole(id, 'console', 'sh'), disabled: !running || node?.kind === 'linux' },
        { label: 'View logs', onClick: () => openConsole(id, 'logs'), disabled: !live },
        { label: '', onClick: () => {}, divider: true },
        { label: 'Start', onClick: () => nodeAction(id, 'start'), disabled: !deployed || running },
        { label: 'Stop', onClick: () => nodeAction(id, 'stop'), disabled: !running },
        { label: 'Restart', onClick: () => nodeAction(id, 'restart'), disabled: !live },
        { label: '', onClick: () => {}, divider: true },
        { label: `Delete ${node?.name ?? 'device'}`, danger: true, onClick: () => {
          if (!deployed || confirm(`Delete ${node?.name}? Its container will be removed from the running lab.`)) deleteNode(id);
        } },
      ],
    });
  }, [nodes, lab, deployed, select, openConsole, nodeAction, deleteNode]);

  const onEdgeContextMenu = useCallback((e: React.MouseEvent, edge: Edge) => {
    e.preventDefault();
    const id = Number(edge.id);
    select({ type: 'link', id });
    setMenu({
      x: e.clientX, y: e.clientY, items: [
        { label: 'Impairments (delay / loss)…', onClick: () => setNetemLink(id), disabled: !deployed },
        { label: 'Delete link', danger: true, onClick: () => deleteLink(id) },
      ],
    });
  }, [deployed, select, deleteLink]);

  // Double-click → console. Detected from click timing: the first click
  // re-renders the node as selected, which makes browsers drop the native
  // dblclick event.
  const lastClick = useRef<{ id: string; t: number }>({ id: '', t: 0 });
  const onNodeClick = useCallback((_e: React.MouseEvent, n: Node) => {
    const now = Date.now();
    const dbl = lastClick.current.id === n.id && now - lastClick.current.t < 400;
    lastClick.current = { id: n.id, t: dbl ? 0 : now };
    select({ type: 'node', id: Number(n.id) });
    if (dbl && lab?.nodes?.[n.id]?.state === 'running') openConsole(Number(n.id));
  }, [lab, select, openConsole]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if ((e.key === 'Delete' || e.key === 'Backspace') && selection &&
        !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
      if (selection.type === 'node') {
        const n = nodes.find(x => x.id === selection.id);
        if (!deployed || confirm(`Delete ${n?.name}? Its container will be removed from the running lab.`)) deleteNode(selection.id);
      } else {
        deleteLink(selection.id);
      }
    }
  }, [selection, nodes, deployed, deleteNode, deleteLink]);

  const netemLabel = (() => {
    const l = links.find(x => x.id === netemLink);
    if (!l) return '';
    const name = (id: number) => nodes.find(n => n.id === id)?.name ?? '?';
    return `${name(l.source_node_id)} ${l.source_interface} ↔ ${name(l.target_node_id)} ${l.target_interface}`;
  })();

  return (
    <div ref={wrapper} className="w-full h-full" tabIndex={0} onKeyDown={onKeyDown}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onInit={(i) => { rf.current = i; }}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        connectionMode={ConnectionMode.Loose}
        connectionRadius={60}
        onNodeClick={onNodeClick}
        onEdgeClick={(_e, ed) => select({ type: 'link', id: Number(ed.id) })}
        onPaneClick={() => select(null)}
        onNodeContextMenu={onNodeContextMenu}
        onEdgeContextMenu={onEdgeContextMenu}
        deleteKeyCode={null}
        zoomOnDoubleClick={false}
        fitView
        fitViewOptions={{ maxZoom: 1, padding: 0.3 }}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} color="#1e293b" />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="!bg-surface-800" nodeColor="#475569" maskColor="rgba(15,23,42,0.7)" />
      </ReactFlow>
      {nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-center text-surface-400">
            <div className="text-lg font-medium text-surface-200">Drag devices from the palette</div>
            <div className="text-sm mt-1">Then drag from a device's blue dot onto another device to cable them.</div>
          </div>
        </div>
      )}
      {menu && <ContextMenu {...menu} onClose={() => setMenu(null)} />}
      {netemLink !== null && currentTopologyId && (
        <LinkNetemModal topologyId={currentTopologyId} linkId={netemLink} label={netemLabel} onClose={() => setNetemLink(null)} />
      )}
    </div>
  );
}

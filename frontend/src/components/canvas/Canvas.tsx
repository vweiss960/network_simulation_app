import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  applyNodeChanges, Background, BackgroundVariant, ConnectionMode, Controls, type Connection, type Edge,
  type Node, type NodeChange, type NodeDragHandler, type ReactFlowInstance,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useLabStore } from '../../stores/useLabStore';
import DeviceNode, { type DeviceNodeData } from './DeviceNode';
import CableEdge, { type CableEdgeData } from './CableEdge';
import ContextMenu, { type MenuItem } from './ContextMenu';
import LinkNetemModal from '../LinkNetemModal';
import { GroupNode, NoteNode, TextNode, type AnnotationNodeData } from './AnnotationNodes';
import CanvasToolbar from './CanvasToolbar';
import AskAIBar from './AskAIBar';
import AddDevicePicker from './AddDevicePicker';
import AIChatPanel from '../agent/AIChatPanel';
import type { Annotation } from '../../types';

const nodeTypes = { device: DeviceNode, group: GroupNode, text: TextNode, note: NoteNode };
const ANN = 'a:';                       // React Flow id prefix for annotations
const annId = (rfId: string) => (rfId.startsWith(ANN) ? rfId.slice(ANN.length) : null);
const edgeTypes = { cable: CableEdge };
export const TEMPLATE_DND = 'application/x-plabs-template';

type Menu = { x: number; y: number; items: MenuItem[] } | null;

export default function Canvas() {
  const {
    nodes, links, lab, kinds, selection, select, addNode, moveNodes, connect, deleteNode, deleteLink,
    openConsole, nodeAction, currentTopologyId, annotations, tool, setTool, addAnnotation, updateAnnotations,
    deleteAnnotation, topology, setCanvasReady, setEditingNode, openCapture, capturing,
  } = useLabStore();
  const capturedLinks = useMemo(() => new Set(Object.values(capturing)), [capturing]);
  const rf = useRef<ReactFlowInstance | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<Menu>(null);
  const [netemLink, setNetemLink] = useState<number | null>(null);
  // Device picker opened from the canvas menu: screen + flow position.
  const [picker, setPicker] = useState<{ x: number; y: number; fx: number; fy: number } | null>(null);

  const deployed = !!lab && lab.status !== 'idle';

  const annNodes: Node<AnnotationNodeData>[] = useMemo(() => annotations.map((a: Annotation) => ({
    id: ANN + a.id,
    type: a.type,
    position: { x: a.x, y: a.y },
    data: { ann: a },
    selected: selection?.type === 'annotation' && selection.id === a.id,
    draggable: !a.locked,
    // Group boxes sit under cables and devices; notes / text above cables.
    zIndex: a.type === 'group' ? -1 : 0,
    ...(a.type !== 'text' ? { style: { width: a.w, height: a.h } } : {}),
  })), [annotations, selection]);

  const deviceNodes: Node<DeviceNodeData>[] = useMemo(() => nodes.map(n => {
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
        pending: !!n.pending && Object.keys(n.pending).length > 0,
        off: n.enabled === false,
        mgmtIp: rt?.mgmt_ip || n.mgmt_ipv4,
        ready: rt?.ready_state ?? 'absent',
        noImage: n.kind === 'bridge' ? (n.network?.mode !== 'internal' && !n.network?.nic) : !n.image_ref,
        deployed,
        networkLabel: n.kind === 'bridge' ? n.network_label : undefined,
      },
    };
  }), [nodes, lab, kinds, selection, deployed]);

  // React Flow works on a local copy so a dragged group box can carry its
  // contents along; it's refreshed from the store whenever that changes,
  // keeping positions of nodes that are mid-drag.
  const computed = useMemo(() => [...annNodes, ...deviceNodes] as Node[], [annNodes, deviceNodes]);
  const [rfNodes, setRfNodes] = useState<Node[]>(computed);
  useEffect(() => {
    setRfNodes(prev => {
      const old = new Map(prev.map(n => [n.id, n]));
      return computed.map(n => {
        const o = old.get(n.id);
        return o?.dragging ? { ...n, position: o.position, dragging: true } : { ...n, width: o?.width, height: o?.height };
      });
    });
  }, [computed]);

  // Fit the view once, when a lab that already has content opens — not when
  // the first device is added to an empty lab (the view would jump).
  // The canvas stays invisible until then, so devices never show at the
  // default viewport and jump into place; it's revealed already fitted.
  const fitted = useRef<number | null>(null);
  const [reveal, setReveal] = useState(false);
  // Visibility and the reveal animation change in the SAME local render. (The
  // store's canvasReady re-renders separately, so keying visibility off it
  // showed the topology for a frame or two before the fade-in started.)
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!topology || topology.id !== currentTopologyId || fitted.current === topology.id) return;
    const show = () => {
      setShown(true);
      setReveal(true);
      setCanvasReady(true);
      setTimeout(() => setReveal(false), 900);
    };
    if (nodes.length === 0 && annotations.length === 0) { fitted.current = topology.id; show(); return; }
    if (rfNodes.length > 0 && rfNodes.every(n => n.width)) {
      fitted.current = topology.id;
      requestAnimationFrame(() => {
        rf.current?.fitView({ maxZoom: 1, padding: 0.3, duration: 0 });
        requestAnimationFrame(show);           // one frame for the new viewport to paint
      });
    }
  }, [topology, currentTopologyId, rfNodes, nodes.length, annotations.length, setCanvasReady]);

  // Group drag: members = devices / text / notes / smaller groups inside the box at drag start.
  const groupDrag = useRef<{ id: string; x: number; y: number; members: { id: string; x: number; y: number }[] } | null>(null);
  const onNodeDragStart: NodeDragHandler = useCallback((_e, node) => {
    const a = annotations.find(x => ANN + x.id === node.id);
    if (!a || a.type !== 'group') { groupDrag.current = null; return; }
    const inside = (n: Node) => {
      const w = n.width ?? 0, h = n.height ?? 0;
      const cx = n.position.x + w / 2, cy = n.position.y + (n.type === 'device' ? 28 : h / 2);
      return n.id !== node.id && cx > a.x && cx < a.x + a.w && cy > a.y && cy < a.y + a.h &&
        (n.type !== 'group' || (n.position.x >= a.x && n.position.y >= a.y &&
                                n.position.x + w <= a.x + a.w && n.position.y + h <= a.y + a.h));
    };
    groupDrag.current = {
      id: node.id, x: node.position.x, y: node.position.y,
      members: rfNodes.filter(n => inside(n) && n.draggable !== false).map(n => ({ id: n.id, x: n.position.x, y: n.position.y })),
    };
  }, [annotations, rfNodes]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const extra: NodeChange[] = [];
    const g = groupDrag.current;
    for (const c of changes) {
      if (g && c.type === 'position' && c.id === g.id && c.position) {
        const dx = c.position.x - g.x, dy = c.position.y - g.y;
        for (const m of g.members) {
          extra.push({ type: 'position', id: m.id, position: { x: m.x + dx, y: m.y + dy }, dragging: c.dragging });
        }
      }
    }
    // Selection is owned by the store (click handlers), not React Flow.
    setRfNodes(nds => applyNodeChanges([...changes, ...extra].filter(c => c.type !== 'select' && c.type !== 'remove'), nds));
  }, []);

  const mgmtNode = nodes.find(n => n.kind === 'bridge' && n.network?.mgmt);
  const rfEdges: Edge<CableEdgeData>[] = useMemo(() => {
    const cables: Edge<CableEdgeData>[] = links.map(l => ({
      id: String(l.id),
      type: 'cable',
      source: String(l.source_node_id),
      target: String(l.target_node_id),
      selected: selection?.type === 'link' && selection.id === l.id,
      data: {
        sourceIf: l.source_interface, targetIf: l.target_interface,
        capturing: capturedLinks.has(l.id),
        inactive: deployed && [l.source_node_id, l.target_node_id].some(id => {
          const n = nodes.find(x => x.id === id);
          return n && n.kind !== 'bridge' && (lab?.nodes?.[String(id)]?.ready_state ?? 'absent') === 'absent';
        }),
      },
    }));
    // Management attachments: every device's mgmt interface is on the lab's
    // management Network node (drawn dashed; not a cable you can edit).
    const mgmt: Edge<CableEdgeData>[] = mgmtNode ? nodes.filter(n => n.kind !== 'bridge').map(n => ({
      id: `mgmt-${n.id}`,
      type: 'cable',
      source: String(n.id),
      target: String(mgmtNode.id),
      selectable: false, focusable: false, deletable: false,
      data: { sourceIf: kinds[n.kind]?.mgmt_iface ?? 'mgmt', targetIf: 'mgmt', mgmt: true },
    })) : [];
    return [...mgmt, ...cables];
  }, [links, selection, nodes, mgmtNode, kinds, deployed, lab, capturedLinks]);

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
    const g = groupDrag.current;
    groupDrag.current = null;
    const moved = new Map<string, { x: number; y: number }>();
    for (const d of dragged) moved.set(d.id, d.position);
    if (g) {
      const gp = moved.get(g.id);
      if (gp) for (const m of g.members) moved.set(m.id, { x: m.x + gp.x - g.x, y: m.y + gp.y - g.y });
    }
    const pos: Record<number, [number, number]> = {};
    const ann: Record<string, Partial<Annotation>> = {};
    for (const [id, p] of moved) {
      const aid = annId(id);
      if (aid) ann[aid] = { x: Math.round(p.x), y: Math.round(p.y) };
      else pos[Number(id)] = [Math.round(p.x), Math.round(p.y)];
    }
    if (Object.keys(pos).length) moveNodes(pos);
    if (Object.keys(ann).length) updateAnnotations(ann);
  }, [moveNodes, updateAnnotations]);

  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target && c.source !== c.target && !annId(c.source) && !annId(c.target)) {
      connect(Number(c.source), Number(c.target));
    }
  }, [connect]);

  const onPaneClick = useCallback((e: React.MouseEvent) => {
    if (tool !== 'select' && rf.current) {
      const p = rf.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      addAnnotation(tool, Math.round(p.x), Math.round(p.y));
      return;
    }
    select(null);
  }, [tool, addAnnotation, select]);

  const onPaneContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    if (!rf.current) return;
    const p = rf.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    const fx = Math.round(p.x), fy = Math.round(p.y);
    select(null);
    setMenu({ x: e.clientX, y: e.clientY, items: [
      { label: 'Add device…', onClick: () => setPicker({ x: e.clientX, y: e.clientY, fx, fy }) },
      { label: '', onClick: () => {}, divider: true },
      { label: 'Add group box here', onClick: () => addAnnotation('group', fx, fy) },
      { label: 'Add text here', onClick: () => addAnnotation('text', fx, fy) },
      { label: 'Add note here', onClick: () => addAnnotation('note', fx, fy) },
    ] });
  }, [select, addAnnotation]);

  const onNodeContextMenu = useCallback((e: React.MouseEvent, n: Node) => {
    e.preventDefault();
    const aid = annId(n.id);
    if (aid) {
      const a = annotations.find(x => x.id === aid);
      if (!a) return;
      select({ type: 'annotation', id: aid });
      setMenu({ x: e.clientX, y: e.clientY, items: [
        { label: a.locked ? 'Unlock' : 'Lock for learners', onClick: () => updateAnnotations({ [aid]: { locked: !a.locked } }) },
        { label: 'Delete', danger: true, disabled: a.locked, onClick: () => deleteAnnotation(aid) },
      ] });
      return;
    }
    const id = Number(n.id);
    const node = nodes.find(x => x.id === id);
    const rt = lab?.nodes?.[n.id];
    const live = rt && rt.state !== 'absent';
    const running = rt?.state === 'running';
    select({ type: 'node', id });
    if (node?.kind === 'bridge') {
      setMenu({ x: e.clientX, y: e.clientY, items: [
        { label: 'Edit network', onClick: () => setEditingNode(id) },
        { label: '', onClick: () => {}, divider: true },
        { label: `Delete ${node.name}`, danger: true, onClick: () => {
          if (!deployed || confirm(`Delete ${node.name}? Devices cabled to it lose that connection.`)) deleteNode(id);
        } },
      ] });
      return;
    }
    setMenu({
      x: e.clientX, y: e.clientY, items: [
        { label: 'Edit node', onClick: () => setEditingNode(id) },
        { label: '', onClick: () => {}, divider: true },
        { label: 'Open console', onClick: () => openConsole(id), disabled: !running },
        { label: 'Open shell (sh)', onClick: () => openConsole(id, 'console', 'sh'), disabled: !running || node?.kind === 'linux' },
        { label: 'View logs', onClick: () => openConsole(id, 'logs'), disabled: !live },
        { label: '', onClick: () => {}, divider: true },
        { label: deployed ? 'Start' : 'Start just this device', onClick: () => nodeAction(id, 'start'), disabled: running },
        { label: 'Stop', onClick: () => nodeAction(id, 'stop'), disabled: !running },
        { label: 'Restart', onClick: () => nodeAction(id, 'restart'), disabled: !live },
        { label: '', onClick: () => {}, divider: true },
        { label: `Delete ${node?.name ?? 'device'}`, danger: true, onClick: () => {
          if (!deployed || confirm(`Delete ${node?.name}? Its container will be removed from the running lab.`)) deleteNode(id);
        } },
      ],
    });
  }, [nodes, lab, deployed, select, openConsole, nodeAction, deleteNode, annotations, updateAnnotations, deleteAnnotation,
      setEditingNode]);

  const onEdgeContextMenu = useCallback((e: React.MouseEvent, edge: Edge) => {
    e.preventDefault();
    if (edge.id.startsWith('mgmt-')) return;
    const id = Number(edge.id);
    select({ type: 'link', id });
    setMenu({
      x: e.clientX, y: e.clientY, items: [
        { label: 'Capture packets', onClick: () => openCapture(id), disabled: !deployed },
        { label: 'Impairments (delay / loss)…', onClick: () => setNetemLink(id), disabled: !deployed },
        { label: '', onClick: () => {}, divider: true },
        { label: 'Delete link', danger: true, onClick: () => deleteLink(id) },
      ],
    });
  }, [deployed, select, deleteLink, openCapture]);

  // Double-click → console. Detected from click timing: the first click
  // re-renders the node as selected, which makes browsers drop the native
  // dblclick event.
  const lastClick = useRef<{ id: string; t: number }>({ id: '', t: 0 });
  const onNodeClick = useCallback((_e: React.MouseEvent, n: Node) => {
    const aid = annId(n.id);
    if (aid) { select({ type: 'annotation', id: aid }); return; }
    const now = Date.now();
    const dbl = lastClick.current.id === n.id && now - lastClick.current.t < 400;
    lastClick.current = { id: n.id, t: dbl ? 0 : now };
    select({ type: 'node', id: Number(n.id) });
    if (dbl && lab?.nodes?.[n.id]?.state === 'running') openConsole(Number(n.id));
  }, [lab, select, openConsole]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setTool('select'); setEditingNode(null); return; }
    if ((e.key === 'Delete' || e.key === 'Backspace') && selection &&
        !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
      if (selection.type === 'annotation') {
        if (!annotations.find(a => a.id === selection.id)?.locked) deleteAnnotation(selection.id);
      } else if (selection.type === 'node') {
        const n = nodes.find(x => x.id === selection.id);
        if (!deployed || confirm(`Delete ${n?.name}? Its container will be removed from the running lab.`)) deleteNode(selection.id);
      } else {
        deleteLink(selection.id);
      }
    }
  }, [selection, nodes, deployed, deleteNode, deleteLink, setTool, annotations, deleteAnnotation, setEditingNode]);

  const netemLabel = (() => {
    const l = links.find(x => x.id === netemLink);
    if (!l) return '';
    const name = (id: number) => nodes.find(n => n.id === id)?.name ?? '?';
    return `${name(l.source_node_id)} ${l.source_interface} ↔ ${name(l.target_node_id)} ${l.target_interface}`;
  })();

  return (
    <div ref={wrapper} className={`w-full h-full bg-[#f4f6f9] ${tool !== 'select' ? 'plabs-placing' : ''}
        ${shown ? '' : 'plabs-hidden'} ${reveal ? 'plabs-reveal' : ''}`}
      tabIndex={0} onKeyDown={onKeyDown}>
      <ReactFlow
        nodes={rfNodes}
        onNodesChange={onNodesChange}
        onNodeDragStart={onNodeDragStart}
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
        onEdgeClick={(_e, ed) => { if (!ed.id.startsWith('mgmt-')) select({ type: 'link', id: Number(ed.id) }); }}
        onPaneClick={onPaneClick}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        onEdgeContextMenu={onEdgeContextMenu}
        deleteKeyCode={null}
        zoomOnDoubleClick={false}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.6} color="#c3cad6" />
        <Controls showInteractive={false} className="plabs-controls" />
      </ReactFlow>
      <AskAIBar />
      <AIChatPanel />
      <CanvasToolbar />
      {nodes.length === 0 && annotations.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-center text-slate-500">
            <div className="text-lg font-semibold text-slate-700">Right-click the canvas to add a device</div>
            <div className="text-sm mt-1">Then drag from a device's blue dot onto another device to cable them.</div>
          </div>
        </div>
      )}
      {menu && <ContextMenu {...menu} onClose={() => setMenu(null)} />}
      {picker && (
        <AddDevicePicker x={picker.x} y={picker.y} onClose={() => setPicker(null)}
          onPick={t => addNode(t.id, picker.fx - 28, picker.fy - 28)} />
      )}
      {netemLink !== null && currentTopologyId && (
        <LinkNetemModal topologyId={currentTopologyId} linkId={netemLink} label={netemLabel} onClose={() => setNetemLink(null)} />
      )}
    </div>
  );
}

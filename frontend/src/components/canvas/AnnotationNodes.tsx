/**
 * Canvas drawing elements (never deployed): labelled group boxes, free
 * text and sticky notes. Each shows a property card beside it when
 * selected (label / text, colour, lock, delete).
 */
import { memo, useEffect, useState } from 'react';
import { NodeResizer, NodeToolbar, Position, useStore, type NodeProps, type ReactFlowState } from 'reactflow';
import { Lock, Trash2 } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import type { Annotation } from '../../types';

export interface AnnotationNodeData { ann: Annotation }

export const ANN_COLORS: Record<string, string> = {
  blue: '#2563eb', orange: '#b45309', teal: '#0f766e', purple: '#7c3aed', gray: '#64748b',
};
const NOTE_COLORS: Record<string, { bg: string; border: string; swatch: string }> = {
  yellow: { bg: '#fef9c3', border: '#facc15', swatch: '#eab308' },
  blue: { bg: '#dbeafe', border: '#93c5fd', swatch: '#3b82f6' },
  teal: { bg: '#ccfbf1', border: '#5eead4', swatch: '#14b8a6' },
  purple: { bg: '#ede9fe', border: '#c4b5fd', swatch: '#8b5cf6' },
  gray: { bg: '#f1f5f9', border: '#cbd5e1', swatch: '#94a3b8' },
};

const colorOf = (c: string) => ANN_COLORS[c] ?? ANN_COLORS.blue;

/** Property card shown beside a selected annotation. */
function PropertyCard({ ann, selected, xPos }: { ann: Annotation; selected: boolean; xPos: number }) {
  const { updateAnnotations, deleteAnnotation } = useLabStore();
  // Right of the item, unless that would run off the canvas.
  const side = useStore((s: ReactFlowState) => {
    const [tx, , zoom] = s.transform;
    return (xPos + (ann.w || 160)) * zoom + tx + 300 > s.width ? Position.Left : Position.Right;
  });
  const [text, setText] = useState(ann.text);
  useEffect(() => setText(ann.text), [ann.text]);
  const commit = () => { if (text !== ann.text) updateAnnotations({ [ann.id]: { text } }); };
  const swatches = ann.type === 'note'
    ? Object.entries(NOTE_COLORS).map(([k, v]) => [k, v.swatch] as const)
    : Object.entries(ANN_COLORS);
  const field = 'w-full px-2.5 py-1.5 text-sm rounded-md border border-slate-300 bg-white text-slate-800 '
    + 'focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none';
  return (
    <NodeToolbar isVisible={selected} position={side} offset={16} align="start">
      <div className="w-64 p-3.5 rounded-xl bg-white border border-slate-200 shadow-lg text-slate-700 nodrag nowheel"
        onMouseDown={e => e.stopPropagation()}>
        <label className="block text-xs font-semibold text-slate-600 mb-1">{ann.type === 'group' ? 'Label' : 'Text'}</label>
        {ann.type === 'note'
          ? <textarea className={`${field} h-24 resize-none`} value={text} onChange={e => setText(e.target.value)} onBlur={commit}
              placeholder="Write a note…" />
          : <input className={field} value={text} onChange={e => setText(e.target.value)} onBlur={commit}
              onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />}
        <div className="text-xs font-semibold text-slate-600 mt-3 mb-1.5">Color</div>
        <div className="flex gap-2">
          {swatches.map(([k, hex]) => (
            <button key={k} title={k} onClick={() => updateAnnotations({ [ann.id]: { color: k } })}
              className={`w-7 h-7 rounded-full border-2 ${ann.color === k ? 'border-slate-800 ring-2 ring-offset-1 ring-slate-300' : 'border-white'}`}
              style={{ background: hex }} />
          ))}
        </div>
        <label className="mt-3 flex items-center justify-between text-sm text-slate-700 cursor-pointer">
          <span title="Locked items can't be moved, resized or deleted">Lock for learners</span>
          <input type="checkbox" className="w-4 h-4 accent-blue-600" checked={ann.locked}
            onChange={e => updateAnnotations({ [ann.id]: { locked: e.target.checked } })} />
        </label>
        <button disabled={ann.locked} onClick={() => deleteAnnotation(ann.id)}
          className="mt-3 flex items-center gap-1.5 text-xs text-red-600 hover:text-red-700 disabled:opacity-40">
          <Trash2 size={13} /> Delete {ann.type === 'group' ? 'group box' : ann.type}
        </button>
      </div>
    </NodeToolbar>
  );
}

function resizeHandler(id: string) {
  return (_e: unknown, p: { x: number; y: number; width: number; height: number }) =>
    useLabStore.getState().updateAnnotations({ [id]: { x: Math.round(p.x), y: Math.round(p.y), w: Math.round(p.width), h: Math.round(p.height) } });
}

export const GroupNode = memo(function GroupNode({ data, selected, xPos }: NodeProps<AnnotationNodeData>) {
  const { ann } = data;
  const hex = colorOf(ann.color);
  return (
    <>
      <NodeResizer isVisible={selected && !ann.locked} minWidth={140} minHeight={90}
        color="#3b82f6" handleClassName="!w-2.5 !h-2.5 !bg-white !border-blue-500 !rounded-sm"
        lineClassName="!border-blue-500" onResizeEnd={resizeHandler(ann.id)} />
      <div className="w-full h-full rounded-2xl"
        style={{
          background: `${hex}12`,
          border: selected ? '1.5px solid #3b82f6' : `1.5px dashed ${hex}66`,
        }}>
        <div className="flex items-center gap-1 px-4 pt-3 text-xs font-bold tracking-wider uppercase" style={{ color: hex }}>
          {ann.text || 'Group'}
          {ann.locked && <Lock size={11} strokeWidth={2.5} />}
        </div>
      </div>
      <PropertyCard ann={ann} selected={selected} xPos={xPos} />
    </>
  );
});

export const TextNode = memo(function TextNode({ data, selected, xPos }: NodeProps<AnnotationNodeData>) {
  const { ann } = data;
  return (
    <>
      <div className={`px-1.5 py-0.5 rounded text-base font-semibold whitespace-pre ${selected ? 'outline outline-2 outline-blue-500' : ''}`}
        style={{ color: colorOf(ann.color) }}>
        {ann.text || 'Text'}
        {ann.locked && <Lock size={11} className="inline ml-1 -mt-0.5" />}
      </div>
      <PropertyCard ann={ann} selected={selected} xPos={xPos} />
    </>
  );
});

export const NoteNode = memo(function NoteNode({ data, selected, xPos }: NodeProps<AnnotationNodeData>) {
  const { ann } = data;
  const c = NOTE_COLORS[ann.color] ?? NOTE_COLORS.yellow;
  return (
    <>
      <NodeResizer isVisible={selected && !ann.locked} minWidth={120} minHeight={60}
        color="#3b82f6" handleClassName="!w-2.5 !h-2.5 !bg-white !border-blue-500 !rounded-sm"
        lineClassName="!border-transparent" onResizeEnd={resizeHandler(ann.id)} />
      <div className={`w-full h-full p-3 rounded-lg shadow-md text-[13px] leading-snug text-slate-800 whitespace-pre-wrap overflow-hidden
          ${selected ? 'outline outline-2 outline-blue-500' : ''}`}
        style={{ background: c.bg, border: `1px solid ${c.border}` }}>
        {ann.text || <span className="text-slate-500 italic">Empty note — type in the card →</span>}
        {ann.locked && <Lock size={11} className="absolute top-2 right-2 text-slate-500" />}
      </div>
      <PropertyCard ann={ann} selected={selected} xPos={xPos} />
    </>
  );
});

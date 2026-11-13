import { MousePointer2, BoxSelect, StickyNote, Type } from 'lucide-react';
import { useLabStore, type CanvasTool } from '../../stores/useLabStore';

const TOOLS: { tool: CanvasTool; label: string; icon: React.ReactNode; hint: string }[] = [
  { tool: 'select', label: '', icon: <MousePointer2 size={16} />, hint: 'Select and move (Esc)' },
  { tool: 'group', label: 'Group box', icon: <BoxSelect size={16} />, hint: 'Click the canvas to place a labelled group box' },
  { tool: 'text', label: 'Text', icon: <Type size={16} />, hint: 'Click the canvas to place text' },
  { tool: 'note', label: 'Note', icon: <StickyNote size={16} />, hint: 'Click the canvas to place a note' },
];

export default function CanvasToolbar() {
  const { tool, setTool } = useLabStore();
  const active = TOOLS.find(t => t.tool === tool);
  return (
    <div className="absolute top-4 right-4 z-10 flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-0.5 p-1 rounded-xl bg-white border border-slate-200 shadow-sm">
        {TOOLS.map(t => (
          <button key={t.tool} onClick={() => setTool(t.tool)} title={t.hint}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm transition-colors
              ${tool === t.tool ? 'bg-slate-100 text-slate-900 font-semibold' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'}`}>
            {t.icon}{t.label && <span>{t.label}</span>}
          </button>
        ))}
      </div>
      {tool !== 'select' && active && (
        <div className="px-2 py-1 rounded-md bg-slate-800 text-white text-xs shadow">{active.hint} · Esc to cancel</div>
      )}
    </div>
  );
}

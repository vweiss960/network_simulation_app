/**
 * "Ask AI to build or change the lab" — the entry point for the lab agent.
 * The agent itself arrives with the AI lab agent work; until then the bar
 * explains that instead of silently doing nothing.
 */
import { useEffect, useRef, useState } from 'react';
import { Sparkles, X } from 'lucide-react';

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);

export default function AskAIBar() {
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [card, setCard] = useState<string | null>(null);

  // ⌘K / Ctrl+K focuses the bar from anywhere in the workspace.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const submit = () => {
    if (!text.trim()) return;
    setCard(text.trim());
  };

  return (
    <div className="absolute top-4 left-4 right-[340px] z-10 flex justify-start pointer-events-none">
    <div className="w-full max-w-[520px] min-w-[260px] pointer-events-auto">
      <div className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl bg-white border border-slate-200 shadow-sm
          focus-within:border-blue-400 focus-within:ring-4 focus-within:ring-blue-100">
        <Sparkles size={18} className="text-blue-600 shrink-0" />
        <input ref={input} value={text} onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') { setCard(null); input.current?.blur(); } }}
          placeholder='Ask AI to build or change the lab — "add R4 in area 1 off R2"'
          className="flex-1 min-w-0 bg-transparent text-sm text-slate-800 placeholder:text-slate-500 outline-none" />
        <kbd className="shrink-0 px-1.5 py-0.5 rounded-md border border-slate-300 bg-slate-50 text-[11px] text-slate-500 font-sans">
          {isMac ? '⌘K' : 'Ctrl K'}
        </kbd>
      </div>
      {card && (
        <div className="mt-2 p-4 rounded-xl bg-white border border-slate-200 shadow-lg text-slate-700">
          <div className="flex items-start justify-between gap-3">
            <div className="text-xs font-bold tracking-wider uppercase text-blue-700">AI lab agent</div>
            <button onClick={() => setCard(null)} className="text-slate-400 hover:text-slate-700"><X size={15} /></button>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed">
            The lab agent isn't available yet. When it is, requests like
            <span className="font-medium text-slate-900"> “{card}”</span> will be turned into changes on this canvas
            for you to review and apply.
          </p>
          <div className="mt-3 flex gap-2">
            <button onClick={() => { setCard(null); setText(''); }}
              className="px-3 py-1.5 rounded-lg border border-slate-300 text-sm hover:bg-slate-50">Dismiss</button>
          </div>
        </div>
      )}
    </div>
    </div>
  );
}

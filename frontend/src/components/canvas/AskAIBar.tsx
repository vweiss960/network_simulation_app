/**
 * "Ask AI to build or change the lab" — the entry point for the lab
 * assistant. Submitting opens the chat panel with the request; Ctrl/⌘ K
 * opens it from anywhere in the workspace. Hidden while the panel is open.
 */
import { useEffect, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { useAgentStore } from '../../stores/useAgentStore';

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);

export default function AskAIBar() {
  const { open, setOpen, send } = useAgentStore();
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (useAgentStore.getState().open) return;
        input.current ? input.current.focus() : setOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setOpen]);

  if (open) return null;
  const submit = () => {
    if (!text.trim()) { setOpen(true); return; }
    send(text.trim());
    setText('');
  };

  return (
    <div className="absolute top-4 left-4 right-[340px] z-10 flex justify-start pointer-events-none">
      <div className="w-full max-w-[520px] min-w-[260px] pointer-events-auto">
        <div className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl bg-white border border-slate-200 shadow-sm
            focus-within:border-blue-400 focus-within:ring-4 focus-within:ring-blue-100">
          <button onClick={() => setOpen(true)} title="Open the lab assistant" className="shrink-0">
            <Sparkles size={18} className="text-blue-600" />
          </button>
          <input ref={input} value={text} onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') input.current?.blur(); }}
            placeholder='Ask AI to build or change the lab — "add R4 in area 1 off R2"'
            className="flex-1 min-w-0 bg-transparent text-sm text-slate-800 placeholder:text-slate-500 outline-none" />
          <kbd className="shrink-0 px-1.5 py-0.5 rounded-md border border-slate-300 bg-slate-50 text-[11px] text-slate-500 font-sans">
            {isMac ? '⌘K' : 'Ctrl K'}
          </kbd>
        </div>
      </div>
    </div>
  );
}

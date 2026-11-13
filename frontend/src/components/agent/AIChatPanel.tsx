/**
 * The lab assistant: a chat docked on the left of the canvas. Replies stream
 * in; each tool the agent uses shows as a step ("Reading R3's running
 * config…"); when it asks to apply configuration, one-click replies appear.
 */
import { Fragment, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, ChevronDown, ChevronRight, History, Loader2, Plus, Send, Sparkles, Square, Trash2, X } from 'lucide-react';
import { useAgentStore, type ChatMessage, type Part } from '../../stores/useAgentStore';
import { usePresence } from '../../hooks/usePresence';
import Markdown from './Markdown';

const SUGGESTIONS = [
  'What is running in this lab and how is it connected?',
  'How can I configure IS-IS between the routers?',
  'Check the routing on every device and tell me what looks wrong.',
];

export default function AIChatPanel() {
  const { open, setOpen, status, model, setModel, messages, busy, activity, send, stop, newChat } = useAgentStore();
  const [draft, setDraft] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  // Follow the conversation unless the user has scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight;
  }, [messages, activity]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);

  // Unrolls down from where the Ask AI bar sits; rolls back up on close.
  const { mounted, visible } = usePresence(open, 320);
  if (!mounted) return null;
  const submit = (text = draft) => {
    if (!text.trim() || busy) return;
    send(text.trim());
    setDraft('');
  };
  const last = messages[messages.length - 1];
  const lastText = last?.role === 'assistant' ? last.parts.filter(p => p.kind === 'text').map(p => (p as { text: string }).text).join('') : '';
  const asksToApply = !busy && /would you like me to (apply|push|configure)|shall i apply|should i apply|want me to apply/i.test(lastText);

  return (
    <div className="absolute top-4 left-4 bottom-4 z-30 w-[560px] max-w-[calc(100%-32px)] flex flex-col rounded-2xl bg-white border border-slate-200 shadow-xl text-slate-800 plabs-motion"
      style={{ clipPath: visible ? 'inset(0 0 0 0 round 16px)' : 'inset(0 0 calc(100% - 52px) 0 round 16px)',
               opacity: visible ? 1 : 0, transform: visible ? 'none' : 'translateY(-4px)' }}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-200">
        <Sparkles size={17} className="text-blue-600 shrink-0" />
        <ConversationPicker />
        <div className="flex-1" />
        {status?.models.length ? (
          <select value={model} onChange={e => setModel(e.target.value)} disabled={busy} title="Model"
            className="text-xs rounded-md border border-slate-300 bg-white px-1.5 py-1 text-slate-700 outline-none focus:border-blue-500">
            {status.models.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        ) : null}
        <button onClick={newChat} title="New conversation" className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800"><Plus size={16} /></button>
        <button onClick={() => setOpen(false)} title="Close" className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800"><X size={16} /></button>
      </div>

      {/* Conversation */}
      <div ref={scroller} className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {status && !status.enabled && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <div className="font-semibold mb-1">The assistant isn't set up yet</div>
            <div>{status.error}</div>
          </div>
        )}
        {messages.length === 0 && status?.enabled && (
          <div className="text-sm text-slate-500">
            <p className="mb-3">Ask about this lab, or ask me to change it. I can read devices and their configs,
              propose configuration and apply it when you say so, and look things up in Cisco's documentation.</p>
            <div className="space-y-1.5">
              {SUGGESTIONS.map(s => (
                <button key={s} onClick={() => submit(s)}
                  className="block w-full text-left px-3 py-2 rounded-lg border border-slate-200 hover:border-blue-300 hover:bg-blue-50 text-slate-700">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => <Message key={i} m={m} live={busy && i === messages.length - 1} activity={activity} />)}
        {asksToApply && (
          <div className="flex gap-2">
            <button onClick={() => submit('Yes, apply it.')}
              className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium">Yes, apply it</button>
            <button onClick={() => submit("No, don't apply it.")}
              className="px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50 text-sm">No</button>
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="p-3 border-t border-slate-200">
        <div className="flex items-end gap-2 rounded-xl border border-slate-300 focus-within:border-blue-500 focus-within:ring-4 focus-within:ring-blue-100 px-3 py-2">
          <textarea ref={input} value={draft} onChange={e => setDraft(e.target.value)} rows={1}
            disabled={!status?.enabled}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
            placeholder={status?.enabled === false ? 'Assistant unavailable' : 'Ask, or tell me what to build or change…'}
            className="flex-1 resize-none bg-transparent text-sm outline-none max-h-40 placeholder:text-slate-400"
            style={{ height: Math.min(160, 22 + 20 * (draft.split('\n').length - 1)) }} />
          {busy
            ? <button onClick={stop} title="Stop" className="p-1.5 rounded-lg bg-slate-800 text-white"><Square size={14} /></button>
            : <button onClick={() => submit()} disabled={!draft.trim() || !status?.enabled} title="Send (Enter)"
                className="p-1.5 rounded-lg bg-blue-600 text-white disabled:opacity-40"><Send size={14} /></button>}
        </div>
        <div className="mt-1.5 text-[11px] text-slate-400">Enter to send · Shift+Enter for a new line · changes it makes are logged</div>
      </div>
    </div>
  );
}

function Message({ m, live, activity }: { m: ChatMessage; live: boolean; activity: string }) {
  if (m.role === 'user') {
    const text = m.parts.map(p => (p.kind === 'text' ? p.text : '')).join('');
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-blue-600 text-white px-3.5 py-2 text-[13.5px] whitespace-pre-wrap">{text}</div>
      </div>
    );
  }
  // Consecutive tool steps form one group; text parts stand alone.
  const groups: (Part | Extract<Part, { kind: 'tool' }>[])[] = [];
  for (const p of m.parts) {
    const last = groups[groups.length - 1];
    if (p.kind === 'tool' && Array.isArray(last)) last.push(p);
    else groups.push(p.kind === 'tool' ? [p] : p);
  }
  return (
    <div>
      {groups.map((g, i) => Array.isArray(g)
        ? <ToolGroup key={i} steps={g} />
        : <Fragment key={i}>{g.kind === 'text' && <Markdown text={g.text} />}</Fragment>)}
      {live && (
        <div className="flex items-center gap-2 mt-1 text-[12.5px] text-blue-700">
          <Loader2 size={13} className="animate-spin" /> {activity || 'Working'}…
        </div>
      )}
      {m.error && (
        <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] text-red-700">{m.error}</div>
      )}
    </div>
  );
}

type ToolStep = Extract<Part, { kind: 'tool' }>;

function StepRow({ p }: { p: ToolStep }) {
  return (
    <div className="flex items-start gap-2 text-[12.5px] text-slate-500">
      <span className="mt-0.5 shrink-0">
        {p.state === 'running' ? <Loader2 size={13} className="animate-spin text-blue-600" />
          : p.state === 'done' ? <Check size={13} className="text-emerald-600" />
          : <AlertTriangle size={13} className="text-amber-600" />}
      </span>
      <span className="min-w-0">
        {p.label}
        {p.state === 'error' && p.summary && <span className="block text-amber-700 break-words">{p.summary}</span>}
      </span>
    </div>
  );
}

/** Tool steps: listed while running, then folded into one line (click to expand). */
function ToolGroup({ steps }: { steps: ToolStep[] }) {
  const [expanded, setExpanded] = useState(false);
  const running = steps.some(s => s.state === 'running');
  if (running || expanded) {
    return (
      <div className="my-1.5 space-y-0.5">
        {!running && (
          <button onClick={() => setExpanded(false)} className="flex items-center gap-1 text-[12px] text-slate-400 hover:text-slate-700">
            <ChevronDown size={13} /> Hide steps
          </button>
        )}
        {steps.map(s => <StepRow key={s.id} p={s} />)}
      </div>
    );
  }
  const errors = steps.filter(s => s.state === 'error').length;
  return (
    <button onClick={() => setExpanded(true)} title="Show the steps"
      className="my-1.5 w-full flex items-center gap-1.5 text-left text-[12.5px] text-slate-500 hover:text-slate-800">
      <ChevronRight size={13} className="shrink-0" />
      {errors ? <AlertTriangle size={13} className="shrink-0 text-amber-600" /> : <Check size={13} className="shrink-0 text-emerald-600" />}
      <span className="shrink-0 font-medium">{steps.length} step{steps.length === 1 ? '' : 's'}{errors ? ` · ${errors} failed` : ''}</span>
      <span className="truncate text-slate-400">· {steps.map(s => s.label).join(' · ')}</span>
    </button>
  );
}

function ago(iso: string | null): string {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

/** Header: the current conversation, with a menu of this lab's earlier ones. */
function ConversationPicker() {
  const { conversations, conversationId, openConversation, deleteConversation, newChat, busy, loadConversations } = useAgentStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    loadConversations();
    const close = (e: Event) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener('pointerdown', close, true);
    return () => window.removeEventListener('pointerdown', close, true);
  }, [open, loadConversations]);
  const current = conversations.find(c => c.id === conversationId);
  return (
    <div ref={ref} className="relative min-w-0">
      <button onClick={() => setOpen(o => !o)} disabled={busy} title="Your conversations in this lab"
        className="flex items-center gap-1.5 max-w-[260px] px-2 py-1 -ml-1 rounded-md hover:bg-slate-100 disabled:opacity-60">
        <span className="font-semibold text-[15px] text-slate-900 truncate">{current ? current.title : 'Lab assistant'}</span>
        <ChevronDown size={14} className="text-slate-500 shrink-0" />
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-40 w-[380px] max-h-[420px] overflow-y-auto rounded-xl bg-white border border-slate-200 shadow-xl py-1">
          <button onClick={() => { newChat(); setOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-blue-700 hover:bg-blue-50">
            <Plus size={15} /> New conversation
          </button>
          <div className="px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
            <History size={12} /> Earlier in this lab
          </div>
          {conversations.length === 0 && <div className="px-3 py-2 text-sm text-slate-400">No saved conversations yet.</div>}
          {conversations.map(c => (
            <div key={c.id} className={`group flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 ${c.id === conversationId ? 'bg-blue-50/60' : ''}`}>
              <button className="flex-1 min-w-0 text-left" onClick={() => { openConversation(c.id); setOpen(false); }}>
                <div className="text-sm text-slate-800 truncate">{c.title}</div>
                <div className="text-[11px] text-slate-400">{ago(c.updated_at)} · {c.model}</div>
              </button>
              <button title="Delete conversation" onClick={() => { if (confirm(`Delete “${c.title}”?`)) deleteConversation(c.id); }}
                className="p-1 rounded text-slate-400 opacity-0 group-hover:opacity-100 hover:text-red-600 hover:bg-red-50">
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

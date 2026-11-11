/**
 * ConfigDesignAIDrawer — slide-out AI assistant for the Config Design view.
 *
 * The agent runs server-side (POST /api/config-templates/ai/) and streams
 * SSE events back. This component:
 *   - Maintains the per-template conversation in memory (cleared on template
 *     switch — see effect on `templateId`)
 *   - Consumes the SSE stream and reacts to each event type:
 *       token         → append to the current assistant message
 *       tool_status   → show a small "running X" indicator
 *       set_body      → snapshot the current body, then apply the new body
 *                       to the editor via the setBody prop
 *       render_result → attach a render outcome to the latest assistant turn
 *       done          → finalize the assistant turn
 *   - Maintains an undo stack so the user can revert the agent's edits
 *
 * The drawer doesn't own the template — it just edits the body that its
 * parent (ConfigDesignView) owns. Snapshots live in the drawer because the
 * concept of "agent-edit history" only matters here.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Send, Undo2, Sparkles, X, Loader2, CheckCircle2, AlertCircle, Wrench, GitBranch, Check } from 'lucide-react';

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  toolEvents?: Array<{ type: 'tool_call'; tool: string; input?: any } | { type: 'render'; ok: boolean; node_name: string; error?: string; line?: number | null }>;
};

interface Props {
  isOpen: boolean;
  onClose: () => void;
  topologyId: number | null;
  templateId: number | null;
  templateName?: string;
  templateDescription?: string;
  templatePlatformDefault?: string;
  templateMatchAttr?: string;
  currentBody: string;
  setBody: (body: string) => void;
  // Chat history is owned by the parent so it persists across template
  // switches — the conversation outlives the per-template editor focus.
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
}

export default function ConfigDesignAIDrawer({
  isOpen, onClose, topologyId, templateId,
  templateName, templateDescription, templatePlatformDefault, templateMatchAttr,
  currentBody, setBody, messages, setMessages,
}: Props) {
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  // Snapshot stack — each entry is the editor body BEFORE an agent edit.
  // Popped by the Undo button. Reset when the user switches templates.
  const [snapshots, setSnapshots] = useState<string[]>([]);
  // Two-stage editing: agent edits land here as a pending proposal. The
  // operator reviews against the live editor body (diff) and clicks Apply
  // to commit (which writes to the editor via setBody + snapshots prior
  // state) or Discard to drop the proposal entirely.
  const [pendingBody, setPendingBody] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  // Tracks the live editor body across event handlers. When multiple
  // set_body events arrive back-to-back the prop is stale until React
  // re-renders — keep our own ref to read the latest synchronously.
  const liveBodyRef = useRef(currentBody);
  useEffect(() => { liveBodyRef.current = currentBody; }, [currentBody]);

  // Reset only the per-template scratch state (undo stack + pending edit)
  // when the user switches templates. Conversation history is owned by the
  // parent and persists across template clicks — so the agent can carry
  // context from one template into the next ("now do the same for OSPF").
  useEffect(() => {
    setSnapshots([]);
    setPendingBody(null);
    setShowDiff(false);
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, [templateId]);

  // Auto-scroll to bottom on new messages.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streaming]);

  const handleSend = async () => {
    if (!input.trim() || streaming || !topologyId) return;

    const userMsg: ChatMessage = { role: 'user', content: input };
    const assistantMsg: ChatMessage = { role: 'assistant', content: '', toolEvents: [] };
    const nextMessages = [...messages, userMsg, assistantMsg];
    setMessages(nextMessages);
    setInput('');
    setStreaming(true);

    // Use the user+assistant turns up to (but not including) the empty placeholder
    // when sending to the backend — Anthropic doesn't want an empty trailing turn.
    const apiMessages = [...messages, userMsg].map(m => ({ role: m.role, content: m.content }));

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/config-templates/ai', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          topology_id: topologyId,
          current_body: currentBody,
          messages: apiMessages,
          // Template metadata for the currently-focused editor target.
          // The backend augments the system prompt with this so the agent
          // always knows which template the user has open right now —
          // critical now that the conversation persists across template
          // clicks.
          template_meta: templateId == null ? null : {
            id: templateId,
            name: templateName || '',
            description: templateDescription || '',
            platform_default: templatePlatformDefault || '',
            match_attr: templateMatchAttr || 'platform',
          },
        }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) throw new Error(`AI request failed: ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE events are separated by blank lines.
        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!raw.startsWith('data: ')) continue;
          const payload = raw.slice(6);
          let event: any;
          try { event = JSON.parse(payload); } catch { continue; }
          applyEvent(event);
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setMessages(m => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (last?.role === 'assistant') last.content += `\n\n_Error: ${e.message || e}_`;
          return copy;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  };

  const applyEvent = (event: any) => {
    if (event.type === 'token') {
      setMessages(m => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (last?.role === 'assistant') last.content += event.text;
        return copy;
      });
    } else if (event.type === 'tool_status') {
      setMessages(m => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (last?.role === 'assistant') {
          last.toolEvents = [...(last.toolEvents || []), { type: 'tool_call', tool: event.tool, input: event.input }];
        }
        return copy;
      });
    } else if (event.type === 'set_body') {
      // Stage the agent's body as a pending proposal — does NOT modify the
      // editor yet. The operator reviews the diff and explicitly Applies or
      // Discards it. Multiple set_body events in one stream just replace the
      // pending proposal (the agent is iterating).
      setPendingBody(event.body);
    } else if (event.type === 'render_result') {
      setMessages(m => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (last?.role === 'assistant') {
          last.toolEvents = [...(last.toolEvents || []), {
            type: 'render', ok: event.ok, node_name: event.node_name, error: event.error, line: event.line,
          }];
        }
        return copy;
      });
    }
    // 'done' is a no-op — the stream closing finalizes the turn.
  };

  const handleUndo = () => {
    if (snapshots.length === 0) return;
    const prior = snapshots[snapshots.length - 1];
    setSnapshots(s => s.slice(0, -1));
    setBody(prior);
  };

  const handleApplyPending = () => {
    if (pendingBody == null) return;
    // Snapshot the current editor body for Undo, then commit the pending
    // body to the editor. The user still has to click Save to persist to
    // the backend — same as if they'd typed the edit themselves.
    setSnapshots(s => [...s, liveBodyRef.current]);
    setBody(pendingBody);
    setPendingBody(null);
    setShowDiff(false);
  };
  const handleDiscardPending = () => {
    setPendingBody(null);
    setShowDiff(false);
  };

  const handleStop = () => {
    abortRef.current?.abort();
  };

  // Compute the line-by-line diff between the editor and the pending edit.
  // Cheap LCS — fine for the typical template size (~50–300 lines).
  const diffLines = useMemo(() => {
    if (pendingBody == null) return null;
    return computeLineDiff(currentBody, pendingBody);
  }, [pendingBody, currentBody]);
  const diffStats = useMemo(() => {
    if (!diffLines) return { added: 0, removed: 0 };
    let added = 0, removed = 0;
    for (const l of diffLines) {
      if (l.type === '+') added++;
      else if (l.type === '-') removed++;
    }
    return { added, removed };
  }, [diffLines]);

  if (!isOpen) return null;

  return (
    <div className="absolute inset-y-0 right-0 w-[420px] bg-surface-900 border-l border-surface-700 flex flex-col shadow-2xl slide-in z-30">
      <div className="h-10 px-3 flex items-center justify-between border-b border-surface-700">
        <span className="text-xs font-medium flex items-center gap-1.5">
          <Sparkles size={12} className="text-violet-400" />
          AI Template Assistant
          <span className="text-[9px] text-surface-500 ml-1 font-mono">sonnet-4-6</span>
        </span>
        <div className="flex items-center gap-1">
          <button
            className="btn btn-ghost btn-sm text-[10px] disabled:opacity-30"
            onClick={handleUndo}
            disabled={snapshots.length === 0 || streaming}
            title={snapshots.length ? `Undo last AI edit (${snapshots.length} available)` : 'No AI edits to undo'}
          >
            <Undo2 size={11} /> Undo
            {snapshots.length > 0 && <span className="ml-1 bg-surface-700 px-1 rounded">{snapshots.length}</span>}
          </button>
          <button className="btn btn-ghost btn-sm p-1" onClick={onClose} title="Close">
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-[11px] text-surface-500 space-y-2">
            <p>Describe the config you want and I'll write a Jinja template for it.</p>
            <p className="text-surface-600">Examples:</p>
            <ul className="text-[10px] text-surface-600 space-y-1 pl-2">
              <li>• "OSPF area 0 for IOS-XR with passive customer-facing interfaces"</li>
              <li>• "FRR with BGP using neighbor IPs from the topology"</li>
              <li>• "JunOS interface configuration with descriptions from the link data"</li>
            </ul>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
            <div
              className={`text-[11px] rounded px-3 py-2 whitespace-pre-wrap max-w-[95%] ${
                m.role === 'user' ? 'bg-amber-700/30 border border-amber-700/40' : 'bg-surface-800 border border-surface-700'
              }`}
            >
              {m.content || (m.role === 'assistant' && streaming && i === messages.length - 1 ? <span className="text-surface-500 italic">thinking…</span> : null)}
              {m.toolEvents && m.toolEvents.length > 0 && (
                <div className="mt-2 space-y-1 border-t border-surface-700/60 pt-2">
                  {m.toolEvents.map((e, j) => (
                    <div key={j} className="text-[10px] flex items-start gap-1.5">
                      {e.type === 'tool_call' && (
                        <>
                          <Wrench size={10} className="text-surface-500 shrink-0 mt-0.5" />
                          <span className="text-surface-400 font-mono">{e.tool}</span>
                        </>
                      )}
                      {e.type === 'render' && e.ok && (
                        <>
                          <CheckCircle2 size={10} className="text-green-400 shrink-0 mt-0.5" />
                          <span className="text-green-300">rendered against <span className="font-mono">{e.node_name}</span></span>
                        </>
                      )}
                      {e.type === 'render' && !e.ok && (
                        <>
                          <AlertCircle size={10} className="text-red-400 shrink-0 mt-0.5" />
                          <span className="text-red-300">
                            render failed on <span className="font-mono">{e.node_name}</span>
                            {e.line ? ` (line ${e.line})` : ''}: {e.error}
                          </span>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Pending agent edit — staged for the operator to review */}
      {pendingBody != null && (
        <div className="border-t border-amber-700/40 bg-amber-900/15 p-2 space-y-1.5">
          <div className="flex items-center gap-1.5 text-[11px] text-amber-200">
            <GitBranch size={11} className="text-amber-400 shrink-0" />
            <span>Pending AI edit</span>
            <span className="text-[10px] text-surface-400">
              <span className="text-green-300">+{diffStats.added}</span>
              <span className="mx-1 text-surface-500">/</span>
              <span className="text-red-300">−{diffStats.removed}</span>
            </span>
            <button
              className="ml-auto text-[10px] underline text-surface-400 hover:text-surface-200"
              onClick={() => setShowDiff(v => !v)}
            >
              {showDiff ? 'Hide diff' : 'View diff'}
            </button>
          </div>
          {showDiff && diffLines && (
            <pre className="text-[10px] font-mono bg-surface-950 border border-surface-700 rounded p-2 max-h-[240px] overflow-y-auto leading-tight">
              {diffLines.map((l, i) => (
                <div key={i} className={
                  l.type === '+' ? 'text-green-300 bg-green-900/15' :
                  l.type === '-' ? 'text-red-300 bg-red-900/15' :
                  'text-surface-400'
                }>
                  {l.type === ' ' ? '  ' : l.type + ' '}{l.line}
                </div>
              ))}
            </pre>
          )}
          <div className="flex gap-1.5">
            <button
              className="btn btn-primary btn-sm text-[11px] flex-1"
              onClick={handleApplyPending}
              title="Commit the agent's proposal to the editor (still need to click Save to persist)"
            >
              <Check size={11} /> Apply
            </button>
            <button
              className="btn btn-ghost btn-sm text-[11px] flex-1 text-red-400"
              onClick={handleDiscardPending}
              title="Throw away the proposal — editor stays as-is"
            >
              <X size={11} /> Discard
            </button>
          </div>
        </div>
      )}

      <div className="border-t border-surface-700 p-2 space-y-1.5">
        <textarea
          className="input input-sm w-full text-xs resize-none"
          rows={3}
          placeholder="Describe the config you want…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              handleSend();
            }
          }}
          disabled={streaming}
        />
        <div className="flex items-center gap-1.5">
          {streaming ? (
            <button className="btn btn-secondary btn-sm text-[11px] flex-1" onClick={handleStop}>
              <Loader2 size={11} className="animate-spin" /> Stop
            </button>
          ) : (
            <button
              className="btn btn-primary btn-sm text-[11px] flex-1"
              onClick={handleSend}
              disabled={!input.trim() || !topologyId || templateId == null}
            >
              <Send size={11} /> Send
              <span className="text-[9px] opacity-60 ml-1">(⌘⏎)</span>
            </button>
          )}
        </div>
        {templateId == null && (
          <div className="text-[10px] text-surface-500">Select a template first.</div>
        )}
      </div>
    </div>
  );
}

/** Line-by-line LCS diff. Fine for the typical Jinja template size; if
 *  templates ever grow into the thousands of lines we'd want to swap this
 *  for an O((m+n) D) Myers diff. Returns lines in original order, each
 *  tagged with ' ' (context), '+' (only in `b`), or '-' (only in `a`).
 */
type DiffLine = { type: ' ' | '+' | '-'; line: string };
function computeLineDiff(a: string, b: string): DiffLine[] {
  const aLines = a.split('\n');
  const bLines = b.split('\n');
  const m = aLines.length;
  const n = bLines.length;
  const lcs: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      lcs[i][j] = aLines[i - 1] === bLines[j - 1]
        ? lcs[i - 1][j - 1] + 1
        : Math.max(lcs[i - 1][j], lcs[i][j - 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && aLines[i - 1] === bLines[j - 1]) {
      out.push({ type: ' ', line: aLines[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || lcs[i][j - 1] >= lcs[i - 1][j])) {
      out.push({ type: '+', line: bLines[j - 1] });
      j--;
    } else {
      out.push({ type: '-', line: aLines[i - 1] });
      i--;
    }
  }
  return out.reverse();
}

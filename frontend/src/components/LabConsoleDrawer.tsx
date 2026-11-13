/**
 * Console drawer: device CLIs (SSH / docker exec) and container logs in
 * xterm.js, styled like VS Code's integrated terminal.
 *  - Full screen, resizable height, split into 1 / 2 / 4 panes.
 *  - Shift+Tab rotates through sessions (plain Tab stays with the device:
 *    it's command completion on network CLIs).
 *  - Right-click pastes SecureCRT-style: a confirmation shows exactly what
 *    will be sent; lines go out one at a time so the device keeps up. A
 *    multi-line Ctrl+V gets the same confirmation. Right-click with a
 *    selection copies it instead.
 * Every session stays mounted (its WebSocket open) whether shown or not.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Columns2, Grid2x2, Maximize2, Minimize2, Square, X } from 'lucide-react';
import { Terminal, type ITheme } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';
import { useLabStore } from '../stores/useLabStore';
import { useAgentStore } from '../stores/useAgentStore';
import { usePresence } from '../hooks/usePresence';

export type ConsoleTab = {
  id: string;        // node-id + kind ("console:42" or "logs:42")
  type: 'console' | 'logs';
  nodeId: number;
  nodeName: string;
  cli?: string;
};

type Props = {
  tabs: ConsoleTab[];
  activeId: string | null;
  onSetActive: (id: string) => void;
  onClose: (id: string) => void;
};

// VS Code "Dark+" integrated-terminal colours.
const VSCODE: ITheme = {
  background: '#1e1e1e', foreground: '#cccccc', cursor: '#aeafad', cursorAccent: '#1e1e1e',
  selectionBackground: '#264f78',
  black: '#000000', red: '#cd3131', green: '#0dbc79', yellow: '#e5e510',
  blue: '#2472c8', magenta: '#bc3fbc', cyan: '#11a8cd', white: '#e5e5e5',
  brightBlack: '#666666', brightRed: '#f14c4c', brightGreen: '#23d18b', brightYellow: '#f5f543',
  brightBlue: '#3b8eea', brightMagenta: '#d670d6', brightCyan: '#29b8db', brightWhite: '#e5e5e5',
};
const FONT = "'Cascadia Code', 'Cascadia Mono', Consolas, 'Courier New', Menlo, monospace";
const C = { bar: '#252526', tabIdle: '#2d2d2d', border: '#3c3c3c', accent: '#0078d4', text: '#969696', textOn: '#ffffff' };

type Layout = 1 | 2 | 4;
/** Pane rectangles (percent) for each layout. */
const RECTS: Record<Layout, { l: number; t: number; w: number; h: number }[]> = {
  1: [{ l: 0, t: 0, w: 100, h: 100 }],
  2: [{ l: 0, t: 0, w: 50, h: 100 }, { l: 50, t: 0, w: 50, h: 100 }],
  4: [{ l: 0, t: 0, w: 50, h: 50 }, { l: 50, t: 0, w: 50, h: 50 }, { l: 0, t: 50, w: 50, h: 50 }, { l: 50, t: 50, w: 50, h: 50 }],
};

const HEIGHT_KEY = 'plabs.console.height';

export default function LabConsoleDrawer({ tabs, activeId, onSetActive, onClose }: Props) {
  const [layout, setLayout] = useState<Layout>(1);
  const [panes, setPanes] = useState<(string | null)[]>([null]);
  const [activePane, setActivePane] = useState(0);
  const [full, setFull] = useState(false);
  const [height, setHeight] = useState<number>(() => {
    try { return Number(localStorage.getItem(HEIGHT_KEY)) || 0; } catch { return 0; }
  });

  // Resize the pane list when the layout changes, filling new panes with
  // sessions that aren't shown yet.
  const changeLayout = (l: Layout) => {
    setLayout(l);
    setPanes(prev => {
      const next = prev.slice(0, l);
      const shown = new Set(next);
      for (const t of tabs) {
        if (next.length >= l) break;
        if (!shown.has(t.id)) { next.push(t.id); shown.add(t.id); }
      }
      while (next.length < l) next.push(null);
      return next;
    });
    setActivePane(p => Math.min(p, l - 1));
  };

  // The store's active session is shown: focus its pane, or put it in the active pane.
  useEffect(() => {
    if (!activeId) return;
    setPanes(prev => {
      const at = prev.indexOf(activeId);
      if (at >= 0) { setActivePane(at); return prev; }
      const next = [...prev];
      next[Math.min(activePane, next.length - 1)] = activeId;
      return next;
    });
  }, [activeId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Closed sessions leave their panes.
  useEffect(() => {
    const ids = new Set(tabs.map(t => t.id));
    setPanes(prev => {
      if (prev.every(p => p === null || ids.has(p))) return prev;
      const shown = new Set(prev.filter(p => p && ids.has(p)));
      const spare = tabs.map(t => t.id).filter(id => !shown.has(id));
      return prev.map(p => (p && ids.has(p) ? p : spare.shift() ?? null));
    });
  }, [tabs]);

  const focusPane = (i: number) => {
    setActivePane(i);
    const id = panes[i];
    if (id && id !== activeId) onSetActive(id);
  };

  // Next / previous session (Shift+Tab, Alt+→/←, or Tab on the tab bar).
  const rotate = useCallback((dir: 1 | -1 = 1) => {
    if (tabs.length < 2) return;
    const i = tabs.findIndex(t => t.id === activeId);
    onSetActive(tabs[(i + dir + tabs.length) % tabs.length].id);
  }, [tabs, activeId, onSetActive]);

  // Drag a tab onto the console area: near the left/right edge splits the
  // view with it on that side; onto a pane shows it there.
  const [dragId, setDragId] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ pane: number; split?: 'left' | 'right' } | null>(null);
  const area = useRef<HTMLDivElement>(null);
  const zoneAt = (e: React.DragEvent) => {
    const b = area.current!.getBoundingClientRect();
    const fx = (e.clientX - b.left) / b.width, fy = (e.clientY - b.top) / b.height;
    if (layout === 1) {
      if (tabs.length > 1 && fx < 0.3) return { pane: 0, split: 'left' as const };
      if (tabs.length > 1 && fx > 0.7) return { pane: 1, split: 'right' as const };
      return { pane: 0 };
    }
    if (layout === 2) return { pane: fx < 0.5 ? 0 : 1 };
    return { pane: (fy < 0.5 ? 0 : 2) + (fx < 0.5 ? 0 : 1) };
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const id = dragId, z = zoneAt(e);
    setDragId(null); setDrop(null);
    if (!id) return;
    if (z.split) {
      // One pane → two: the dragged session on the chosen side, the one
      // that was showing (or the next session) on the other.
      const other = (panes[0] && panes[0] !== id ? panes[0] : tabs.find(t => t.id !== id)?.id) ?? null;
      setLayout(2);
      setPanes(z.split === 'left' ? [id, other] : [other, id]);
      setActivePane(z.split === 'left' ? 0 : 1);
    } else {
      setPanes(prev => {
        const next = [...prev];
        const from = next.indexOf(id);
        if (from >= 0 && from !== z.pane) next[from] = next[z.pane];   // swap
        next[z.pane] = id;
        return next;
      });
      setActivePane(z.pane);
    }
    onSetActive(id);
  };

  // Drag the top edge to resize.
  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    const parent = (e.currentTarget.parentElement?.parentElement) as HTMLElement | null;
    const box = parent?.getBoundingClientRect();
    const move = (ev: MouseEvent) => {
      if (!box) return;
      const h = Math.max(140, Math.min(box.bottom - ev.clientY, box.height - 60));
      setHeight(h);
    };
    const up = () => {
      window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up);
      setHeight(h => { try { localStorage.setItem(HEIGHT_KEY, String(Math.round(h))); } catch { /* ignore */ } return h; });
    };
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  };

  // The lab assistant panel sits on the left of the canvas (16 + 560 + 16 px):
  // the drawer starts to its right instead of covering it.
  // It slides up from the bottom edge when the first console opens, back
  // down when the last one closes, and sideways when the chat opens/closes.
  const chatOpen = useAgentStore(s => s.open);
  const { mounted, visible } = usePresence(tabs.length > 0);
  if (!mounted) return null;
  const rects = RECTS[layout];
  const style = full ? undefined : { height: height || '42vh', left: chatOpen ? 592 : 0,
                                     transform: visible ? 'translateY(0)' : 'translateY(100%)' };

  return (
    <div className={`${full ? 'fixed inset-0 z-[60]' : 'absolute bottom-0 left-0 right-0 z-40 plabs-motion'} flex flex-col shadow-2xl`}
      style={{ ...style, background: VSCODE.background, borderTop: `1px solid ${C.border}` }}>
      {!full && <div onMouseDown={startDrag} className="absolute -top-1 left-0 right-0 h-2 cursor-ns-resize z-10" title="Drag to resize" />}
      {/* Tab bar */}
      <div className="h-9 shrink-0 flex items-stretch" style={{ background: C.bar, borderBottom: `1px solid ${C.border}` }}>
        <div className="flex-1 flex items-stretch overflow-x-auto">
          {tabs.map(t => {
            const on = t.id === activeId;
            const pane = panes.indexOf(t.id);
            return (
              <div key={t.id} onClick={() => onSetActive(t.id)}
                draggable onDragStart={e => { e.dataTransfer.setData('text/plain', t.id); e.dataTransfer.effectAllowed = 'move'; setDragId(t.id); }}
                onDragEnd={() => { setDragId(null); setDrop(null); }}
                tabIndex={0} title="Drag onto the console to split · Tab / Shift+Tab here switches sessions"
                onKeyDown={e => { if (e.key === 'Tab') { e.preventDefault(); rotate(e.shiftKey ? -1 : 1); } }}
                className="group flex items-center gap-2 pl-3 pr-2 cursor-pointer text-[12px] whitespace-nowrap select-none outline-none focus-visible:ring-1 focus-visible:ring-[#0078d4]"
                style={{
                  background: on ? VSCODE.background : C.tabIdle, color: on ? C.textOn : C.text,
                  borderTop: `1px solid ${on ? C.accent : 'transparent'}`, borderRight: `1px solid ${C.bar}`,
                }}>
                <span className="w-2 h-2 rounded-full" style={{ background: t.type === 'console' ? '#23d18b' : '#3b8eea' }} />
                <span>{t.nodeName}</span>
                <span style={{ color: '#6e7681' }} className="text-[10px]">{t.type === 'console' ? (t.cli || 'cli') : 'logs'}</span>
                {layout > 1 && pane >= 0 && <span className="text-[9px] px-1 rounded" style={{ background: '#3c3c3c', color: '#cccccc' }}>{pane + 1}</span>}
                <button className={`ml-0.5 rounded p-0.5 hover:bg-[#ffffff1a] ${on ? '' : 'opacity-0 group-hover:opacity-100'}`}
                  onClick={e => { e.stopPropagation(); onClose(t.id); }} title="Close session">
                  <X size={13} />
                </button>
              </div>
            );
          })}
        </div>
        <div className="flex items-center gap-0.5 px-2 shrink-0" style={{ color: '#c5c5c5' }}>
          <span className="text-[10px] mr-2 hidden lg:inline" style={{ color: '#8b949e' }}
            title="Plain Tab stays with the device (command completion)">
            Shift+Tab / Alt+→ next session · drag a tab to split · right-click paste
          </span>
          <ToolBtn title="Single pane" on={layout === 1} onClick={() => changeLayout(1)}><Square size={14} /></ToolBtn>
          <ToolBtn title="Split in two" on={layout === 2} onClick={() => changeLayout(2)}><Columns2 size={15} /></ToolBtn>
          <ToolBtn title="Split in four" on={layout === 4} onClick={() => changeLayout(4)}><Grid2x2 size={15} /></ToolBtn>
          <ToolBtn title={full ? 'Exit full screen' : 'Full screen'} onClick={() => setFull(f => !f)}>
            {full ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </ToolBtn>
          <ToolBtn title="Close all sessions" onClick={() => tabs.forEach(t => onClose(t.id))}><X size={15} /></ToolBtn>
        </div>
      </div>
      {/* Panes — every session stays mounted so its connection stays open. */}
      <div ref={area} className="flex-1 relative min-h-0">
        {rects.map((r, i) => (
          <div key={`frame${i}`} onMouseDown={() => focusPane(i)} className="absolute"
            style={{
              left: `${r.l}%`, top: `${r.t}%`, width: `${r.w}%`, height: `${r.h}%`,
              boxShadow: layout > 1 ? `inset 0 0 0 1px ${i === activePane ? C.accent : C.border}` : undefined,
            }}>
            {!panes[i] && (
              <div className="w-full h-full flex items-center justify-center text-xs" style={{ color: '#6e7681' }}>
                Empty pane — click it, then pick a session tab
              </div>
            )}
          </div>
        ))}
        {dragId && (
          <div className="absolute inset-0 z-30"
            onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; const z = zoneAt(e);
              if (z.pane !== drop?.pane || z.split !== drop?.split) setDrop(z); }}
            onDragLeave={() => setDrop(null)} onDrop={onDrop}>
            {drop && (() => {
              const r = drop.split ? RECTS[2][drop.split === 'left' ? 0 : 1] : rects[drop.pane];
              return (
                <div className="absolute rounded flex items-center justify-center text-xs text-white pointer-events-none"
                  style={{ left: `${r.l}%`, top: `${r.t}%`, width: `${r.w}%`, height: `${r.h}%`,
                           background: '#0078d433', boxShadow: 'inset 0 0 0 2px #0078d4' }}>
                  {drop.split ? `Split — ${tabs.find(t => t.id === dragId)?.nodeName} on the ${drop.split}`
                              : `Show ${tabs.find(t => t.id === dragId)?.nodeName} here`}
                </div>
              );
            })()}
          </div>
        )}
        {tabs.map(t => {
          const p = panes.indexOf(t.id);
          const r = p >= 0 ? rects[p] : rects[0];
          return (
            <div key={t.id} onMouseDown={() => p >= 0 && focusPane(p)} className="absolute"
              style={{
                left: `calc(${r.l}% + ${layout > 1 ? 1 : 0}px)`, top: `calc(${r.t}% + ${layout > 1 ? 1 : 0}px)`,
                width: `calc(${r.w}% - ${layout > 1 ? 2 : 0}px)`, height: `calc(${r.h}% - ${layout > 1 ? 2 : 0}px)`,
                visibility: p >= 0 ? 'visible' : 'hidden',
              }}>
              {layout > 1 && p >= 0 && (
                <div className="absolute top-1 right-3 z-10 text-[10px] px-1.5 rounded pointer-events-none"
                  style={{ background: '#00000080', color: '#cccccc' }}>{t.nodeName}</div>
              )}
              {t.type === 'console'
                ? <LabConsole nodeId={t.nodeId} nodeName={t.nodeName} cli={t.cli}
                    focused={p >= 0 && p === activePane} onRotate={rotate} />
                : <LabLogs nodeId={t.nodeId} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ToolBtn({ title, on, onClick, children }: { title: string; on?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button title={title} onClick={onClick}
      className={`p-1.5 rounded hover:bg-[#ffffff1a] ${on ? 'bg-[#ffffff1f] text-white' : ''}`}>{children}</button>
  );
}

/** Copy text without the async Clipboard API (unavailable over plain http). */
function copyText(text: string): boolean {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

/** A single xterm.js terminal bridged to the backend console WS. */
function LabConsole({ nodeId, nodeName, cli, focused, onRotate }:
  { nodeId: number; nodeName: string; cli?: string; focused: boolean; onRotate: (dir?: 1 | -1) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const rotateRef = useRef(onRotate);
  rotateRef.current = onRotate;
  const currentTopologyId = useLabStore(s => s.currentTopologyId);
  // Paste confirmation: the text (editable) + whether the clipboard was readable.
  const [paste, setPaste] = useState<{ text: string; auto: boolean } | null>(null);
  const [sending, setSending] = useState<{ done: number; total: number } | null>(null);
  const [note, setNote] = useState('');

  useEffect(() => {
    if (!containerRef.current || !currentTopologyId) return;

    const term = new Terminal({
      cursorBlink: true, fontFamily: FONT, fontSize: 14, lineHeight: 1.15,
      theme: VSCODE, convertEol: true, scrollback: 5000, allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    setTimeout(() => { try { fit.fit(); } catch { /* not visible yet */ } }, 30);
    termRef.current = term;

    // Shift+Tab rotates sessions; everything else (incl. Tab) goes to the device.
    // Session switching: Shift+Tab → next, Alt+→ / Alt+← → next / previous.
    // Plain Tab is left for the device's command completion.
    term.attachCustomKeyEventHandler(e => {
      const sw = (e.key === 'Tab' && e.shiftKey && !e.ctrlKey && !e.altKey) ? 1
        : (e.altKey && !e.ctrlKey && !e.shiftKey && e.key === 'ArrowRight') ? 1
        : (e.altKey && !e.ctrlKey && !e.shiftKey && e.key === 'ArrowLeft') ? -1 : 0;
      if (!sw) return true;
      e.preventDefault();
      if (e.type === 'keydown') rotateRef.current(sw as 1 | -1);
      return false;
    });

    const token = localStorage.getItem('auth_token') || '';
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const qs = new URLSearchParams({ token });
    if (cli) qs.set('cli', cli);
    const ws = new WebSocket(`${proto}//${window.location.host}/api/topologies/${currentTopologyId}/lab/console/${nodeId}?${qs}`);
    wsRef.current = ws;
    ws.onopen = () => { try { ws.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } })); } catch { /* closed */ } };
    ws.onmessage = ev => { if (typeof ev.data === 'string') term.write(ev.data); };
    ws.onclose = () => term.write('\r\n\x1b[31m[connection closed]\x1b[0m\r\n');
    ws.onerror = () => term.write('\r\n\x1b[31m[websocket error]\x1b[0m\r\n');
    term.onData(data => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });

    // Multi-line Ctrl+V → the same confirmation as right-click paste.
    const onPaste = (e: ClipboardEvent) => {
      const text = e.clipboardData?.getData('text') ?? '';
      if (/\r|\n/.test(text.replace(/[\r\n]+$/, ''))) {
        e.preventDefault(); e.stopImmediatePropagation();
        setPaste({ text, auto: true });
      }
    };
    containerRef.current.addEventListener('paste', onPaste, true);

    const onResize = () => {
      try {
        fit.fit();
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } }));
      } catch { /* hidden */ }
    };
    const el = containerRef.current;
    const ro = new ResizeObserver(onResize);
    ro.observe(el);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      el.removeEventListener('paste', onPaste, true);
      ro.disconnect();
      try { ws.close(); } catch { /* already closed */ }
      try { term.dispose(); } catch { /* disposed */ }
    };
  }, [nodeId, cli, currentTopologyId]);

  useEffect(() => { if (focused && !paste) termRef.current?.focus(); }, [focused, paste]);

  const flash = (msg: string) => { setNote(msg); setTimeout(() => setNote(''), 1800); };

  // Right-click: copy the selection if there is one, otherwise paste (with confirmation).
  const onContextMenu = async (e: React.MouseEvent) => {
    e.preventDefault();
    const term = termRef.current;
    if (term?.hasSelection()) {
      flash(copyText(term.getSelection()) ? 'Copied' : 'Copy failed');
      term.clearSelection();
      return;
    }
    let text = '';
    let auto = false;
    try {
      if (navigator.clipboard?.readText) { text = await navigator.clipboard.readText(); auto = true; }
    } catch { /* not allowed (plain http) → the dialog asks for Ctrl+V */ }
    setPaste({ text, auto });
  };

  // Send line by line with a short gap so the device's input buffer keeps up.
  const send = async (text: string) => {
    const ws = wsRef.current;
    setPaste(null);
    if (!ws || ws.readyState !== WebSocket.OPEN) { flash('Not connected'); return; }
    const lines = text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n');
    setSending({ done: 0, total: lines.length });
    for (let i = 0; i < lines.length; i++) {
      if (ws.readyState !== WebSocket.OPEN) break;
      ws.send(lines[i] + '\r');
      setSending({ done: i + 1, total: lines.length });
      await new Promise(r => setTimeout(r, lines.length > 1 ? 60 : 0));
    }
    setSending(null);
    termRef.current?.focus();
  };

  const lineCount = paste?.text ? paste.text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n').length : 0;

  return (
    <div className="w-full h-full relative" onContextMenu={onContextMenu}>
      <div ref={containerRef} className="w-full h-full pl-2 pt-1" />
      {note && <div className="absolute top-2 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded text-xs"
        style={{ background: C.accent, color: '#fff' }}>{note}</div>}
      {sending && (
        <div className="absolute bottom-2 right-3 px-2 py-0.5 rounded text-xs" style={{ background: '#3c3c3c', color: '#cccccc' }}>
          Pasting {sending.done}/{sending.total} lines…
        </div>
      )}
      {paste && (
        <div className="absolute inset-0 z-20 flex items-center justify-center p-4" style={{ background: '#00000080' }}
          onMouseDown={e => e.stopPropagation()} onContextMenu={e => e.stopPropagation()}>
          <div className="w-full max-w-xl rounded-md shadow-2xl flex flex-col max-h-full"
            style={{ background: C.bar, border: `1px solid ${C.border}`, color: '#cccccc' }}>
            <div className="px-4 pt-3 pb-2">
              <div className="text-sm font-semibold text-white">Paste to {nodeName}?</div>
              <div className="text-xs mt-1" style={{ color: C.text }}>
                {paste.text
                  ? <>The following <b className="text-white">{lineCount} line{lineCount === 1 ? '' : 's'}</b> will be sent to {nodeName}, one line at a time. Review or edit before sending.</>
                  : <>Press <b className="text-white">Ctrl+V</b> (⌘V) in the box to paste your clipboard here, review it, then send. (The browser only lets the page read the clipboard over https.)</>}
              </div>
            </div>
            <textarea autoFocus value={paste.text} onChange={e => setPaste({ ...paste, text: e.target.value })}
              spellCheck={false} placeholder="Ctrl+V to paste here"
              onKeyDown={e => {
                if (e.key === 'Escape') { setPaste(null); termRef.current?.focus(); }
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && paste.text) send(paste.text);
              }}
              className="mx-4 min-h-[160px] max-h-[50vh] flex-1 p-2 rounded text-[13px] leading-snug outline-none resize-y"
              style={{ background: VSCODE.background, color: VSCODE.foreground, border: `1px solid ${C.border}`, fontFamily: FONT }} />
            <div className="px-4 py-3 flex items-center justify-end gap-2">
              {!paste.auto && <span className="mr-auto text-[11px]" style={{ color: '#6e7681' }}>Ctrl+Enter sends</span>}
              <button onClick={() => { setPaste(null); termRef.current?.focus(); }}
                className="px-3 py-1 rounded text-sm hover:bg-[#ffffff1a]" style={{ border: `1px solid ${C.border}` }}>Cancel</button>
              <button disabled={!paste.text} onClick={() => send(paste.text)}
                className="px-3 py-1 rounded text-sm text-white disabled:opacity-40" style={{ background: C.accent }}>
                Paste{paste.text ? ` ${lineCount} line${lineCount === 1 ? '' : 's'}` : ''}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Log-streaming pane — same WS pattern but one-way (server → client). */
function LabLogs({ nodeId }: { nodeId: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const currentTopologyId = useLabStore(s => s.currentTopologyId);

  useEffect(() => {
    if (!containerRef.current || !currentTopologyId) return;
    const term = new Terminal({
      cursorBlink: false, disableStdin: true, fontFamily: FONT, fontSize: 13,
      theme: VSCODE, convertEol: true, scrollback: 10000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    setTimeout(() => { try { fit.fit(); } catch { /* hidden */ } }, 30);

    const token = localStorage.getItem('auth_token') || '';
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${window.location.host}/api/topologies/${currentTopologyId}/lab/logs/${nodeId}?token=${encodeURIComponent(token)}`);
    ws.onmessage = ev => { if (typeof ev.data === 'string') term.write(ev.data); };
    ws.onclose = () => term.write('\r\n\x1b[33m[log stream ended]\x1b[0m\r\n');
    ws.onerror = () => term.write('\r\n\x1b[31m[websocket error]\x1b[0m\r\n');

    const onResize = () => { try { fit.fit(); } catch { /* hidden */ } };
    const el = containerRef.current;
    const ro = new ResizeObserver(onResize);
    ro.observe(el);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      ro.disconnect();
      try { ws.close(); } catch { /* closed */ }
      try { term.dispose(); } catch { /* disposed */ }
    };
  }, [nodeId, currentTopologyId]);

  return <div ref={containerRef} className="w-full h-full pl-2 pt-1" />;
}

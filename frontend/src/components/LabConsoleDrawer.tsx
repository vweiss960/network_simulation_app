/**
 * Console drawer: device CLIs (SSH / docker exec) and container logs in
 * xterm.js, styled like VS Code's integrated terminal.
 *  - Full screen, resizable height, split into 1 / 2 / 4 panes. Each pane
 *    has its own tabs (like VS Code editor groups); drag tabs between panes.
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
import CaptureView from './CaptureView';

export type ConsoleTab = {
  id: string;        // node-id + kind ("console:42", "logs:42") or "capture:<capture id>"
  type: 'console' | 'logs' | 'capture';
  nodeId: number;    // 0 for a capture
  nodeName: string;  // tab label ("R1 ⇄ R2" for a capture)
  cli?: string;
  captureId?: string;
  linkId?: number;
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

const STRIP = 36;   // px: a pane's tab strip

/** Groups for a layout: tabs of dropped groups merge into the remaining ones;
 *  new groups each take a spare tab from the fullest group. */
function regroup(groups: string[][], active: (string | null)[], l: Layout) {
  let g = groups.map(x => [...x]);
  if (g.length > l) {
    const keep = g.slice(0, l);
    g.slice(l).forEach((tabs, i) => keep[i % l].push(...tabs));
    g = keep;
  }
  while (g.length < l) {
    const donor = g.reduce((best, x, i) => (x.length > g[best].length ? i : best), 0);
    const spare = g[donor].length > 1 ? g[donor].filter(id => id !== active[donor]).pop() : undefined;
    if (spare) g[donor] = g[donor].filter(id => id !== spare);
    g.push(spare ? [spare] : []);
  }
  const a = g.map((tabs, i) => (active[i] && tabs.includes(active[i]!) ? active[i] : tabs[tabs.length - 1] ?? null));
  return { groups: g, active: a };
}

export default function LabConsoleDrawer({ tabs, activeId, onSetActive, onClose }: Props) {
  // Editor groups, VS Code style: each pane has its own tabs.
  const [groups, setGroups] = useState<string[][]>([[]]);
  const [groupActive, setGroupActive] = useState<(string | null)[]>([null]);
  const [activeGroup, setActiveGroup] = useState(0);
  const layout = (groups.length >= 3 ? 4 : groups.length) as Layout;
  const [full, setFull] = useState(false);
  const [height, setHeight] = useState<number>(() => {
    try { return Number(localStorage.getItem(HEIGHT_KEY)) || 0; } catch { return 0; }
  });

  const apply = (g: string[][], a: (string | null)[]) => { setGroups(g); setGroupActive(a); };

  const changeLayout = (l: Layout) => {
    const r = regroup(groups, groupActive, l);
    apply(r.groups, r.active);
    setActiveGroup(i => Math.min(i, l - 1));
  };

  // Keep the groups in step with the sessions: new ones open in the focused
  // pane; closed ones leave (a split whose pane empties collapses).
  useEffect(() => {
    const ids = new Set(tabs.map(t => t.id));
    const placed = new Set(groups.flat());
    const fresh = tabs.map(t => t.id).filter(id => !placed.has(id));
    const gone = groups.some(g => g.some(id => !ids.has(id)));
    if (!fresh.length && !gone) return;
    let g = groups.map(x => x.filter(id => ids.has(id)));
    let a: (string | null)[] = groupActive.map((id, i) => (id && ids.has(id) ? id : g[i][g[i].length - 1] ?? null));
    const target = Math.min(activeGroup, g.length - 1);
    g[target] = [...g[target], ...fresh];
    if (fresh.length) a[target] = fresh[fresh.length - 1];
    // A pane emptied by closing its last session goes away.
    const emptied = groups.map((x, i) => x.length > 0 && g[i].length === 0);
    if (g.length > 1 && emptied.some(Boolean)) {
      const keep = g.map((_, i) => i).filter(i => !emptied[i]);
      g = keep.map(i => g[i]); a = keep.map(i => a[i]);
      if (!g.length) { g = [[]]; a = [null]; }
      if (g.length === 3) { g.push([]); a.push(null); }
      setActiveGroup(i => Math.min(i, g.length - 1));
    }
    apply(g, a);
  }, [tabs]); // eslint-disable-line react-hooks/exhaustive-deps

  // The store's active session: show it in its pane and focus that pane.
  useEffect(() => {
    if (!activeId) return;
    const gi = groups.findIndex(g => g.includes(activeId));
    if (gi < 0) return;
    setActiveGroup(gi);
    if (groupActive[gi] !== activeId) setGroupActive(a => a.map((x, i) => (i === gi ? activeId : x)));
  }, [activeId, groups]); // eslint-disable-line react-hooks/exhaustive-deps

  const show = (gi: number, id: string) => {
    setActiveGroup(gi);
    setGroupActive(a => a.map((x, i) => (i === gi ? id : x)));
    if (id !== activeId) onSetActive(id);
  };
  const focusPane = (gi: number) => {
    setActiveGroup(gi);
    const id = groupActive[gi];
    if (id && id !== activeId) onSetActive(id);
  };

  // Next / previous session across all panes (Shift+Tab, Alt+→/←, or Tab on a tab).
  const rotate = useCallback((dir: 1 | -1 = 1) => {
    const order = groups.flat();
    if (order.length < 2) return;
    const i = order.indexOf(activeId ?? '');
    onSetActive(order[(i + dir + order.length) % order.length]);
  }, [groups, activeId, onSetActive]);

  // Drag a tab: onto another pane (or a position in its tab strip) moves it
  // there; in a single pane, near the left/right edge splits with it on that side.
  const [dragId, setDragId] = useState<string | null>(null);
  type Drop = { pane: number; split?: 'left' | 'right'; before?: string | null };
  const [drop, setDrop] = useState<Drop | null>(null);
  const area = useRef<HTMLDivElement>(null);
  const rects = RECTS[layout];
  const zoneAt = (e: React.DragEvent): Drop => {
    const b = area.current!.getBoundingClientRect();
    const fx = (e.clientX - b.left) / b.width, fy = (e.clientY - b.top) / b.height;
    if (layout === 1 && tabs.length > 1 && e.clientY - b.top > STRIP) {
      if (fx < 0.3) return { pane: 0, split: 'left' };
      if (fx > 0.7) return { pane: 1, split: 'right' };
    }
    const pane = Math.max(0, rects.findIndex(r => fx * 100 >= r.l && fx * 100 <= r.l + r.w && fy * 100 >= r.t && fy * 100 <= r.t + r.h));
    // Over a tab strip: insert before the tab under the pointer.
    const stripTop = b.top + (rects[pane].t / 100) * b.height;
    if (e.clientY - stripTop <= STRIP) {
      const els = Array.from(area.current!.querySelectorAll<HTMLElement>(`[data-tab-group="${pane}"]`));
      const at = els.find(el => { const r = el.getBoundingClientRect(); return e.clientX < r.left + r.width / 2; });
      return { pane, before: at?.dataset.tabId ?? null };
    }
    return { pane };
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const id = dragId, z = zoneAt(e);
    setDragId(null); setDrop(null);
    if (!id) return;
    const rest = groups.map(g => g.filter(x => x !== id));
    const restActive = groupActive.map((x, i) => (x === id ? rest[i][rest[i].length - 1] ?? null : x));
    if (z.split) {
      const others = rest[0];
      if (!others.length) return;
      const g = z.split === 'left' ? [[id], others] : [others, [id]];
      const a = z.split === 'left' ? [id, restActive[0]] : [restActive[0], id];
      apply(g, a);
      setActiveGroup(z.split === 'left' ? 0 : 1);
      onSetActive(id);
      return;
    }
    const target = [...rest[z.pane]];
    const at = z.before ? target.indexOf(z.before) : -1;
    if (at >= 0) target.splice(at, 0, id); else target.push(id);
    let g = rest.map((x, i) => (i === z.pane ? target : x));
    let a = restActive.map((x, i) => (i === z.pane ? id : x));
    // Moving the last tab out of a pane closes that pane.
    const from = groups.findIndex(x => x.includes(id));
    if (from !== z.pane && g[from].length === 0 && g.length > 1) {
      const keep = g.map((_, i) => i).filter(i => i !== from);
      const newPane = keep.indexOf(z.pane);
      g = keep.map(i => g[i]); a = keep.map(i => a[i]);
      if (g.length === 3) { g.push([]); a.push(null); }
      apply(g, a); setActiveGroup(newPane);
    } else {
      apply(g, a); setActiveGroup(z.pane);
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
  const capturing = useLabStore(s => s.capturing);
  const { mounted, visible } = usePresence(tabs.length > 0);
  if (!mounted) return null;
  const style = full ? undefined : { height: height || '42vh', left: chatOpen ? 592 : 0,
                                     transform: visible ? 'translateY(0)' : 'translateY(100%)' };
  const byId = new Map(tabs.map(t => [t.id, t]));
  const groupOf = (id: string) => groups.findIndex(g => g.includes(id));
  // The layout / full-screen / close-all buttons sit in the top-right pane's strip.
  const toolsPane = layout === 1 ? 0 : 1;
  const split = layout > 1;

  const tools = (
    <div className="flex items-center gap-0.5 px-2 shrink-0" style={{ color: '#c5c5c5' }}>
      <span className="text-[10px] mr-2 hidden xl:inline" style={{ color: '#8b949e' }}
        title="Plain Tab stays with the device (command completion)">
        Shift+Tab next session · drag tabs between panes · right-click paste
      </span>
      <ToolBtn title="Single pane" on={layout === 1} onClick={() => changeLayout(1)}><Square size={14} /></ToolBtn>
      <ToolBtn title="Split in two" on={layout === 2} onClick={() => changeLayout(2)}><Columns2 size={15} /></ToolBtn>
      <ToolBtn title="Split in four" on={layout === 4} onClick={() => changeLayout(4)}><Grid2x2 size={15} /></ToolBtn>
      <ToolBtn title={full ? 'Exit full screen' : 'Full screen'} onClick={() => setFull(f => !f)}>
        {full ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
      </ToolBtn>
      <ToolBtn title="Close all sessions" onClick={() => tabs.forEach(t => onClose(t.id))}><X size={15} /></ToolBtn>
    </div>
  );

  return (
    <div className={`plabs-console ${full ? 'fixed inset-0 z-[60]' : 'absolute bottom-0 left-0 right-0 z-40 plabs-motion'} flex flex-col shadow-2xl`}
      style={{ ...style, background: VSCODE.background, borderTop: `1px solid ${C.border}` }}>
      {!full && <div onMouseDown={startDrag} className="absolute -top-1 left-0 right-0 h-2 cursor-ns-resize z-10" title="Drag to resize" />}
      {/* Panes, each with its own tab strip. Sessions are rendered once below
          (every one stays mounted so its connection stays open). */}
      <div ref={area} className="flex-1 relative min-h-0">
        {rects.map((r, gi) => {
          const focused = split && gi === activeGroup;
          return (
            <div key={`pane${gi}`} onMouseDown={() => focusPane(gi)} className="absolute flex flex-col"
              style={{ left: `${r.l}%`, top: `${r.t}%`, width: `${r.w}%`, height: `${r.h}%`,
                       borderLeft: split && r.l > 0 ? `1px solid ${C.border}` : undefined,
                       borderTop: split && r.t > 0 ? `1px solid ${C.border}` : undefined }}>
              <div className="shrink-0 flex items-stretch" style={{ height: STRIP, background: C.bar, borderBottom: `1px solid ${C.border}` }}>
                <div className="flex-1 min-w-0 flex items-stretch overflow-x-auto">
                  {(groups[gi] ?? []).map(id => {
                    const t = byId.get(id);
                    if (!t) return null;
                    const on = groupActive[gi] === id;
                    const lit = on && (!split || gi === activeGroup);
                    return (
                      <div key={id} data-tab-group={gi} data-tab-id={id}
                        onClick={e => { e.stopPropagation(); show(gi, id); }}
                        draggable onDragStart={e => { e.dataTransfer.setData('text/plain', id); e.dataTransfer.effectAllowed = 'move'; setDragId(id); }}
                        onDragEnd={() => { setDragId(null); setDrop(null); }}
                        tabIndex={0} title="Drag to another pane, or to a side of the console to split · Tab here switches sessions"
                        onKeyDown={e => { if (e.key === 'Tab') { e.preventDefault(); rotate(e.shiftKey ? -1 : 1); } }}
                        className="group flex items-center gap-2 pl-3 pr-2 cursor-pointer text-[12px] whitespace-nowrap select-none outline-none focus-visible:ring-1 focus-visible:ring-[#0078d4]"
                        style={{
                          background: on ? VSCODE.background : C.tabIdle, color: on ? C.textOn : C.text,
                          borderTop: `1px solid ${lit ? C.accent : 'transparent'}`, borderRight: `1px solid ${C.bar}`,
                          opacity: dragId === id ? 0.5 : 1,
                          boxShadow: drop && !drop.split && drop.pane === gi && drop.before === id ? `inset 2px 0 0 ${C.accent}` : undefined,
                        }}>
                        {t.type === 'capture'
                          ? <span className={`w-2 h-2 rounded-full ${capturing[t.captureId!] ? 'animate-pulse' : ''}`}
                              style={{ background: capturing[t.captureId!] ? '#f14c4c' : '#6e7681' }} />
                          : <span className="w-2 h-2 rounded-full" style={{ background: t.type === 'console' ? '#23d18b' : '#3b8eea' }} />}
                        <span>{t.nodeName}</span>
                        <span style={{ color: '#6e7681' }} className="text-[10px]">{t.type === 'console' ? (t.cli || 'cli') : t.type}</span>
                        <button className={`ml-0.5 rounded p-0.5 hover:bg-[#ffffff1a] ${on ? '' : 'opacity-0 group-hover:opacity-100'}`}
                          onClick={e => { e.stopPropagation(); onClose(id); }} title="Close session">
                          <X size={13} />
                        </button>
                      </div>
                    );
                  })}
                </div>
                {gi === toolsPane && tools}
              </div>
              <div className="flex-1 min-h-0 relative"
                style={{ boxShadow: focused ? `inset 0 0 0 1px ${C.accent}55` : undefined }}>
                {!(groups[gi] ?? []).length && (
                  <div className="absolute inset-0 flex items-center justify-center text-xs" style={{ color: '#6e7681' }}>
                    Empty pane — drag a session tab here
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {dragId && (
          <div className="absolute inset-0 z-30"
            onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; const z = zoneAt(e);
              if (z.pane !== drop?.pane || z.split !== drop?.split || z.before !== drop?.before) setDrop(z); }}
            onDragLeave={() => setDrop(null)} onDrop={onDrop}>
            {drop && drop.before === undefined && (() => {
              const r = drop.split ? RECTS[2][drop.split === 'left' ? 0 : 1] : rects[drop.pane];
              const name = byId.get(dragId)?.nodeName;
              const same = !drop.split && groupOf(dragId) === drop.pane;
              if (same) return null;
              return (
                <div className="absolute rounded flex items-center justify-center text-xs text-white pointer-events-none"
                  style={{ left: `${r.l}%`, top: `${r.t}%`, width: `${r.w}%`, height: `${r.h}%`,
                           background: '#0078d433', boxShadow: 'inset 0 0 0 2px #0078d4' }}>
                  {drop.split ? `Split — ${name} on the ${drop.split}` : `Move ${name} to this pane`}
                </div>
              );
            })()}
          </div>
        )}
        {tabs.map(t => {
          const gi = groupOf(t.id);
          const r = rects[Math.max(0, gi)];
          const shown = gi >= 0 && groupActive[gi] === t.id;
          return (
            <div key={t.id} onMouseDown={() => gi >= 0 && focusPane(gi)} className="absolute"
              style={{
                left: `calc(${r.l}% + ${split && r.l > 0 ? 1 : 0}px)`, top: `calc(${r.t}% + ${STRIP + (split && r.t > 0 ? 1 : 0)}px)`,
                width: `calc(${r.w}% - ${split && r.l > 0 ? 1 : 0}px)`, height: `calc(${r.h}% - ${STRIP + (split && r.t > 0 ? 1 : 0)}px)`,
                visibility: shown ? 'visible' : 'hidden',
              }}>
              {t.type === 'console'
                ? <LabConsole nodeId={t.nodeId} nodeName={t.nodeName} cli={t.cli}
                    focused={shown && (!split || gi === activeGroup)} onRotate={rotate} />
                : t.type === 'capture' ? <CaptureView captureId={t.captureId!} />
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

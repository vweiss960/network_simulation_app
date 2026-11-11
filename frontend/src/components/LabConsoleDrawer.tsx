import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';
import { useStore } from '../stores/useStore';

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

export default function LabConsoleDrawer({ tabs, activeId, onSetActive, onClose }: Props) {
  if (tabs.length === 0) return null;
  return (
    <div className="absolute bottom-0 left-0 right-0 z-40 bg-surface-900 border-t-2 border-emerald-700/60 shadow-2xl flex flex-col" style={{ height: '40vh' }}>
      {/* Tab bar */}
      <div className="h-8 bg-surface-800 border-b border-surface-700 flex items-center px-2 gap-1 shrink-0 overflow-x-auto">
        {tabs.map(t => (
          <div
            key={t.id}
            className={`flex items-center gap-1 px-3 h-7 rounded-t cursor-pointer text-[11px] whitespace-nowrap ${
              t.id === activeId
                ? 'bg-surface-900 text-emerald-300 border-t border-l border-r border-emerald-700/60'
                : 'text-surface-400 hover:text-surface-200 hover:bg-surface-700/50'
            }`}
            onClick={() => onSetActive(t.id)}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${t.type === 'console' ? 'bg-emerald-400' : 'bg-blue-400'}`} />
            <span>{t.nodeName}</span>
            <span className="text-[9px] text-surface-500">{t.type === 'console' ? (t.cli || 'sh') : 'logs'}</span>
            <button
              className="ml-1 text-surface-500 hover:text-red-400"
              onClick={(e) => { e.stopPropagation(); onClose(t.id); }}
              title="Close tab"
            >
              <X size={11} />
            </button>
          </div>
        ))}
      </div>
      {/* Panes — keep all of them mounted so each WS stays open as user switches tabs */}
      <div className="flex-1 relative bg-black">
        {tabs.map(t => (
          <div
            key={t.id}
            className="absolute inset-0"
            style={{ visibility: t.id === activeId ? 'visible' : 'hidden' }}
          >
            {t.type === 'console'
              ? <LabConsole nodeId={t.nodeId} cli={t.cli} />
              : <LabLogs nodeId={t.nodeId} />}
          </div>
        ))}
      </div>
    </div>
  );
}

/** A single xterm.js terminal bridged to the backend console WS. */
function LabConsole({ nodeId, cli }: { nodeId: number; cli?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const currentTopologyId = useStore(s => s.currentTopologyId);

  useEffect(() => {
    if (!containerRef.current || !currentTopologyId) return;

    const term = new Terminal({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 13,
      theme: { background: '#000000', foreground: '#e5e7eb' },
      convertEol: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    setTimeout(() => { try { fit.fit(); } catch {} }, 30);
    termRef.current = term;
    fitRef.current = fit;

    const token = localStorage.getItem('auth_token') || '';
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const qs = new URLSearchParams({ token });
    if (cli) qs.set('cli', cli);
    const url = `${proto}//${window.location.host}/api/topologies/${currentTopologyId}/lab/console/${nodeId}?${qs.toString()}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      // tell the backend our terminal size
      try {
        ws.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } }));
      } catch {}
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') term.write(ev.data);
    };
    ws.onclose = () => term.write('\r\n\x1b[31m[connection closed]\x1b[0m\r\n');
    ws.onerror = () => term.write('\r\n\x1b[31m[websocket error]\x1b[0m\r\n');

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    });

    const onResize = () => {
      try {
        fit.fit();
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } }));
        }
      } catch {}
    };
    const ro = new ResizeObserver(onResize);
    ro.observe(containerRef.current);
    window.addEventListener('resize', onResize);

    return () => {
      window.removeEventListener('resize', onResize);
      ro.disconnect();
      try { ws.close(); } catch {}
      try { term.dispose(); } catch {}
    };
  }, [nodeId, cli, currentTopologyId]);

  return <div ref={containerRef} className="w-full h-full p-1" />;
}

/** Log-streaming pane — same WS pattern but one-way (server → client). */
function LabLogs({ nodeId }: { nodeId: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const currentTopologyId = useStore(s => s.currentTopologyId);

  useEffect(() => {
    if (!containerRef.current || !currentTopologyId) return;
    const term = new Terminal({
      cursorBlink: false,
      disableStdin: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 12,
      theme: { background: '#0a0a0a', foreground: '#d1d5db' },
      convertEol: true,
      scrollback: 10000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    setTimeout(() => { try { fit.fit(); } catch {} }, 30);
    termRef.current = term;
    fitRef.current = fit;

    const token = localStorage.getItem('auth_token') || '';
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/api/topologies/${currentTopologyId}/lab/logs/${nodeId}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (ev) => { if (typeof ev.data === 'string') term.write(ev.data); };
    ws.onclose = () => term.write('\r\n\x1b[33m[log stream ended]\x1b[0m\r\n');
    ws.onerror = () => term.write('\r\n\x1b[31m[websocket error]\x1b[0m\r\n');

    const onResize = () => { try { fit.fit(); } catch {} };
    const ro = new ResizeObserver(onResize);
    ro.observe(containerRef.current);
    window.addEventListener('resize', onResize);

    return () => {
      window.removeEventListener('resize', onResize);
      ro.disconnect();
      try { ws.close(); } catch {}
      try { term.dispose(); } catch {}
    };
  }, [nodeId, currentTopologyId]);

  return <div ref={containerRef} className="w-full h-full p-1" />;
}

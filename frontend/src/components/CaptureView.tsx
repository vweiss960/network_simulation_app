/**
 * Live packet capture on a lab link, shown as a console-drawer tab: a
 * Wireshark-style packet list (streamed over a WebSocket, virtualized), a
 * display filter (Wireshark syntax, run by tshark on the server), the
 * protocol tree + hex of the selected packet, Stop and a .pcapng download.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownToLine, ChevronDown, ChevronRight, Download, Loader2, RotateCw, Square } from 'lucide-react';
import { api, downloadCapture, type CaptureInfo, type CaptureRow, type PacketDetail } from '../utils/api';
import { useLabStore } from '../stores/useLabStore';

const ROW = 20;
const FONT = "'Cascadia Code', 'JetBrains Mono', Menlo, Consolas, monospace";
const K = {
  bg: '#1e1e1e', bar: '#252526', border: '#3c3c3c', text: '#cccccc', dim: '#8b949e', head: '#2d2d2d',
  sel: '#04395e', selBorder: '#0078d4',
};
// Row tints by protocol, in the spirit of Wireshark's colouring rules.
const TINT: [RegExp, string][] = [
  [/^(ISIS|OSPF|BGP|LDP|RSVP|PIM|IGMP|MLD|EIGRP|RIP|VRRP|HSRP|BFD)/, '#3a3417'],   // routing / control plane
  [/^(ARP|LLDP|CDP|LACP|STP|RSTP|MSTP|LOOP|CFM|SLOW)/, '#2b2f20'],                  // L2 / link
  [/^(ICMP|ICMPv6)/, '#3a2433'],
  [/^(TCP|SSH|TELNET|HTTP|TLS|NETCONF|SSL)/, '#2a2540'],
  [/^(UDP|DNS|NTP|SNMP|SYSLOG|GTP|VXLAN|DHCP|BOOTP)/, '#1c2c3d'],
  [/^(MPLS|SRv6|IPv6)/, '#1d3330'],
];
const tint = (p: string) => TINT.find(([re]) => re.test(p.toUpperCase()))?.[1];
const bytes = (n: number) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} kB` : `${(n / 1048576).toFixed(1)} MB`;
// Columns: No, Time, Source, Destination, Protocol, Length, Info
const COLS = '64px 96px minmax(120px,1fr) minmax(120px,1fr) 88px 64px minmax(200px,3fr)';

export default function CaptureView({ captureId }: { captureId: string }) {
  const tid = useLabStore(s => s.currentTopologyId)!;
  const setCapturing = useLabStore(s => s.setCapturing);
  const openCapture = useLabStore(s => s.openCapture);
  const [info, setInfo] = useState<CaptureInfo | null>(null);
  const rows = useRef<CaptureRow[]>([]);
  const [version, setVersion] = useState(0);         // bumps when rows arrive
  const [gone, setGone] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  const [follow, setFollow] = useState(true);

  // --- live rows ------------------------------------------------------------
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let raf = 0;
    const connect = () => {
      const token = localStorage.getItem('auth_token') || '';
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${window.location.host}/api/topologies/${tid}/lab/captures/${captureId}/stream?token=${encodeURIComponent(token)}`);
      rows.current = [];                               // the server replays everything on connect
      ws.onmessage = ev => {
        const m = JSON.parse(ev.data);
        if (m.error) { setGone(m.error); return; }
        if (m.rows?.length) {
          rows.current.push(...m.rows);
          if (!raf) raf = requestAnimationFrame(() => { raf = 0; setVersion(v => v + 1); });
        }
        if (m.capture) {
          setInfo(m.capture);
          setCapturing(captureId, m.capture.state === 'running' ? m.capture.link_id : null);
        }
      };
      ws.onclose = () => {
        if (closed) return;
        // Still running (a network blip / backend restart)? Try again.
        api.listCaptures(tid).then(cs => {
          const c = cs.find(x => x.id === captureId);
          if (!c) { setGone('This capture is no longer on the server (the backend restarted).'); setCapturing(captureId, null); }
          else { setInfo(c); if (c.state === 'running' && !closed) setTimeout(connect, 1000); }
        }).catch(() => { if (!closed) setTimeout(connect, 2000); });
      };
    };
    connect();
    return () => { closed = true; cancelAnimationFrame(raf); ws?.close(); };
  }, [tid, captureId, setCapturing]);

  // --- display filter -------------------------------------------------------
  const [filterText, setFilterText] = useState('');
  const [applied, setApplied] = useState('');
  const [matches, setMatches] = useState<Set<number> | null>(null);
  const [filterErr, setFilterErr] = useState('');
  const [filtering, setFiltering] = useState(false);
  const checkedUpTo = useRef(0);
  const seq = useRef(0);          // only the latest request's answer counts
  const runFilter = useCallback(async (f: string) => {
    const mine = ++seq.current;
    if (!f) { setMatches(null); setFilterErr(''); setApplied(''); setFiltering(false); return; }
    setFiltering(true);
    const upTo = rows.current.length ? rows.current[rows.current.length - 1][0] : 0;
    try {
      const r = await api.captureMatch(tid, captureId, f);
      if (mine !== seq.current) return;
      setMatches(new Set(r.numbers)); setFilterErr(''); setApplied(f); checkedUpTo.current = upTo;
    } catch (e) {
      if (mine !== seq.current) return;
      setFilterErr((e as Error).message); setMatches(null); setApplied('');
    } finally { if (mine === seq.current) setFiltering(false); }
  }, [tid, captureId]);
  // While capturing, re-run the filter over new packets every couple of seconds.
  const running = info?.state === 'running';
  useEffect(() => {
    if (!applied || !running) return;
    const t = setInterval(() => {
      const last = rows.current[rows.current.length - 1]?.[0] ?? 0;
      if (last > checkedUpTo.current) runFilter(applied);
    }, 2000);
    return () => clearInterval(t);
  }, [applied, running, runFilter]);

  const visible = useMemo(() => {
    void version;
    if (!matches) return rows.current;
    return rows.current.filter(r => matches.has(r[0]));
  }, [version, matches]);

  // --- virtualized list -----------------------------------------------------
  const list = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState({ top: 0, height: 300 });
  useEffect(() => {
    const el = list.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setScroll(s => ({ ...s, height: el.clientHeight })));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const el = list.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [visible.length, follow]);
  const first = Math.max(0, Math.floor(scroll.top / ROW) - 10);
  const last = Math.min(visible.length, Math.ceil((scroll.top + scroll.height) / ROW) + 10);

  // --- selected packet --------------------------------------------------------
  const [detail, setDetail] = useState<PacketDetail | null>(null);
  const [detailErr, setDetailErr] = useState('');
  useEffect(() => {
    if (selected === null) { setDetail(null); return; }
    let live = true;
    setDetailErr('');
    api.capturePacket(tid, captureId, selected)
      .then(d => { if (live) setDetail(d); })
      .catch(e => { if (live) { setDetail(null); setDetailErr((e as Error).message); } });
    return () => { live = false; };
  }, [tid, captureId, selected]);
  const moveSelection = (delta: number) => {
    const i = visible.findIndex(r => r[0] === selected);
    const next = visible[Math.max(0, Math.min(visible.length - 1, (i < 0 ? visible.length : i) + delta))];
    if (!next) return;
    setSelected(next[0]); setFollow(false);
    const el = list.current;
    const idx = visible.indexOf(next);
    if (el && (idx * ROW < el.scrollTop || (idx + 1) * ROW > el.scrollTop + el.clientHeight)) el.scrollTop = idx * ROW - el.clientHeight / 2;
  };

  const [busy, setBusy] = useState(false);
  const stop = async () => {
    setBusy(true);
    try { setInfo(await api.stopCapture(tid, captureId)); } finally { setBusy(false); }
  };
  const total = info?.packets ?? rows.current.length;

  return (
    <div className="w-full h-full flex flex-col text-[12px]" style={{ background: K.bg, color: K.text }}>
      {/* Toolbar */}
      <div className="h-9 shrink-0 flex items-center gap-2 px-2" style={{ background: K.bar, borderBottom: `1px solid ${K.border}` }}>
        {running
          ? <span className="flex items-center gap-1.5 text-[#f14c4c] font-medium"><span className="w-2 h-2 rounded-full bg-[#f14c4c] animate-pulse" />Capturing</span>
          : <span className="flex items-center gap-1.5" style={{ color: K.dim }}><span className="w-2 h-2 rounded-full bg-[#6e7681]" />{info?.state === 'error' ? 'Ended' : 'Stopped'}</span>}
        {info && <span className="truncate" style={{ color: K.dim }} title="The capture point: one end of the cable. Both directions are seen.">
          on {info.side}{info.filter && <> · <span className="text-[#dcdcaa]">{info.filter}</span></>}</span>}
        <span className="shrink-0 tabular-nums" style={{ color: K.dim }}>
          · {total.toLocaleString()} packets{matches ? ` (${visible.length.toLocaleString()} shown)` : ''}{info ? ` · ${bytes(info.bytes)}` : ''}
        </span>
        <form className="flex-1 min-w-[160px] max-w-[520px] ml-auto relative" onSubmit={e => { e.preventDefault(); runFilter(filterText.trim()); }}>
          <input value={filterText} onChange={e => setFilterText(e.target.value)} spellCheck={false}
            onKeyDown={e => { if (e.key === 'Escape') { setFilterText(''); runFilter(''); } }}
            placeholder="Display filter (Wireshark syntax) — isis, bgp, icmp, ip.addr == 10.0.0.1 …  Enter applies"
            title={filterErr || 'Wireshark display filter. Enter applies, Esc clears.'}
            className="w-full h-6 px-2 rounded outline-none text-[12px]"
            style={{ fontFamily: FONT, color: '#e0e0e0', border: `1px solid ${filterErr ? '#f14c4c' : applied ? '#23d18b' : K.border}`,
                     background: filterErr ? '#3a1d1d' : applied ? '#17301f' : '#2a2a2a' }} />
          {filtering && <Loader2 size={12} className="absolute right-2 top-1.5 animate-spin" style={{ color: K.dim }} />}
        </form>
        <IconBtn title={follow ? 'Following new packets (click to stop)' : 'Follow new packets'} on={follow}
          onClick={() => setFollow(f => !f)}><ArrowDownToLine size={14} /></IconBtn>
        {running
          ? <button onClick={stop} disabled={busy} title="Stop capturing (the packets stay here and downloadable)"
              className="flex items-center gap-1 h-6 px-2 rounded text-white disabled:opacity-50" style={{ background: '#a1260d' }}>
              <Square size={11} fill="currentColor" /> Stop
            </button>
          : info && <button onClick={() => openCapture(info.link_id, info.filter)} title="Start a new capture on this link"
              className="flex items-center gap-1 h-6 px-2 rounded hover:bg-[#ffffff1a]" style={{ border: `1px solid ${K.border}` }}>
              <RotateCw size={12} /> New capture
            </button>}
        <button onClick={() => downloadCapture(tid, captureId).catch(e => alert((e as Error).message))} disabled={!info}
          title="Save as .pcapng to open in Wireshark (works while capturing too)"
          className="flex items-center gap-1 h-6 px-2 rounded text-white disabled:opacity-40" style={{ background: '#0e639c' }}>
          <Download size={12} /> pcapng
        </button>
      </div>
      {(gone || info?.note || filterErr) && (
        <div className="px-3 py-1 text-[11px] shrink-0" style={{ background: '#2d2a1a', color: '#e2c08d', borderBottom: `1px solid ${K.border}` }}>
          {filterErr ? `Display filter: ${filterErr}` : gone || info?.note}
        </div>
      )}

      {/* Packet list */}
      <div className="grid shrink-0 px-0 font-medium select-none" style={{ gridTemplateColumns: COLS, background: K.head, borderBottom: `1px solid ${K.border}`, color: K.dim }}>
        {['No.', 'Time', 'Source', 'Destination', 'Protocol', 'Length', 'Info'].map(h => <div key={h} className="px-2 py-0.5 truncate">{h}</div>)}
      </div>
      <div ref={list} tabIndex={0} className="relative overflow-auto outline-none"
        style={{ flex: selected !== null ? '1 1 45%' : '1 1 100%', fontFamily: FONT, minHeight: ROW * 3 }}
        onScroll={e => {
          const el = e.currentTarget;
          setScroll({ top: el.scrollTop, height: el.clientHeight });
          const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < ROW * 2;
          if (atEnd !== follow) setFollow(atEnd);
        }}
        onKeyDown={e => {
          if (e.key === 'ArrowDown') { e.preventDefault(); moveSelection(1); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); moveSelection(-1); }
          else if (e.key === 'Escape') setSelected(null);
        }}>
        {visible.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center" style={{ color: K.dim, fontFamily: 'inherit' }}>
            {running ? (matches ? 'No packets match the filter yet.' : 'Waiting for packets…') : matches ? 'No packets match the filter.' : 'No packets were captured.'}
          </div>
        )}
        <div style={{ height: visible.length * ROW, position: 'relative' }}>
          {visible.slice(first, last).map((r, i) => {
            const sel = r[0] === selected;
            return (
              <div key={r[0]} onClick={() => { setSelected(r[0]); setFollow(false); }}
                className="grid absolute left-0 right-0 cursor-default hover:brightness-125"
                style={{ top: (first + i) * ROW, height: ROW, lineHeight: `${ROW}px`, gridTemplateColumns: COLS,
                         background: sel ? K.sel : tint(r[4]) ?? 'transparent',
                         boxShadow: sel ? `inset 0 0 0 1px ${K.selBorder}` : undefined, color: sel ? '#fff' : K.text }}>
                <div className="px-2 truncate text-right tabular-nums" style={{ color: sel ? '#fff' : K.dim }}>{r[0]}</div>
                <div className="px-2 truncate tabular-nums">{r[1].toFixed(6)}</div>
                <div className="px-2 truncate" title={r[2]}>{r[2]}</div>
                <div className="px-2 truncate" title={r[3]}>{r[3]}</div>
                <div className="px-2 truncate">{r[4]}</div>
                <div className="px-2 truncate text-right tabular-nums">{r[5]}</div>
                <div className="px-2 truncate" title={r[6]}>{r[6]}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Selected packet: protocol tree + hex */}
      {selected !== null && (
        <div className="flex min-h-0" style={{ flex: '1 1 55%', borderTop: `1px solid ${K.border}` }}>
          <div className="flex-[3] min-w-0 overflow-auto py-1" style={{ fontFamily: FONT }}>
            {detail ? <Tree key={detail.number} lines={detail.tree} />
              : <div className="px-3 py-2" style={{ color: detailErr ? '#f14c4c' : K.dim }}>{detailErr || 'Decoding…'}</div>}
          </div>
          <pre className="flex-[2] min-w-0 overflow-auto m-0 px-3 py-1 text-[11.5px]"
            style={{ fontFamily: FONT, color: '#9cdcfe', borderLeft: `1px solid ${K.border}`, background: '#1a1a1a' }}>
            {detail?.hex}
          </pre>
        </div>
      )}
    </div>
  );
}

function IconBtn({ title, on, onClick, children }: { title: string; on?: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button title={title} onClick={onClick}
    className={`h-6 w-6 flex items-center justify-center rounded hover:bg-[#ffffff1a] ${on ? 'bg-[#ffffff1f] text-white' : ''}`}
    style={{ color: on ? '#fff' : K.dim }}>{children}</button>;
}

type TNode = { text: string; children: TNode[] };

/** tshark -V output → a collapsible tree (4 spaces per level). Protocol
 *  layers start collapsed except the innermost one, like Wireshark showing
 *  the interesting part. */
function Tree({ lines }: { lines: string[] }) {
  const roots = useMemo(() => {
    const out: TNode[] = [];
    const stack: { depth: number; node: TNode }[] = [];
    for (const raw of lines) {
      const depth = Math.floor((raw.length - raw.trimStart().length) / 4);
      const node = { text: raw.trim(), children: [] };
      while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
      (stack.length ? stack[stack.length - 1].node.children : out).push(node);
      stack.push({ depth, node });
    }
    return out;
  }, [lines]);
  return <div>{roots.map((n, i) => <TreeNode key={i} node={n} depth={0} initiallyOpen={i === roots.length - 1} />)}</div>;
}

function TreeNode({ node, depth, initiallyOpen }: { node: TNode; depth: number; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const has = node.children.length > 0;
  return (
    <>
      <div onClick={() => has && setOpen(o => !o)}
        className={`flex items-start gap-0.5 pr-3 whitespace-pre ${has ? 'cursor-pointer hover:bg-[#2a2d2e]' : ''}`}
        style={{ paddingLeft: 6 + depth * 16, color: depth === 0 ? '#e0e0e0' : K.text, lineHeight: '19px' }}>
        <span className="w-3.5 shrink-0 mt-[3px]" style={{ color: K.dim }}>
          {has && (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />)}
        </span>
        <span className={depth === 0 ? 'font-semibold' : ''}>{node.text}</span>
      </div>
      {open && node.children.map((c, i) => <TreeNode key={i} node={c} depth={depth + 1} initiallyOpen={depth >= 0} />)}
    </>
  );
}

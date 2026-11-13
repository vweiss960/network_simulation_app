import { useEffect, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { useAuthStore } from '../stores/useAuthStore';
import { DEVICE_ICONS } from './canvas/icons';

/** The fan-of-rays mark (same artwork as the app header). */
export function PhaneroLogo({ className = '' }: { className?: string }) {
  return (
    <svg width="34" height="20" viewBox="0 0 24 14" fill="none" className={`text-primary-400 ${className}`}>
      <path d="M12 13 Q10.2 12.2, 2.2 11.3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M12 13 Q9.2 8.5, 5.6 5.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M12 13 Q11.7 7, 11.5 1.2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M12 13 Q13.2 6.8, 14.8 2.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M12 13 Q15 8.8, 19 5.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M12 13 Q15.2 10, 20.5 7.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M12 13 Q15.5 12.4, 22 11.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

/** Logo + wordmark + tagline (the current branding), for a light background. */
function Brand() {
  return (
    <div>
      <div className="flex items-center gap-3">
        <PhaneroLogo className="!text-primary-500" />
        <span className="text-sm font-semibold tracking-[0.2em] uppercase text-slate-900">
          Phanero <span className="text-emerald-600">Labs</span>
        </span>
      </div>
      <p className="mt-2.5 text-sm text-slate-600">
        see what others <span className="text-slate-900 font-semibold">cannot.</span>
      </p>
    </div>
  );
}

// ── Whiteboard sketch ───────────────────────────────────────────────────
// A hand-drawn network on a dotted whiteboard: lines with a little wobble,
// the canvas's own network symbols, and margin notes in handwriting. A
// turbulence filter roughens everything slightly. Lines draw themselves in
// once on load (skipped for reduced motion); otherwise it's static.

/** Deterministic PRNG so the sketch is the same on every load. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(11);
const j = (n: number) => (rnd() * 2 - 1) * n;

/** A slightly wobbly straight line, as drawn with a marker. */
function wobbleLine(x1: number, y1: number, x2: number, y2: number, amp = 2.2): string {
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len, ny = dx / len;
  const o1 = j(amp), o2 = j(amp);
  const c1 = [x1 + dx * 0.33 + nx * o1, y1 + dy * 0.33 + ny * o1];
  const c2 = [x1 + dx * 0.66 + nx * o2, y1 + dy * 0.66 + ny * o2];
  return `M${x1 + j(1)},${y1 + j(1)} C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${x2 + j(1)},${y2 + j(1)}`;
}

/** A loose hand-drawn ellipse (overshoots its start, like a quick loop). */
function sketchEllipse(cx: number, cy: number, rx: number, ry: number): string {
  const pts: string[] = [];
  const start = rnd() * Math.PI * 2, turns = 1.08, steps = 36;
  for (let i = 0; i <= steps; i++) {
    const a = start + (i / steps) * Math.PI * 2 * turns;
    const k = 1 + j(0.035) + (i / steps) * 0.05;
    pts.push(`${(cx + Math.cos(a) * rx * k).toFixed(1)},${(cy + Math.sin(a) * ry * k).toFixed(1)}`);
  }
  return `M${pts[0]} L${pts.slice(1).join(' ')}`;
}

/** A hand-drawn cloud outline. */
function cloud(x: number, y: number, w: number, h: number): string {
  const b = y + h;
  return `M${x + w * 0.12},${b} C${x - w * 0.08},${b} ${x - w * 0.06},${y + h * 0.48} ${x + w * 0.16},${y + h * 0.5}
    C${x + w * 0.12},${y + h * 0.02} ${x + w * 0.52},${y - h * 0.12} ${x + w * 0.6},${y + h * 0.28}
    C${x + w * 0.72},${y + h * 0.08} ${x + w * 1.02},${y + h * 0.22} ${x + w * 0.94},${y + h * 0.58}
    C${x + w * 1.1},${y + h * 0.66} ${x + w * 1.06},${b + 2} ${x + w * 0.86},${b} Z`;
}

const INK = '#9299a3', RED = '#cf8383', BLUE = '#a8bcd8', GREEN = '#86c39b', FAINT = '#b3b9c2';
const HAND = "'Caveat', 'Segoe Print', 'Bradley Hand', 'Comic Sans MS', cursive";

function Sym({ kind, x, y, s = 60 }: { kind: string; x: number; y: number; s?: number }) {
  const S = DEVICE_ICONS[kind];
  return (
    <g transform={`translate(${x - s / 2},${y - s / 2})`}>
      <g style={{ color: INK }}><S size={s} fill="#f8f9fb" /></g>
    </g>
  );
}

function Hand({ x, y, children, color = INK, size = 26, rotate = 0 }:
  { x: number; y: number; children: React.ReactNode; color?: string; size?: number; rotate?: number }) {
  return (
    <text x={x} y={y} fill={color} fontFamily={HAND} fontSize={size}
      transform={rotate ? `rotate(${rotate} ${x} ${y})` : undefined}>{children}</text>
  );
}

function Ink({ d, color = INK, w = 2.4, dash }: { d: string; color?: string; w?: number; dash?: string }) {
  return <path d={d} fill="none" stroke={color} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={dash} />;
}

/** Something drawn, then mostly wiped: faint remnants under smeared eraser streaks. */
function Erased({ children, streaks }: { children: React.ReactNode; streaks: [number, number, number, number][] }) {
  return (
    <g>
      <g opacity={0.2}>{children}</g>
      <g filter="url(#sk-smear)">
        {streaks.map(([x, y, w, h], i) => (
          <path key={i} d={`M${x},${y} q${w * 0.25},${-h * 0.3} ${w * 0.5},0 t${w * 0.5},0 M${x + 6},${y + h * 0.45} q${w * 0.25},${-h * 0.3} ${w * 0.5},0 t${w * 0.46},0`}
            fill="none" stroke="#f3f5f9" strokeOpacity={0.9} strokeWidth={h * 0.6} strokeLinecap="round" />
        ))}
      </g>
    </g>
  );
}

// Precomputed so the wobble is stable across renders.
const SK = {
  isp: cloud(520, 52, 150, 76),
  ispLine: wobbleLine(585, 128, 548, 238),
  ispB: cloud(840, 40, 130, 66),
  ispBLine: wobbleLine(880, 106, 790, 240),
  ibgp: `M570,268 Q665,${282 + j(3)} 758,270`,
  e2off: wobbleLine(790, 268, 880, 268),
  fw1: wobbleLine(560, 300, 640, 388), fw2: wobbleLine(760, 300, 690, 388),
  aclLoop: sketchEllipse(650, 380, 66, 44),
  c1: wobbleLine(640, 432, 486, 505), c2: wobbleLine(690, 432, 775, 505),
  oldLink: wobbleLine(560, 300, 760, 500),
  core: wobbleLine(492, 525, 760, 528),
  coreLoop: sketchEllipse(615, 528, 205, 60),
  a1: wobbleLine(455, 548, 412, 640), a2: wobbleLine(478, 548, 580, 666),
  a3: `M760,550 L640,668`,
  d1: wobbleLine(408, 700, 382, 790), d2: wobbleLine(428, 700, 460, 790),
  ospfArrow: 'M330,348 C420,360 455,410 425,476',
  ospfHead: 'M413,462 L425,476 L436,459',
  strike: wobbleLine(98, 337, 258, 337, 1.2),
  rrArrow: 'M690,236 C700,200 735,190 760,196',
  dualArrow: 'M905,150 C930,190 900,230 830,252',
  // Behind the sign-in panel: a spine/leaf fabric, a table, a timer scribble.
  spines: [[1070, 240], [1310, 240]] as [number, number][],
  leaves: [[1000, 420], [1145, 420], [1290, 420], [1420, 420]] as [number, number][],
  fabricLoop: sketchEllipse(1210, 330, 270, 150),
  tableH: [wobbleLine(985, 600, 1395, 602, 1.4), wobbleLine(985, 640, 1395, 641, 1.4),
           wobbleLine(985, 680, 1395, 683, 1.4), wobbleLine(985, 720, 1395, 722, 1.4)],
  tableV: [wobbleLine(1060, 570, 1062, 760, 1.4), wobbleLine(1250, 570, 1252, 760, 1.4)],
  oneStrike: wobbleLine(1262, 708, 1330, 706, 1.2),
  helloArrow: wobbleLine(1040, 840, 1300, 846, 1.6),
  helloHead: 'M1288,836 L1302,846 L1287,855',
  // Right of the panel: the DR site.
  dc2: cloud(1478, 108, 150, 82),
  dcLine: wobbleLine(1540, 196, 1532, 520),
  drDash: 'M1430,440 C1470,470 1480,520 1505,548',
  drLoop: sketchEllipse(1540, 662, 50, 26),
  drDown: wobbleLine(1520, 588, 1500, 768), drDown2: wobbleLine(1546, 588, 1585, 760),
  zig: 'M592,507 l7,-8 l7,8 l7,-8 l7,8',
  // Bottom middle: a to-do list that drifted off.
  todoUnder: wobbleLine(640, 808, 760, 810, 1.4),
  srStrike: wobbleLine(678, 873, 734, 871, 1.2),
  circle3: sketchEllipse(648, 912, 14, 13),
};

function WhiteboardSketch() {
  return (
    <svg className="absolute inset-0 w-full h-full" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden>
      <defs>
        {/* Gentle marker roughness on everything drawn. */}
        <filter id="sk-rough" x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="3" />
          <feDisplacementMap in="SourceGraphic" scale="2.2" />
        </filter>
        {/* Eraser streaks: soft-edged smears. */}
        <filter id="sk-smear" x="-10%" y="-30%" width="120%" height="160%">
          <feGaussianBlur stdDeviation="4" />
        </filter>
      </defs>

      {/* ── Wiped earlier drafts ── */}
      <Erased streaks={[[826, 52, 170, 46], [820, 96, 150, 40]]}>
        <Ink d={SK.ispB} /><Ink d={SK.ispBLine} />
        <Hand x={866} y={90} size={26}>ISP-B?</Hand>
      </Erased>
      <Erased streaks={[[590, 360, 150, 40], [640, 430, 120, 36]]}>
        <Ink d={SK.oldLink} />
        <Hand x={650} y={455} size={22}>direct?</Hand>
      </Erased>
      <Erased streaks={[[80, 120, 190, 34]]}>
        <Hand x={88} y={140} size={24}>192.168.0.0/16 → summarize??</Hand>
      </Erased>
      <Erased streaks={[[1110, 120, 210, 38]]}>
        <Hand x={1120} y={140} size={26}>3-tier → ??</Hand>
      </Erased>
      <Erased streaks={[[1000, 770, 220, 30]]}>
        <Hand x={1010} y={790} size={22}>hold 180 / keep 60</Hand>
      </Erased>

      <g filter="url(#sk-rough)">
        {/* WAN edge */}
        <Ink d={SK.isp} />
        <Hand x={556} y={106} size={30} rotate={-4}>ISP-A</Hand>
        <Hand x={680} y={92} size={24} rotate={-6}>AS 65001</Hand>
        <Ink d={SK.ispLine} />
        <Sym kind="router" x={540} y={268} />
        <Sym kind="router" x={776} y={268} />
        <Hand x={500} y={228} size={24} rotate={-8}>edge-1</Hand>
        <Hand x={778} y={228} size={24} rotate={-6}>edge-2</Hand>
        <Ink d={SK.ibgp} />
        <Hand x={636} y={256} size={22} rotate={-6}>iBGP</Hand>
        <Ink d={SK.rrArrow} color={FAINT} w={1.8} />
        <Hand x={664} y={226} size={20} color={FAINT} rotate={-8}>RR?</Hand>
        <Ink d={SK.e2off} />
        <Hand x={918} y={140} size={24} color={RED} rotate={8}>dual-homed!!</Hand>
        <Ink d={SK.dualArrow} color={RED} w={1.8} />

        {/* Firewall */}
        <Ink d={SK.fw1} /><Ink d={SK.fw2} />
        <Sym kind="firewall" x={662} y={410} s={70} />
        <Ink d={SK.aclLoop} color={RED} w={2} />
        <Hand x={722} y={378} color={RED} size={28} rotate={-6}>ACL!!</Hand>
        <Hand x={700} y={430} size={26} rotate={-8}>FW</Hand>
        <Hand x={700} y={460} size={20} color={FAINT} rotate={-6}>(HA pair?)</Hand>

        {/* Core */}
        <Ink d={SK.c1} /><Ink d={SK.c2} />
        <Sym kind="router" x={462} y={525} />
        <Sym kind="router" x={784} y={528} />
        <Ink d={SK.core} />
        <Ink d={SK.coreLoop} color={BLUE} w={2.6} />
        <Ink d={SK.zig} color={RED} w={2} />
        <Hand x={632} y={510} color={RED} size={26} rotate={-3}>MTU 9000??</Hand>
        <Hand x={408} y={572} size={22} rotate={-10}>core-1</Hand>
        <Hand x={800} y={572} size={22} rotate={-8}>core-2</Hand>
        <Hand x={330} y={610} size={20} color={BLUE} rotate={-4}>BFD 300x3</Hand>
        <Hand x={850} y={500} size={20} color={FAINT} rotate={-6}>/31 ??</Hand>

        {/* Access */}
        <Ink d={SK.a1} /><Ink d={SK.a2} />
        <Ink d={SK.a3} dash="6 8" />
        <Sym kind="switch" x={406} y={668} s={74} />
        <Sym kind="switch" x={592} y={690} s={74} />
        <Hand x={452} y={644} size={22} rotate={-6}>acc-1</Hand>
        <Hand x={640} y={720} size={22} rotate={-4}>acc-2</Hand>
        <Hand x={548} y={622} size={22} rotate={-6}>LACP?</Hand>
        <Hand x={700} y={636} size={20} color={FAINT} rotate={-4}>vPC</Hand>
        <Ink d={wobbleLine(698, 630, 734, 628, 1)} color={RED} w={1.8} />
        <Hand x={740} y={640} size={20} color={FAINT} rotate={-4}>Po1?</Hand>
        <Ink d={SK.d1} w={2} /><Ink d={SK.d2} w={2} />

        {/* Margin notes */}
        <Hand x={84} y={192} size={24} color="#a5abb5">10.0.12.0/30</Hand>
        <Hand x={84} y={224} size={24} color="#a5abb5">10.0.13.0/30</Hand>
        <Hand x={84} y={256} size={24} color="#a5abb5">lo0 → /32s</Hand>
        <Ink d="M206,246 l8,10 l16,-22" color={GREEN} w={2.6} />
        <Hand x={84} y={294} size={22} color={BLUE} rotate={-2}>VLAN 10 users · 20 voice · 99 mgmt</Hand>
        <Hand x={100} y={344} size={24} color={RED} rotate={-1}>static routes</Hand>
        <Ink d={SK.strike} color={RED} w={2} />
        <Hand x={272} y={346} size={28} color={RED} rotate={-4}>OSPF!</Hand>
        <Ink d={SK.ospfArrow} color={BLUE} w={2.2} />
        <Ink d={SK.ospfHead} color={BLUE} w={2.2} />
        <Hand x={96} y={420} size={22} color="#a5abb5" rotate={-3}>area 0 ↔ area 1 — ABR = core?</Hand>
        <Hand x={96} y={452} size={20} color={FAINT} rotate={-3}>stub? totally stubby?</Hand>
        <Hand x={140} y={500} size={22} color={RED} rotate={-6}>?? ask NOC re: MTU</Hand>

        {/* Bottom middle: the to-do list that drifted off */}
        <Hand x={642} y={800} size={24} color="#a5abb5" rotate={-2}>next:</Hand>
        <Ink d={SK.todoUnder} color={FAINT} w={1.6} />
        <Hand x={646} y={836} size={22} color="#a5abb5" rotate={-2}>1. OSPF → IS-IS?</Hand>
        <Hand x={646} y={870} size={22} color="#a5abb5" rotate={-2}>2. MPLS</Hand>
        <Ink d={SK.srStrike} color={RED} w={1.6} />
        <Hand x={742} y={870} size={22} color={RED} rotate={-4}>SR</Hand>
        <Hand x={642} y={918} size={20} color="#a5abb5" rotate={-2}>3</Hand>
        <Ink d={SK.circle3} color={FAINT} w={1.6} />
        <Hand x={668} y={918} size={22} color="#a5abb5" rotate={-2}>prepend x3 on ISP-B</Hand>

        {/* ── Behind the sign-in panel: a DC fabric ── */}
        <Hand x={1010} y={190} size={28} rotate={-3}>DC-1 fabric</Hand>
        <Ink d={wobbleLine(1008, 198, 1150, 196, 1.2)} w={1.8} />
        {SK.spines.flatMap(([sx, sy]) => SK.leaves.map(([lx, ly], i) => (
          <Ink key={`${sx}-${i}`} d={wobbleLine(sx, sy + 26, lx, ly - 26, 1.6)} w={2} />
        )))}
        {SK.spines.map(([x, y], i) => <Sym key={`s${i}`} kind="router" x={x} y={y} s={54} />)}
        {SK.leaves.map(([x, y], i) => <Sym key={`l${i}`} kind="switch" x={x} y={y} s={62} />)}
        <Hand x={1040} y={292} size={22} rotate={-6}>spine-1</Hand>
        <Hand x={1328} y={292} size={22} rotate={-6}>spine-2</Hand>
        {SK.leaves.map(([x, y], i) => <Hand key={`ll${i}`} x={x - 22} y={y + 48} size={20} rotate={-5}>{`leaf-${i + 1}`}</Hand>)}
        <Ink d={SK.fabricLoop} color={BLUE} w={2.2} />
        <Hand x={1176} y={330} size={26} color={BLUE} rotate={-4}>EVPN / VXLAN</Hand>
        <Hand x={1190} y={512} size={22} color="#a5abb5" rotate={-3}>VNI 10010 → VLAN 10</Hand>
        <Hand x={990} y={540} size={22} color={RED} rotate={-5}>ASN per leaf??</Hand>

        {/* the VLAN table */}
        {SK.tableH.map((d, i) => <Ink key={`th${i}`} d={d} w={1.6} />)}
        {SK.tableV.map((d, i) => <Ink key={`tv${i}`} d={d} w={1.6} />)}
        <Hand x={1000} y={592} size={22}>VLAN</Hand>
        <Hand x={1080} y={592} size={22}>subnet</Hand>
        <Hand x={1268} y={592} size={22}>GW</Hand>
        <Hand x={1010} y={632} size={22} color="#a5abb5">10</Hand>
        <Hand x={1074} y={632} size={22} color="#a5abb5">10.10.0.0/24</Hand>
        <Hand x={1268} y={632} size={22} color="#a5abb5">.1</Hand>
        <Hand x={1010} y={672} size={22} color="#a5abb5">20</Hand>
        <Hand x={1074} y={672} size={22} color="#a5abb5">10.20.0.0/24</Hand>
        <Hand x={1268} y={672} size={22} color="#a5abb5">.1</Hand>
        <Hand x={1010} y={712} size={22} color="#a5abb5">99</Hand>
        <Hand x={1074} y={712} size={22} color="#a5abb5">10.99.0.0/24</Hand>
        <Hand x={1266} y={712} size={22} color="#a5abb5">.1</Hand>
        <Ink d={SK.oneStrike} color={RED} w={1.6} />
        <Hand x={1300} y={712} size={22} color={RED} rotate={-4}>.254</Hand>
        <Hand x={1010} y={752} size={22} color={FAINT}>…</Hand>

        {/* hello / dead scribble */}
        <Hand x={990} y={850} size={22}>R1</Hand>
        <Ink d={SK.helloArrow} w={1.8} /><Ink d={SK.helloHead} w={1.8} />
        <Hand x={1130} y={834} size={20} rotate={-2}>hello 10s</Hand>
        <Hand x={1314} y={852} size={22}>R2</Hand>
        <Hand x={1120} y={892} size={22} color={RED} rotate={-4}>dead 40 — why 120 on R2?!</Hand>

        {/* ── Right of the panel: the DR site ── */}
        <Ink d={SK.dc2} />
        <Hand x={1520} y={170} size={30} rotate={-4}>DC-2</Hand>
        <Ink d={SK.dcLine} />
        <Ink d={SK.drDash} dash="5 8" />
        <Sym kind="router" x={1532} y={556} />
        <Hand x={1478} y={512} size={22} rotate={-6}>dr-core</Hand>
        <Ink d={SK.drLoop} color={RED} w={2} />
        <Hand x={1502} y={672} size={26} color={RED} rotate={-6}>DR site?</Hand>
        <Ink d={SK.drDown} /><Ink d={SK.drDown2} />
        <Sym kind="host" x={1500} y={800} s={58} />
        <Hand x={1462} y={880} size={20} color={FAINT} rotate={-6}>RPO 15m?</Hand>
      </g>
    </svg>
  );
}

// ── Server status ───────────────────────────────────────────────────────
type ServerState = 'checking' | 'up' | 'down';

function useServerStatus(): ServerState {
  const [state, setState] = useState<ServerState>('checking');
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 3000);
      try {
        const r = await fetch('/api/health', { signal: ctrl.signal, cache: 'no-store' });
        if (!cancelled) setState(r.ok ? 'up' : 'down');
      } catch {
        if (!cancelled) setState('down');
      } finally {
        clearTimeout(timeout);
      }
    };
    check();
    // Poll only while the tab is visible; re-check as soon as it's shown again.
    const iv = setInterval(() => { if (!document.hidden) check(); }, 15000);
    const onVisible = () => { if (!document.hidden) check(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => { cancelled = true; clearInterval(iv); document.removeEventListener('visibilitychange', onVisible); };
  }, []);
  return state;
}

export default function LoginPage() {
  const { login } = useAuthStore();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const server = useServerStatus();
  // The original Phanero (network planning) runs on this host's default port.
  const phaneroUrl = `${window.location.protocol}//${window.location.hostname}/`;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setShowPassword(false);   // never leave a revealed password on screen after submitting
    setIsLoading(true);
    try {
      await login(username, password);
    } catch (err: any) {
      setError(err.message || 'Login failed');
    }
    setIsLoading(false);
  };

  const inputClass = 'w-full h-12 rounded-lg bg-white/90 border border-slate-300 px-4 text-base text-slate-900 '
    + 'shadow-sm focus:outline-none focus:border-primary-500 focus:ring-4 focus:ring-primary-500/15 transition-colors';

  return (
    <div className="min-h-screen relative overflow-hidden text-slate-800 flex justify-center md:justify-start"
      style={{
        background: '#f3f5f9',
        backgroundImage: 'radial-gradient(#cdd3dc 1.2px, transparent 1.3px)',
        backgroundSize: '22px 22px',
      }}>
      {/* Whiteboard (md+), with the brand top-left and the pitch bottom-left */}
      <div className="hidden md:block absolute inset-0">
        <WhiteboardSketch />
        <div className="absolute top-10 left-14"><Brand /></div>
        <div className="absolute left-0 bottom-0 w-[680px] h-[300px] pointer-events-none"
          style={{ background: 'radial-gradient(ellipse at 18% 78%, #f3f5f9 45%, rgba(243,245,249,0.85) 62%, rgba(243,245,249,0) 80%)' }} />
        <div className="absolute left-14 bottom-12 max-w-[520px]">
          <h2 className="text-[44px] leading-[1.08] font-bold tracking-tight text-slate-900">
            Build it. Break it.<br />Learn it.
          </h2>
          <p className="mt-4 text-base leading-relaxed text-slate-600">
            From whiteboard sketch to running lab. Real network devices, each reachable on its own address.
          </p>
        </div>
      </div>

      {/* Sign-in column — 520px wide, its centre at 65% of the width (flush
          right on tablet widths), full height. */}
      <div className="relative w-full max-w-[520px] md:ml-auto md:mr-[max(0px,calc(35%-260px))] min-h-screen
          flex flex-col px-8 sm:px-11 pt-10 pb-6 sm:border-x border-white/70 shadow-[0_0_60px_rgb(15_23_42/0.10)]"
        style={{ background: 'rgba(236, 241, 250, 0.78)', backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)' }}>
        <div className="md:hidden mb-8"><Brand /></div>

        <div className="flex-1 flex items-center">
          <form onSubmit={handleSubmit} className="w-full space-y-6">
            <div>
              {/* Same treatment as the PHANERO wordmark: uppercase, semibold, wide tracking. */}
              <h1 className="text-[28px] font-bold tracking-[0.18em] uppercase text-slate-900">Sign in</h1>
              <p className="mt-2 text-base text-slate-600">Pick up where your network left off.</p>
            </div>

            <div>
              <label htmlFor="login-username" className="block mb-2 font-mono text-xs text-slate-600">
                <span className="text-slate-400 mr-1.5">›</span>username
              </label>
              <input id="login-username" type="text" className={inputClass} value={username}
                onChange={e => setUsername(e.target.value)} autoFocus autoComplete="username" />
            </div>

            <div>
              <label htmlFor="login-password" className="block mb-2 font-mono text-xs text-slate-600">
                <span className="text-slate-400 mr-1.5">›</span>password
              </label>
              <div className="relative">
                <input id="login-password" type={showPassword ? 'text' : 'password'} className={`${inputClass} pr-12`}
                  value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" />
                <button type="button" onClick={() => setShowPassword(v => !v)}
                  className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-800"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  title={showPassword ? 'Hide password' : 'Show password'}>
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            {error && (
              <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
            )}

            <button type="submit" disabled={isLoading || !username || !password}
              className="w-full h-12 rounded-lg flex items-center justify-between px-5 text-base font-semibold text-white
                bg-[#2f45a8] hover:bg-[#283c96] shadow-sm transition-colors
                focus:outline-none focus-visible:ring-4 focus-visible:ring-primary-500/25
                disabled:bg-[#3a4fae] disabled:cursor-not-allowed">
              <span>{isLoading ? 'Signing in…' : 'Sign in'}</span>
              <span aria-hidden className="text-xl leading-none">⟶</span>
            </button>

            <p className="text-sm text-slate-600">Trouble signing in? Contact your lab administrator.</p>
          </form>
        </div>

        <div className="mt-8 border-t border-slate-300/80 pt-4 flex items-center gap-2 font-mono text-xs">
          <span className={`w-1.5 h-1.5 rounded-full ${
            server === 'up' ? 'bg-emerald-500' : server === 'down' ? 'bg-red-500' : 'bg-slate-400 animate-pulse'
          }`} />
          <span className={server === 'down' ? 'text-red-600' : 'text-slate-600'}>
            {server === 'up' ? 'server reachable' : server === 'down' ? 'server unreachable' : 'checking server…'}
          </span>
          <a href={phaneroUrl} className="ml-auto font-sans text-[13px] text-primary-700 hover:text-primary-800 hover:underline">
            Open Phanero →
          </a>
        </div>
      </div>
    </div>
  );
}

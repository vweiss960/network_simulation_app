import { useEffect, useRef, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { useAuthStore } from '../stores/useAuthStore';

/** The Phanero fan-of-rays mark (same artwork as the app header). */
function PhaneroLogo({ className = '' }: { className?: string }) {
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

function Wordmark() {
  return (
    <div className="flex items-center gap-3">
      <PhaneroLogo />
      <span className="text-sm font-semibold tracking-[0.2em] uppercase text-surface-100">
        Phanero
      </span>
    </div>
  );
}

// ── Background illustration ─────────────────────────────────────────────
// A network floating in 3D space, seen through a slowly orbiting camera with
// a real depth of field: nodes near the focal plane are crisp, nodes in front
// of or behind it blur and fade with distance. Deliberately dim and slow so
// it never competes with the form. One still frame for reduced motion.
type V3 = { x: number; y: number; z: number };

/** Deterministic PRNG so the scene is the same on every load. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildScene() {
  const rnd = mulberry32(7);
  const nodes: V3[] = Array.from({ length: 46 }, () => ({
    x: (rnd() * 2 - 1) * 1.7, y: (rnd() * 2 - 1) * 0.75, z: (rnd() * 2 - 1) * 1.3,
  }));
  const d2 = (a: V3, b: V3) => (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;
  const linkSet = new Set<string>();
  nodes.forEach((n, i) => {
    nodes.map((m, j) => [j, d2(n, m)] as const).filter(([j]) => j !== i)
      .sort((a, b) => a[1] - b[1]).slice(0, 2)
      .forEach(([j]) => linkSet.add(i < j ? `${i}-${j}` : `${j}-${i}`));
  });
  const links = [...linkSet].map(k => k.split('-').map(Number) as [number, number]);
  // A highlighted route: a nearest-neighbor walk across the field.
  const path: number[] = [0];
  while (path.length < 6) {
    const cur = nodes[path[path.length - 1]];
    const next = nodes.map((m, j) => [j, d2(cur, m)] as const)
      .filter(([j]) => !path.includes(j) && nodes[j].x > cur.x - 0.1)
      .sort((a, b) => a[1] - b[1])[0];
    if (!next) break;
    path.push(next[0]);
  }
  const amber: [number, number] = links[Math.floor(links.length * 0.7)];
  // A few nodes get dotted "drop lines" to a floor plane below the field.
  const drops = nodes.map((_, i) => i).filter(i => i % 6 === 2);
  return { nodes, links, path, amber, drops };
}

// Pre-rendered node sprites: each (style, blur level) is painted ONCE into a
// small offscreen canvas, then stamped each frame with drawImage — no per-frame
// canvas filters, which are expensive without a GPU. Blur is painted with
// radial gradients rather than ctx.filter so it also works in Safari.
const BLUR_LEVELS = [0, 2, 4, 6, 9];
const SPRITE_R = 10;    // node radius inside a sprite (scaled when drawn)
const SPRITE_PAD = 32;  // room for the widest blur halo (~2.5 × 9px)

/** "rgb(r,g,b)" + alpha → "rgba(r,g,b,a)" */
const withAlpha = (rgb: string, a: number) => rgb.replace('rgb(', 'rgba(').replace(')', `,${a})`);

function makeSprite(ring: string, dot: boolean, blur: number): HTMLCanvasElement {
  const size = (SPRITE_R + SPRITE_PAD) * 2;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d')!;
  const mid = size / 2;
  if (!blur) {
    // In focus: a crisp ring.
    g.fillStyle = 'rgb(11,18,32)';
    g.strokeStyle = ring;
    g.lineWidth = 2.4;
    g.beginPath(); g.arc(mid, mid, SPRITE_R, 0, Math.PI * 2); g.fill(); g.stroke();
  } else {
    // Out of focus: the ring smeared into a soft disc, wider and fainter with blur.
    const outer = SPRITE_R + blur * 2.5;
    const at = (r: number) => Math.min(1, Math.max(0, r / outer));
    const grad = g.createRadialGradient(mid, mid, 0, mid, mid, outer);
    grad.addColorStop(0, 'rgba(11,18,32,0.9)');
    grad.addColorStop(at(SPRITE_R - blur * 0.8), 'rgba(11,18,32,0.75)');
    grad.addColorStop(at(SPRITE_R), withAlpha(ring, 0.8 / (1 + blur * 0.18)));
    grad.addColorStop(at(SPRITE_R + blur * 1.2), withAlpha(ring, 0.25 / (1 + blur * 0.18)));
    grad.addColorStop(1, withAlpha(ring, 0));
    g.fillStyle = grad;
    g.beginPath(); g.arc(mid, mid, outer, 0, Math.PI * 2); g.fill();
  }
  if (dot) {
    const r = SPRITE_R * 0.3 + blur * 0.6;
    const grad = g.createRadialGradient(mid, mid, 0, mid, mid, r);
    grad.addColorStop(0, ring);
    grad.addColorStop(blur ? 0.3 : 0.95, ring);
    grad.addColorStop(1, withAlpha(ring, 0));
    g.fillStyle = grad;
    g.beginPath(); g.arc(mid, mid, r, 0, Math.PI * 2); g.fill();
  }
  return c;
}

function LoginBackdrop() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const scene = buildScene();
    const pathLinks = new Set(scene.path.slice(1).map((j, i) => {
      const a = scene.path[i]; return a < j ? `${a}-${j}` : `${j}-${a}`;
    }));
    // Per-link style is fixed; work it out once rather than every frame.
    const linkStyles = scene.links.map(([a, b]) => {
      const kind = pathLinks.has(a < b ? `${a}-${b}` : `${b}-${a}`) ? 'path'
        : (scene.amber[0] === a && scene.amber[1] === b) || (scene.amber[0] === b && scene.amber[1] === a) ? 'amber' : 'plain';
      return { a, b, kind };
    });
    const onPath = new Set(scene.path);
    const sprites = {
      plain: BLUR_LEVELS.map(b => makeSprite('rgb(110,118,170)', false, b)),
      path: BLUR_LEVELS.map(b => makeSprite('rgb(63,174,159)', true, b)),
    };
    const glowSprite = makeSprite('rgb(127,214,200)', true, 4);
    const motionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    let tooSlow = false;      // set by the safety net below
    const animate = () => !tooSlow && !motionQuery?.matches;
    const CAM = 3.4;          // camera distance from the field's center = focal plane
    const FLOOR_Y = 1.05;     // floor plane for drop lines (screen-down is +y)
    // Render at half resolution (it's a soft background), and never more than
    // ~0.35 MP however large the screen, so big displays cost no more.
    const MAX_PIXELS = 350_000;
    let w = 0, h = 0, raf = 0;

    const resize = () => {
      w = canvas.clientWidth; h = canvas.clientHeight;
      const res = Math.min(0.5, Math.sqrt(MAX_PIXELS / Math.max(1, w * h)));
      canvas.width = Math.max(1, Math.round(w * res)); canvas.height = Math.max(1, Math.round(h * res));
      ctx.setTransform(res, 0, 0, res, 0, 0);   // draw in CSS pixels
    };
    const project = (p: V3, yaw: number, pitch: number) => {
      // Orbit: rotate the scene around Y (yaw), then tilt around X (pitch).
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const x1 = p.x * cy - p.z * sy, z1 = p.x * sy + p.z * cy;
      const y2 = p.y * cp - z1 * sp, z2 = p.y * sp + z1 * cp;
      const depth = z2 + CAM;
      const f = Math.min(w, h * 1.6) * 0.9;
      // Centre the scene left of the off-centre sign-in column (the backdrop
      // is only shown on md+ screens, where that column sits right of centre).
      const cx = w * 0.4;
      return { sx: cx + (x1 * f) / depth, sy: h * 0.52 + (y2 * f) / depth, depth, scale: f / depth };
    };
    // Depth of field: blur grows with distance from the focal plane.
    const blurOf = (depth: number) => Math.min(9, Math.abs(depth - CAM) * 4.2);
    const alphaOf = (blur: number) => Math.max(0.12, 0.85 - blur * 0.08);
    const level = (blur: number) => {
      let best = 0;
      BLUR_LEVELS.forEach((b, i) => { if (Math.abs(b - blur) < Math.abs(BLUR_LEVELS[best] - blur)) best = i; });
      return best;
    };

    const draw = (t: number) => {
      const yaw = t * 0.00003 + 0.6;                 // ~3.5 min per revolution
      const pitch = 0.18 + Math.sin(t * 0.00005) * 0.05;
      ctx.clearRect(0, 0, w, h);
      const pts = scene.nodes.map(n => project(n, yaw, pitch));

      // Links, far first. Out-of-focus links are drawn wider and fainter,
      // which reads as soft without a blur filter.
      const order = linkStyles.map(ls => ({ ls, depth: (pts[ls.a].depth + pts[ls.b].depth) / 2 }))
        .sort((x, y) => y.depth - x.depth);
      for (const { ls: { a, b, kind }, depth } of order) {
        const blur = blurOf(depth);
        const alpha = alphaOf(blur) / (1 + blur * 0.25);
        ctx.strokeStyle = kind === 'path' ? `rgba(63,174,159,${alpha * 0.7})`
          : kind === 'amber' ? `rgba(180,130,30,${alpha * 0.6})` : `rgba(95,102,150,${alpha * 0.45})`;
        ctx.lineWidth = Math.max(0.6, (kind === 'plain' ? 1.3 : 2.2) * pts[a].scale / 330) + blur * 0.9;
        ctx.beginPath(); ctx.moveTo(pts[a].sx, pts[a].sy); ctx.lineTo(pts[b].sx, pts[b].sy); ctx.stroke();
      }

      // Drop lines from a few nodes to the floor plane.
      ctx.setLineDash([2, 5]);
      ctx.lineWidth = 1;
      for (const i of scene.drops) {
        const n = scene.nodes[i];
        const floor = project({ x: n.x, y: FLOOR_Y, z: n.z }, yaw, pitch);
        const alpha = alphaOf(blurOf(pts[i].depth)) * 0.35;
        ctx.strokeStyle = `rgba(71,85,105,${alpha})`;
        ctx.beginPath(); ctx.moveTo(pts[i].sx, pts[i].sy); ctx.lineTo(floor.sx, floor.sy); ctx.stroke();
        ctx.fillStyle = `rgba(52,120,90,${alpha})`;
        ctx.beginPath(); ctx.ellipse(floor.sx, floor.sy, 7 * floor.scale / 330, 2.4 * floor.scale / 330, 0, 0, Math.PI * 2); ctx.fill();
      }
      ctx.setLineDash([]);

      // Nodes, far first, stamped from the pre-blurred sprites.
      const nodeOrder = pts.map((p, i) => ({ p, i })).sort((a, b) => b.p.depth - a.p.depth);
      for (const { p, i } of nodeOrder) {
        const blur = blurOf(p.depth);
        const sprite = (onPath.has(i) ? sprites.path : sprites.plain)[level(blur)];
        const k = Math.max(0.3, 0.75 * p.scale / 330);   // sprite scale (node radius 10 → ~7.5px in focus)
        const size = sprite.width * k;
        ctx.globalAlpha = alphaOf(blur) * (onPath.has(i) ? 1 : 0.8);
        ctx.drawImage(sprite, p.sx - size / 2, p.sy - size / 2, size, size);
      }
      ctx.globalAlpha = 1;

      // A faint marker travelling along the highlighted route (~16 s per pass).
      if (animate() && scene.path.length > 1) {
        const segs = scene.path.length - 1;
        const u = ((t / 16000) % 1) * segs;
        const k = Math.floor(u), f = u - k;
        const a = pts[scene.path[k]], b = pts[scene.path[k + 1]];
        const size = glowSprite.width * 0.45;
        ctx.globalAlpha = 0.35;
        ctx.drawImage(glowSprite, a.sx + (b.sx - a.sx) * f - size / 2, a.sy + (b.sy - a.sy) * f - size / 2, size, size);
        ctx.globalAlpha = 1;
      }
    };

    let lastT = 0;
    // Nothing to draw while the backdrop is hidden (narrow screens) or empty.
    const visible = () => w > 0 && h > 0;
    resize();
    // Resizing clears the canvas, so repaint the current frame right away.
    const ro = new ResizeObserver(() => { resize(); if (visible()) draw(lastT); });
    ro.observe(canvas);

    // ~15 fps is plenty for a background this slow. Safety net: after a few
    // warm-up frames, if this machine averages over ~4 ms per frame, stop
    // animating and keep the current frame so the login never feels sluggish.
    const WARMUP = 5, SAMPLE = 20;
    let last = -Infinity, frames = 0, spent = 0;
    const loop = (t: number) => {
      if (!animate()) return;
      if (visible() && t - last >= 66) {
        const t0 = performance.now();
        draw(t);
        lastT = last = t;
        if (frames < WARMUP + SAMPLE) {
          if (frames >= WARMUP) spent += performance.now() - t0;
          frames++;
          if (frames === WARMUP + SAMPLE && spent / SAMPLE > 4) { tooSlow = true; return; }
        }
      }
      raf = requestAnimationFrame(loop);
    };
    const start = () => {
      cancelAnimationFrame(raf);
      if (animate()) raf = requestAnimationFrame(loop);
      else if (visible()) draw(lastT);
    };
    // Follow the OS "reduce motion" setting live.
    motionQuery?.addEventListener?.('change', start);
    if (visible()) draw(0);
    start();
    return () => { cancelAnimationFrame(raf); ro.disconnect(); motionQuery?.removeEventListener?.('change', start); };
  }, []);

  return (
    <div className="absolute inset-0 pointer-events-none" aria-hidden>
      <canvas ref={canvasRef} className="w-full h-full" style={{ opacity: 0.75 }} />
      {/* Edge vignette, then the corner glow on top of it (bottom-left) — plain gradients, no CSS filters. */}
      <div className="absolute inset-0" style={{
        background: 'radial-gradient(ellipse at 40% 52%, transparent 40%, rgba(7,11,20,0.9) 100%)',
      }} />
      <div className="absolute left-0 bottom-0" style={{
        width: '80%', height: '85%',
        // Large soft glow rising from the bottom-left corner of the screen.
        background: 'radial-gradient(ellipse at 0% 100%, rgba(37,99,235,0.24) 0%, rgba(37,99,235,0.10) 30%, rgba(37,99,235,0.03) 55%, transparent 75%)',
      }} />
    </div>
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

  const inputClass = 'w-full h-12 rounded-lg bg-surface-950/70 border border-surface-700 px-4 text-base text-surface-100 '
    + 'focus:outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-500/25 transition-colors';

  return (
    <div className="min-h-screen relative overflow-hidden bg-[#070b14] text-surface-200 flex justify-center md:justify-start">
      {/* Backdrop across the whole screen (it shows faintly through the card) */}
      <div className="hidden md:block absolute inset-0">
        <LoginBackdrop />
        <div className="absolute top-8 left-8">
          <Wordmark />
          <p className="mt-3 text-sm text-surface-400">
            see what others <span className="text-white font-semibold">cannot.</span>
          </p>
        </div>
      </div>

      {/* Sign-in column (460px wide): off-centre to the right — its centre at
          75% of the width, halfway between the middle and the right edge; on
          tablet widths (< ~920px) it sits flush right instead. Full height,
          translucent (no backdrop blur — that would re-blur the moving backdrop
          every frame); fades in with a small rise-and-settle. */}
      <div className="login-card-in relative w-full max-w-[460px] md:ml-auto md:mr-[max(0px,calc(25%-230px))] min-h-screen bg-[#0c1322]/70 sm:border-x border-white/10 shadow-2xl shadow-black/50 flex flex-col px-8 sm:px-11 pt-10 pb-6">
        <div className="md:hidden mb-8">
          <Wordmark />
          <p className="mt-2 text-sm text-surface-400">
            see what others <span className="text-white font-semibold">cannot.</span>
          </p>
        </div>

        <div className="flex-1 flex items-center">
          <form onSubmit={handleSubmit} className="w-full space-y-6">
            <div>
              {/* Same treatment as the PHANERO wordmark: uppercase, semibold, wide tracking. */}
              <h1 className="text-2xl font-semibold tracking-[0.2em] uppercase text-white">Sign in</h1>
              <p className="mt-2 text-base text-primary-200/80">Pick up where your network left off.</p>
            </div>

            <div>
              <label htmlFor="login-username" className="block mb-2 font-mono text-xs text-surface-300">
                <span className="text-surface-500 mr-1.5">›</span>username
              </label>
              <input
                id="login-username"
                type="text"
                className={inputClass}
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
              />
            </div>

            <div>
              <label htmlFor="login-password" className="block mb-2 font-mono text-xs text-surface-300">
                <span className="text-surface-500 mr-1.5">›</span>password
              </label>
              <div className="relative">
                <input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  className={`${inputClass} pr-12`}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(v => !v)}
                  className="absolute right-4 top-1/2 -translate-y-1/2 text-surface-400 hover:text-surface-200"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  title={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            {error && (
              <div className="text-sm text-red-400 bg-red-900/20 border border-red-800/30 rounded-lg px-3 py-2">
                {error}
              </div>
            )}

            <button
              type="submit"
              // Muted deep blue: quieter than the solid primary button, still the main action.
              className="w-full h-12 rounded-lg flex items-center justify-between px-5 text-base font-medium
                bg-primary-900/60 border border-primary-500/40 text-primary-100 transition-colors
                enabled:hover:bg-primary-800/70 enabled:hover:border-primary-400/60 enabled:hover:text-white
                focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40
                disabled:text-primary-200/60 disabled:border-primary-500/25 disabled:cursor-not-allowed"
              disabled={isLoading || !username || !password}
            >
              <span>{isLoading ? 'Signing in…' : 'Sign in'}</span>
              <span aria-hidden className="text-xl leading-none">⟶</span>
            </button>

            <p className="text-sm text-surface-400">Trouble signing in? Contact your Phanero administrator.</p>
          </form>
        </div>

        <div className="mt-8 border-t border-white/10 pt-4 flex items-center gap-2 font-mono text-xs">
          <span className={`w-1.5 h-1.5 rounded-full ${
            server === 'up' ? 'bg-emerald-400' : server === 'down' ? 'bg-red-400' : 'bg-surface-500 animate-pulse'
          }`} />
          <span className={server === 'down' ? 'text-red-300' : 'text-surface-400'}>
            {server === 'up' ? 'server reachable' : server === 'down' ? 'server unreachable' : 'checking server…'}
          </span>
        </div>
      </div>
    </div>
  );
}

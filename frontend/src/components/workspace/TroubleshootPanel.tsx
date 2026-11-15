/**
 * The troubleshooting panel of a learner's lab: let Chaos loose (difficulty,
 * number of faults), follow it while it verifies the network and breaks it,
 * then work the tickets — Check, hints (by difficulty), network docs
 * (Guided), Give up — and read the debrief once the round is over.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertTriangle, BookOpen, Check, CheckCircle2, ChevronDown, ChevronRight, Flag, Lightbulb, Loader2, RotateCcw,
  Siren, Sparkles, Ticket as TicketIcon, Timer, X } from 'lucide-react';
import { api } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { Difficulty, ScenarioAttemptView, Ticket } from '../../types';
import Markdown from '../agent/Markdown';
import { DIFFICULTY_LABEL } from '../pages/ScenarioPage';

type Data = Awaited<ReturnType<typeof api.scenarioForTopology>>;

const fmt = (s: number | null) => (s == null ? '—' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`);

export default function TroubleshootPanel({ onClose }: { onClose: () => void }) {
  const currentTopologyId = useLabStore(s => s.currentTopologyId);
  const lab = useLabStore(s => s.lab);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [difficulty, setDifficulty] = useState<Difficulty>('standard');
  const [faults, setFaults] = useState(1);
  const [view, setView] = useState<'tickets' | 'docs'>('tickets');
  const [now, setNow] = useState(Date.now());
  const fetchedAt = useRef(Date.now());              // when `elapsed` was computed
  const loaded = useRef(false);

  const load = useCallback(async () => {
    if (!currentTopologyId) return;
    try {
      const d = await api.scenarioForTopology(currentTopologyId);
      fetchedAt.current = Date.now();
      setData(d);
      if (!loaded.current) { setDifficulty(d.attempt.difficulty); setFaults(d.attempt.fault_count); loaded.current = true; }
    } catch (e) { setError((e as Error).message); }
  }, [currentTopologyId]);
  useEffect(() => { load(); }, [load]);

  const a = data?.attempt;
  // Follow Chaos while it works; tick the clock during a round.
  useEffect(() => {
    if (a?.state !== 'preparing') return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [a?.state, load]);
  useEffect(() => {
    if (a?.state !== 'active') return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [a?.state]);

  if (error) return <Frame onClose={onClose}><div className="p-4 text-sm text-red-300">{error}</div></Frame>;
  if (!data || !a) return <Frame onClose={onClose}><div className="p-4 text-sm text-surface-400 flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Loading…</div></Frame>;

  const setA = (v: ScenarioAttemptView) => { fetchedAt.current = Date.now(); setData(d => (d ? { ...d, attempt: v } : d)); };
  const act = async (key: string, fn: () => Promise<ScenarioAttemptView>, ok?: (v: ScenarioAttemptView) => void) => {
    setBusy(key);
    try { const v = await fn(); setA(v); ok?.(v); } catch (e) { toast.error((e as Error).message); }
    finally { setBusy(null); }
  };
  const deployed = !!lab && ['running', 'syncing'].includes(lab.status);
  const live = a.state === 'active' && a.elapsed != null
    ? a.elapsed + Math.max(0, Math.floor((now - fetchedAt.current) / 1000)) : a.elapsed;

  const unleash = () => act('round', () => api.chaosRound(a.id, difficulty, faults), () => setView('tickets'));
  const check = () => act('check', () => api.chaosCheck(a.id), v => {
    if (v.state === 'solved') toast.success('Network restored — every ticket cleared', { icon: '🏆' });
    else toast(`${v.tickets.filter(t => t.resolved).length} of ${v.tickets.length} tickets cleared`);
  });

  return (
    <Frame onClose={onClose}>
      <div className="px-4 pt-3 pb-3 border-b border-surface-700">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-rose-300 mb-1"><Siren size={12} /> Troubleshooting · {DIFFICULTY_LABEL[a.difficulty]}</div>
        <div className="text-[15px] font-semibold text-surface-100 leading-snug pr-6">{data.scenario.title}</div>
        {a.state === 'active' && (
          <div className="mt-2 flex items-center gap-3 text-[12px] text-surface-300">
            <span className="flex items-center gap-1 tabular-nums"><Timer size={13} /> {fmt(live)}</span>
            <span>{a.tickets.filter(t => t.resolved).length}/{a.tickets.length} cleared</span>
            <span>{a.checks_run} check{a.checks_run === 1 ? '' : 's'}</span>
            {a.difficulty !== 'expert' && <span>{a.hints_used} hint{a.hints_used === 1 ? '' : 's'}</span>}
          </div>
        )}
        {a.docs_md && (a.state === 'active' || a.state === 'solved' || a.state === 'revealed') && (
          <div className="mt-2 flex gap-1">
            <Tab on={view === 'tickets'} onClick={() => setView('tickets')}><TicketIcon size={13} /> Tickets</Tab>
            <Tab on={view === 'docs'} onClick={() => setView('docs')}><BookOpen size={13} /> Network docs</Tab>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {view === 'docs' && a.docs_md ? <div className="p-4"><Markdown text={a.docs_md} dark /></div> : (
          <>
            {(a.state === 'idle' || a.state === 'solved' || a.state === 'revealed' || a.state === 'error') && (
              <div className="p-4 border-b border-surface-700">
                {a.state === 'error' && (
                  <div className="mb-3 rounded-md border border-red-500/40 bg-red-500/10 p-2.5 text-[12.5px] text-red-200 flex gap-2">
                    <AlertTriangle size={15} className="shrink-0 mt-0.5" /> {a.error}
                  </div>
                )}
                {a.state === 'idle' && (
                  <p className="text-[13px] text-surface-300 mb-3 leading-relaxed">
                    Your lab is a copy of a <b>working</b> network. When you're ready, Chaos verifies it's healthy, breaks it,
                    and the tickets start coming in.
                  </p>
                )}
                {(a.state === 'solved' || a.state === 'revealed') && <div className="text-[13px] font-semibold text-surface-100 mb-2">Another round?</div>}
                <div className="text-[11.5px] font-semibold text-surface-400 mb-1">Difficulty</div>
                <div className="flex gap-1 mb-1">
                  {(['guided', 'standard', 'expert'] as Difficulty[]).map(d => (
                    <button key={d} onClick={() => setDifficulty(d)}
                      className={`flex-1 py-1 rounded text-[12.5px] ${difficulty === d ? 'bg-rose-600 text-white font-semibold' : 'bg-surface-700 text-surface-300 hover:bg-surface-600'}`}>{DIFFICULTY_LABEL[d]}</button>
                  ))}
                </div>
                <p className="text-[11.5px] text-surface-400 mb-3">{data.difficulties[difficulty]}</p>
                <div className="text-[11.5px] font-semibold text-surface-400 mb-1">Faults</div>
                <div className="flex gap-1 mb-3">
                  {Array.from({ length: a.max_faults }, (_, i) => i + 1).map(n => (
                    <button key={n} onClick={() => setFaults(n)}
                      className={`w-9 py-1 rounded text-[13px] font-semibold ${faults === n ? 'bg-rose-600 text-white' : 'bg-surface-700 text-surface-300 hover:bg-surface-600'}`}>{n}</button>
                  ))}
                </div>
                <button onClick={unleash} disabled={!deployed || busy === 'round'}
                  className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-sm font-semibold disabled:opacity-40">
                  {busy === 'round' ? <Loader2 size={15} className="animate-spin" /> : <Siren size={15} />} Let Chaos loose
                </button>
                {!deployed && <p className="text-[11.5px] text-amber-300 mt-1.5">Deploy the lab first (Deploy in the header).</p>}
                {(a.state === 'solved' || a.state === 'revealed') && <p className="text-[11.5px] text-surface-400 mt-1.5">Chaos puts the network back together first, then breaks it again.</p>}
                {a.state === 'error' && (
                  <button onClick={() => { if (confirm('Wipe this lab and rebuild the healthy network from scratch? Devices take a few minutes to boot.')) act('rebuild', () => api.chaosRebuild(a.id)); }}
                    disabled={busy === 'rebuild'} className="mt-2 w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg bg-surface-700 hover:bg-surface-600 text-[12.5px] text-surface-200">
                    <RotateCcw size={13} /> Rebuild the lab
                  </button>
                )}
              </div>
            )}

            {a.state === 'preparing' && (
              <div className="p-6 text-center">
                <Loader2 size={28} className="animate-spin mx-auto text-rose-400 mb-3" />
                <div className="text-[14px] font-semibold text-surface-100">{a.progress || 'Chaos is getting ready'}…</div>
                <p className="text-[12px] text-surface-400 mt-1.5">Chaos only strikes a network that's verifiably healthy. This can take a minute or two.</p>
              </div>
            )}

            {(a.state === 'active' || a.state === 'solved' || a.state === 'revealed') && (
              <div className="p-3 space-y-2.5">
                {a.state !== 'active' && <RoundSummary a={a} />}
                {a.tickets.map((t, i) => (
                  <TicketCard key={i} n={i + 1} t={t} difficulty={a.difficulty} active={a.state === 'active'}
                    onHint={() => act(`hint${i}`, () => api.chaosHint(a.id, i))} hinting={busy === `hint${i}`} />
                ))}
                {a.state === 'active' && (
                  <>
                    <button onClick={check} disabled={busy === 'check'}
                      className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold disabled:opacity-50">
                      {busy === 'check' ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} Check the network
                    </button>
                    {a.health_summary && <HealthView a={a} />}
                    <button onClick={() => { if (confirm('Give up and see what Chaos did?')) act('giveup', () => api.chaosGiveUp(a.id)); }}
                      className="w-full flex items-center justify-center gap-1.5 py-1.5 text-[12px] text-surface-400 hover:text-surface-200">
                      <Flag size={13} /> Give up and show the faults
                    </button>
                  </>
                )}
                {(a.state === 'solved' || a.state === 'revealed') && <ChaosDebrief a={a} onLoaded={text => setA({ ...a, debrief_ai: text })} />}
              </div>
            )}
          </>
        )}
      </div>
    </Frame>
  );
}

function TicketCard({ n, t, difficulty, active, onHint, hinting }:
  { n: number; t: Ticket; difficulty: Difficulty; active: boolean; onHint: () => void; hinting: boolean }) {
  const [open, setOpen] = useState(!active);
  return (
    <div className={`rounded-lg border ${t.resolved ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-surface-600 bg-surface-900/50'}`}>
      <div className="px-3 pt-2.5 pb-2">
        <div className="flex items-center justify-between gap-2 mb-1">
          <span className="text-[11px] uppercase tracking-wide text-surface-400">#{n} · {t.reporter}</span>
          {t.resolved
            ? <span className="flex items-center gap-1 text-[11px] font-semibold text-emerald-300"><CheckCircle2 size={12} /> Cleared</span>
            : <span className="text-[11px] font-semibold text-amber-300">Open</span>}
        </div>
        <p className="text-[13px] text-surface-100 leading-relaxed">{t.ticket}</p>
        {t.hints.length > 0 && (
          <ul className="mt-2 space-y-1">
            {t.hints.map((h, i) => <li key={i} className="flex gap-1.5 text-[12px] text-amber-200"><Lightbulb size={12} className="shrink-0 mt-0.5" /><Markdown text={h} dark /></li>)}
          </ul>
        )}
        {active && difficulty !== 'expert' && t.hints_left > 0 && !t.resolved && (
          <button onClick={onHint} disabled={hinting} className="mt-1.5 flex items-center gap-1 text-[12px] text-sky-300 hover:underline">
            {hinting ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />}
            {t.hints.length ? 'Another hint' : 'Show a hint'}{difficulty === 'standard' ? ' (counted)' : ''}
          </button>
        )}
      </div>
      {t.debrief && (
        <div className="border-t border-surface-700">
          <button onClick={() => setOpen(o => !o)} className="w-full flex items-center gap-1.5 px-3 py-2 text-left text-[12.5px] font-semibold text-rose-200">
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} What Chaos did: {t.debrief.title}
          </button>
          {open && (
            <div className="px-3 pb-3 space-y-2 text-[12.5px]">
              <div className="text-[11px] uppercase tracking-wide text-surface-400">{t.debrief.protocol.toUpperCase()} · {t.debrief.category === 'adjacency' ? 'neighbor adjacency' : 'route advertisement'}</div>
              <Markdown text={t.debrief.explanation} dark />
              <ConfigBlock title="Chaos changed" cfg={t.debrief.changed} tone="text-rose-200" />
              <ConfigBlock title="The fix" cfg={t.debrief.fix} tone="text-emerald-200" />
              {t.debrief.find && <div><div className="font-semibold text-surface-300 mb-0.5">How to spot it</div><Markdown text={t.debrief.find} dark /></div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConfigBlock({ title, cfg, tone }: { title: string; cfg: Record<string, string>; tone: string }) {
  return (
    <div>
      <div className="font-semibold text-surface-300 mb-0.5">{title}</div>
      {Object.entries(cfg).map(([dev, c]) => (
        <div key={dev} className="mb-1">
          <div className="text-[11px] text-surface-400">{dev}</div>
          <pre className={`whitespace-pre-wrap rounded bg-black/30 p-2 font-mono text-[11.5px] ${tone}`}>{c.trim()}</pre>
        </div>
      ))}
    </div>
  );
}

function HealthView({ a }: { a: ScenarioAttemptView }) {
  const [open, setOpen] = useState(false);
  const s = a.health_summary!;
  return (
    <div className="rounded-lg border border-surface-700">
      <button onClick={() => setOpen(o => !o)} disabled={!a.health}
        className="w-full flex items-center justify-between px-3 py-2 text-[12px] text-surface-300">
        <span className="flex items-center gap-1.5">{a.health && (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />)}
          {a.difficulty === 'guided' ? 'Network health' : a.difficulty === 'standard' ? 'Customer-facing monitoring' : 'Network health'}</span>
        <span className={s.ok === s.total ? 'text-emerald-300' : 'text-amber-300'}>{s.ok}/{s.total} checks pass</span>
      </button>
      {open && a.health && (
        <ul className="px-3 pb-2 space-y-0.5">
          {a.health.map((h, i) => (
            <li key={i} className="flex gap-1.5 text-[11.5px]">
              <span className={h.ok ? 'text-emerald-400' : 'text-red-400'}>{h.ok ? '✓' : '✗'}</span>
              <span className="text-surface-300">{h.describe}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RoundSummary({ a }: { a: ScenarioAttemptView }) {
  const solved = a.state === 'solved';
  return (
    <div className={`rounded-lg p-3 ${solved ? 'bg-emerald-500/10 border border-emerald-500/40' : 'bg-surface-900/60 border border-surface-600'}`}>
      <div className={`text-[14px] font-semibold ${solved ? 'text-emerald-200' : 'text-surface-100'}`}>
        {solved ? 'Network restored' : 'Round over — here is what Chaos did'}
      </div>
      <div className="mt-1 text-[12px] text-surface-300 flex flex-wrap gap-x-3">
        <span>{a.tickets.length} fault{a.tickets.length === 1 ? '' : 's'}</span>
        <span>{fmt(a.elapsed)}</span>
        <span>{a.checks_run} check{a.checks_run === 1 ? '' : 's'}</span>
        {a.difficulty !== 'expert' && <span>{a.hints_used} hint{a.hints_used === 1 ? '' : 's'}</span>}
      </div>
    </div>
  );
}

function ChaosDebrief({ a, onLoaded }: { a: ScenarioAttemptView; onLoaded: (t: string) => void }) {
  const [loading, setLoading] = useState(false);
  if (a.debrief_ai) {
    return <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-3"><div className="flex items-center gap-1.5 text-[12px] font-semibold text-rose-200 mb-1"><Sparkles size={13} /> Chaos's debrief</div><Markdown text={a.debrief_ai} dark /></div>;
  }
  return (
    <button disabled={loading} onClick={async () => {
      setLoading(true);
      try {
        const r = await api.chaosDebrief(a.id);
        if (r.text) onLoaded(r.text); else toast('The Chaos debrief needs the AI add-on — the details above are the full story.');
      } catch (e) { toast.error((e as Error).message); }
      finally { setLoading(false); }
    }} className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg border border-rose-500/30 text-[12.5px] text-rose-200 hover:bg-rose-500/10">
      {loading ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />} Ask Chaos for its debrief
    </button>
  );
}

function Frame({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="relative w-[360px] h-full flex flex-col bg-surface-800 text-surface-200">
      <button onClick={onClose} title="Hide the troubleshooting panel"
        className="absolute top-2.5 right-2.5 z-10 p-1 rounded text-surface-400 hover:text-surface-100 hover:bg-surface-700"><X size={15} /></button>
      {children}
    </div>
  );
}

function Tab({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`flex items-center gap-1 px-2.5 py-1 rounded text-[12px] ${on ? 'bg-surface-600 text-white' : 'text-surface-400 hover:text-surface-100'}`}>
      {children}
    </button>
  );
}

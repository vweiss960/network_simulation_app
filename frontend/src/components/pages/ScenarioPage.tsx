/**
 * One troubleshooting scenario: what it covers (never the faults), the
 * topology without design, difficulty and how many faults Chaos creates,
 * and Start / Resume — which builds (or reopens) the learner's lab.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ArrowLeft, Loader2, Play, Siren } from 'lucide-react';
import { api } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { Difficulty, ScenarioDetail } from '../../types';
import TopologyPreview from '../lesson/TopologyPreview';
import { LevelBadge, TrackBadge } from '../lesson/meta';
import { PROTOCOL_LABEL } from './TroubleshootingPage';

export const DIFFICULTY_LABEL: Record<Difficulty, string> = { guided: 'Guided', standard: 'Standard', expert: 'Expert' };

export default function ScenarioPage() {
  const { scenarioId, setPage, openTopology } = useLabStore();
  const [s, setS] = useState<ScenarioDetail | null>(null);
  const [error, setError] = useState('');
  const [difficulty, setDifficulty] = useState<Difficulty>('standard');
  const [faults, setFaults] = useState(1);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!scenarioId) return;
    api.getScenario(scenarioId).then(d => {
      setS(d);
      if (d.attempt) { setDifficulty(d.attempt.difficulty); setFaults(d.attempt.fault_count); }
    }).catch(e => setError((e as Error).message));
  }, [scenarioId]);

  if (error) return <Shell><div className="text-red-600">{error}</div></Shell>;
  if (!s) return <Shell><div className="text-slate-400 flex items-center gap-2"><Loader2 size={15} className="animate-spin" /> Loading…</div></Shell>;

  const start = async () => {
    setStarting(true);
    try {
      const r = await api.startScenario(s.id, difficulty, faults);
      if (r.created) toast.success('Your lab is ready — deploy it, then let Chaos loose');
      if (r.attempt.topology_id) openTopology(r.attempt.topology_id);
    } catch (e) { toast.error((e as Error).message); }
    finally { setStarting(false); }
  };

  return (
    <Shell>
      <button onClick={() => setPage('troubleshooting')} className="flex items-center gap-1 text-[13px] text-blue-700 hover:underline mb-3">
        <ArrowLeft size={14} /> Troubleshooting
      </button>
      <div className="rounded-xl bg-white border border-slate-200 shadow-sm p-6 mb-5">
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <TrackBadge track={s.track} /><LevelBadge level={s.level} />
          {s.protocols.map(p => <span key={p} className="px-1.5 py-px rounded border border-slate-200 bg-slate-50 text-[11.5px] font-medium text-slate-600">{PROTOCOL_LABEL[p] ?? p}</span>)}
        </div>
        <h1 className="text-[26px] leading-tight font-bold text-slate-900 flex items-center gap-2.5"><Siren size={24} className="text-rose-600" />{s.title}</h1>
        <p className="mt-1.5 text-[14px] text-slate-600 leading-relaxed max-w-[760px]">{s.summary}</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-5 items-start">
        <section className="rounded-xl bg-white border border-slate-200 shadow-sm p-5 min-w-0">
          <h2 className="text-[16px] font-semibold text-slate-900 mb-1">The network</h2>
          <p className="text-[12.5px] text-slate-500 mb-3">{s.topology_summary}. Devices and cables only — the design is for you to discover.</p>
          <TopologyPreview topology={s.topology} height={360} />
          <h3 className="text-[13px] font-semibold text-slate-700 mt-4 mb-1.5">What Chaos knows how to break</h3>
          <table className="text-[12.5px] text-slate-600">
            <thead><tr className="text-left text-slate-500"><th className="pr-6 font-medium">Protocol</th><th className="pr-6 font-medium">Neighbor adjacency</th><th className="font-medium">Route advertisement</th></tr></thead>
            <tbody>
              {Object.entries(s.coverage).map(([p, c]) => (
                <tr key={p}><td className="pr-6 py-0.5 font-medium text-slate-800">{PROTOCOL_LABEL[p] ?? p}</td>
                  <td className="pr-6">{c.adjacency ?? 0} faults</td><td>{c.advertisement ?? 0} faults</td></tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="rounded-xl bg-white border border-slate-200 shadow-sm p-5">
          <h2 className="text-[16px] font-semibold text-slate-900 mb-3">Your round</h2>
          <div className="text-[12px] font-semibold text-slate-600 mb-1.5">Difficulty</div>
          <div className="space-y-2 mb-4">
            {(['guided', 'standard', 'expert'] as Difficulty[]).map(d => (
              <label key={d} className={`block rounded-lg border p-2.5 cursor-pointer ${difficulty === d ? 'border-rose-400 bg-rose-50/60' : 'border-slate-200 hover:border-slate-300'}`}>
                <span className="flex items-center gap-2 text-[13.5px] font-semibold text-slate-900">
                  <input type="radio" checked={difficulty === d} onChange={() => setDifficulty(d)} className="accent-rose-600" />
                  {DIFFICULTY_LABEL[d]}
                </span>
                <span className="block text-[12px] text-slate-500 mt-0.5 ml-5">{s.difficulties[d]}</span>
              </label>
            ))}
          </div>
          <div className="text-[12px] font-semibold text-slate-600 mb-1.5">How many faults should Chaos create?</div>
          <div className="flex gap-1.5 mb-1">
            {Array.from({ length: s.max_faults }, (_, i) => i + 1).map(n => (
              <button key={n} onClick={() => setFaults(n)}
                className={`w-10 h-9 rounded-lg border text-[14px] font-semibold ${faults === n ? 'border-rose-500 bg-rose-600 text-white' : 'border-slate-300 text-slate-700 hover:bg-slate-50'}`}>{n}</button>
            ))}
          </div>
          <p className="text-[11.5px] text-slate-500 mb-4">Several faults can interact — fixing one may reveal the next.</p>
          <button onClick={start} disabled={starting}
            className="w-full flex items-center justify-center gap-1.5 px-4 py-2.5 text-sm font-semibold rounded-lg bg-rose-600 hover:bg-rose-500 text-white shadow-sm disabled:opacity-50">
            {starting ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
            {s.attempt ? 'Open my lab' : 'Build my lab'}
          </button>
          <p className="text-[11.5px] text-slate-500 mt-2">
            You get your own copy of the network. Deploy it, and once every device is up you let Chaos loose from the lab.
          </p>
        </section>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800"><div className="max-w-[1160px] mx-auto px-8 py-7">{children}</div></div>;
}

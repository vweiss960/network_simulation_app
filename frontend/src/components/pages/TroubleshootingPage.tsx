/**
 * Troubleshooting scenarios: a known-good network that "Chaos" breaks with
 * faults from a vetted catalog. The learner works the tickets with show
 * commands — no design, no answer key.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Network, Siren, Stethoscope, Ticket } from 'lucide-react';
import { api } from '../../utils/api';
import { useLabStore } from '../../stores/useLabStore';
import type { ScenarioSummary } from '../../types';
import { LevelBadge, TrackBadge } from '../lesson/meta';

export const PROTOCOL_LABEL: Record<string, string> = { bgp: 'BGP', isis: 'IS-IS', ospf: 'OSPF' };

export default function TroubleshootingPage() {
  const { openScenario } = useLabStore();
  const [items, setItems] = useState<ScenarioSummary[] | null>(null);
  useEffect(() => { api.listScenarios().then(setItems).catch(e => { toast.error((e as Error).message); setItems([]); }); }, []);

  return (
    <div className="h-full overflow-y-auto bg-[#f3f5f9] text-slate-800">
      <div className="max-w-[1160px] mx-auto px-8 py-9">
        <h1 className="text-[28px] leading-tight font-bold text-slate-900">Troubleshooting</h1>
        <p className="mt-1 text-sm text-slate-500 max-w-[760px]">
          Each scenario starts as a working network. Then Chaos gets in and breaks it — one fault or several, picked
          from a catalog of each protocol's usual suspects. All you get is the topology and the tickets: find the
          faults with show commands and fix them, without breaking anything else.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 my-6">
          <Step icon={<Siren size={17} />} tint="bg-rose-50 text-rose-600" title="Chaos strikes"
            text="Once the healthy network is verified, Chaos injects the faults you asked for — with names that give nothing away." />
          <Step icon={<Ticket size={17} />} tint="bg-amber-50 text-amber-600" title="Work the tickets"
            text="Customers, peers and monitoring report what they see. You have the CLI of every router." />
          <Step icon={<Stethoscope size={17} />} tint="bg-emerald-50 text-emerald-600" title="Prove the fix"
            text="Check grades the outcome — every ticket cleared and the whole network healthy — then Chaos explains itself." />
        </div>

        {items === null ? <div className="py-10 text-center text-sm text-slate-400">Loading…</div>
          : items.length === 0 ? <div className="rounded-xl bg-white border border-slate-200 py-12 text-center text-sm text-slate-500">No scenarios yet.</div>
          : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {items.map(s => (
                <button key={s.id} onClick={() => openScenario(s.id)}
                  className="text-left rounded-xl bg-white border border-slate-200 shadow-sm p-5 hover:border-rose-300 hover:shadow transition">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-8 h-8 rounded-lg bg-rose-50 text-rose-600 flex items-center justify-center"><Siren size={17} /></span>
                    <TrackBadge track={s.track} /><LevelBadge level={s.level} />
                    {s.protocols.map(p => <span key={p} className="px-1.5 py-px rounded border border-slate-200 bg-slate-50 text-[11.5px] font-medium text-slate-600">{PROTOCOL_LABEL[p] ?? p}</span>)}
                  </div>
                  <div className="text-[16.5px] font-semibold text-slate-900 leading-snug">{s.title}</div>
                  <p className="mt-1.5 text-[13px] text-slate-600 leading-relaxed line-clamp-3">{s.summary}</p>
                  <div className="mt-3 flex items-center gap-4 text-[12.5px] text-slate-500">
                    <span className="flex items-center gap-1"><Network size={13} /> {s.topology_summary}</span>
                    <span>{Object.values(s.coverage).reduce((n, c) => n + Object.values(c).reduce((a, b) => a + b, 0), 0)} faults in the catalog</span>
                    <span>up to {s.max_faults} at once</span>
                  </div>
                  {s.attempt && (
                    <div className="mt-2 text-[12.5px] font-semibold text-rose-700">
                      {s.attempt.state === 'active' ? 'Round in progress — resume' : `Open your lab · ${s.attempt.rounds} round${s.attempt.rounds === 1 ? '' : 's'} played`}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
      </div>
    </div>
  );
}

function Step({ icon, tint, title, text }: { icon: React.ReactNode; tint: string; title: string; text: string }) {
  return (
    <div className="flex gap-3 p-4 rounded-xl bg-white border border-slate-200 shadow-sm">
      <div className={`w-9 h-9 shrink-0 rounded-lg flex items-center justify-center ${tint}`}>{icon}</div>
      <div>
        <div className="text-[14px] font-semibold text-slate-900">{title}</div>
        <div className="text-[13px] text-slate-500 leading-snug mt-0.5">{text}</div>
      </div>
    </div>
  );
}

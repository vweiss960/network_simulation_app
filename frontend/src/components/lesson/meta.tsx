/** Labels and badges shared by the lesson pages. */
import type { AIPolicy, HintsMode, LessonLevel, LessonTrack } from '../../types';

export const LEVELS: LessonLevel[] = ['beginner', 'intermediate', 'advanced'];
export const TRACKS: LessonTrack[] = ['enterprise', 'service_provider', 'data_center', 'automation', 'security', 'general'];

export const LEVEL_LABEL: Record<LessonLevel, string> = { beginner: 'Beginner', intermediate: 'Intermediate', advanced: 'Advanced' };
export const TRACK_LABEL: Record<LessonTrack, string> = {
  enterprise: 'Enterprise', service_provider: 'Service Provider', data_center: 'Data Center',
  automation: 'Automation', security: 'Security', general: 'General',
};
export const AI_LABEL: Record<AIPolicy, string> = { teach: 'Teach', hints: 'Hints only', off: 'Off' };
export const AI_HELP: Record<AIPolicy, string> = {
  teach: 'The assistant explains concepts and can configure devices when asked.',
  hints: 'The assistant can look at the lab and nudge, but never applies config or gives the full answer.',
  off: 'The assistant is turned off in this lesson.',
};
export const HINTS_LABEL: Record<HintsMode, string> = { open: 'Shown', on_request: 'On request', none: 'None' };

const LEVEL_STYLE: Record<LessonLevel, string> = {
  beginner: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  intermediate: 'bg-blue-50 text-blue-700 border-blue-200',
  advanced: 'bg-violet-50 text-violet-700 border-violet-200',
};
const TRACK_STYLE: Record<LessonTrack, string> = {
  enterprise: 'bg-sky-50 text-sky-800 border-sky-200',
  service_provider: 'bg-indigo-50 text-indigo-800 border-indigo-200',
  data_center: 'bg-cyan-50 text-cyan-800 border-cyan-200',
  automation: 'bg-amber-50 text-amber-800 border-amber-200',
  security: 'bg-rose-50 text-rose-800 border-rose-200',
  general: 'bg-slate-50 text-slate-700 border-slate-200',
};

export function LevelBadge({ level }: { level: LessonLevel }) {
  return <span className={`inline-block px-2 py-px rounded-full border text-[12px] font-medium ${LEVEL_STYLE[level]}`}>{LEVEL_LABEL[level]}</span>;
}

export function TrackBadge({ track }: { track: LessonTrack }) {
  return <span className={`inline-block px-2 py-px rounded-md border text-[11.5px] font-medium ${TRACK_STYLE[track]}`}>{TRACK_LABEL[track]}</span>;
}

export function DraftBadge() {
  return <span className="inline-block px-1.5 py-px rounded border border-amber-300 bg-amber-50 text-amber-700 text-[11px] font-medium">Draft</span>;
}

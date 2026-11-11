import { Search, X } from 'lucide-react';
import Highlight from './Highlight';
import { snippet, type SearchHit } from '../utils/search';

/** Panel search: input + field chips (All or one field) + result count. */
export default function SearchBox({ query, onQuery, scope, onScope, fields, shown, total, noun, children }: {
  query: string;
  onQuery: (q: string) => void;
  scope: string;
  onScope: (s: string) => void;
  fields: { key: string; label: string }[];
  shown: number;
  total: number;
  /** Plural item name for the count, e.g. "flows". */
  noun: string;
  /** Extra controls on the input row (e.g. a "Delete all" button). */
  children?: React.ReactNode;
}) {
  const scoped = fields.find(f => f.key === scope);
  const chip = (key: string, label: string) => (
    <button
      key={key}
      type="button"
      onClick={() => onScope(key)}
      className={`px-2 py-0.5 rounded-full text-[10px] border transition-colors ${
        scope === key
          ? 'bg-primary-600/25 border-primary-500/50 text-primary-100'
          : 'border-surface-700 text-surface-400 hover:text-surface-200 hover:border-surface-600'
      }`}
    >
      {label}
    </button>
  );
  return (
    <div className="space-y-1.5">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-surface-500" />
          <input
            className="w-full pl-8 pr-7 text-xs"
            placeholder={scoped ? `Search ${scoped.label.toLowerCase()}…` : `Search ${noun}…`}
            value={query}
            onChange={e => onQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Escape' && query) { e.stopPropagation(); onQuery(''); } }}
          />
          {query && (
            <button
              type="button"
              onClick={() => onQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-surface-500 hover:text-surface-200"
              title="Clear search (Esc)"
              aria-label="Clear search"
            >
              <X size={12} />
            </button>
          )}
        </div>
        {children}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {chip('all', 'All')}
        {fields.map(f => chip(f.key, f.label))}
        {query.trim() && (
          <span className="ml-auto text-[10px] text-surface-500">{shown} of {total} {noun}</span>
        )}
      </div>
    </div>
  );
}

/** "in description: …match…" lines for hits in fields the row doesn't show. */
export function MatchedIn({ hits, hidden, query }: { hits: SearchHit[]; hidden: string[]; query: string }) {
  const extra = hits.filter(h => hidden.includes(h.field));
  if (!extra.length) return null;
  return (
    <div className="text-[9px] text-surface-500 truncate">
      {extra.map((h, i) => (
        <span key={h.field}>
          {i > 0 && ' · '}in {h.label.toLowerCase()}: <span className="text-surface-300"><Highlight text={snippet(h.text, query)} query={query} /></span>
        </span>
      ))}
    </div>
  );
}

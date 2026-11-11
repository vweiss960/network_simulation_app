import { useState } from 'react';
import type { NodeInstance } from '../types';

/** Case-insensitive name filter shared by the node search boxes. */
export function matchNodes(nodes: NodeInstance[], query: string, limit = 8): NodeInstance[] {
  const q = query.trim().toLowerCase();
  return (q ? nodes.filter(n => n.name.toLowerCase().includes(q)) : nodes).slice(0, limit);
}

/** Up/Down arrow handling for a suggestion list (wraps; -1 = none highlighted).
 *  Returns true if it consumed the key. */
export function moveHighlight(e: React.KeyboardEvent, count: number, setHl: (f: (i: number) => number) => void): boolean {
  if (!count || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return false;
  e.preventDefault();
  setHl(e.key === 'ArrowDown' ? i => (i + 1) % count : i => (i <= 0 ? count - 1 : i - 1));
  return true;
}

/** The "/" hotkey search: type a node name, arrow through matches, Enter to jump. */
export default function NodeSearchBox({ nodes, onPick, onClose, emptyText = 'No matching nodes', focusClass = 'focus:border-primary-500' }: {
  nodes: NodeInstance[];
  onPick: (n: NodeInstance) => void;
  onClose: () => void;
  emptyText?: string;
  focusClass?: string;
}) {
  const [query, setQuery] = useState('');
  const [hl, setHl] = useState(0);
  const matches = matchNodes(nodes, query);
  const active = Math.min(hl, matches.length - 1);

  return (
    <div className="absolute top-3 left-1/2 -translate-x-1/2 z-30 w-[300px]">
      <input
        autoFocus
        value={query}
        onChange={e => { setQuery(e.target.value); setHl(0); }}
        onKeyDown={e => {
          if (moveHighlight(e, matches.length, setHl)) return;
          if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
          if (e.key === 'Enter' && matches.length) onPick(matches[Math.max(0, active)]);
        }}
        onBlur={() => setTimeout(onClose, 150)}
        placeholder="Search nodes…  (↑↓ to choose, Enter to jump)"
        className={`w-full text-[12px] bg-surface-900/95 border border-surface-700 rounded px-2.5 py-1.5 text-surface-100 placeholder-surface-500 shadow-lg backdrop-blur-sm focus:outline-none ${focusClass}`}
      />
      {matches.length > 0 && (
        <div className="mt-1 bg-surface-900/95 border border-surface-700 rounded shadow-lg overflow-hidden">
          {matches.map((n, i) => (
            <button
              key={n.id}
              onMouseDown={e => e.preventDefault()}
              onMouseEnter={() => setHl(i)}
              onClick={() => onPick(n)}
              className={`w-full text-left text-[11px] text-surface-200 px-2.5 py-1.5 border-b border-surface-800 last:border-0 ${i === active ? 'bg-surface-700' : ''}`}
            >{n.name}{n.role ? <span className="text-surface-500"> · {n.role}</span> : null}</button>
          ))}
        </div>
      )}
      {query.trim() && matches.length === 0 && (
        <div className="mt-1 bg-surface-900/95 border border-surface-700 rounded px-2.5 py-1.5 text-[11px] text-surface-400 shadow-lg">{emptyText}</div>
      )}
    </div>
  );
}

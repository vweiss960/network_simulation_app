// Field-aware panel search (Flows / Links / Nodes). Each panel declares the
// fields it searches; the user can search all of them or scope to one, and
// results report which fields matched so rows can show it.

export interface SearchField<T> {
  key: string;
  label: string;
  /** Text to search for this item; several values (e.g. both link ends) allowed. */
  get: (item: T) => string | null | undefined | (string | null | undefined)[];
}

export interface SearchHit {
  field: string;
  label: string;
  /** The matching value (for "matched in" snippets). */
  text: string;
}

/** Case-insensitive, trimmed substring match of `query` against `item`'s
 *  fields — all of them, or only `scope`. An empty query matches everything. */
export function matchItem<T>(item: T, fields: SearchField<T>[], query: string, scope: string): { ok: boolean; hits: SearchHit[] } {
  const q = query.trim().toLowerCase();
  if (!q) return { ok: true, hits: [] };
  const hits: SearchHit[] = [];
  for (const f of fields) {
    if (scope !== 'all' && f.key !== scope) continue;
    const raw = f.get(item);
    const values = (Array.isArray(raw) ? raw : [raw]).filter((v): v is string => !!v);
    const hit = values.find(v => v.toLowerCase().includes(q));
    if (hit) hits.push({ field: f.key, label: f.label, text: hit });
  }
  return { ok: hits.length > 0, hits };
}

/** ~`width` characters of `text` around the first match of `query`. */
export function snippet(text: string, query: string, width = 40): string {
  const q = query.trim().toLowerCase();
  const i = text.toLowerCase().indexOf(q);
  if (i < 0 || text.length <= width) return text;
  const start = Math.max(0, i - Math.floor((width - q.length) / 2));
  const end = Math.min(text.length, start + width);
  return `${start > 0 ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}`;
}

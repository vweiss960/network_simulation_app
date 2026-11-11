import { Fragment } from 'react';

/** Renders `text` with every case-insensitive occurrence of `query` highlighted. */
export default function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim();
  if (!q || !text) return <>{text}</>;
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (let i = lower.indexOf(needle); i >= 0; i = lower.indexOf(needle, i + needle.length)) {
    if (i > at) parts.push(<Fragment key={`t${at}`}>{text.slice(at, i)}</Fragment>);
    parts.push(<mark key={`m${i}`} className="bg-amber-500/25 text-amber-100 rounded-sm">{text.slice(i, i + needle.length)}</mark>);
    at = i + needle.length;
  }
  if (at < text.length) parts.push(<Fragment key={`t${at}`}>{text.slice(at)}</Fragment>);
  return <>{parts}</>;
}

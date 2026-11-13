/**
 * Minimal Markdown for agent replies: fenced code (with a copy button),
 * headings, bullet / numbered lists, tables, bold, italics, inline code,
 * links, paragraphs. Escapes everything else — no raw HTML is rendered.
 */
import { useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';

function copy(text: string): boolean {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="my-2 rounded-lg overflow-hidden border border-slate-700 bg-[#1e1e1e]">
      <div className="flex items-center justify-between px-3 py-1 bg-[#252526] text-[11px] text-slate-400">
        <span className="font-mono">{lang || 'config'}</span>
        <button className="flex items-center gap-1 hover:text-white"
          onClick={() => { if (copy(code)) { setDone(true); setTimeout(() => setDone(false), 1500); } }}>
          {done ? <Check size={12} /> : <Copy size={12} />} {done ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="px-3 py-2 overflow-x-auto text-[12.5px] leading-[1.45] text-[#d4d4d4] font-mono whitespace-pre">{code}</pre>
    </div>
  );
}

/** Inline: `code`, **bold**, *italic* / _italic_, [text](url). */
function inline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))|(\*[^*\s][^*]*\*)|(_[^_\s][^_]*_)/g;
  let last = 0, m: RegExpExecArray | null, i = key;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (m[1]) out.push(<code key={i++} className="px-1 py-px rounded bg-slate-100 text-[12.5px] font-mono text-slate-800">{tok.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={i++} className="font-semibold text-slate-900">{tok.slice(2, -2)}</strong>);
    else if (m[3]) {
      const label = tok.slice(1, tok.indexOf(']'));
      out.push(<a key={i++} href={m[4]} target="_blank" rel="noreferrer" className="text-blue-700 hover:underline">{label}</a>);
    } else out.push(<em key={i++}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0, k = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i++;
      blocks.push(<CodeBlock key={k++} lang={fence[1]} code={body.join('\n')} />);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const size = h[1].length <= 2 ? 'text-[15px]' : 'text-[14px]';
      blocks.push(<div key={k++} className={`${size} font-semibold text-slate-900 mt-3 mb-1`}>{inline(h[2])}</div>);
      i++;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const row = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      const head = row(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(row(lines[i++]));
      blocks.push(
        <div key={k++} className="my-2 overflow-x-auto">
          <table className="text-[12.5px] border-collapse">
            <thead><tr>{head.map((c, j) => <th key={j} className="text-left font-semibold border-b border-slate-300 px-2 py-1">{inline(c)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j} className="border-b border-slate-200 px-2 py-1 align-top">{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>);
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ''));
        i++;
      }
      const L = ordered ? 'ol' : 'ul';
      blocks.push(<L key={k++} className={`my-1.5 pl-5 space-y-0.5 ${ordered ? 'list-decimal' : 'list-disc'}`}>
        {items.map((it, j) => <li key={j}>{inline(it)}</li>)}</L>);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])
           && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={k++} className="my-1.5">{inline(para.join(' '))}</p>);
  }
  return <div className="text-[13.5px] leading-relaxed text-slate-700">{blocks}</div>;
}

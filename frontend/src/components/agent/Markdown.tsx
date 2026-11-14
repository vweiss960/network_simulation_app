/**
 * Minimal Markdown for agent replies and lesson text: fenced code (with a
 * copy button), headings, bullet / numbered lists, tables, blockquotes,
 * horizontal rules, bold, italics, inline code, links, paragraphs. Escapes
 * everything else — no raw HTML is rendered. `dark` styles it for the dark
 * workspace panels.
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

const LIGHT = {
  body: 'text-slate-700', strong: 'text-slate-900', head: 'text-slate-900',
  code: 'bg-slate-100 text-slate-800', link: 'text-blue-700', th: 'border-slate-300', td: 'border-slate-200',
  quote: 'border-blue-300 bg-blue-50/60 text-slate-700', hr: 'border-slate-200',
};
const DARK = {
  body: 'text-slate-300', strong: 'text-white', head: 'text-white',
  code: 'bg-white/10 text-slate-100', link: 'text-sky-300', th: 'border-slate-600', td: 'border-slate-700',
  quote: 'border-sky-500/60 bg-sky-500/10 text-slate-200', hr: 'border-slate-700',
};
type Theme = typeof LIGHT;

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
function inline(text: string, t: Theme): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))|(\*[^*\s][^*]*\*)|(_[^_\s][^_]*_)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (m[1]) out.push(<code key={i++} className={`px-1 py-px rounded text-[12.5px] font-mono ${t.code}`}>{tok.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={i++} className={`font-semibold ${t.strong}`}>{tok.slice(2, -2)}</strong>);
    else if (m[3]) {
      const label = tok.slice(1, tok.indexOf(']'));
      out.push(<a key={i++} href={m[4]} target="_blank" rel="noreferrer" className={`${t.link} hover:underline`}>{label}</a>);
    } else out.push(<em key={i++}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const LIST = /^\s*([-*•]|\d+[.)])\s+/;
const TABLE = /^\s*\|.*\|\s*$/;
const HR = /^\s*([-*_])(\s*\1){2,}\s*$/;

function blocksOf(text: string, t: Theme): ReactNode[] {
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
      blocks.push(<div key={k++} className={`${size} font-semibold ${t.head} mt-3 mb-1`}>{inline(h[2], t)}</div>);
      i++;
      continue;
    }
    if (HR.test(line)) {
      blocks.push(<hr key={k++} className={`my-3 ${t.hr}`} />);
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ''));
      blocks.push(<div key={k++} className={`my-2 pl-3 pr-2 py-1.5 border-l-[3px] rounded-r ${t.quote}`}>{blocksOf(body.join('\n'), t)}</div>);
      continue;
    }
    if (TABLE.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const row = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      const head = row(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && TABLE.test(lines[i])) rows.push(row(lines[i++]));
      blocks.push(
        <div key={k++} className="my-2 overflow-x-auto">
          <table className="text-[12.5px] border-collapse">
            <thead><tr>{head.map((c, j) => <th key={j} className={`text-left font-semibold border-b px-2 py-1 ${t.th} ${t.strong}`}>{inline(c, t)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j} className={`border-b px-2 py-1 align-top ${t.td}`}>{inline(c, t)}</td>)}</tr>)}</tbody>
          </table>
        </div>);
      continue;
    }
    if (LIST.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line);
      const items: string[] = [];
      while (i < lines.length && LIST.test(lines[i])) {
        let item = lines[i].replace(LIST, '');
        i++;
        // Continuation lines (indented, not a new item) belong to this item.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !LIST.test(lines[i])) item += ' ' + lines[i++].trim();
        items.push(item);
      }
      const L = ordered ? 'ol' : 'ul';
      blocks.push(<L key={k++} className={`my-1.5 pl-5 space-y-0.5 ${ordered ? 'list-decimal' : 'list-disc'}`}>
        {items.map((it, j) => <li key={j}>{inline(it, t)}</li>)}</L>);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])
           && !LIST.test(lines[i]) && !TABLE.test(lines[i]) && !/^\s*>/.test(lines[i]) && !HR.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={k++} className="my-1.5">{inline(para.join(' '), t)}</p>);
  }
  return blocks;
}

export default function Markdown({ text, dark = false }: { text: string; dark?: boolean }) {
  const t = dark ? DARK : LIGHT;
  return <div className={`text-[13.5px] leading-relaxed ${t.body}`}>{blocksOf(text, t)}</div>;
}

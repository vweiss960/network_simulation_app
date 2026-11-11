import type { SiteTemplateDefinition } from '../types';

// Same placeholder syntax as backend services/site_templates.py.
const VAR_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

export const substitute = (text: string, values: Record<string, string>) =>
  (text || '').replace(VAR_RE, (_, name) => values[name] ?? '');

/** Every {{name}} used anywhere in the definition's text fields. */
export function usedVariables(def: SiteTemplateDefinition): Set<string> {
  const texts: string[] = [
    ...def.nodes.flatMap(n => [n.name, n.location, n.mgmt_ip]),
    ...def.links.flatMap(l => [l.carrier, l.circuit_id, l.link_id_label]),
    ...def.patch_panels.flatMap(p => [p.name, p.location, p.rack, p.notes,
      ...Object.values(p.ports).flatMap(port => [port.label, port.notes])]),
  ];
  const out = new Set<string>();
  for (const t of texts) for (const m of (t || '').matchAll(VAR_RE)) out.add(m[1]);
  return out;
}

/** "API error 400: {"detail":"..."}" -> the server's message. */
export function apiErrorMessage(e: any): string {
  const m = String(e?.message || e);
  const body = m.replace(/^API error \d+: /, '');
  try {
    const d = JSON.parse(body)?.detail;
    if (typeof d === 'string') return d;
    if (d?.message) return d.message;
  } catch { /* not JSON */ }
  return body;
}

/**
 * Fill in a site template's variables and drop the site (devices, links,
 * patch panels) into the current topology at the right-clicked point.
 * Opened from the canvas pane menu → Add Site Template.
 */
import { useState } from 'react';
import toast from 'react-hot-toast';
import { Building2, X } from 'lucide-react';
import { useStore } from '../stores/useStore';
import { apiErrorMessage, substitute } from '../utils/siteTemplates';
import type { SiteTemplate } from '../types';

interface Props {
  template: SiteTemplate;
  position: { x: number; y: number };
  onClose: () => void;
}

export default function SiteTemplateInstantiateModal({ template, position, onClose }: Props) {
  const instantiate = useStore(s => s.instantiateSiteTemplate);
  const panelsEnabled = !!useStore(s => s.features)?.patch_panels_enabled;
  const isChild = useStore(s => !!s.topologies.find(t => t.id === s.currentTopologyId)?.parent_topology_id);
  const existingNames = useStore(s => s.nodes).map(n => n.name);
  const d = template.definition;
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(d.variables.map(v => [v.name, v.default])));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const filled = Object.fromEntries(d.variables.map(v => [v.name, (values[v.name] ?? '').trim() || v.default.trim()]));
  const missing = d.variables.filter(v => v.required && !filled[v.name]);
  const names = d.nodes.map(n => substitute(n.name, filled));
  const clashes = names.filter(n => existingNames.includes(n));
  const panelsSkipped = d.patch_panels.length > 0 && (!panelsEnabled || isChild);

  const submit = async () => {
    if (missing.length || clashes.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await instantiate(template.id, filled, position.x, position.y);
      const parts = [`${res.nodes.length} device${res.nodes.length !== 1 ? 's' : ''}`];
      if (d.links.length) parts.push(`${d.links.length} link${d.links.length !== 1 ? 's' : ''}`);
      if (res.patch_panels) parts.push(`${res.patch_panels} patch panel${res.patch_panels !== 1 ? 's' : ''}`);
      toast.success(`Added ${template.name}: ${parts.join(', ')}`);
      res.warnings.forEach(w => toast(w, { icon: '⚠️' }));
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e));
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-surface-800 border border-surface-600 rounded-lg shadow-xl p-4 w-[420px] max-w-[calc(100vw-32px)] fade-in"
        onKeyDown={e => { if (e.key === 'Enter' && !(e.target instanceof HTMLButtonElement)) submit(); if (e.key === 'Escape') onClose(); }}>
        <div className="flex items-center justify-between mb-1">
          <div className="text-sm font-medium flex items-center gap-1.5"><Building2 size={14} /> Add site: {template.name}</div>
          <button className="text-surface-400 hover:text-surface-200" onClick={onClose}><X size={16} /></button>
        </div>
        {template.description && <div className="text-[11px] text-surface-400 mb-2">{template.description}</div>}

        <div className="space-y-2 my-3">
          {d.variables.map((v, i) => (
            <label key={v.name} className="text-[11px] text-surface-300 block">
              {v.label || v.name}{v.required && <span className="text-red-400"> *</span>}
              <span className="text-surface-500 font-mono ml-1.5">{`{{${v.name}}}`}</span>
              <input className="w-full text-xs mt-0.5" autoFocus={i === 0} value={values[v.name] ?? ''}
                placeholder={v.default || ''}
                onChange={e => { setValues({ ...values, [v.name]: e.target.value }); setError(null); }} />
            </label>
          ))}
          {d.variables.length === 0 && <div className="text-[11px] text-surface-500">This template has no variables.</div>}
        </div>

        <div className="bg-surface-900 border border-surface-700 rounded p-2 text-[11px] mb-3">
          <div className="text-surface-400 mb-1">Will add</div>
          {d.nodes.map((n, i) => (
            <div key={n.key} className={`font-mono ${clashes.includes(names[i]) ? 'text-red-400' : 'text-surface-200'}`}>
              {names[i] || <span className="text-surface-500 italic">(name pending)</span>}
              {substitute(n.location, filled) && <span className="text-surface-500"> @ {substitute(n.location, filled)}</span>}
              {clashes.includes(names[i]) && <span className="font-sans"> — already exists</span>}
            </div>
          ))}
          <div className="text-surface-400 mt-1">
            {d.links.length} link{d.links.length !== 1 ? 's' : ''}
            {d.patch_panels.length > 0 && !panelsSkipped && `, ${d.patch_panels.length} patch panel${d.patch_panels.length !== 1 ? 's' : ''}`}
          </div>
          {panelsSkipped && (
            <div className="text-amber-400 mt-1">
              {d.patch_panels.length} patch panel{d.patch_panels.length !== 1 ? 's' : ''} will be skipped — {isChild ? 'not supported on planning layers' : 'the patch panel module is off'}.
            </div>
          )}
        </div>

        {error && <div className="text-xs text-red-400 mb-2">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn btn-ghost btn-sm text-xs" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary btn-sm text-xs" onClick={submit} disabled={busy || missing.length > 0 || clashes.length > 0}>
            {busy ? 'Adding…' : 'Add site'}
          </button>
        </div>
      </div>
    </div>
  );
}

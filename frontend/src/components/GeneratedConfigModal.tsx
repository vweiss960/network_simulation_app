/**
 * GeneratedConfigModal — view the cached generated_config for one device.
 *
 * Fetches /api/config-templates/generated/{node_id}, which returns the last
 * successful render Phanero cached on that node (set by the bulk "Render all
 * devices" action). Provides copy-to-clipboard and download-as-.cfg shortcuts.
 *
 * Used from the Nodes panel (button in the node editor) and from the canvas
 * right-click menu. Both share this one modal — pass the node_id when opening,
 * pass null/undefined to close.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Copy, Download, RefreshCw, X, FileText } from 'lucide-react';
import { api } from '../utils/api';

interface Props {
  nodeId: number | null;
  nodeName?: string;
  onClose: () => void;
}

export default function GeneratedConfigModal({ nodeId, nodeName, onClose }: Props) {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<{
    generated_config: string;
    generated_lab_config?: string;
    template_name: string | null;
    template_id: number | null;
    config_template_id: number | null;
    config_template_group_id?: number | null;
    resolved_kind?: 'template' | 'group' | null;
    resolved_name?: string | null;
  } | null>(null);
  const [error, setError] = useState('');
  // Which render to show: design-side (default) or lab-translated. The lab
  // tab is only visible when a non-empty generated_lab_config exists.
  const [variant, setVariant] = useState<'design' | 'lab'>('design');

  const fetchConfig = async (id: number) => {
    setLoading(true);
    setError('');
    try {
      const r = await api.getGeneratedConfig(id);
      setData(r);
    } catch (e: any) {
      setError(e?.message || String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (nodeId == null) { setData(null); setError(''); return; }
    fetchConfig(nodeId);
  }, [nodeId]);

  if (nodeId == null) return null;

  const hasLab = !!data?.generated_lab_config;
  const body = variant === 'lab' && hasLab
    ? (data?.generated_lab_config || '')
    : (data?.generated_config || '');
  const isOverride = data != null && (data.resolved_kind === 'group'
    ? data.config_template_group_id != null
    : data.config_template_id != null);
  const onCopy = () => {
    navigator.clipboard.writeText(body).then(
      () => toast.success('Copied'),
      () => toast.error('Copy failed'),
    );
  };
  const onDownload = () => {
    const blob = new Blob([body], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${nodeName || `node-${nodeId}`}${variant === 'lab' ? '-lab' : ''}.cfg`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-surface-800 border border-surface-600 rounded-lg w-[800px] max-w-full max-h-[80vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700">
          <div className="flex items-center gap-2 min-w-0">
            <FileText size={14} className="text-amber-400 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">
                Generated config — {nodeName || `node ${nodeId}`}
              </div>
              {data?.resolved_name && (
                <div className="text-[10px] text-surface-500">
                  {data.resolved_kind === 'group' ? 'Group' : 'Template'}: <span className="font-mono text-amber-300">{data.resolved_name}</span>
                  {isOverride
                    ? <span className="ml-1 text-[9px] bg-surface-700 px-1 rounded">per-device override</span>
                    : <span className="ml-1 text-[9px] text-surface-600">(via default rule)</span>}
                </div>
              )}
              {!loading && data && !data.resolved_name && (
                <div className="text-[10px] text-amber-400">
                  No template or group resolves for this device (no per-device override and no default rule matches).
                </div>
              )}
            </div>
          </div>
          <button className="btn btn-ghost btn-sm p-1" onClick={onClose}>
            <X size={14} />
          </button>
        </div>

        {/* Variant tabs — Design = original interface names, Lab = lab
            interface names (used as the startup-config when deployed). */}
        {hasLab && (
          <div className="flex items-stretch border-b border-surface-700">
            {(['design', 'lab'] as const).map(v => (
              <button
                key={v}
                className={`px-4 py-1.5 text-[11px] font-medium border-b-2 transition-colors ${
                  variant === v
                    ? 'border-amber-500 text-surface-100'
                    : 'border-transparent text-surface-400 hover:text-surface-200'
                }`}
                onClick={() => setVariant(v)}
                title={v === 'design'
                  ? 'Rendered against design-view interface names (eg GigabitEthernet0/0/0/1)'
                  : 'Rendered against lab interface names (eg Gi0-0-0-0). This is what clab loads as startup-config on deploy.'}
              >
                {v === 'design' ? 'Design' : 'Lab'}
              </button>
            ))}
          </div>
        )}

        <div className="flex-1 min-h-0 overflow-y-auto p-4">
          {loading && (
            <div className="flex items-center gap-2 text-surface-400 text-xs">
              <RefreshCw size={12} className="animate-spin" /> Loading…
            </div>
          )}
          {!loading && error && (
            <div className="text-[11px] text-red-300 bg-red-900/20 border border-red-700/40 rounded p-3">
              {error}
            </div>
          )}
          {!loading && !error && body && (
            <pre className="text-[11px] font-mono bg-surface-950 border border-surface-700 rounded p-3 whitespace-pre-wrap break-all">
              {body}
            </pre>
          )}
          {!loading && !error && !body && (
            <div className="text-[11px] text-surface-500 text-center py-6">
              No generated config cached for this device yet. In Config Design view, click
              <span className="font-mono mx-1">Render all devices</span>
              to populate it.
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 px-4 py-2 border-t border-surface-700">
          <button
            className="btn btn-ghost btn-sm text-[11px]"
            onClick={() => nodeId != null && fetchConfig(nodeId)}
            disabled={loading}
            title="Refetch from the database"
          >
            <RefreshCw size={11} /> Refresh
          </button>
          <div className="flex-1" />
          <button
            className="btn btn-ghost btn-sm text-[11px]"
            onClick={onCopy}
            disabled={!body}
          >
            <Copy size={11} /> Copy
          </button>
          <button
            className="btn btn-primary btn-sm text-[11px]"
            onClick={onDownload}
            disabled={!body}
          >
            <Download size={11} /> Download .cfg
          </button>
        </div>
      </div>
    </div>
  );
}

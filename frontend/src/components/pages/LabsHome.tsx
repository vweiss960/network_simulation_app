import { useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Copy, Download, FolderOpen, Plus, Trash2, Upload } from 'lucide-react';
import { useLabStore } from '../../stores/useLabStore';
import { api } from '../../utils/api';
import type { Topology } from '../../types';

const STATUS_DOT: Record<string, string> = {
  idle: 'bg-surface-500', running: 'bg-emerald-400', deploying: 'bg-sky-400 animate-pulse',
  syncing: 'bg-sky-400 animate-pulse', destroying: 'bg-amber-400 animate-pulse', failed: 'bg-red-500',
};

export default function LabsHome() {
  const { topologies, loadTopologies, openTopology } = useLabStore();
  const [name, setName] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const create = async () => {
    try {
      const t = await api.createTopology(name.trim() || 'Untitled lab');
      setName('');
      openTopology(t.id);
    } catch (e) { toast.error((e as Error).message); }
  };

  const onImport = async (f: File | undefined) => {
    if (!f) return;
    try {
      const t = await api.importTopology(f);
      toast.success(`Imported ${t.name}`);
      loadTopologies();
    } catch (e) { toast.error((e as Error).message); }
    if (fileRef.current) fileRef.current.value = '';
  };

  const exportYaml = async (t: Topology) => {
    const text = await api.exportTopology(t.id);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'application/x-yaml' }));
    a.download = `${t.name.replace(/[^\w.-]+/g, '_')}.plabs.yml`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const remove = async (t: Topology) => {
    const msg = t.lab_status !== 'idle'
      ? `Delete "${t.name}"? Its running lab will be destroyed.` : `Delete "${t.name}"?`;
    if (!confirm(msg)) return;
    const id = toast.loading(`Deleting ${t.name}…`);
    try { await api.deleteTopology(t.id); toast.success('Deleted', { id }); loadTopologies(); }
    catch (e) { toast.error((e as Error).message, { id }); }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto p-6">
        <div className="flex items-end justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold">Labs</h1>
            <p className="text-sm text-surface-400">Each lab is a canvas of real devices. Deploy it and every device gets its own management IP.</p>
          </div>
          <div className="flex gap-2">
            <input value={name} onChange={e => setName(e.target.value)} onKeyDown={e => e.key === 'Enter' && create()}
              placeholder="New lab name" className="px-3 py-1.5 text-sm rounded bg-surface-800 border border-surface-700 focus:border-primary-500 outline-none" />
            <button onClick={create} className="whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-primary-600 hover:bg-primary-500 text-white">
              <Plus size={15} /> New lab
            </button>
            <button onClick={() => fileRef.current?.click()} title="Import a Phanero Labs or containerlab YAML file"
              className="whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-surface-700 hover:bg-surface-600">
              <Upload size={15} /> Import
            </button>
            <input ref={fileRef} type="file" accept=".yml,.yaml" className="hidden" onChange={e => onImport(e.target.files?.[0])} />
          </div>
        </div>

        {topologies.length === 0 ? (
          <div className="border border-dashed border-surface-600 rounded-lg p-12 text-center text-surface-400">
            No labs yet. Create one, or import a containerlab <code>.clab.yml</code>.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {topologies.map(t => (
              <div key={t.id} className="group rounded-lg border border-surface-700 bg-surface-800/60 hover:border-surface-500 p-4 flex flex-col">
                <button className="text-left flex-1" onClick={() => openTopology(t.id)}>
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${STATUS_DOT[t.lab_status] ?? STATUS_DOT.idle}`} />
                    <span className="font-medium text-surface-100 truncate">{t.name}</span>
                  </div>
                  <div className="text-xs text-surface-400 mt-1">{t.node_count} devices · {t.link_count} cables · {t.lab_status === 'idle' ? 'not deployed' : t.lab_status}</div>
                  {t.description && <div className="text-xs text-surface-500 mt-2 line-clamp-2">{t.description}</div>}
                </button>
                <div className="flex gap-3 mt-3 text-surface-400 text-xs opacity-70 group-hover:opacity-100">
                  <button className="flex items-center gap-1 hover:text-surface-100" onClick={() => openTopology(t.id)}><FolderOpen size={13} /> Open</button>
                  <button className="flex items-center gap-1 hover:text-surface-100"
                    onClick={async () => { await api.duplicateTopology(t.id); loadTopologies(); }}><Copy size={13} /> Duplicate</button>
                  <button className="flex items-center gap-1 hover:text-surface-100" onClick={() => exportYaml(t)}><Download size={13} /> Export</button>
                  <div className="flex-1" />
                  <button className="flex items-center gap-1 hover:text-red-300" onClick={() => remove(t)}><Trash2 size={13} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

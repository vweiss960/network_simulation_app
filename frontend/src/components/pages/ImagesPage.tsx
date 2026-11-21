import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { Upload } from 'lucide-react';
import { api } from '../../utils/api';
import QcowImageManager from '../panels/QcowImageManager';
import UploadsPanel from './UploadsPanel';
import BuiltinImages from './BuiltinImages';
import { useUploadStore } from '../../stores/useUploadStore';

/** Device images: container tarballs (docker load) and qcow2 VMs (vrnetlab build). */
export default function ImagesPage() {
  const [files, setFiles] = useState<any[]>([]);
  const [docker, setDocker] = useState<string[]>([]);
  const [loading, setLoading] = useState<string | null>(null);
  const [found, setFound] = useState<{ filename: string; size_mb: number; platform: string }[]>([]);
  const [folder, setFolder] = useState('');
  const [platforms, setPlatforms] = useState<{ key: string; label: string; qcow_hint: string }[]>([]);
  const [qcowKey, setQcowKey] = useState(0);   // remounts the VM image manager after a register
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    api.listLabImages().then(r => setFiles(r.images)).catch(() => {});
    api.listDockerImages().then(r => setDocker(r.images)).catch(() => {});
    api.listUnregisteredQcow().then(r => { setFound(r.files); setFolder(r.folder); }).catch(() => {});
    api.listQcowPlatforms().then(r => setPlatforms(r.platforms)).catch(() => {});
  };

  const register = async (filename: string, platform: string) => {
    if (!platform) { toast.error('Pick the platform first'); return; }
    try {
      await api.registerQcow(filename, platform);
      toast.success(`${filename} registered — click Build below`);
      setQcowKey(k => k + 1);
      refresh();
    } catch (e) { toast.error((e as Error).message); }
  };
  useEffect(refresh, []);

  const upload = async (f?: File) => {
    if (!f) return;
    if (/\.(qcow2?|img|vmdk)$/i.test(f.name)) {
      toast.error('That is a VM disk image — upload it under "VM images (qcow2 → vrnetlab)" below, or copy it to the server and Register it.', { duration: 9000 });
      if (fileRef.current) fileRef.current.value = '';
      return;
    }
    if (fileRef.current) fileRef.current.value = '';
    useUploadStore.getState().start(f, 'tarball', '', () => refresh());
  };

  const load = async (filename: string) => {
    setLoading(filename);
    try {
      const r = await api.loadLabImage(filename);
      toast.success(`Loaded ${(r.loaded_refs || []).join(', ') || filename}`);
      refresh();
    } catch (e) { toast.error((e as Error).message, { duration: 8000 }); }
    finally { setLoading(null); }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto p-6 space-y-8">
        <div>
          <h1 className="text-2xl font-semibold">Images</h1>
          <p className="text-sm text-surface-400">
            Container images (e.g. the XRd tarballs) are loaded into Docker. VM images (qcow2 for Catalyst 8000v,
            XRv 9000, CSR, vIOS) are wrapped into containers with vrnetlab; a successful build fills in the image on matching templates.
          </p>
        </div>

        <UploadsPanel />

        <BuiltinImages />

        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="font-semibold">Container image files</h2>
            <button onClick={() => fileRef.current?.click()}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded bg-surface-700 hover:bg-surface-600">
              <Upload size={14} /> Upload .tgz / .tar
            </button>
            <input ref={fileRef} type="file" className="hidden" accept=".tgz,.tar,.tar.gz,.gz" onChange={e => upload(e.target.files?.[0])} />
          </div>
          <div className="rounded border border-surface-700 divide-y divide-surface-700">
            {files.length === 0 && <div className="p-3 text-sm text-surface-400">No image files in the images directory.</div>}
            {files.map(f => (
              <div key={f.filename} className="flex items-center gap-3 p-3 text-sm">
                <span className="font-mono flex-1 truncate">{f.filename}</span>
                <span className="text-surface-400 text-xs">{f.size_mb ? `${f.size_mb} MB` : ''}</span>
                {f.loaded ? <span className="text-xs text-emerald-300">loaded</span> : null}
                <button onClick={() => load(f.filename)} disabled={loading !== null}
                  className="px-2 py-1 text-xs rounded bg-primary-600 hover:bg-primary-500 text-white disabled:opacity-50">
                  {loading === f.filename ? 'Loading…' : 'docker load'}
                </button>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="font-semibold mb-2">VM images (qcow2 → vrnetlab)</h2>
          <p className="text-xs text-surface-400 mb-2">
            Upload a qcow2 below, or copy it to the server (faster for multi-GB images), e.g.{' '}
            <code className="select-all">scp image.qcow2 server:{folder || '…/device_images/qcow'}/</code>, then register it here.
            Building wraps the VM in a container image (Cat8000v / Cat9000v builds boot the VM once to install — several minutes).
          </p>
          {found.length > 0 && (
            <div className="rounded border border-sky-800/60 bg-sky-950/20 p-3 mb-3">
              <div className="text-xs font-semibold text-sky-200 mb-2">Found on the server, not registered yet</div>
              {found.map(f => <FoundRow key={f.filename} f={f} platforms={platforms} onRegister={register} />)}
            </div>
          )}
          <div className="rounded border border-surface-700 p-3"><QcowImageManager key={qcowKey} /></div>
        </section>

        <section>
          <h2 className="font-semibold mb-2">Images in Docker</h2>
          <div className="rounded border border-surface-700 p-3 grid grid-cols-1 md:grid-cols-2 gap-1 text-xs font-mono text-surface-300 max-h-80 overflow-y-auto">
            {docker.map(i => <div key={i} className="truncate select-all">{i}</div>)}
          </div>
        </section>
      </div>
    </div>
  );
}

function FoundRow({ f, platforms, onRegister }: {
  f: { filename: string; size_mb: number; platform: string };
  platforms: { key: string; label: string; qcow_hint: string }[];
  onRegister: (filename: string, platform: string) => void;
}) {
  const [platform, setPlatform] = useState(f.platform);
  return (
    <div className="flex items-center gap-2 py-1 text-sm">
      <span className="font-mono flex-1 truncate">{f.filename}</span>
      <span className="text-xs text-surface-400">{(f.size_mb / 1024).toFixed(1)} GB</span>
      <select value={platform} onChange={e => setPlatform(e.target.value)}
        className="px-2 py-1 text-xs rounded bg-surface-900 border border-surface-700">
        <option value="">— platform —</option>
        {platforms.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
      </select>
      <button onClick={() => onRegister(f.filename, platform)}
        className="px-2 py-1 text-xs rounded bg-primary-600 hover:bg-primary-500 text-white">Register</button>
    </div>
  );
}

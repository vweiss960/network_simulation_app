/**
 * QcowImageManager — upload .qcow2 VM disk images, build them into
 * vrnetlab Docker images, and surface the resulting tags for use in
 * NodeTemplate.image_ref.
 *
 * Lives inside LabPanel under the existing "Device Images" section.
 * State is global (not per-topology), since once a vrnetlab image is built
 * it's available to every topology.
 *
 * The build itself runs server-side in a background thread; this component
 * polls /status while a build is active and renders a live log tail.
 */
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import {
  Upload, RefreshCw, Hammer, Trash2, Copy, CheckCircle, XCircle, ChevronDown, ChevronRight,
} from 'lucide-react';
import { api } from '../../utils/api';

type QcowImage = {
  id: number;
  filename: string;
  size_mb: number;
  vendor: string;
  platform: string;
  clab_kind: string;
  status: 'uploaded' | 'building' | 'built' | 'failed';
  built_image_tag: string;
  tag_still_loaded: boolean;
  error_message: string;
  uploaded_at: string | null;
  built_at: string | null;
};

type Platform = { key: string; vendor: string; label: string; clab_kind: string; qcow_hint: string };

export default function QcowImageManager() {
  const [open, setOpen] = useState(false);
  const [images, setImages] = useState<QcowImage[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [activeBuildId, setActiveBuildId] = useState<number | null>(null);
  const [uploadPlatform, setUploadPlatform] = useState<string>('');
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  // Image whose build log we're currently watching. null when no panel open.
  const [logImageId, setLogImageId] = useState<number | null>(null);
  const [logBody, setLogBody] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const logScrollRef = useRef<HTMLPreElement>(null);

  const refresh = async () => {
    try {
      const r = await api.listQcowImages();
      setImages(r.images);
      setPlatforms(r.platforms);
      setActiveBuildId(r.active_build_id);
      if (!uploadPlatform && r.platforms.length > 0) setUploadPlatform(r.platforms[0].key);
    } catch (e: any) {
      // Don't toast — the QCOW endpoints only exist when Lab Mode is on.
      // Silently leaving the panel empty is the right behaviour when off.
    }
  };

  useEffect(() => { refresh(); }, []);

  // Poll the list while ANY image is in 'building' state — that covers
  // updates from other browser tabs too, not just the one that kicked off
  // the build.
  useEffect(() => {
    const anyBuilding = images.some(i => i.status === 'building') || activeBuildId != null;
    if (!anyBuilding) return;
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [images, activeBuildId]);

  // Tail the log for whichever image is currently being watched. Stops
  // when the build completes (status leaves 'building'), but does one
  // final poll so the final lines + the "BUILT" marker are visible.
  useEffect(() => {
    if (logImageId == null) { setLogBody(''); return; }
    let stopped = false;
    let lastStatusBuilding = true;
    const tick = async () => {
      try {
        const r = await api.getQcowImageStatus(logImageId);
        if (stopped) return;
        setLogBody(r.log || '(no log yet)');
        if (r.status !== 'building') {
          // One more poll already done — bail out of the loop.
          lastStatusBuilding = false;
        }
        // Auto-scroll to bottom unless the user scrolled up
        const el = logScrollRef.current;
        if (el) {
          const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
          if (nearBottom) el.scrollTop = el.scrollHeight;
        }
      } catch {/* ignore transient */}
    };
    tick();
    const id = setInterval(() => {
      if (!lastStatusBuilding) { clearInterval(id); return; }
      tick();
    }, 2000);
    return () => { stopped = true; clearInterval(id); };
  }, [logImageId]);

  const upload = async (file: File) => {
    if (!uploadPlatform) { toast.error('Pick a platform first.'); return; }
    setUploadPct(0);
    try {
      const r = await api.uploadQcowImage(file, uploadPlatform, p => setUploadPct(p));
      toast.success(`Uploaded ${r.filename} (${r.size_mb} MB). Click Build to wrap into a Docker image.`);
      refresh();
    } catch (e: any) {
      toast.error(`Upload failed: ${e?.message || e}`, { duration: 10000 });
    }
    setUploadPct(null);
    if (fileRef.current) fileRef.current.value = '';
  };

  const startBuild = async (img: QcowImage) => {
    if (!confirm(
      `Build ${img.filename} as a vrnetlab Docker image?\n\n` +
      `This runs 'make docker-image' under hellt/vrnetlab and can take 5-20 minutes ` +
      `and several GB of disk. Only one build can run at a time.`
    )) return;
    setBusyId(img.id);
    try {
      await api.buildQcowImage(img.id);
      toast.success(`Build queued for ${img.filename}`);
      setLogImageId(img.id); // open log panel for this image
      refresh();
    } catch (e: any) {
      toast.error(`Build failed to start: ${e?.message || e}`);
    }
    setBusyId(null);
  };

  const remove = async (img: QcowImage) => {
    const lines = [
      `Delete ${img.filename}?`, '',
      `Will remove:`,
      `  • the qcow file under device_images/qcow/`,
      img.built_image_tag ? `  • the built Docker image '${img.built_image_tag}' (docker rmi -f)` : null,
      `  • the build log`,
      '', 'Continue?',
    ].filter(Boolean).join('\n');
    if (!confirm(lines)) return;
    setBusyId(img.id);
    try {
      await api.deleteQcowImage(img.id, true);
      toast.success(`Deleted ${img.filename}`);
      if (logImageId === img.id) setLogImageId(null);
      refresh();
    } catch (e: any) {
      toast.error(`Delete failed: ${e?.message || e}`);
    }
    setBusyId(null);
  };

  const statusBadge = (s: QcowImage['status']) => {
    const map: Record<QcowImage['status'], string> = {
      uploaded: 'bg-surface-700 text-surface-300',
      building: 'bg-blue-900/40 text-blue-300',
      built: 'bg-green-900/40 text-green-300',
      failed: 'bg-red-900/40 text-red-300',
    };
    return <span className={`text-[9px] px-1.5 py-0.5 rounded ${map[s]}`}>{s}</span>;
  };

  return (
    <div className="border border-surface-700 rounded">
      <button
        className="w-full px-3 py-2 flex items-center justify-between hover:bg-surface-800 transition-colors"
        onClick={() => { setOpen(v => !v); if (!open) refresh(); }}
      >
        <span className="text-xs font-medium">
          VM Images / QCOW Builds ({images.length})
          {activeBuildId != null && <span className="ml-2 text-[10px] text-blue-300">build in progress</span>}
        </span>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open && (
        <div className="border-t border-surface-700 p-3 space-y-2">
          <div className="text-[10px] text-surface-500">
            Upload a vendor <code className="text-surface-400">.qcow2</code> disk image; the backend
            wraps it into a Docker container via{' '}
            <a href="https://github.com/hellt/vrnetlab" target="_blank" rel="noreferrer" className="text-primary-400 hover:underline">
              hellt/vrnetlab
            </a>
            . The resulting tag is what goes into a node template's <code>image_ref</code>.
          </div>

          {/* Upload form */}
          <div className="flex items-center gap-2 p-2 bg-surface-900/50 border border-dashed border-surface-700 rounded">
            <select
              className="text-[10px] bg-surface-800 border border-surface-700 rounded px-1.5 py-1 text-surface-200"
              value={uploadPlatform}
              onChange={e => setUploadPlatform(e.target.value)}
              disabled={uploadPct !== null}
            >
              {platforms.map(p => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
            <input
              ref={fileRef}
              type="file"
              accept=".qcow2"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0];
                if (f) upload(f);
              }}
            />
            <button
              className="btn btn-ghost btn-sm text-[10px]"
              onClick={() => fileRef.current?.click()}
              disabled={uploadPct !== null || !uploadPlatform}
            >
              <Upload size={11} /> Upload .qcow2
            </button>
            {uploadPct !== null ? (
              <div className="flex-1 flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-surface-700 rounded overflow-hidden">
                  <div className="h-full bg-primary-500 transition-all" style={{ width: `${uploadPct}%` }} />
                </div>
                <span className="text-[10px] text-surface-400 w-10 text-right">{uploadPct}%</span>
              </div>
            ) : (
              <span className="text-[10px] text-surface-500 flex-1 truncate">
                {platforms.find(p => p.key === uploadPlatform)?.qcow_hint || 'Pick a platform'}
              </span>
            )}
          </div>

          {images.length === 0 && (
            <div className="text-[10px] text-surface-500 text-center py-3">
              No QCOW images uploaded yet.
            </div>
          )}

          {/* Image list */}
          <div className="space-y-1">
            {images.map(img => (
              <div key={img.id} className="border border-surface-700/60 rounded p-2 space-y-1">
                <div className="flex items-center gap-2 text-[10px]">
                  <div className="flex-1 min-w-0">
                    <div className="font-mono truncate text-surface-200" title={img.filename}>{img.filename}</div>
                    <div className="text-surface-500 flex items-center gap-1.5">
                      {statusBadge(img.status)}
                      <span>{img.size_mb} MB</span>
                      <span>·</span>
                      <span>{img.platform}</span>
                      <span>·</span>
                      <span className="font-mono">{img.clab_kind}</span>
                    </div>
                  </div>
                  {img.status === 'uploaded' && (
                    <button
                      className="btn btn-primary btn-sm text-[10px]"
                      onClick={() => startBuild(img)}
                      disabled={busyId === img.id || activeBuildId != null}
                      title={activeBuildId != null ? 'Another build is running' : 'Build vrnetlab image'}
                    >
                      <Hammer size={11} /> Build
                    </button>
                  )}
                  {img.status === 'building' && (
                    <button
                      className="btn btn-ghost btn-sm text-[10px]"
                      onClick={() => setLogImageId(logImageId === img.id ? null : img.id)}
                    >
                      <RefreshCw size={11} className="animate-spin" /> {logImageId === img.id ? 'Hide log' : 'View log'}
                    </button>
                  )}
                  {(img.status === 'built' || img.status === 'failed') && (
                    <button
                      className="btn btn-ghost btn-sm text-[10px]"
                      onClick={() => setLogImageId(logImageId === img.id ? null : img.id)}
                    >
                      {logImageId === img.id ? 'Hide log' : 'View log'}
                    </button>
                  )}
                  <button
                    className="btn btn-ghost btn-sm text-[10px] text-red-300 hover:bg-red-900/30"
                    onClick={() => remove(img)}
                    disabled={busyId === img.id || img.status === 'building'}
                    title={img.status === 'building' ? 'Cannot delete while building' : 'Delete'}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
                {img.status === 'built' && img.built_image_tag && (
                  <div className="flex items-center gap-2 text-[10px] pl-1">
                    <CheckCircle size={11} className="text-green-400 shrink-0" />
                    <span className="text-surface-400">Image tag:</span>
                    <code className="font-mono text-green-300 truncate flex-1" title={img.built_image_tag}>
                      {img.built_image_tag}
                    </code>
                    <button
                      className="text-primary-400 hover:text-primary-300"
                      onClick={() => navigator.clipboard.writeText(img.built_image_tag).then(() => toast.success('Copied'))}
                      title="Copy tag for use in node template image_ref"
                    >
                      <Copy size={11} />
                    </button>
                    {!img.tag_still_loaded && (
                      <span className="text-amber-400" title="Tag was removed from Docker daemon since build">
                        (missing from docker)
                      </span>
                    )}
                  </div>
                )}
                {img.status === 'failed' && img.error_message && (
                  <div className="flex items-start gap-1.5 text-[10px] pl-1">
                    <XCircle size={11} className="text-red-400 mt-0.5 shrink-0" />
                    <span className="text-red-300">{img.error_message}</span>
                  </div>
                )}
                {logImageId === img.id && (
                  <pre
                    ref={logScrollRef}
                    className="text-[10px] font-mono bg-surface-950 border border-surface-700 rounded p-2 mt-1 max-h-[280px] overflow-auto whitespace-pre-wrap"
                  >
                    {logBody || '(loading log…)'}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

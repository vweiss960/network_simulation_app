import { create } from 'zustand';
import toast from 'react-hot-toast';

/**
 * Resumable uploads of large device images.
 *
 * The file goes up in CHUNK-sized pieces, each tagged with its byte offset;
 * the server's partial file is the source of truth for progress. Any failure
 * (network drop, laptop sleep, server restart) → wait with backoff, ask the
 * server how much it has, continue from there. Picking the same file again
 * later (even after a reload) resumes the same server-side session.
 */

const CHUNK = 8 * 1024 * 1024;
const BASE = '/api/lab/uploads';

export type Purpose = 'qcow' | 'tarball';

export interface UploadState {
  id: string;
  filename: string;
  size: number;
  received: number;
  purpose: Purpose;
  state: 'uploading' | 'retrying' | 'finishing' | 'done' | 'failed' | 'cancelled';
  bytesPerSec: number;
  message: string;
}

interface UploadStore {
  uploads: Record<string, UploadState>;
  start: (file: File, purpose: Purpose, platform?: string, onDone?: (result: any) => void) => Promise<void>;
  cancel: (id: string) => Promise<void>;
  dismiss: (id: string) => void;
}

const controllers: Record<string, AbortController> = {};

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const token = localStorage.getItem('auth_token');
  return { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...extra };
}

async function call(path: string, init: RequestInit = {}): Promise<any> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: headers(init.headers as Record<string, string>) });
  const text = await res.text();
  let body: any = text;
  try { body = JSON.parse(text); } catch { /* text */ }
  if (!res.ok) {
    const err: any = new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return body;
}

const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((resolve) => {
  const t = setTimeout(resolve, ms);
  signal.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
});

const waitOnline = (signal: AbortSignal) => navigator.onLine ? Promise.resolve() : new Promise<void>((resolve) => {
  const on = () => { window.removeEventListener('online', on); resolve(); };
  window.addEventListener('online', on);
  signal.addEventListener('abort', on, { once: true });
});

export const useUploadStore = create<UploadStore>((set, get) => {
  const patch = (id: string, p: Partial<UploadState>) =>
    set(s => (s.uploads[id] ? { uploads: { ...s.uploads, [id]: { ...s.uploads[id], ...p } } } : s));

  return {
    uploads: {},

    start: async (file, purpose, platform = '', onDone) => {
      let session: any;
      try {
        session = await call('/', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: file.name, size: file.size, last_modified: file.lastModified, purpose, platform }),
        });
      } catch (e) { toast.error((e as Error).message); return; }
      const id: string = session.id;
      if (get().uploads[id] && ['uploading', 'retrying', 'finishing'].includes(get().uploads[id].state)) {
        toast('That file is already uploading', { icon: 'ℹ️' });
        return;
      }
      if (session.received > 0) toast(`Resuming ${file.name} at ${Math.round(100 * session.received / file.size)}%`, { icon: '↻' });
      const ctl = new AbortController();
      controllers[id] = ctl;
      set(s => ({ uploads: { ...s.uploads, [id]: {
        id, filename: file.name, size: file.size, received: session.received, purpose,
        state: 'uploading', bytesPerSec: 0, message: '' } } }));

      let received: number = session.received;
      let backoff = 1000;
      const samples: { t: number; b: number }[] = [{ t: Date.now(), b: received }];

      while (received < file.size && !ctl.signal.aborted) {
        const end = Math.min(received + CHUNK, file.size);
        try {
          const r = await call(`/${id}?offset=${received}`, {
            method: 'PUT', body: file.slice(received, end), signal: ctl.signal,
            headers: { 'Content-Type': 'application/octet-stream' },
          });
          received = r.received;
          backoff = 1000;
          const now = Date.now();
          samples.push({ t: now, b: received });
          while (samples.length > 2 && now - samples[0].t > 15000) samples.shift();
          const span = (now - samples[0].t) / 1000;
          patch(id, { received, state: 'uploading', message: '',
                      bytesPerSec: span > 0 ? (received - samples[0].b) / span : 0 });
        } catch (e: any) {
          if (ctl.signal.aborted) break;
          if (e.status === 401 || e.status === 404) {
            patch(id, { state: 'failed', message: e.message });
            delete controllers[id];
            return;
          }
          patch(id, { state: 'retrying', bytesPerSec: 0,
                      message: navigator.onLine ? `${e.message || 'Connection lost'} — retrying` : 'Offline — waiting for the network' });
          await waitOnline(ctl.signal);
          await sleep(backoff, ctl.signal);
          backoff = Math.min(backoff * 2, 30000);
          try { received = (await call(`/${id}`)).received; } catch { /* keep trying */ }
          samples.length = 0;
          samples.push({ t: Date.now(), b: received });
        }
      }
      if (ctl.signal.aborted) return;
      patch(id, { state: 'finishing', received, message: '' });
      try {
        const result = await call(`/${id}/complete`, { method: 'POST' });
        patch(id, { state: 'done', message: purpose === 'qcow' ? 'Uploaded — ready to Build' : 'Uploaded — ready to load' });
        toast.success(`${file.name} uploaded`);
        onDone?.(result);
      } catch (e) {
        patch(id, { state: 'failed', message: (e as Error).message });
        toast.error((e as Error).message, { duration: 10000 });
      }
      delete controllers[id];
    },

    cancel: async (id) => {
      controllers[id]?.abort();
      delete controllers[id];
      try { await call(`/${id}`, { method: 'DELETE' }); } catch { /* already gone */ }
      patch(id, { state: 'cancelled', message: 'Cancelled' });
    },

    dismiss: (id) => set(s => { const u = { ...s.uploads }; delete u[id]; return { uploads: u }; }),
  };
});

/** True while any upload is still transferring (used for the leave-page warning). */
export const uploadsActive = () =>
  Object.values(useUploadStore.getState().uploads).some(u => ['uploading', 'retrying', 'finishing'].includes(u.state));

window.addEventListener('beforeunload', (e) => {
  if (uploadsActive()) { e.preventDefault(); e.returnValue = ''; }
});

export const fmtBytes = (n: number) =>
  n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(2)} GB` : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(0)} MB` : `${(n / 1024).toFixed(0)} KB`;

export const fmtEta = (sec: number) =>
  !isFinite(sec) || sec <= 0 ? '' : sec >= 3600 ? `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`
    : sec >= 60 ? `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s` : `${Math.round(sec)}s`;

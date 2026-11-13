import type { Device, DeviceKind, LabNetworkInfo, LabState, Link, MgmtSettings, NetworkSpec, NodeTemplate, Snapshot, Topology, Annotation } from '../types';

const BASE = '/api';

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('auth_token');
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = `Bearer ${token}`;
  return h;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string) { super(detail); }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders(), ...options });
  if (!res.ok) {
    if (res.status === 401) {
      localStorage.removeItem('auth_token');
      window.location.href = '/';
    }
    const text = await res.text();
    let detail = text;
    try { const j = JSON.parse(text); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { /* raw */ }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  const ct = res.headers.get('content-type') || '';
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit =>
  ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

/** A packet capture on a lab link (backend services/packet_capture.py). */
export interface CaptureInfo {
  id: string; topology_id: number; link_id: number; title: string; side: string; filter: string;
  state: 'running' | 'stopped' | 'error'; note: string; packets: number; bytes: number;
  started: number; ended: number | null; username: string;
}
/** [number, time (s), source, destination, protocol, length, info] */
export type CaptureRow = [number, number, string, string, string, number, string];
export interface PacketDetail { number: number; tree: string[]; hex: string }

/** Save a capture's pcapng (authenticated fetch → browser download). */
export async function downloadCapture(tid: number, cid: string): Promise<void> {
  const res = await fetch(`${BASE}/topologies/${tid}/captures/${cid}/download`, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || `HTTP ${res.status}`);
  const name = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') || '')?.[1] || 'capture.pcapng';
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** One event from the lab agent's stream (see backend routes/agent.py). */
export type AgentEvent =
  | { type: 'conversation'; id: string; model: string }
  | { type: 'text'; delta: string }
  | { type: 'status'; text: string }
  | { type: 'tool'; id: string; name: string; label: string; state: 'running' | 'done' | 'error'; summary?: string }
  | { type: 'lab_changed' }
  | { type: 'error'; message: string }
  | { type: 'done' };

export interface AgentConversationSummary { id: string; title: string; model: string; updated_at: string | null }

export interface AIStatus {
  enabled: boolean; error: string | null; default_model: string;
  models: { id: string; label: string; provider: string }[];
}

/** POST a chat message and read the server-sent events as they arrive. */
export async function streamAgentChat(tid: number, body: { message: string; conversation_id?: string | null; model?: string },
                                      onEvent: (e: AgentEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(`${BASE}/topologies/${tid}/agent/chat`, {
    method: 'POST', headers: authHeaders(), body: JSON.stringify(body), signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text();
    let detail = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* raw */ }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = frame.split('\n').find(l => l.startsWith('data: '));
      if (line) {
        try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignore a malformed frame */ }
      }
    }
  }
}

export const api = {
  aiStatus: () => request<AIStatus>('/ai/status'),
  listAgentConversations: (tid: number) =>
    request<AgentConversationSummary[]>(`/topologies/${tid}/agent/conversations`),
  getAgentConversation: (tid: number, cid: string) =>
    request<{ id: string; title: string; model: string; messages: any[] }>(`/topologies/${tid}/agent/conversations/${cid}`),
  deleteAgentConversation: (tid: number, cid: string) =>
    request<{ ok: boolean }>(`/topologies/${tid}/agent/conversations/${cid}`, json('DELETE')),
  stopAgent: (tid: number, cid: string) =>
    request<{ ok: boolean }>(`/topologies/${tid}/agent/conversations/${cid}/stop`, json('POST')),
  getFeatures: () => request<{ ai_enabled: boolean; lab_telemetry_enabled: boolean }>('/features'),

  // Topologies
  listTopologies: () => request<Topology[]>('/topologies/'),
  getTopology: (id: number) => request<Topology>(`/topologies/${id}`),
  createTopology: (name: string, description = '') =>
    request<Topology>('/topologies/', json('POST', { name, description })),
  updateTopology: (id: number, data: Partial<Pick<Topology, 'name' | 'description'>>) =>
    request<Topology>(`/topologies/${id}`, json('PATCH', data)),
  deleteTopology: (id: number) => request<{ ok: boolean }>(`/topologies/${id}`, json('DELETE')),
  duplicateTopology: (id: number) => request<Topology>(`/topologies/${id}/duplicate`, json('POST')),
  exportTopology: (id: number) => request<string>(`/topologies/${id}/export`),
  importTopology: async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    const token = localStorage.getItem('auth_token');
    const res = await fetch(`${BASE}/topologies/import`, {
      method: 'POST', body: fd, headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      const t = await res.text();
      let d = t;
      try { d = JSON.parse(t).detail; } catch { /* raw */ }
      throw new ApiError(res.status, d);
    }
    return res.json() as Promise<Topology>;
  },

  // Devices + links
  listNodes: (tid: number) => request<Device[]>(`/topologies/${tid}/nodes/`),
  createNode: (tid: number, data: { template_id?: number; kind?: string; image_ref?: string; name?: string; x: number; y: number }) =>
    request<Device>(`/topologies/${tid}/nodes/`, json('POST', data)),
  updateNode: (tid: number, nid: number, data: Partial<Omit<Device, 'network'>> & { interface_count?: number; password?: string; network?: NetworkSpec; defer?: boolean }) =>
    request<Device>(`/topologies/${tid}/nodes/${nid}`, json('PATCH', data)),
  getAnnotations: (tid: number) => request<Annotation[]>(`/topologies/${tid}/annotations`),
  putAnnotations: (tid: number, items: Annotation[]) =>
    request<Annotation[]>(`/topologies/${tid}/annotations`, json('PUT', items)),
  discardPending: (tid: number, nid: number) => request<Device>(`/topologies/${tid}/nodes/${nid}/pending`, json('DELETE')),
  saveNodePositions: (tid: number, positions: Record<number, [number, number]>) =>
    request<{ ok: boolean }>(`/topologies/${tid}/nodes/positions`, json('PUT', { positions })),
  deleteNode: (tid: number, nid: number) => request<{ ok: boolean }>(`/topologies/${tid}/nodes/${nid}`, json('DELETE')),
  listLinks: (tid: number) => request<Link[]>(`/topologies/${tid}/links/`),
  createLink: (tid: number, data: { source_node_id: number; target_node_id: number; source_interface?: string; target_interface?: string }) =>
    request<Link>(`/topologies/${tid}/links/`, json('POST', data)),
  updateLink: (tid: number, lid: number, data: Partial<Pick<Link, 'source_interface' | 'target_interface' | 'label' | 'color'>>) =>
    request<Link>(`/topologies/${tid}/links/${lid}`, json('PATCH', data)),
  deleteLink: (tid: number, lid: number) => request<{ ok: boolean }>(`/topologies/${tid}/links/${lid}`, json('DELETE')),
  freeInterfaces: (tid: number, nid: number) =>
    request<{ interfaces: { name: string; kernel: string; used: boolean }[] }>(`/topologies/${tid}/links/free-interfaces/${nid}`),

  // Templates + kinds
  listTemplates: (checkImages = true) => request<NodeTemplate[]>(`/node-templates/?check_images=${checkImages}`),
  createTemplate: (data: Partial<NodeTemplate>) => request<NodeTemplate>('/node-templates/', json('POST', data)),
  updateTemplate: (id: number, data: Partial<NodeTemplate>) => request<NodeTemplate>(`/node-templates/${id}`, json('PATCH', data)),
  deleteTemplate: (id: number) => request<{ ok: boolean }>(`/node-templates/${id}`, json('DELETE')),
  listKinds: () => request<{ kinds: DeviceKind[] }>('/lab/kinds'),
  listDockerImages: () => request<{ images: string[] }>('/lab/docker-images'),

  // Lab lifecycle
  getLabStatus: (tid: number) => request<LabState>(`/topologies/${tid}/lab/status`),
  getLabYaml: (tid: number) => request<{ yaml: string; resource_estimate: Record<string, number | boolean> }>(`/topologies/${tid}/lab/yaml`),
  getLabEstimate: (tid: number) => request<any>(`/topologies/${tid}/lab/estimate`),
  getLabPrereqs: (tid: number) => request<any>(`/topologies/${tid}/lab/prereqs`),
  deployLab: (tid: number, all = false) => request<any>(`/topologies/${tid}/lab/deploy${all ? '?all=true' : ''}`, json('POST')),
  destroyLab: (tid: number) => request<any>(`/topologies/${tid}/lab/destroy`, json('POST')),
  redeployLab: (tid: number) => request<any>(`/topologies/${tid}/lab/redeploy`, json('POST')),
  startNode: (tid: number, nid: number) => request<any>(`/topologies/${tid}/lab/node/${nid}/start`, json('POST')),
  stopNode: (tid: number, nid: number) => request<any>(`/topologies/${tid}/lab/node/${nid}/stop`, json('POST')),
  restartNode: (tid: number, nid: number) => request<any>(`/topologies/${tid}/lab/node/${nid}/restart`, json('POST')),
  startCapture: (tid: number, lid: number, filter = '') =>
    request<CaptureInfo>(`/topologies/${tid}/links/${lid}/capture`, json('POST', { filter })),
  listCaptures: (tid: number) => request<CaptureInfo[]>(`/topologies/${tid}/captures`),
  stopCapture: (tid: number, cid: string) => request<CaptureInfo>(`/topologies/${tid}/captures/${cid}/stop`, json('POST')),
  deleteCapture: (tid: number, cid: string) => request<{ ok: boolean }>(`/topologies/${tid}/captures/${cid}`, json('DELETE')),
  capturePacket: (tid: number, cid: string, n: number) => request<PacketDetail>(`/topologies/${tid}/captures/${cid}/packets/${n}`),
  captureMatch: (tid: number, cid: string, filter: string) =>
    request<{ numbers: number[] }>(`/topologies/${tid}/captures/${cid}/match?filter=${encodeURIComponent(filter)}`),
  getLinkDegradation: (tid: number, lid: number) => request<any>(`/topologies/${tid}/lab/link/${lid}/netem`),
  setLinkDegradation: (tid: number, lid: number, data: { delay: string; jitter: string; loss: number; rate: number; corruption: number; direction?: 'both' | 'a' | 'b' }) =>
    request<any>(`/topologies/${tid}/lab/link/${lid}/netem`, json('POST', data)),
  resetLinkDegradation: (tid: number, lid: number) => request<any>(`/topologies/${tid}/lab/link/${lid}/netem`, json('DELETE')),

  // Images
  listLabImages: () => request<{ images: any[] }>('/lab/images'),
  loadLabImage: (filename: string) => request<any>('/lab/images/load', json('POST', { filename })),
  getHostCapacity: () =>
    request<{ memory_mb: number; memory_available_mb: number; cpu_cores: number; hugepages_count: number }>('/lab/host-capacity'),

  // Device access + snapshots
  runCli: (tid: number, nid: number, commands: string[]) =>
    request<{ results: { command: string; output: string }[] }>(`/topologies/${tid}/nodes/${nid}/cli`, json('POST', { commands })),
  configureDevice: (tid: number, nid: number, config: string) =>
    request<{ ok: boolean; errors: string[]; output: string }>(`/topologies/${tid}/nodes/${nid}/configure`, json('POST', { config })),
  runningConfig: (tid: number, nid: number) => request<{ config: string }>(`/topologies/${tid}/nodes/${nid}/running-config`),
  listSnapshots: (tid: number, nid?: number) =>
    request<Snapshot[]>(`/topologies/${tid}/snapshots${nid ? `?node_id=${nid}` : ''}`),
  getSnapshot: (tid: number, sid: number) => request<Snapshot & { config: string }>(`/topologies/${tid}/snapshots/${sid}`),
  snapshotNode: (tid: number, nid: number, label = '') =>
    request<Snapshot>(`/topologies/${tid}/nodes/${nid}/snapshots`, json('POST', { label })),
  snapshotLab: (tid: number, label = '') =>
    request<{ label: string; taken: Snapshot[]; skipped: { node: string; reason: string }[] }>(`/topologies/${tid}/snapshots`, json('POST', { label })),
  restoreSnapshot: (tid: number, sid: number, mode: 'live' | 'reboot') =>
    request<any>(`/topologies/${tid}/snapshots/${sid}/restore`, json('POST', { mode })),
  deleteSnapshot: (tid: number, sid: number) => request<any>(`/topologies/${tid}/snapshots/${sid}`, json('DELETE')),

  // Host networking
  getLabNetwork: () => request<LabNetworkInfo>('/lab-network/'),
  putLabNetwork: (data: MgmtSettings) =>
    request<{ settings: MgmtSettings }>('/lab-network/', json('PUT', data)),

  listUnregisteredQcow: () =>
    request<{ files: { filename: string; size_mb: number; platform: string }[]; folder: string }>('/lab/qcow-images/unregistered'),
  registerQcow: (filename: string, platform: string) =>
    request<any>('/lab/qcow-images/register', json('POST', { filename, platform })),
  listQcowPlatforms: () => request<{ platforms: { key: string; label: string; qcow_hint: string }[] }>('/lab/qcow-images'),

  // Users
  listUsers: () => request<any[]>('/auth/users'),
  createUser: (data: { username: string; password: string; display_name?: string; is_admin?: boolean }) =>
    request<any>('/auth/users', json('POST', data)),
  updateUser: (id: number, data: { password?: string; display_name?: string; is_admin?: boolean }) =>
    request<any>(`/auth/users/${id}`, json('PUT', data)),
  deleteUser: (id: number) => request<any>(`/auth/users/${id}`, json('DELETE')),

  uploadLabImage: async (file: File, onProgress?: (pct: number) => void) => {
    // Multipart upload — uses XHR for progress events (fetch doesn't expose them yet)
    const token = localStorage.getItem('auth_token');
    return await new Promise<any>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `/api/lab/images/upload`);
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      if (onProgress && xhr.upload) {
        xhr.upload.onprogress = e => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({}); }
        } else {
          if (xhr.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
          }
          reject(new Error(`API error ${xhr.status}: ${xhr.responseText}`));
        }
      };
      xhr.onerror = () => reject(new Error('Network error during upload'));
      const fd = new FormData();
      fd.append('file', file);
      xhr.send(fd);
    });
  },
  // ---- QCOW VM image build flow (vrnetlab) ----
  // Parallel to the .tgz `docker load` flow above. Returns image rows with
  // build state + the supported platform registry for the upload form.
  listQcowImages: () =>
    request<{
      images: Array<{
        id: number; filename: string; size_mb: number;
        vendor: string; platform: string; clab_kind: string;
        status: 'uploaded' | 'building' | 'built' | 'failed';
        built_image_tag: string; tag_still_loaded: boolean;
        error_message: string; uploaded_at: string | null; built_at: string | null;
      }>;
      platforms: Array<{ key: string; vendor: string; label: string; clab_kind: string; qcow_hint: string; hints: string[] }>;
      active_build_id: number | null;
    }>(`/lab/qcow-images`),
  uploadQcowImage: async (file: File, platform: string, onProgress?: (pct: number) => void) => {
    // Multipart with both file + platform field. Same XHR-for-progress
    // pattern as uploadLabImage above — fetch can't expose upload progress.
    const token = localStorage.getItem('auth_token');
    return await new Promise<any>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `/api/lab/qcow-images/upload`);
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      if (onProgress && xhr.upload) {
        xhr.upload.onprogress = e => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({}); }
        } else {
          if (xhr.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
          }
          reject(new Error(`API error ${xhr.status}: ${xhr.responseText}`));
        }
      };
      xhr.onerror = () => reject(new Error('Network error during upload'));
      const fd = new FormData();
      fd.append('file', file);
      fd.append('platform', platform);
      xhr.send(fd);
    });
  },
  reclassifyQcowImage: (id: number, platform: string) =>
    request<{ id: number; platform: string; clab_kind: string; status: string }>(
      `/lab/qcow-images/${id}`, json('PATCH', { platform })),
  buildQcowImage: (id: number) =>
    request<{ id: number; status: string }>(`/lab/qcow-images/${id}/build`, { method: 'POST' }),
  getQcowImageStatus: (id: number) =>
    request<{ id: number; status: string; built_image_tag: string; error_message: string; log: string }>(
      `/lab/qcow-images/${id}/status`,
    ),
  deleteQcowImage: (id: number, alsoDockerRmi = true) =>
    request<any>(`/lab/qcow-images/${id}?also_docker_rmi=${alsoDockerRmi ? 'true' : 'false'}`, { method: 'DELETE' }),

};

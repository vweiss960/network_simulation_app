import type { Device, DeviceKind, LabNetworkInfo, LabState, Link, MgmtSettings, NetworkSpec, NodeTemplate, Topology } from '../types';

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

export const api = {
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
  updateNode: (tid: number, nid: number, data: Partial<Omit<Device, 'network'>> & { interface_count?: number; password?: string; network?: NetworkSpec }) =>
    request<Device>(`/topologies/${tid}/nodes/${nid}`, json('PATCH', data)),
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
  deployLab: (tid: number) => request<any>(`/topologies/${tid}/lab/deploy`, json('POST')),
  destroyLab: (tid: number) => request<any>(`/topologies/${tid}/lab/destroy`, json('POST')),
  redeployLab: (tid: number) => request<any>(`/topologies/${tid}/lab/redeploy`, json('POST')),
  startNode: (tid: number, nid: number) => request<any>(`/topologies/${tid}/lab/node/${nid}/start`, json('POST')),
  stopNode: (tid: number, nid: number) => request<any>(`/topologies/${tid}/lab/node/${nid}/stop`, json('POST')),
  restartNode: (tid: number, nid: number) => request<any>(`/topologies/${tid}/lab/node/${nid}/restart`, json('POST')),
  getLinkDegradation: (tid: number, lid: number) => request<any>(`/topologies/${tid}/lab/link/${lid}/netem`),
  setLinkDegradation: (tid: number, lid: number, data: { delay: string; jitter: string; loss: number; rate: number; corruption: number }) =>
    request<any>(`/topologies/${tid}/lab/link/${lid}/netem`, json('POST', data)),
  resetLinkDegradation: (tid: number, lid: number) => request<any>(`/topologies/${tid}/lab/link/${lid}/netem`, json('DELETE')),

  // Images
  listLabImages: () => request<{ images: any[] }>('/lab/images'),
  loadLabImage: (filename: string) => request<any>('/lab/images/load', json('POST', { filename })),
  getHostCapacity: () =>
    request<{ memory_mb: number; memory_available_mb: number; cpu_cores: number; hugepages_count: number }>('/lab/host-capacity'),

  // Host networking
  getLabNetwork: () => request<LabNetworkInfo>('/lab-network/'),
  putLabNetwork: (data: MgmtSettings) =>
    request<{ settings: MgmtSettings; status: LabNetworkInfo['status'] }>('/lab-network/', json('PUT', data)),

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
      platforms: Array<{ key: string; vendor: string; label: string; clab_kind: string; qcow_hint: string }>;
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
  buildQcowImage: (id: number) =>
    request<{ id: number; status: string }>(`/lab/qcow-images/${id}/build`, { method: 'POST' }),
  getQcowImageStatus: (id: number) =>
    request<{ id: number; status: string; built_image_tag: string; error_message: string; log: string }>(
      `/lab/qcow-images/${id}/status`,
    ),
  deleteQcowImage: (id: number, alsoDockerRmi = true) =>
    request<any>(`/lab/qcow-images/${id}?also_docker_rmi=${alsoDockerRmi ? 'true' : 'false'}`, { method: 'DELETE' }),

};

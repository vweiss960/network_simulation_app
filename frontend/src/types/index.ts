export interface Iface { name: string; kernel: string }

export interface DeviceKind {
  kind: string; label: string; family: string; mgmt_iface: string; default_user: string;
  ssh: boolean; boot_seconds: number; min_memory_mb: number; min_cpu_cores: number;
  requires_hugepages: boolean; is_vm: boolean; max_interfaces: number; default_interfaces: number;
  description: string; services: Record<string, number>; example_interfaces: Iface[];
}

export interface NodeTemplate {
  id: number; name: string; vendor: string; model: string; kind: string; image_ref: string;
  icon: string; interface_count: number; min_memory_mb: number; min_cpu_cores: number;
  requires_hugepages: boolean; startup_config: string; description: string; created_by: string;
  image_available: boolean | null;
}

export interface Topology {
  id: number; name: string; description: string; node_count: number; link_count: number; lab_status: LabStatus;
}

export interface Device {
  id: number; topology_id: number; template_id: number | null; name: string; kind: string;
  image_ref: string; icon: string; x: number; y: number; interfaces: Iface[]; mgmt_ipv4: string;
  username: string; startup_config: string; min_memory_mb: number; min_cpu_cores: number;
  color: string; notes: string;
}

export interface Link {
  id: number; topology_id: number; source_node_id: number; target_node_id: number;
  source_interface: string; target_interface: string; label: string; color: string;
}

export type LabStatus = 'idle' | 'deploying' | 'running' | 'syncing' | 'destroying' | 'failed';
export type ReadyState = 'absent' | 'booting' | 'up' | 'down' | 'starting' | 'unknown';

export interface NodeRuntime {
  container: string; state: string; ready_state: ReadyState; uptime_sec: number | null; mgmt_ip: string;
}

export interface LabState {
  status: LabStatus; lab_name: string; last_error: string; pending_sync: boolean;
  started_at: string | null; finished_at: string | null;
  nodes: Record<string, NodeRuntime>;
  skipped_nodes: { id: number; name: string; reason: string }[];
}

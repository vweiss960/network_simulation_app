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
  /** Labs made from a lesson: a learner's attempt, or the lesson's authoring lab. */
  lesson_id?: number | null; lesson_role?: 'attempt' | 'authoring' | 'troubleshoot' | 'troubleshoot_test' | null;
  scenario_id?: number | null;
  lesson_ai_policy?: 'teach' | 'hints' | 'off' | null;
}

// --- Lessons -------------------------------------------------------------------
export type LessonLevel = 'beginner' | 'intermediate' | 'advanced';
export type LessonTrack = 'enterprise' | 'service_provider' | 'data_center' | 'automation' | 'security' | 'general';
export type AIPolicy = 'teach' | 'hints' | 'off';
export type HintsMode = 'open' | 'on_request' | 'none';

export interface CheckResult { ok: boolean; describe: string; detail: string; output: string }
export interface TaskResult {
  passed: boolean; checked_at: number; checks: CheckResult[];
  /** Passed earlier; later tasks have since changed what it checked (it stays passed). */
  changed_since?: boolean; passed_at?: number;
}

export interface LessonAttempt {
  id: number; topology_id: number | null; lesson_version: string; outdated?: boolean;
  results: Record<string, TaskResult>; passed: number; total: number;
  /** Hints the learner may see now, per task. */
  hints: Record<string, string[]>;
  started_at: string | null; completed_at: string | null;
}

export interface LessonSummary {
  id: number; slug: string; title: string; summary: string; level: LessonLevel; track: LessonTrack;
  topic: string; tags: string[]; minutes: number; status: 'draft' | 'published'; ai_policy: AIPolicy;
  hints_mode: HintsMode; version: string; author: string; topology_summary: string; task_count: number;
  updated_at: string | null; attempt: LessonAttempt | null;
}

/** A task as the learner sees it before revealing anything. */
export interface PublicTask {
  id: string; title: string; body: string; hint_count: number; has_solution: boolean;
  checks: { type: string; describe: string }[];
}

export type LessonCheck = {
  type: 'cli' | 'route' | 'ping' | 'config'; describe?: string;
  device?: string; command?: string; expect?: Record<string, string | number | boolean>[];
  prefix?: string; protocol?: string; present?: boolean;
  from?: string; to?: string; count?: number; source?: string; min_success?: number;
};
/** A task as authored (admin, ?full=true). */
export interface LessonTask {
  id: string; title: string; body: string; hints: string[]; solution: Record<string, string>; checks: LessonCheck[];
}

export interface LessonSection { title: string; body: string; minutes: number }

export interface LessonTopology {
  nodes: { name: string; kind: string; image?: string; icon?: string; position?: [number, number]; enabled?: boolean;
           notes?: string; startup_config?: string; network?: NetworkSpec | null }[];
  links: { a: string; a_interface: string; b: string; b_interface: string; label: string }[];
  annotations: Annotation[];
}

export interface LessonDetail extends LessonSummary {
  topology_yaml: string; topology: LessonTopology; tasks: (PublicTask | LessonTask)[]; content_md: string; sections: LessonSection[];
  requires: { kind: string; label: string; image: string; devices: string[]; ready: boolean }[];
  authoring_topology_id: number | null; can_edit: boolean;
  modules: { id: number; slug: string; title: string; section: string }[];
}

// --- Modules: ordered lessons around one topic (e.g. a blueprint section) ---------
/** A module item: a lesson or a troubleshooting scenario (by slug). */
export interface ModuleItem {
  kind?: 'lesson' | 'scenario'; lesson: string; scenario?: string; section: string; title: string; objectives: string[];
}
export type ModuleItemState = 'not_installed' | 'not_started' | 'in_progress' | 'completed';
export interface ModuleMeta {
  slug: string; title: string; summary: string; level: LessonLevel; track: LessonTrack; blueprint: string;
  tags: string[]; status: 'draft' | 'published'; version: string; author: string;
}
export interface ModuleSummary extends ModuleMeta {
  id: number; lesson_count: number; completed: number; minutes: number; next_lesson_id: number | null;
  next_item: { kind: 'lesson' | 'scenario'; id: number } | null;
  updated_at: string | null;
}
export interface ModuleDetail extends ModuleSummary {
  overview_md: string; sections: LessonSection[]; can_edit: boolean;
  items: (ModuleItem & {
    lesson_id: number | null; scenario_id?: number | null; lesson_title: string; lesson_summary: string; level: LessonLevel | null;
    minutes: number | null; task_count: number; topology_summary: string; protocols?: string[]; fault_count?: number;
    status: { state: ModuleItemState; passed?: number; total?: number; outdated?: boolean; solved?: number };
  })[];
}

export interface Device {
  id: number; topology_id: number; template_id: number | null; name: string; kind: string;
  image_ref: string; icon: string; x: number; y: number; interfaces: Iface[]; mgmt_ipv4: string;
  username: string; startup_config: string; min_memory_mb: number; min_cpu_cores: number;
  color: string; notes: string;
  network: NetworkSpec | null; network_label: string;
  /** Changes saved for the device's next restart (deployed labs). */
  pending?: PendingChanges;
  /** Powered on (part of the lab when deployed). Off devices start one at a time. */
  enabled?: boolean;
}

/** Canvas drawing element: labelled group box, free text, or a note. */
export interface Annotation {
  id: string; type: 'group' | 'text' | 'note';
  x: number; y: number; w: number; h: number;
  text: string; color: string; locked: boolean;
}

export interface PendingChanges { image_ref?: string; interface_count?: number; startup_config?: string }

export interface NetworkSpec { mode: 'internal' | 'access' | 'trunk'; nic: string; vlan: number; mgmt?: boolean }

export interface HostNic {
  name: string; mac: string; operstate: string; carrier: boolean; speed_mbps: number | null;
  addresses: string[]; master: string; protected: boolean; host_managed: boolean; nm_state: string;
}

export interface MgmtSettings { subnet: string; mtu: number }

export interface Learned { subnet: string; gateway: string; dns: string[]; server_ip: string }

export interface LabNetworkInfo {
  settings: MgmtSettings;
  status: { internal_subnet: string; bridged: { docker_network: string; bridge: string; learned: Learned | null; leases: number }[] };
  nics: HostNic[];
  bridges: string[];
  usage: { topology_id: number; topology: string; node: string; nic: string; vlan: number; bridge: string; mgmt: boolean }[];
}

export interface Link {
  id: number; topology_id: number; source_node_id: number; target_node_id: number;
  source_interface: string; target_interface: string; label: string; color: string;
}

export type LabStatus = 'idle' | 'deploying' | 'running' | 'syncing' | 'destroying' | 'failed' | 'stopped';
export type ReadyState = 'absent' | 'booting' | 'up' | 'down' | 'starting' | 'unknown';

export interface NodeRuntime {
  container: string | null; state: string; ready_state: ReadyState; uptime_sec: number | null; mgmt_ip: string;
  bridge?: string; carrier?: boolean | null;
}

export interface LabState {
  mgmt: { bridged: boolean; network: string; subnet: string; gateway: string; node_id: number | null };
  status: LabStatus; lab_name: string; last_error: string; pending_sync: boolean;
  started_at: string | null; finished_at: string | null;
  nodes: Record<string, NodeRuntime>;
  skipped_nodes: { id: number; name: string; reason: string; off?: boolean }[];
}

export interface Snapshot {
  id: number; node_id: number; node: string; label: string; family: string;
  created_by: string; created_at: string | null; lines: number;
}

// --- Troubleshooting scenarios (Chaos) -------------------------------------------
export type Difficulty = 'guided' | 'standard' | 'expert';
export type ScenarioState = 'idle' | 'preparing' | 'active' | 'solved' | 'revealed' | 'error';
export interface ScenarioSummary {
  id: number; slug: string; title: string; summary: string; level: LessonLevel; track: LessonTrack; tags: string[];
  protocols: string[]; max_faults: number; version: string; author: string; status: string; topology_summary: string;
  coverage: Record<string, Record<string, number>>;
  attempt: { id: number; state: ScenarioState; topology_id: number | null; difficulty: Difficulty; fault_count: number; rounds: number } | null;
}
export interface ScenarioDetail extends ScenarioSummary {
  topology: LessonTopology; difficulties: Record<Difficulty, string>; can_edit: boolean;
  modules: { id: number; slug: string; title: string; section: string }[];
}
export interface TicketDebrief {
  title: string; protocol: string; category: string; where: string; explanation: string; find: string;
  changed: Record<string, string>; fix: Record<string, string>; silent: boolean;
}
export interface Ticket {
  reporter: string; ticket: string; resolved: boolean; hints: string[]; hints_left: number; debrief?: TicketDebrief;
}
export interface ScenarioAttemptView {
  id: number; scenario_id: number; topology_id: number | null; state: ScenarioState; progress: string; error: string;
  difficulty: Difficulty; fault_count: number; max_faults: number; tickets: Ticket[]; elapsed: number | null;
  checks_run: number; hints_used: number; last_check_at: number | null;
  health: { describe: string; ok: boolean; scope: string }[] | null; health_summary: { ok: number; total: number } | null;
  docs_md: string; debrief_ai: string | null; outdated: boolean;
  /** The lab was rebuilt from scratch during the round (a blunt but valid fix). */
  rebuilt: boolean;
  /** Shown once the round is over: Chaos's theme / back-story and the learner's config changes (diff per router). */
  theme: string | null; story: string | null; changes: Record<string, string> | null;
  history: { difficulty: Difficulty; faults: number; outcome: string; started_at: number | null; ended_at: number; hints: number; checks: number; rebuilt?: boolean; theme?: string }[];
}

// --- Traffic generator nodes (backend services/traffic_gen.py) ------------------

export interface TrafficInterface { name: string; ipv4: string; gateway4: string; ipv6: string; gateway6: string }
export interface TrafficStreamCfg {
  name: string; dst: string; port: number; rate_pps: number; size: number;
  dscp: number; ttl: number | null; enabled: boolean;
}
export interface TrafficReceiverCfg { group: string; source: string; port: number; ipv6?: boolean }
export interface TrafficReflectorCfg { enabled: boolean; port: number; dscp: 'keep' | number }
export interface TrafficConfig {
  interface: TrafficInterface; streams: TrafficStreamCfg[];
  receivers: TrafficReceiverCfg[]; reflector: TrafficReflectorCfg;
}
/** DSCP value → packet count over the last 10 s. */
export type DscpCounts = Record<string, number>;
export interface TrafficStreamStats {
  name: string; dst: string; port: number; dscp: number; sent: number; pps: number; error: string;
  reflected?: { received: number; pps: number; loss_pct: number | null; dscp_back: DscpCounts;
                dscp_at_reflector: DscpCounts; rtt_ms: number | null; last_seen_s: number };
}
export interface TrafficFlowStats {
  src: string; dst: string; port: number; stream: string; multicast: boolean; packets: number;
  lost: number; reordered: number; jitter_ms: number; last_seen_s: number;
  pps: number; kbps: number; loss_pct: number | null; dscp: DscpCounts;
}
export interface TrafficStats {
  ts: number; config_error: string;
  interface: Partial<TrafficInterface> & { error: string };
  streams: TrafficStreamStats[];
  receivers: { group: string; source: string; port: number; joined: boolean; error: string }[];
  flows: TrafficFlowStats[];
  reflector: { enabled?: boolean; port?: number; dscp?: 'keep' | number; reflected: number };
}
export interface TrafficState {
  node_id: number; name: string; config: TrafficConfig; running: boolean; container: string;
  stats: TrafficStats | null; dscp_names: Record<string, number>;
}

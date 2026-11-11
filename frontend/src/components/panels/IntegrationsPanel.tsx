import { useState, useEffect, useMemo } from 'react';
import { useStore } from '../../stores/useStore';
import { api } from '../../utils/api';
import toast from 'react-hot-toast';
import {
  Plus, Trash2, ChevronLeft, RefreshCw, CheckCircle, XCircle,
  Power, PowerOff, Cloud, AlertTriangle, RotateCw,
} from 'lucide-react';

const PHANERO_NODE_FIELDS = [
  'id', 'name', 'vendor', 'model', 'platform', 'location',
  'latitude', 'longitude', 'mtbf_hours', 'mttr_hours', 'capacity_gbps',
  'prometheus_id', 'mgmt_ip', 'is_planned',
];

const PHANERO_LINK_FIELDS = [
  'id', 'source_node_id', 'target_node_id',
  'source_node_name', 'target_node_name',
  'source_interface', 'target_interface',
  'bandwidth_gbps', 'cost', 'latency_ms', 'carrier',
  'mtbf_hours', 'mttr_hours',
  'circuit_cost', 'circuit_id', 'link_id_label',
  'source_ip', 'target_ip', 'is_planned',
];

type System = {
  id: number;
  name: string;
  base_url: string;
  auth_type: 'api_key' | 'bearer' | 'basic';
  auth_header_name: string;
  auth_value: string;
  enabled: boolean;
  verify_ssl: boolean;
};

type Mapping = {
  id: number;
  external_system_id: number;
  phanero_resource: 'node' | 'link';
  target_table: string;
  primary_key_column: string;
  field_map: Record<string, string>;
  enabled: boolean;
};

type QueueItem = {
  id: number;
  phanero_resource: string;
  phanero_id: number;
  operation: string;
  status: 'pending' | 'success' | 'failed' | 'cancelled';
  retry_count: number;
  last_error: string;
  created_at: string | null;
  completed_at: string | null;
};

export default function IntegrationsPanel() {
  const { currentTopologyId } = useStore();
  const [systems, setSystems] = useState<System[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const refreshSystems = async () => {
    if (!currentTopologyId) return;
    try {
      const list = await api.listIntegrations(currentTopologyId);
      setSystems(list);
    } catch (e: any) {
      toast.error(`Load failed: ${e.message}`);
    }
  };

  useEffect(() => { refreshSystems(); }, [currentTopologyId]);

  const selected = systems.find(s => s.id === selectedId) || null;

  if (selected) {
    return (
      <SystemDetail
        system={selected}
        onBack={() => { setSelectedId(null); refreshSystems(); }}
        onChanged={refreshSystems}
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-surface-400">
          Sync nodes and links to an external CRUD-style API.
        </p>
        <button
          className="btn btn-primary btn-sm"
          onClick={() => setShowCreate(true)}
        >
          <Plus size={14} /> Add System
        </button>
      </div>

      {showCreate && (
        <CreateSystemForm
          topologyId={currentTopologyId!}
          onCancel={() => setShowCreate(false)}
          onCreated={async () => { setShowCreate(false); await refreshSystems(); }}
        />
      )}

      {systems.length === 0 && !showCreate && (
        <div className="text-center text-xs text-surface-500 py-6 border border-dashed border-surface-700 rounded">
          No external systems configured.
        </div>
      )}

      {systems.map(s => (
        <div
          key={s.id}
          className="border border-surface-700 rounded p-3 hover:bg-surface-800 cursor-pointer"
          onClick={() => setSelectedId(s.id)}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Cloud size={14} className={s.enabled ? 'text-green-400' : 'text-surface-500'} />
              <span className="text-sm font-medium">{s.name}</span>
              {s.enabled
                ? <span className="text-[10px] px-1.5 py-0.5 bg-green-900/40 text-green-300 rounded">enabled</span>
                : <span className="text-[10px] px-1.5 py-0.5 bg-surface-700 text-surface-400 rounded">disabled</span>
              }
            </div>
            <ChevronLeft size={14} className="text-surface-500 rotate-180" />
          </div>
          <div className="text-[11px] text-surface-500 mt-1 truncate">{s.base_url}</div>
        </div>
      ))}
    </div>
  );
}

// ---- Create Form ----

function CreateSystemForm({
  topologyId, onCancel, onCreated,
}: {
  topologyId: number;
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState({
    name: '',
    base_url: '',
    auth_type: 'api_key' as 'api_key' | 'bearer' | 'basic',
    auth_header_name: 'X-API-Key',
    auth_value: '',
    enabled: false,
    verify_ssl: true,
  });
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!form.name || !form.base_url) {
      toast.error('Name and Base URL are required');
      return;
    }
    setSaving(true);
    try {
      await api.createIntegration(topologyId, form);
      toast.success('System added');
      onCreated();
    } catch (e: any) {
      toast.error(`Create failed: ${e.message}`);
    }
    setSaving(false);
  };

  return (
    <div className="border border-surface-700 rounded p-3 space-y-2 bg-surface-800/50">
      <div className="text-xs font-medium">New External System</div>
      <Input label="Name" value={form.name} onChange={v => setForm({ ...form, name: v })} placeholder="External System" />
      <Input label="Base URL" value={form.base_url} onChange={v => setForm({ ...form, base_url: v })} placeholder="https://api.example.com" />
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] text-surface-400">Auth Type</label>
          <select
            className="w-full bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
            value={form.auth_type}
            onChange={e => setForm({ ...form, auth_type: e.target.value as any })}
          >
            <option value="api_key">API Key (header)</option>
            <option value="bearer">Bearer Token</option>
            <option value="basic">Basic Auth</option>
          </select>
        </div>
        {form.auth_type === 'api_key' && (
          <Input label="Header Name" value={form.auth_header_name} onChange={v => setForm({ ...form, auth_header_name: v })} />
        )}
      </div>
      <Input
        label={form.auth_type === 'basic' ? 'user:password' : form.auth_type === 'bearer' ? 'Bearer token' : 'API key value'}
        value={form.auth_value}
        onChange={v => setForm({ ...form, auth_value: v })}
        type="password"
      />
      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />
        Enabled (sync events will be sent)
      </label>
      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input type="checkbox" checked={form.verify_ssl} onChange={e => setForm({ ...form, verify_ssl: e.target.checked })} />
        Verify SSL certificate
      </label>
      {!form.verify_ssl && (
        <div className="text-[10px] text-yellow-400 flex items-start gap-1">
          <AlertTriangle size={10} className="mt-0.5 shrink-0" />
          <span>SSL verification disabled — connections won't be checked against trusted CAs. Use only for self-signed or internal certs.</span>
        </div>
      )}
      <div className="flex gap-2 justify-end pt-1">
        <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={saving}>
          {saving ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  );
}

// ---- System Detail (tabs) ----

function SystemDetail({
  system, onBack, onChanged,
}: {
  system: System;
  onBack: () => void;
  onChanged: () => void;
}) {
  const { currentTopologyId } = useStore();
  const [tab, setTab] = useState<'connection' | 'mappings' | 'queue'>('connection');

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          <ChevronLeft size={14} /> Back
        </button>
        <span className="text-sm font-medium truncate">{system.name}</span>
      </div>

      <div className="flex border-b border-surface-700">
        {(['connection', 'mappings', 'queue'] as const).map(t => (
          <button
            key={t}
            className={`px-3 py-1.5 text-xs capitalize ${
              tab === t ? 'text-primary-400 border-b-2 border-primary-500' : 'text-surface-400 hover:text-surface-200'
            }`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'connection' && currentTopologyId && (
        <ConnectionTab topologyId={currentTopologyId} system={system} onChanged={onChanged} onDeleted={onBack} />
      )}
      {tab === 'mappings' && currentTopologyId && (
        <MappingsTab topologyId={currentTopologyId} system={system} />
      )}
      {tab === 'queue' && currentTopologyId && (
        <QueueTab topologyId={currentTopologyId} systemId={system.id} />
      )}
    </div>
  );
}

// ---- Connection Tab ----

function ConnectionTab({
  topologyId, system, onChanged, onDeleted,
}: {
  topologyId: number; system: System; onChanged: () => void; onDeleted: () => void;
}) {
  const [form, setForm] = useState({ ...system });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => { setForm({ ...system }); setTestResult(null); }, [system.id]);

  const save = async () => {
    setSaving(true);
    try {
      const payload: any = {
        name: form.name,
        base_url: form.base_url,
        auth_type: form.auth_type,
        auth_header_name: form.auth_header_name,
        enabled: form.enabled,
        verify_ssl: form.verify_ssl,
      };
      // Only send auth_value if user changed it (was originally "***")
      if (form.auth_value !== '***') payload.auth_value = form.auth_value;
      await api.updateIntegration(topologyId, system.id, payload);
      toast.success('Saved');
      onChanged();
    } catch (e: any) {
      toast.error(`Save failed: ${e.message}`);
    }
    setSaving(false);
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.testIntegration(topologyId, system.id);
      setTestResult({ ok: true, message: `Connected — ${r.tables_count} table(s) found` });
    } catch (e: any) {
      let msg = e.message;
      try {
        const detail = JSON.parse(e.message.split(': ').slice(1).join(': ') || '{}');
        msg = detail?.detail?.error || msg;
      } catch { /* keep raw */ }
      setTestResult({ ok: false, message: msg });
    }
    setTesting(false);
  };

  const remove = async () => {
    if (!confirm(`Delete external system "${system.name}"? Mappings, refs, and queue items will be removed.`)) return;
    try {
      await api.deleteIntegration(topologyId, system.id);
      toast.success('Deleted');
      onDeleted();
    } catch (e: any) {
      toast.error(`Delete failed: ${e.message}`);
    }
  };

  const syncAll = async () => {
    if (!confirm('Enqueue every existing node and link for sync?')) return;
    try {
      const r = await api.syncAllToIntegration(topologyId, system.id);
      if (r.enabled) {
        toast.success(`Queued ${r.queued} item(s)`);
      } else {
        toast(`Queued ${r.queued} item(s) — system is disabled, items will be cancelled when processed. Enable to actually sync.`, { duration: 6000, icon: '⚠️' });
      }
    } catch (e: any) {
      toast.error(`Sync-all failed: ${e.message}`);
    }
  };

  return (
    <div className="space-y-2">
      <Input label="Name" value={form.name} onChange={v => setForm({ ...form, name: v })} />
      <Input label="Base URL" value={form.base_url} onChange={v => setForm({ ...form, base_url: v })} />
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] text-surface-400">Auth Type</label>
          <select
            className="w-full bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
            value={form.auth_type}
            onChange={e => setForm({ ...form, auth_type: e.target.value as any })}
          >
            <option value="api_key">API Key (header)</option>
            <option value="bearer">Bearer Token</option>
            <option value="basic">Basic Auth</option>
          </select>
        </div>
        {form.auth_type === 'api_key' && (
          <Input label="Header Name" value={form.auth_header_name} onChange={v => setForm({ ...form, auth_header_name: v })} />
        )}
      </div>
      <Input
        label="Auth Value (leave as *** to keep current)"
        value={form.auth_value}
        onChange={v => setForm({ ...form, auth_value: v })}
        type="password"
      />
      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />
        {form.enabled ? <Power size={12} className="text-green-400" /> : <PowerOff size={12} className="text-surface-500" />}
        Enabled
      </label>
      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input type="checkbox" checked={form.verify_ssl} onChange={e => setForm({ ...form, verify_ssl: e.target.checked })} />
        Verify SSL certificate
      </label>
      {!form.verify_ssl && (
        <div className="text-[10px] text-yellow-400 flex items-start gap-1">
          <AlertTriangle size={10} className="mt-0.5 shrink-0" />
          <span>SSL verification disabled — connections won't be checked against trusted CAs. Use only for self-signed or internal certs.</span>
        </div>
      )}

      {testResult && (
        <div className={`text-xs flex items-start gap-1.5 p-2 rounded ${
          testResult.ok ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'
        }`}>
          {testResult.ok ? <CheckCircle size={12} className="mt-0.5 shrink-0" /> : <XCircle size={12} className="mt-0.5 shrink-0" />}
          <span className="break-all">{testResult.message}</span>
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={test} disabled={testing}>
          {testing ? <RefreshCw size={12} className="animate-spin" /> : <CheckCircle size={12} />} Test
        </button>
        <button className="btn btn-ghost btn-sm" onClick={syncAll}>
          <Cloud size={12} /> Sync All Existing
        </button>
        <button className="btn btn-ghost btn-sm text-red-400 ml-auto" onClick={remove}>
          <Trash2 size={12} /> Delete
        </button>
      </div>
    </div>
  );
}

// ---- Mappings Tab ----

function MappingsTab({
  topologyId, system,
}: {
  topologyId: number; system: System;
}) {
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [tables, setTables] = useState<{ name: string }[]>([]);
  const [tablesError, setTablesError] = useState<string | null>(null);
  const [editing, setEditing] = useState<'node' | 'link' | null>(null);

  const refresh = async () => {
    try {
      const m = await api.listIntegrationMappings(topologyId, system.id);
      setMappings(m);
    } catch (e: any) {
      toast.error(`Load mappings failed: ${e.message}`);
    }
  };

  const loadTables = async () => {
    setTablesError(null);
    try {
      const r = await api.listIntegrationTables(topologyId, system.id);
      setTables(r.tables || []);
    } catch (e: any) {
      let msg = e.message;
      try {
        const detail = JSON.parse(e.message.split(': ').slice(1).join(': ') || '{}');
        msg = detail?.detail?.error || msg;
      } catch { /* keep raw */ }
      setTablesError(msg);
    }
  };

  useEffect(() => { refresh(); loadTables(); }, [system.id]);

  const nodeMapping = mappings.find(m => m.phanero_resource === 'node') || null;
  const linkMapping = mappings.find(m => m.phanero_resource === 'link') || null;

  if (editing) {
    return (
      <MappingEditor
        topologyId={topologyId}
        systemId={system.id}
        resource={editing}
        existing={editing === 'node' ? nodeMapping : linkMapping}
        tables={tables}
        onCancel={() => setEditing(null)}
        onSaved={async () => { setEditing(null); await refresh(); }}
      />
    );
  }

  return (
    <div className="space-y-3">
      {tablesError && (
        <div className="text-xs flex items-start gap-1.5 p-2 rounded bg-yellow-900/30 text-yellow-300">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span className="break-all">Could not load tables: {tablesError}</span>
        </div>
      )}

      <MappingCard
        label="Nodes"
        mapping={nodeMapping}
        onEdit={() => setEditing('node')}
        onDelete={async () => {
          if (!nodeMapping) return;
          if (!confirm('Delete node mapping?')) return;
          try {
            await api.deleteIntegrationMapping(topologyId, system.id, nodeMapping.id);
            await refresh();
          } catch (e: any) { toast.error(`Delete failed: ${e.message}`); }
        }}
      />

      <MappingCard
        label="Links"
        mapping={linkMapping}
        onEdit={() => setEditing('link')}
        onDelete={async () => {
          if (!linkMapping) return;
          if (!confirm('Delete link mapping?')) return;
          try {
            await api.deleteIntegrationMapping(topologyId, system.id, linkMapping.id);
            await refresh();
          } catch (e: any) { toast.error(`Delete failed: ${e.message}`); }
        }}
      />
    </div>
  );
}

function MappingCard({
  label, mapping, onEdit, onDelete,
}: {
  label: string; mapping: Mapping | null; onEdit: () => void; onDelete: () => void;
}) {
  return (
    <div className="border border-surface-700 rounded p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-medium">{label}</span>
        <div className="flex gap-1">
          <button className="btn btn-ghost btn-sm" onClick={onEdit}>
            {mapping ? 'Edit' : 'Configure'}
          </button>
          {mapping && (
            <button className="btn btn-ghost btn-sm text-red-400" onClick={onDelete}>
              <Trash2 size={12} />
            </button>
          )}
        </div>
      </div>
      {mapping ? (
        <div className="text-[11px] text-surface-500 space-y-0.5">
          <div>Table: <span className="text-surface-300">{mapping.target_table}</span></div>
          <div>Primary Key: <span className="text-surface-300">{mapping.primary_key_column}</span></div>
          <div>{Object.keys(mapping.field_map).length} field(s) mapped · {mapping.enabled ? 'enabled' : 'disabled'}</div>
        </div>
      ) : (
        <div className="text-[11px] text-surface-500">Not configured</div>
      )}
    </div>
  );
}

// ---- Mapping Editor ----

function MappingEditor({
  topologyId, systemId, resource, existing, tables, onCancel, onSaved,
}: {
  topologyId: number;
  systemId: number;
  resource: 'node' | 'link';
  existing: Mapping | null;
  tables: { name: string }[];
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [targetTable, setTargetTable] = useState(existing?.target_table || '');
  const [pkColumn, setPkColumn] = useState(existing?.primary_key_column || 'id');
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [fieldMap, setFieldMap] = useState<Array<[string, string]>>(
    existing ? Object.entries(existing.field_map) : []
  );
  const [columns, setColumns] = useState<{ name: string; type?: string; primary_key?: boolean }[]>([]);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [saving, setSaving] = useState(false);

  const phaneroFields = resource === 'node' ? PHANERO_NODE_FIELDS : PHANERO_LINK_FIELDS;

  useEffect(() => {
    if (!targetTable) { setColumns([]); return; }
    setLoadingSchema(true);
    api.getIntegrationTableSchema(topologyId, systemId, targetTable)
      .then(s => {
        setColumns(s.columns || []);
        // Auto-detect PK column if available
        const pk = (s.columns || []).find((c: any) => c.primary_key);
        if (pk && !existing) setPkColumn(pk.name);
        if (s.primary_key && !existing) setPkColumn(s.primary_key);
      })
      .catch(() => setColumns([]))
      .finally(() => setLoadingSchema(false));
  }, [targetTable, topologyId, systemId]);

  const addRow = () => setFieldMap([...fieldMap, ['', '']]);
  const updateRow = (idx: number, key: 0 | 1, val: string) => {
    const next = [...fieldMap];
    next[idx] = key === 0 ? [val, next[idx][1]] : [next[idx][0], val];
    setFieldMap(next);
  };
  const removeRow = (idx: number) => setFieldMap(fieldMap.filter((_, i) => i !== idx));

  const save = async () => {
    if (!targetTable) { toast.error('Pick a target table'); return; }
    const map: Record<string, string> = {};
    for (const [k, v] of fieldMap) {
      if (k.trim()) map[k.trim()] = v;
    }
    setSaving(true);
    try {
      await api.saveIntegrationMapping(topologyId, systemId, {
        phanero_resource: resource,
        target_table: targetTable,
        primary_key_column: pkColumn,
        field_map: map,
        enabled,
      });
      toast.success('Mapping saved');
      onSaved();
    } catch (e: any) {
      toast.error(`Save failed: ${e.message}`);
    }
    setSaving(false);
  };

  const availableColumns = useMemo(() => columns.map(c => c.name).filter(Boolean), [columns]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button className="btn btn-ghost btn-sm" onClick={onCancel}>
          <ChevronLeft size={14} /> Back
        </button>
        <span className="text-sm font-medium capitalize">{resource} Mapping</span>
      </div>

      <div>
        <label className="text-[10px] text-surface-400">Target Table</label>
        {tables.length > 0 ? (
          <select
            className="w-full bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
            value={targetTable}
            onChange={e => setTargetTable(e.target.value)}
          >
            <option value="">— Select —</option>
            {tables.map(t => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select>
        ) : (
          <input
            className="w-full bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
            value={targetTable}
            onChange={e => setTargetTable(e.target.value)}
            placeholder="table_name"
          />
        )}
      </div>

      <Input label="Primary Key Column" value={pkColumn} onChange={setPkColumn} />

      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        Mapping enabled
      </label>

      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-surface-400">
            Field Map {loadingSchema && <span className="text-surface-500">(loading schema…)</span>}
          </span>
          <button className="btn btn-ghost btn-sm" onClick={addRow}>
            <Plus size={12} /> Add Field
          </button>
        </div>
        <div className="text-[10px] text-surface-500 mb-1">
          Left = external column. Right = template, e.g. <code>{'{name}'}</code>, <code>{'{circuit_id}-A'}</code>.
        </div>
        <div className="space-y-1">
          {fieldMap.map(([extCol, template], i) => (
            <div key={i} className="flex gap-1">
              {availableColumns.length > 0 ? (
                <select
                  className="flex-1 bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
                  value={extCol}
                  onChange={e => updateRow(i, 0, e.target.value)}
                >
                  <option value="">— column —</option>
                  {availableColumns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <input
                  className="flex-1 bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
                  value={extCol}
                  onChange={e => updateRow(i, 0, e.target.value)}
                  placeholder="external_column"
                />
              )}
              <input
                className="flex-1 bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs font-mono"
                value={template}
                onChange={e => updateRow(i, 1, e.target.value)}
                placeholder="{phanero_field}"
                list={`phanero-fields-${resource}`}
              />
              <button
                className="btn btn-ghost btn-sm text-red-400"
                onClick={() => removeRow(i)}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          <datalist id={`phanero-fields-${resource}`}>
            {phaneroFields.map(f => <option key={f} value={`{${f}}`} />)}
          </datalist>
        </div>
        <div className="text-[10px] text-surface-500 mt-1">
          Available fields: {phaneroFields.map(f => `{${f}}`).join(', ')}
        </div>
      </div>

      <div className="flex gap-2 justify-end pt-1">
        <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save Mapping'}
        </button>
      </div>
    </div>
  );
}

// ---- Queue Tab ----

function QueueTab({
  topologyId, systemId,
}: {
  topologyId: number; systemId: number;
}) {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const r = await api.listIntegrationQueue(topologyId, systemId, statusFilter, 100);
      setItems(r);
    } catch (e: any) {
      toast.error(`Load queue failed: ${e.message}`);
    }
    setLoading(false);
  };

  useEffect(() => { refresh(); }, [systemId, statusFilter]);

  const retry = async (id: number) => {
    try {
      await api.retryIntegrationQueueItem(topologyId, systemId, id);
      toast.success('Retry queued');
      refresh();
    } catch (e: any) {
      toast.error(`Retry failed: ${e.message}`);
    }
  };

  const clearStatus = async (status: string) => {
    if (!confirm(`Clear all queue items with status "${status}"?`)) return;
    try {
      const r = await api.clearIntegrationQueue(topologyId, systemId, status);
      toast.success(`Cleared ${r.deleted}`);
      refresh();
    } catch (e: any) {
      toast.error(`Clear failed: ${e.message}`);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <select
          className="bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
        >
          <option value="all">All</option>
          <option value="pending">Pending</option>
          <option value="success">Success</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={refresh} disabled={loading}>
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
        <button className="btn btn-ghost btn-sm ml-auto" onClick={() => clearStatus('success')}>
          Clear Successful
        </button>
        <button className="btn btn-ghost btn-sm text-red-400" onClick={() => clearStatus('failed')}>
          Clear Failed
        </button>
      </div>

      {items.length === 0 ? (
        <div className="text-center text-xs text-surface-500 py-6 border border-dashed border-surface-700 rounded">
          No items.
        </div>
      ) : (
        <div className="space-y-1">
          {items.map(it => (
            <div key={it.id} className="border border-surface-700 rounded p-2 text-xs">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <StatusBadge status={it.status} />
                  <span className="font-medium">{it.operation}</span>
                  <span className="text-surface-400">{it.phanero_resource}#{it.phanero_id}</span>
                  {it.retry_count > 0 && (
                    <span className="text-[10px] text-yellow-400">×{it.retry_count}</span>
                  )}
                </div>
                {it.status === 'failed' && (
                  <button className="btn btn-ghost btn-sm" onClick={() => retry(it.id)}>
                    <RotateCw size={12} /> Retry
                  </button>
                )}
              </div>
              {it.last_error && (
                <div className="text-[10px] text-red-400 mt-1 break-all">{it.last_error}</div>
              )}
              <div className="text-[10px] text-surface-500 mt-0.5">
                {it.created_at && new Date(it.created_at).toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    pending: 'bg-blue-900/40 text-blue-300',
    success: 'bg-green-900/40 text-green-300',
    failed: 'bg-red-900/40 text-red-300',
    cancelled: 'bg-surface-700 text-surface-400',
  };
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded ${map[status] || 'bg-surface-700'}`}>
      {status}
    </span>
  );
}

// ---- Helpers ----

function Input({
  label, value, onChange, placeholder, type,
}: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string;
}) {
  return (
    <div>
      <label className="text-[10px] text-surface-400">{label}</label>
      <input
        type={type || 'text'}
        className="w-full bg-surface-900 border border-surface-700 rounded px-2 py-1 text-xs"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

/**
 * Groups sub-sidebar for ConfigDesignView.
 *
 * Renders a list of template groups (think: "projects of templates"). Picking
 * a group opens an inline editor with the group's metadata + ordered member
 * list. Members can be reordered, added (from existing templates), or
 * removed. Clicking a member loads that template body into the main editor
 * via the `selectTemplate` prop — the parent then drives CodeMirror as
 * normal.
 *
 * Groups concatenate their members' rendered output in order to form a
 * complete device config — the agent treats each member like a file in a
 * project (banner, interfaces, OSPF, etc.).
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import {
  Plus, Trash2, ChevronUp, ChevronDown, Pencil, Save, X, FolderTree,
} from 'lucide-react';
import { api } from '../utils/api';

type Member = {
  id: number;
  template_id: number;
  template_name: string;
  position: number;
  body_length: number;
};
type Group = {
  id: number;
  name: string;
  description: string;
  platform_default: string;   // default-rule VALUE (historical name)
  match_attr: string;         // which device attribute the value matches on
  members: Member[];
};
type Template = { id: number; name: string; platform_default: string; match_attr?: string };

export const ruleBadge = (attr: string | undefined, value: string) =>
  (attr && attr !== 'platform' ? `${attr}=` : '') + value;

interface Props {
  templates: Template[];                    // all available templates (for the "add to group" picker)
  selectedTemplateId: number | null;        // which template body is open in the editor
  selectTemplate: (id: number) => void;     // tell the parent to open this template in CodeMirror
  suggestionsByAttr: Record<string, string[]>;              // per-attribute value suggestions
  matchAttrOptions: Array<{ key: string; label: string }>;  // builtin + custom attrs
  // Lifts the currently-focused group id to the parent so the Preview pane
  // can offer a "Render group" action against the selected device.
  onSelectedGroupChange?: (groupId: number | null, groupName: string | null) => void;
}

export default function ConfigDesignGroupsSidebar({
  templates, selectedTemplateId, selectTemplate, suggestionsByAttr, matchAttrOptions,
  onSelectedGroupChange,
}: Props) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);

  // Push selection changes up to the parent so the Preview pane reacts.
  useEffect(() => {
    if (!onSelectedGroupChange) return;
    const g = groups.find(x => x.id === selectedGroupId);
    onSelectedGroupChange(selectedGroupId, g?.name ?? null);
  }, [selectedGroupId, groups, onSelectedGroupChange]);
  const [editingMeta, setEditingMeta] = useState<{ name: string; description: string; platform_default: string; match_attr: string } | null>(null);
  const [showAddMember, setShowAddMember] = useState(false);

  const reload = async () => {
    try {
      const list = await api.listConfigTemplateGroups();
      setGroups(list);
      return list;
    } catch (e: any) {
      toast.error(`Failed to load groups: ${e.message || e}`);
      return [];
    }
  };

  useEffect(() => { reload(); }, []);

  const selected = groups.find(g => g.id === selectedGroupId);

  const onCreateGroup = async () => {
    const name = prompt('Name for the new group?');
    if (!name?.trim()) return;
    try {
      const g = await api.createConfigTemplateGroup({ name: name.trim() });
      await reload();
      setSelectedGroupId(g.id);
      toast.success(`Created group "${g.name}"`);
    } catch (e: any) {
      toast.error(e?.message?.includes('409') ? 'Group with that name already exists' : `Create failed: ${e.message || e}`);
    }
  };

  const onSaveMeta = async () => {
    if (!selected || !editingMeta) return;
    try {
      await api.updateConfigTemplateGroup(selected.id, editingMeta);
      await reload();
      setEditingMeta(null);
      toast.success('Saved');
    } catch (e: any) {
      toast.error(e.message || String(e));
    }
  };

  const onDeleteGroup = async (g: Group) => {
    if (!confirm(`Delete group "${g.name}"? Member templates are not deleted; only the grouping.`)) return;
    try {
      await api.deleteConfigTemplateGroup(g.id);
      await reload();
      if (selectedGroupId === g.id) setSelectedGroupId(null);
      toast.success('Deleted');
    } catch (e: any) {
      toast.error(e.message || String(e));
    }
  };

  const onAddMember = async (templateId: number) => {
    if (!selected) return;
    try {
      await api.addTemplateToGroup(selected.id, templateId);
      await reload();
      setShowAddMember(false);
      toast.success('Added');
    } catch (e: any) {
      toast.error(e?.message?.includes('409') ? 'Already in this group' : `Add failed: ${e.message || e}`);
    }
  };

  const onRemoveMember = async (memberId: number) => {
    if (!selected) return;
    try {
      await api.removeTemplateFromGroup(selected.id, memberId);
      await reload();
    } catch (e: any) {
      toast.error(e.message || String(e));
    }
  };

  const onReorder = async (memberId: number, dir: -1 | 1) => {
    if (!selected) return;
    const ordered = [...selected.members].sort((a, b) => a.position - b.position);
    const idx = ordered.findIndex(m => m.id === memberId);
    if (idx < 0) return;
    const swap = idx + dir;
    if (swap < 0 || swap >= ordered.length) return;
    const reordered = [...ordered];
    [reordered[idx], reordered[swap]] = [reordered[swap], reordered[idx]];
    try {
      await api.reorderGroupMembers(selected.id, reordered.map(m => m.id));
      await reload();
    } catch (e: any) {
      toast.error(e.message || String(e));
    }
  };

  // ---- Render ----
  if (!selected) {
    // List view
    return (
      <>
        <div className="h-10 px-3 flex items-center justify-between border-b border-surface-700">
          <span className="text-xs font-medium flex items-center gap-1.5">
            <FolderTree size={12} /> Groups ({groups.length})
          </span>
          <button className="btn btn-ghost btn-sm text-[10px]" onClick={onCreateGroup}>
            <Plus size={11} /> New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {groups.length === 0 && (
            <div className="text-[11px] text-surface-500 px-2 py-3">
              No groups yet. Groups stitch ordered template snippets into a composite device config.
            </div>
          )}
          {groups.map(g => (
            <button
              key={g.id}
              className="w-full text-left px-2 py-1.5 rounded text-xs flex items-center justify-between gap-2 hover:bg-surface-800 border border-transparent"
              onClick={() => setSelectedGroupId(g.id)}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate">{g.name}</div>
                <div className="text-[10px] text-surface-500">{g.members.length} member{g.members.length === 1 ? '' : 's'}</div>
              </div>
              {g.platform_default && (
                <span className="text-[9px] font-mono bg-surface-700 px-1.5 py-0.5 rounded shrink-0">{ruleBadge(g.match_attr, g.platform_default)}</span>
              )}
            </button>
          ))}
        </div>
      </>
    );
  }

  // Group detail view
  const orderedMembers = [...selected.members].sort((a, b) => a.position - b.position);
  const candidates = templates.filter(t => !orderedMembers.some(m => m.template_id === t.id));

  return (
    <>
      <div className="h-10 px-3 flex items-center justify-between border-b border-surface-700">
        <button
          className="text-[10px] text-surface-400 hover:text-surface-200"
          onClick={() => { setSelectedGroupId(null); setEditingMeta(null); }}
        >
          ← Groups
        </button>
        <button
          className="btn btn-ghost btn-sm p-1 text-red-400/70 hover:text-red-400"
          onClick={() => onDeleteGroup(selected)}
          title="Delete this group (templates are kept)"
        >
          <Trash2 size={11} />
        </button>
      </div>

      <div className="p-2 border-b border-surface-700 space-y-1.5">
        {editingMeta ? (
          <>
            <input
              className="input input-sm w-full text-xs"
              placeholder="Group name"
              value={editingMeta.name}
              onChange={e => setEditingMeta(s => s && ({ ...s, name: e.target.value }))}
            />
            <input
              className="input input-sm w-full text-xs"
              placeholder="Description"
              value={editingMeta.description}
              onChange={e => setEditingMeta(s => s && ({ ...s, description: e.target.value }))}
            />
            <div className="flex items-center gap-1.5">
              <select
                className="input input-sm text-xs w-32 shrink-0"
                value={editingMeta.match_attr || 'platform'}
                onChange={e => setEditingMeta(s => s && ({ ...s, match_attr: e.target.value }))}
                title="Which device attribute this default rule matches on"
              >
                {matchAttrOptions.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select>
              <input
                className="input input-sm flex-1 text-xs font-mono"
                placeholder="Default-rule value (optional)"
                list="config-group-attr-suggestions"
                value={editingMeta.platform_default}
                onChange={e => setEditingMeta(s => s && ({ ...s, platform_default: e.target.value }))}
              />
              <datalist id="config-group-attr-suggestions">
                {(suggestionsByAttr[editingMeta.match_attr || 'platform'] || []).map(p => <option key={p} value={p} />)}
              </datalist>
            </div>
            <div className="flex gap-1.5">
              <button className="btn btn-primary btn-sm text-[10px] flex-1" onClick={onSaveMeta}>
                <Save size={10} /> Save
              </button>
              <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => setEditingMeta(null)}>
                <X size={10} />
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{selected.name}</div>
                {selected.description && (
                  <div className="text-[10px] text-surface-500">{selected.description}</div>
                )}
                {selected.platform_default && (
                  <div className="text-[10px] text-surface-500 mt-0.5">
                    default for <span className="font-mono text-amber-300">{ruleBadge(selected.match_attr, selected.platform_default)}</span>
                  </div>
                )}
              </div>
              <button
                className="btn btn-ghost btn-sm p-1"
                onClick={() => setEditingMeta({
                  name: selected.name,
                  description: selected.description,
                  platform_default: selected.platform_default,
                  match_attr: selected.match_attr || 'platform',
                })}
                title="Edit group metadata"
              >
                <Pencil size={11} />
              </button>
            </div>
          </>
        )}
      </div>

      <div className="flex items-center justify-between px-2 py-1.5 text-[10px] uppercase tracking-wide text-surface-500 border-b border-surface-700">
        <span>Members ({orderedMembers.length}, rendered top → bottom)</span>
        <button
          className="btn btn-ghost btn-sm text-[10px] py-0.5"
          onClick={() => setShowAddMember(v => !v)}
          disabled={candidates.length === 0}
          title={candidates.length === 0 ? 'No other templates available to add' : 'Add a template to this group'}
        >
          <Plus size={10} /> Add
        </button>
      </div>

      {showAddMember && candidates.length > 0 && (
        <div className="px-2 py-1 border-b border-surface-700 max-h-[140px] overflow-y-auto">
          {candidates.map(t => (
            <button
              key={t.id}
              className="w-full text-left px-2 py-1 text-[11px] hover:bg-surface-800 rounded"
              onClick={() => onAddMember(t.id)}
            >
              + {t.name}
              {t.platform_default && <span className="ml-1 text-[9px] font-mono text-surface-500">[{ruleBadge(t.match_attr, t.platform_default)}]</span>}
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {orderedMembers.length === 0 && (
          <div className="text-[11px] text-surface-500 px-2 py-3">
            No members yet. Click <span className="font-mono">+ Add</span> to slot in an existing template.
          </div>
        )}
        {orderedMembers.map((m, i) => (
          <div
            key={m.id}
            className={`group flex items-center gap-1 px-2 py-1.5 rounded text-xs ${
              selectedTemplateId === m.template_id
                ? 'bg-amber-700/30 border border-amber-600/40'
                : 'hover:bg-surface-800 border border-transparent'
            }`}
          >
            <span className="text-[10px] text-surface-500 font-mono w-5 shrink-0">{i + 1}.</span>
            <button
              className="flex-1 text-left truncate"
              onClick={() => selectTemplate(m.template_id)}
              title="Open this member template in the editor"
            >
              {m.template_name}
            </button>
            <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                className="btn btn-ghost btn-sm p-0.5 text-surface-400 hover:text-surface-200"
                onClick={() => onReorder(m.id, -1)}
                disabled={i === 0}
                title="Move up"
              >
                <ChevronUp size={11} />
              </button>
              <button
                className="btn btn-ghost btn-sm p-0.5 text-surface-400 hover:text-surface-200"
                onClick={() => onReorder(m.id, 1)}
                disabled={i === orderedMembers.length - 1}
                title="Move down"
              >
                <ChevronDown size={11} />
              </button>
              <button
                className="btn btn-ghost btn-sm p-0.5 text-red-400/70 hover:text-red-400"
                onClick={() => onRemoveMember(m.id)}
                title="Remove from group (template itself isn't deleted)"
              >
                <X size={11} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

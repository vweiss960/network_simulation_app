/**
 * ViewsPanel — manage sub-topology views for the current topology.
 *
 * The canvas shows the UNION of the active (checked) views, stacked as colored
 * layers. The "Default view" is every node MINUS the ones the user removed, so
 * new / inherited nodes appear automatically. Saved views are fixed named
 * subsets, created from a canvas multi-selection via the right-click menu.
 *
 * Editing a view (one at a time) ghosts non-members on the canvas so you can
 * right-click to add / remove members. For the Default view, removing a node
 * just hides it (adds it to the exclusion set); re-adding brings it back.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Pencil, Trash2, Layers, Check, GripVertical } from 'lucide-react';
import { useStore } from '../../stores/useStore';
import { resolveViewColors, adHocLayerColor, VIEW_LAYER_PALETTE } from '../../utils/colors';

type SavedView = { id: number; name: string; description: string; node_ids: number[]; color: string };

export default function ViewsPanel() {
  const currentTopologyId = useStore(s => s.currentTopologyId);
  const nodes = useStore(s => s.nodes);
  const savedViews = useStore(s => (s as any).savedViews) as SavedView[];
  const activeViewIds = useStore(s => (s as any).activeViewIds) as Array<number | 'default' | 'adhoc'>;
  const defaultExcludedNodeIds = useStore(s => (s as any).defaultExcludedNodeIds) as number[];
  const adHocView = useStore(s => (s as any).adHocView) as number[] | null;
  const editingViewId = useStore(s => (s as any).editingViewId) as number | 'default' | null;
  const loadTopologyViews = useStore(s => (s as any).loadTopologyViews) as (id: number) => Promise<void>;
  const toggleActiveView = useStore(s => (s as any).toggleActiveView) as (id: number | 'default' | 'adhoc') => void;
  const reorderSavedView = useStore(s => (s as any).reorderSavedView) as (draggedId: number, targetId: number) => void;
  const setEditingView = useStore(s => (s as any).setEditingView) as (id: number | 'default' | null) => void;
  const resetToDefaultView = useStore(s => (s as any).resetToDefaultView) as () => void;
  const deleteTopologyView = useStore(s => (s as any).deleteTopologyView) as (tid: number, vid: number) => Promise<void>;
  const updateTopologyView = useStore(s => (s as any).updateTopologyView) as (tid: number, vid: number, data: any) => Promise<void>;
  // Protected topology not in edit mode → views are display-only (show/hide +
  // reorder stay; create/rename/delete/recolor/edit-members are mutations → hidden).
  const canEdit = useStore(s => (s as any).canEditCurrentTopology()) as boolean;

  const [colorEditId, setColorEditId] = useState<number | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);

  useEffect(() => {
    if (currentTopologyId) loadTopologyViews(currentTopologyId);
  }, [currentTopologyId, loadTopologyViews]);

  const defaultCount = nodes.length - defaultExcludedNodeIds.length;
  const isActive = (id: number | 'default' | 'adhoc') => activeViewIds.includes(id);
  const isEditing = (id: number | 'default') => editingViewId === id;
  // Stable per-view colors (matches the canvas): stored color wins, else a
  // collision-free palette color keyed by id, independent of list order.
  const viewColorMap = resolveViewColors(savedViews);
  const colorOf = (v: SavedView) => viewColorMap.get(v.id) || VIEW_LAYER_PALETTE[0];

  const setViewColor = async (v: SavedView, color: string) => {
    if (!currentTopologyId) return;
    setColorEditId(null);
    try {
      await updateTopologyView(currentTopologyId, v.id, { color });
    } catch (e: any) {
      toast.error(`Color change failed: ${e?.message || e}`);
    }
  };

  const onRename = async (v: SavedView) => {
    if (!currentTopologyId) return;
    const name = window.prompt('Rename view:', v.name);
    if (!name || !name.trim() || name.trim() === v.name) return;
    try {
      await updateTopologyView(currentTopologyId, v.id, { name: name.trim() });
      toast.success('Renamed');
    } catch (e: any) {
      toast.error(`Rename failed: ${e?.message || e}`);
    }
  };

  const onDelete = async (v: SavedView) => {
    if (!currentTopologyId) return;
    if (!confirm(`Delete view "${v.name}"? (The nodes themselves are not affected.)`)) return;
    try {
      await deleteTopologyView(currentTopologyId, v.id);
      toast.success('Deleted');
    } catch (e: any) {
      toast.error(`Delete failed: ${e?.message || e}`);
    }
  };

  // A reusable swatch/check toggle for the "show this layer" control.
  const LayerToggle = ({ id, color }: { id: number | 'default' | 'adhoc'; color?: string }) => {
    const on = isActive(id);
    return (
      <button
        className="w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors"
        style={{
          borderColor: color || (on ? '#a78bfa' : '#475569'),
          backgroundColor: on ? (color || '#7c3aed') : 'transparent',
        }}
        onClick={() => toggleActiveView(id)}
        title={on ? 'Hide this layer' : 'Show this layer (stacks with others)'}
      >
        {on && <Check size={11} className="text-white" />}
      </button>
    );
  };

  return (
    <div className="space-y-3">
      <div className="text-[11px] text-surface-400 leading-relaxed">
        Check views to <span className="text-surface-200 font-medium">stack them as layers</span> — the canvas
        shows the union, each layer in its own color. The <span className="text-surface-200 font-medium">Default
        view</span> holds every node (new and inherited nodes join it automatically); edit it to remove nodes.
        Select nodes on the canvas, then right-click → <span className="font-mono text-[10px]">Save selected as view</span>.
      </div>

      {/* Editing indicator */}
      {editingViewId != null && (
        <div className="border border-amber-500/50 bg-amber-900/15 rounded p-2 space-y-1.5">
          <div className="flex items-center gap-2 text-[11px] text-amber-200">
            <Pencil size={12} />
            <span>Editing {editingViewId === 'default' ? 'Default view' : (savedViews.find(v => v.id === editingViewId)?.name || 'view')}</span>
          </div>
          <div className="text-[10px] text-amber-300/80">
            Non-members appear ghosted on the canvas. Right-click any node →
            <span className="font-mono"> Add</span> / <span className="font-mono">Remove</span>.
          </div>
          <button className="btn btn-primary btn-sm text-[10px]" onClick={() => setEditingView(null)}>
            Done editing
          </button>
        </div>
      )}

      {/* Default view */}
      <div className={`border rounded px-2 py-1.5 flex items-center gap-2 ${
        isEditing('default') ? 'border-amber-500/60 bg-amber-900/15' : isActive('default') ? 'border-violet-500/50 bg-violet-900/10' : 'border-surface-700'
      }`}>
        <LayerToggle id="default" />
        <div className="flex-1 min-w-0">
          <div className="text-[12px] font-medium flex items-center gap-1.5">
            <Layers size={12} className="text-surface-400" /> Default view
          </div>
          <div className="text-[10px] text-surface-500">
            {defaultCount} of {nodes.length} node{nodes.length === 1 ? '' : 's'}
            {defaultExcludedNodeIds.length > 0 && ` · ${defaultExcludedNodeIds.length} removed`}
          </div>
        </div>
        {canEdit && (
          <button
            className={`btn btn-ghost btn-sm text-[10px] ${isEditing('default') ? 'text-amber-300' : ''}`}
            onClick={() => setEditingView(isEditing('default') ? null : 'default')}
            title="Add / remove nodes from the Default view"
          >
            <Pencil size={12} />
          </button>
        )}
      </div>

      {/* Ad-hoc selection layer (transient) */}
      {adHocView && adHocView.length > 0 && (
        <div className={`border rounded px-2 py-1.5 flex items-center gap-2 ${
          isActive('adhoc') ? 'border-violet-500/50 bg-violet-900/10' : 'border-surface-700'
        }`}>
          <LayerToggle id="adhoc" color={adHocLayerColor(viewColorMap.values())} />
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-medium">Selection</div>
            <div className="text-[10px] text-surface-500">{adHocView.length} node{adHocView.length === 1 ? '' : 's'} · not saved</div>
          </div>
        </div>
      )}

      {/* Saved views */}
      <div>
        <div className="text-[10px] uppercase tracking-wide text-surface-500">
          Saved views ({savedViews.length})
        </div>
        {savedViews.length > 1 && (
          <div className="text-[10px] text-surface-500 mb-1">
            Drag to reorder layers — the <span className="text-emerald-300">bottom</span> view's color wins on overlap.
          </div>
        )}
        {savedViews.length === 0 && (
          <div className="text-[11px] text-surface-500 border border-dashed border-surface-700 rounded p-3 text-center">
            No saved views yet.
          </div>
        )}
        <div className="space-y-1">
          {savedViews.map(v => {
            const active = isActive(v.id);
            const editing = isEditing(v.id);
            const color = colorOf(v);
            return (
              <div
                key={v.id}
                draggable
                onDragStart={() => setDragId(v.id)}
                onDragOver={e => e.preventDefault()}
                onDrop={() => { if (dragId != null) reorderSavedView(dragId, v.id); setDragId(null); }}
                onDragEnd={() => setDragId(null)}
                className={`border rounded px-2 py-1.5 ${dragId === v.id ? 'opacity-50' : ''} ${
                  editing ? 'border-amber-500/60 bg-amber-900/15' : active ? 'border-violet-500/60 bg-violet-900/15' : 'border-surface-700'
                }`}
              >
                <div className="flex items-center gap-2">
                  <GripVertical size={12} className="text-surface-600 cursor-grab shrink-0" />
                  <LayerToggle id={v.id} color={color} />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-medium truncate">{v.name}</div>
                    <div className="text-[10px] text-surface-500">{v.node_ids.length} node{v.node_ids.length === 1 ? '' : 's'}</div>
                  </div>
                  {canEdit && (
                    <>
                      <button
                        className="btn btn-ghost btn-sm text-[10px]"
                        onClick={() => setColorEditId(colorEditId === v.id ? null : v.id)}
                        title="Change this view's color"
                      >
                        <span className="w-3.5 h-3.5 rounded-full border border-surface-500 inline-block" style={{ backgroundColor: color }} />
                      </button>
                      <button
                        className={`btn btn-ghost btn-sm text-[10px] ${editing ? 'text-amber-300' : ''}`}
                        onClick={() => setEditingView(editing ? null : v.id)}
                        title="Edit members"
                      >
                        <Pencil size={12} />
                      </button>
                      <button className="btn btn-ghost btn-sm text-[10px]" onClick={() => onRename(v)} title="Rename">
                        <span className="text-[10px]">Aa</span>
                      </button>
                      <button className="btn btn-ghost btn-sm text-[10px] text-red-300 hover:bg-red-900/30" onClick={() => onDelete(v)} title="Delete">
                        <Trash2 size={12} />
                      </button>
                    </>
                  )}
                </div>
                {canEdit && colorEditId === v.id && (
                  <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-surface-700">
                    {VIEW_LAYER_PALETTE.map(c => (
                      <button key={c} className="w-5 h-5 rounded-sm border border-surface-600 hover:scale-110 transition-transform"
                        style={{ backgroundColor: c, outline: color === c ? '2px solid white' : 'none', outlineOffset: '1px' }}
                        onClick={() => setViewColor(v, c)} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {activeViewIds.length > 1 || defaultExcludedNodeIds.length > 0 || (adHocView && adHocView.length) ? (
        <button className="btn btn-ghost btn-sm text-[10px] w-full" onClick={() => resetToDefaultView()}>
          Reset to Default view
        </button>
      ) : null}
    </div>
  );
}

import { useEffect, useState } from 'react';
import { useStore } from '../../stores/useStore';
import toast from 'react-hot-toast';
import { Save } from 'lucide-react';
import { api } from '../../utils/api';
import CustomAttributesSection from './CustomAttributesSection';

export default function SettingsPanel() {
  const { globalSettings, updateSetting, loadSettings } = useStore();
  const [editing, setEditing] = useState<Record<string, string>>({});
  // Pull feature flags so Custom Attributes only renders when Config Design
  // is enabled — keeps non-config users from seeing template-only knobs.
  const [features, setFeatures] = useState<{ config_design_enabled?: boolean } | null>(null);
  useEffect(() => { api.getFeatures().then(setFeatures).catch(() => setFeatures({})); }, []);

  const dirtyKeys = Object.keys(editing).filter(k => {
    const setting = globalSettings.find(s => s.key === k);
    return setting && String(setting.value) !== editing[k];
  });
  const hasDirty = dirtyKeys.length > 0;

  const handleSaveAll = async () => {
    try {
      for (const key of dirtyKeys) {
        await updateSetting(key, parseFloat(editing[key]));
      }
      setEditing({});
      toast.success(`Saved ${dirtyKeys.length} setting${dirtyKeys.length > 1 ? 's' : ''}`);
    } catch { toast.error('Failed to save settings'); }
  };

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-surface-500">
        Global defaults for MTBF, MTTR, simulation parameters, and cost settings.
        These apply to all nodes and links that don't have their own values set.
        Changing a default here immediately affects any node/link using the default.
      </p>

      {globalSettings.map(s => {
        const isDirty = editing[s.key] !== undefined && editing[s.key] !== String(s.value);
        return (
          <div key={s.id} className={`bg-surface-800 rounded-lg p-3 transition-colors ${isDirty ? 'ring-1 ring-amber-500/50' : ''}`}>
            <div className="text-xs font-medium mb-1">{s.description}</div>
            <div className="text-[10px] text-surface-500 mb-2 font-mono">{s.key}</div>
            <input
              className="w-full"
              type="number"
              value={editing[s.key] ?? String(s.value)}
              onChange={e => setEditing({ ...editing, [s.key]: e.target.value })}
            />
            {isDirty && (
              <div className="text-[10px] text-amber-400 mt-1">
                Changed from {s.value} → unsaved
              </div>
            )}
          </div>
        );
      })}

      {hasDirty && (
        <div className="sticky bottom-0 bg-surface-900/95 backdrop-blur-sm pt-2 pb-1 border-t border-surface-700">
          <button className="btn btn-primary btn-sm w-full" onClick={handleSaveAll}>
            <Save size={14} /> Save {dirtyKeys.length} Change{dirtyKeys.length > 1 ? 's' : ''}
          </button>
          <button
            className="btn btn-sm w-full mt-1 text-surface-400 hover:text-surface-200"
            onClick={() => setEditing({})}
          >
            Discard Changes
          </button>
        </div>
      )}

      {globalSettings.length === 0 && (
        <div className="text-center py-8 text-surface-500 text-sm">Loading settings...</div>
      )}

      <CustomAttributesSection enabled={!!features?.config_design_enabled} />
    </div>
  );
}

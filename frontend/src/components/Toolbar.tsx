import { Fragment, useEffect, useState } from 'react';
import { useStore } from '../stores/useStore';
import { CLOSE_MENUS_EVENT } from '../utils/hotkeys';
import {
  Box, Link2, GitBranch, Play, Settings, Layout, MessageSquare, Radar, Layers, Wrench, Activity, Users, DollarSign, Cloud, Container, ScanEye, Building2,
  Library, Database, ChevronRight, Route, type LucideIcon,
} from 'lucide-react';

type PanelId =
  | 'lab' | 'nodes' | 'links' | 'flows' | 'customers' | 'views'
  | 'simulation' | 'paths' | 'maintenance' | 'srlgs' | 'cost-analysis'
  | 'templates' | 'site-templates' | 'discovery' | 'prometheus' | 'integrations'
  | 'settings';
type Tool = { id: PanelId; icon: LucideIcon; label: string; flag?: string; labOnly?: boolean };
type Section = {
  key: string;
  label: string;
  tools: Tool[];
  /** Collapse into one rail icon with a flyout (when it has 2+ visible tools). */
  flyout?: { icon: LucideIcon; label: string };
};

// The rail, top to bottom. Frequent tools are one click away; the occasional
// Library / Data groups fold into a flyout each. Settings sits at the bottom.
const SECTIONS: Section[] = [
  // Lab Settings comes first in lab mode so it's quick to get back to.
  // The header Lab button toggles modes; this button just re-opens the panel.
  { key: 'lab', label: 'Lab', tools: [{ id: 'lab', icon: Container, label: 'Lab Settings', labOnly: true }] },
  {
    key: 'model', label: 'Model', tools: [
      { id: 'nodes', icon: Box, label: 'Nodes' },
      { id: 'links', icon: Link2, label: 'Links' },
      { id: 'flows', icon: GitBranch, label: 'Flows' },
      { id: 'customers', icon: Users, label: 'Customers' },
      { id: 'views', icon: ScanEye, label: 'Sub-topology Views' },
    ],
  },
  {
    key: 'analyze', label: 'Analyze', tools: [
      { id: 'simulation', icon: Play, label: 'Simulate' },
      { id: 'paths', icon: Route, label: 'Path Analysis' },
      { id: 'maintenance', icon: Wrench, label: 'Maintenance' },
      { id: 'srlgs', icon: Layers, label: 'SRLGs' },
      { id: 'cost-analysis', icon: DollarSign, label: 'Cost Analysis', flag: 'cost_enabled' },
    ],
  },
  {
    key: 'library', label: 'Library', flyout: { icon: Library, label: 'Library' }, tools: [
      { id: 'templates', icon: Layout, label: 'Device Templates', flag: 'lab_enabled' },
      { id: 'site-templates', icon: Building2, label: 'Site Templates' },
    ],
  },
  {
    key: 'data', label: 'Data', flyout: { icon: Database, label: 'Data' }, tools: [
      { id: 'discovery', icon: Radar, label: 'Discovery', flag: 'discovery_enabled' },
      { id: 'prometheus', icon: Activity, label: 'Prometheus', flag: 'prometheus_enabled' },
      { id: 'integrations', icon: Cloud, label: 'Integrations', flag: 'integrations_enabled' },
    ],
  },
];
const SETTINGS_TOOL: Tool = { id: 'settings', icon: Settings, label: 'Settings' };

// Hide tools that don't apply to a planning (child) topology — those are
// managed in the root and inherited automatically.
const hiddenForChild = new Set<PanelId>(['discovery', 'prometheus', 'integrations', 'templates']);
// Hide tools that don't apply in lab mode — the canvas is showing the
// translated/live lab view, not the design, so design-time concerns
// (simulation, customers, cost allocation, etc.) are out of scope.
const hiddenInLabMode = new Set<PanelId>([
  'flows', 'simulation', 'paths', 'srlgs', 'maintenance', 'discovery', 'customers',
  'cost-analysis', 'prometheus', 'integrations', 'settings', 'site-templates',
]);
// Config Design mode hides every side-panel entry — the dedicated view has
// its own variable browser, AI drawer, and device picker, so the toolbar's
// tools (nodes, links, templates, etc.) would just add visual noise.
const hiddenInConfigMode = new Set<PanelId>([
  'nodes', 'links', 'flows', 'simulation', 'paths', 'srlgs', 'maintenance',
  'cost-analysis', 'prometheus', 'discovery', 'customers', 'integrations',
  'settings', 'templates', 'site-templates',
]);

/** Styled tooltip that slides out to the right of a rail button. */
function Tip({ label }: { label: string }) {
  return (
    <span className="pointer-events-none absolute left-full ml-2.5 top-1/2 -translate-y-1/2 z-50 whitespace-nowrap rounded-md bg-surface-800 border border-surface-600 px-2 py-1 text-[11px] font-medium text-surface-100 shadow-lg opacity-0 -translate-x-1 group-hover/btn:opacity-100 group-hover/btn:translate-x-0 transition-all duration-150">
      {label}
    </span>
  );
}

/** Rail button: accent bar + soft tint when active. */
function RailButton({ icon: Icon, label, active, onClick, tip = true, accent = 'primary', children }: {
  icon: LucideIcon; label: string; active: boolean; onClick: () => void; tip?: boolean;
  accent?: 'primary' | 'violet'; children?: React.ReactNode;
}) {
  const bar = accent === 'violet' ? 'bg-violet-400' : 'bg-primary-400';
  const on = accent === 'violet' ? 'bg-violet-500/15 text-violet-200' : 'bg-primary-500/15 text-primary-200';
  const off = accent === 'violet'
    ? 'text-violet-400 hover:bg-surface-800 hover:text-violet-300'
    : 'text-surface-400 hover:bg-surface-800 hover:text-surface-100';
  return (
    <div className="relative group/btn">
      {active && <span className={`absolute -left-2 top-2 bottom-2 w-0.5 rounded-r ${bar}`} />}
      <button
        className={`w-10 h-10 rounded-lg flex items-center justify-center transition-colors ${active ? on : off}`}
        onClick={onClick}
        aria-label={label}
      >
        <Icon size={18} />
      </button>
      {tip && <Tip label={label} />}
      {children}
    </div>
  );
}

export default function Toolbar() {
  const { sidePanel, setSidePanel, topologies, currentTopologyId } = useStore();
  const labMode = useStore(s => (s as any).labMode) as boolean;
  const configMode = useStore(s => (s as any).configMode) as boolean;
  const features = useStore(s => s.features);
  // Which section's flyout is open (by section key).
  const [flyout, setFlyout] = useState<string | null>(null);
  // Close an open flyout on Esc or the F ("full screen") hotkey.
  useEffect(() => {
    if (!flyout) return;
    const close = () => setFlyout(null);
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    window.addEventListener(CLOSE_MENUS_EVENT, close);
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener(CLOSE_MENUS_EVENT, close); window.removeEventListener('keydown', onKey); };
  }, [flyout]);

  const currentTopo = topologies.find(t => t.id === currentTopologyId);
  const isChildTopology = !!currentTopo?.parent_topology_id;

  const visible = (t: Tool) => {
    // Optional feature modules: hidden everywhere unless enabled via env var.
    if (t.flag && !(features as any)?.[t.flag]) return false;
    if (labMode) return !hiddenInLabMode.has(t.id);  // labOnly entries appear in lab mode
    if (t.labOnly) return false;  // Lab Settings only in lab mode
    if (configMode) return !hiddenInConfigMode.has(t.id);
    if (isChildTopology && hiddenForChild.has(t.id)) return false;
    return true;
  };
  const sections = SECTIONS
    .map(s => ({ ...s, tools: s.tools.filter(visible) }))
    .filter(s => s.tools.length > 0);
  const toggle = (id: PanelId) => setSidePanel(sidePanel === id ? 'none' : id);

  return (
    <div className="group/rail w-14 bg-surface-900 border-r border-surface-700 flex flex-col items-center py-2 gap-1 shrink-0">
      {sections.map((section, i) => {
        const collapsed = !!section.flyout && section.tools.length > 1;
        return (
          <Fragment key={section.key}>
            {/* Section divider; its label fades in while the rail is hovered. */}
            <div className="relative h-3 w-full flex items-center justify-center">
              {i > 0 && <span className="w-6 h-px bg-surface-700 group-hover/rail:opacity-0 transition-opacity" />}
              <span className="absolute inset-x-0 text-center text-[8px] font-semibold uppercase tracking-wider text-surface-500 opacity-0 group-hover/rail:opacity-100 transition-opacity">
                {section.label}
              </span>
            </div>
            {collapsed ? (
              <RailButton
                icon={section.flyout!.icon}
                label={section.flyout!.label}
                active={section.tools.some(t => t.id === sidePanel) || flyout === section.key}
                onClick={() => setFlyout(flyout === section.key ? null : section.key)}
                tip={flyout !== section.key}
              >
                <ChevronRight size={9} className="absolute right-0 bottom-0.5 text-surface-500 pointer-events-none" />
                {flyout === section.key && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setFlyout(null)} />
                    <div className="absolute left-full ml-2.5 top-0 z-50 min-w-[190px] bg-surface-800 border border-surface-600 rounded-lg shadow-xl py-1 fade-in">
                      <div className="px-3 pt-1 pb-1.5 text-[9px] font-semibold uppercase tracking-wider text-surface-500">{section.label}</div>
                      {section.tools.map(t => {
                        const Icon = t.icon;
                        const active = sidePanel === t.id;
                        return (
                          <button
                            key={t.id}
                            className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-xs transition-colors ${
                              active ? 'bg-primary-500/15 text-primary-200' : 'text-surface-200 hover:bg-surface-700'
                            }`}
                            onClick={() => { toggle(t.id); setFlyout(null); }}
                          >
                            <Icon size={14} className={active ? 'text-primary-300' : 'text-surface-400'} />
                            {t.label}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
              </RailButton>
            ) : (
              section.tools.map(t => (
                <RailButton key={t.id} icon={t.icon} label={t.label} active={sidePanel === t.id} onClick={() => toggle(t.id)} />
              ))
            )}
          </Fragment>
        );
      })}

      {/* Bottom dock: the general-purpose AI Chat — gated behind the AI module
          (PHANERO_AI_ENABLED) and hidden in Config Design mode since that view
          has its own template-tuned AI drawer — and Settings. */}
      <div className="mt-auto flex flex-col items-center gap-1">
        {features?.ai_enabled && !configMode && (
          <RailButton icon={MessageSquare} label="AI Assistant" active={sidePanel === 'chat'} accent="violet"
            onClick={() => setSidePanel(sidePanel === 'chat' ? 'none' : 'chat')} />
        )}
        {visible(SETTINGS_TOOL) && (
          <RailButton icon={Settings} label="Settings" active={sidePanel === 'settings'} onClick={() => toggle('settings')} />
        )}
      </div>
    </div>
  );
}

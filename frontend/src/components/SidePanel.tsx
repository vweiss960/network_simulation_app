import { useStore } from '../stores/useStore';
import { X } from 'lucide-react';
import NodesPanel from './panels/NodesPanel';
import LinksPanel from './panels/LinksPanel';
import FlowsPanel from './panels/FlowsPanel';
import SimulationPanel from './panels/SimulationPanel';
import SettingsPanel from './panels/SettingsPanel';
import TemplatesPanel from './panels/TemplatesPanel';
import ChatPanel from './panels/ChatPanel';
import DiscoveryPanel from './panels/DiscoveryPanel';
import UserManagementPanel from './panels/UserManagementPanel';
import SrlgPanel from './panels/SrlgPanel';
import MaintenancePanel from './panels/MaintenancePanel';
import PrometheusPanel from './panels/PrometheusPanel';
import CustomerPanel from './panels/CustomerPanel';
import CostAnalysisPanel from './panels/CostAnalysisPanel';
import IntegrationsPanel from './panels/IntegrationsPanel';
import LabPanel from './panels/LabPanel';
import ViewsPanel from './panels/ViewsPanel';
import SiteTemplatesPanel from './panels/SiteTemplatesPanel';
import PathAnalysisPanel from './panels/PathAnalysisPanel';

const panelTitles: Record<string, string> = {
  nodes: 'Nodes',
  links: 'Links',
  flows: 'Flows / Demands',
  simulation: 'Simulation',
  settings: 'Global Settings',
  templates: 'Device Templates',
  chat: 'AI Assistant',
  discovery: 'Network Discovery',
  users: 'User Management',
  srlgs: 'Shared Risk Groups',
  maintenance: 'Maintenance Window',
  prometheus: 'Prometheus',
  customers: 'Customers',
  'cost-analysis': 'Cost Allocation',
  integrations: 'External Integrations',
  lab: 'Lab (containerlab)',
  views: 'Sub-topology Views',
  'site-templates': 'Site Templates',
  paths: 'Path Analysis',
};

function PanelCount({ panel }: { panel: string }) {
  const { nodes, links, flows } = useStore();
  const count = panel === 'nodes' ? nodes.length
    : panel === 'links' ? links.length
    : panel === 'flows' ? flows.length
    : null;
  if (count === null) return null;
  return <span className="text-[10px] text-surface-500 ml-1.5">({count})</span>;
}

// Panels that belong to an optional feature module — don't render if its flag
// is off (defensive: a persisted sidePanel could point at a disabled module).
const PANEL_FLAG: Record<string, string> = {
  discovery: 'discovery_enabled',
  'cost-analysis': 'cost_enabled',
  prometheus: 'prometheus_enabled',
  integrations: 'integrations_enabled',
  chat: 'ai_enabled',
  templates: 'lab_enabled',
};

export default function SidePanel() {
  const { sidePanel, setSidePanel } = useStore();
  const features = useStore(s => s.features);

  if (sidePanel === 'none') return null;
  const reqFlag = PANEL_FLAG[sidePanel];
  if (reqFlag && features && !(features as any)[reqFlag]) return null;

  const isChat = sidePanel === 'chat';

  return (
    <div className={`${isChat ? 'w-[440px]' : 'w-[380px]'} bg-surface-900 border-l border-surface-700 flex flex-col shrink-0 slide-in`}>
      <div className="h-10 flex items-center justify-between px-3 border-b border-surface-700">
        <span className="text-sm font-medium">{panelTitles[sidePanel]}<PanelCount panel={sidePanel} /></span>
        <button
          className="btn btn-ghost btn-sm p-1"
          onClick={() => setSidePanel('none')}
        >
          <X size={14} />
        </button>
      </div>
      <div className={`flex-1 overflow-y-auto ${isChat ? '' : 'p-3'} ${isChat ? 'flex flex-col' : ''}`}>
        {sidePanel === 'nodes' && <NodesPanel />}
        {sidePanel === 'links' && <LinksPanel />}
        {sidePanel === 'flows' && <FlowsPanel />}
        {sidePanel === 'simulation' && <SimulationPanel />}
        {sidePanel === 'settings' && <SettingsPanel />}
        {sidePanel === 'templates' && <TemplatesPanel />}
        {sidePanel === 'chat' && <ChatPanel />}
        {sidePanel === 'discovery' && <DiscoveryPanel />}
        {sidePanel === 'users' && <UserManagementPanel />}
        {sidePanel === 'srlgs' && <SrlgPanel />}
        {sidePanel === 'maintenance' && <MaintenancePanel />}
        {sidePanel === 'prometheus' && <PrometheusPanel />}
        {sidePanel === 'customers' && <CustomerPanel />}
        {sidePanel === 'cost-analysis' && <CostAnalysisPanel />}
        {sidePanel === 'integrations' && <IntegrationsPanel />}
        {sidePanel === 'lab' && <LabPanel />}
        {sidePanel === 'views' && <ViewsPanel />}
        {sidePanel === 'site-templates' && <SiteTemplatesPanel />}
        {sidePanel === 'paths' && <PathAnalysisPanel />}
      </div>
    </div>
  );
}

import { useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { Boxes, Cable, HardDrive, LayoutGrid, LogOut, Users } from 'lucide-react';
import { useAuthStore } from './stores/useAuthStore';
import { useLabStore, type Page } from './stores/useLabStore';
import LoginPage, { PhaneroLogo } from './components/LoginPage';
import LabsHome from './components/pages/LabsHome';
import ImagesPage from './components/pages/ImagesPage';
import TemplatesPage from './components/pages/TemplatesPage';
import NetworkPage from './components/pages/NetworkPage';
import Workspace from './components/workspace/Workspace';
import UserManagementPanel from './components/panels/UserManagementPanel';

const NAV: { page: Page; label: string; icon: typeof Boxes; admin?: boolean }[] = [
  { page: 'labs', label: 'Labs', icon: LayoutGrid },
  { page: 'templates', label: 'Templates', icon: Boxes },
  { page: 'images', label: 'Images', icon: HardDrive },
  { page: 'network', label: 'Network', icon: Cable },
  { page: 'users', label: 'Users', icon: Users, admin: true },
];

export default function App() {
  const { isAuthenticated, isLoading, checkAuth, user, logout } = useAuthStore();
  const { page, setPage, bootstrap, currentTopologyId } = useLabStore();

  useEffect(() => { checkAuth(); }, [checkAuth]);
  useEffect(() => { if (isAuthenticated) bootstrap(); }, [isAuthenticated, bootstrap]);

  if (isLoading) return <div className="h-screen flex items-center justify-center text-surface-400">Loading…</div>;
  if (!isAuthenticated) return <><Toaster position="bottom-right" /><LoginPage /></>;

  const inWorkspace = page === 'workspace' && currentTopologyId;
  return (
    <div className="h-screen flex flex-col">
      <Toaster position="bottom-right" toastOptions={{ style: { background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155' } }} />
      {!inWorkspace && (
        <nav className="h-12 shrink-0 flex items-center gap-1 px-4 border-b border-surface-700 bg-surface-900">
          <div className="flex items-center gap-2 mr-6">
            <PhaneroLogo />
            <span className="text-sm font-semibold tracking-[0.15em] uppercase">Phanero <span className="text-emerald-400">Labs</span></span>
          </div>
          {NAV.filter(n => !n.admin || user?.is_admin).map(n => (
            <button key={n.page} onClick={() => setPage(n.page)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm ${page === n.page ? 'bg-surface-700 text-surface-100' : 'text-surface-400 hover:text-surface-100'}`}>
              <n.icon size={15} /> {n.label}
            </button>
          ))}
          <div className="flex-1" />
          <span className="text-xs text-surface-400 mr-2">{user?.display_name || user?.username}</span>
          <button onClick={logout} className="p-1.5 rounded hover:bg-surface-700 text-surface-400" title="Sign out"><LogOut size={15} /></button>
        </nav>
      )}
      <div className="flex-1 min-h-0">
        {inWorkspace ? <Workspace />
          : page === 'templates' ? <TemplatesPage />
          : page === 'images' ? <ImagesPage />
          : page === 'network' ? <NetworkPage />
          : page === 'users' ? <div className="max-w-4xl mx-auto p-6"><UserManagementPanel /></div>
          : <LabsHome />}
      </div>
    </div>
  );
}

import { useEffect } from 'react';
import { Toaster } from 'react-hot-toast';
import { LogOut, Network, Users } from 'lucide-react';
import { useAuthStore } from './stores/useAuthStore';
import { useLabStore, type Page } from './stores/useLabStore';
import LoginPage, { PhaneroLogo } from './components/LoginPage';
import LabsHome from './components/pages/LabsHome';
import ImagesPage from './components/pages/ImagesPage';
import TemplatesPage from './components/pages/TemplatesPage';
import NetworkPage from './components/pages/NetworkPage';
import Workspace from './components/workspace/Workspace';
import UserManagementPanel from './components/panels/UserManagementPanel';

// Primary destinations on the left (Curricula joins Labs with the curriculum
// work); configuration pages on the right.
const NAV_MAIN: { page: Page; label: string }[] = [{ page: 'labs', label: 'Labs' }];
const NAV_SETUP: { page: Page; label: string }[] = [{ page: 'templates', label: 'Templates' }, { page: 'images', label: 'Images' }];

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
        <nav className="h-[60px] shrink-0 flex items-center gap-1 px-5 bg-surface-900 border-b border-surface-700">
          <div className="flex items-center gap-2.5 mr-7">
            <PhaneroLogo />
            <span className="text-[15px] font-semibold tracking-[0.18em] uppercase text-white">Phanero <span className="text-emerald-400">Labs</span></span>
          </div>
          {NAV_MAIN.map(n => <NavBtn key={n.page} on={page === n.page} onClick={() => setPage(n.page)}>{n.label}</NavBtn>)}
          <div className="flex-1" />
          {NAV_SETUP.map(n => <NavBtn key={n.page} on={page === n.page} onClick={() => setPage(n.page)}>{n.label}</NavBtn>)}
          <div className="w-px h-6 bg-surface-700 mx-2" />
          <NavIcon on={page === 'network'} title="Lab network (NICs, management)" onClick={() => setPage('network')}><Network size={17} /></NavIcon>
          {user?.is_admin && <NavIcon on={page === 'users'} title="Users" onClick={() => setPage('users')}><Users size={17} /></NavIcon>}
          <div className="w-px h-6 bg-surface-700 mx-2" />
          <span className="text-sm text-slate-300 mr-1">{user?.display_name || user?.username}</span>
          <button onClick={logout} className="p-2 rounded-md hover:bg-white/10 text-slate-300" title="Sign out"><LogOut size={17} /></button>
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

function NavBtn({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`px-3.5 py-2 rounded-lg text-sm font-medium transition-colors ${on ? 'bg-white/10 text-white' : 'text-slate-300 hover:text-white'}`}>
      {children}
    </button>
  );
}

function NavIcon({ on, title, onClick, children }: { on: boolean; title: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} title={title}
      className={`p-2 rounded-lg transition-colors ${on ? 'bg-white/10 text-white' : 'text-slate-300 hover:text-white hover:bg-white/5'}`}>
      {children}
    </button>
  );
}

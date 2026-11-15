import { useEffect, useRef, useState } from 'react';
import { Toaster } from 'react-hot-toast';
import { BookOpen, ChevronDown, GraduationCap, Layers, LogOut, Network, Route, Siren, Users } from 'lucide-react';
import { useAuthStore } from './stores/useAuthStore';
import { useLabStore, type Page } from './stores/useLabStore';
import LoginPage, { PhaneroLogo } from './components/LoginPage';
import LabsHome from './components/pages/LabsHome';
import ImagesPage from './components/pages/ImagesPage';
import TemplatesPage from './components/pages/TemplatesPage';
import NetworkPage from './components/pages/NetworkPage';
import Workspace from './components/workspace/Workspace';
import LessonsPage from './components/pages/LessonsPage';
import LessonPage from './components/pages/LessonPage';
import LessonEditor from './components/pages/LessonEditor';
import ModulesPage from './components/pages/ModulesPage';
import ModulePage from './components/pages/ModulePage';
import ModuleEditor from './components/pages/ModuleEditor';
import TroubleshootingPage from './components/pages/TroubleshootingPage';
import ScenarioPage from './components/pages/ScenarioPage';
import UserManagementPanel from './components/panels/UserManagementPanel';

// Primary destinations on the left; configuration pages on the right.
const NAV_MAIN: { page: Page; label: string; also?: Page[] }[] = [{ page: 'labs', label: 'Labs' }];
// The learning add-on (PLABS_LEARNING_ENABLED): one menu, from the largest
// unit to the building block.
const LEARNING: { page: Page | null; label: string; hint: string; icon: React.ReactNode; also: Page[] }[] = [
  { page: null, label: 'Curricula', hint: 'Coming soon: modules in a path to a goal', icon: <Route size={16} />, also: [] },
  { page: 'modules', label: 'Modules', hint: 'Lessons grouped by topic or blueprint section', icon: <Layers size={16} />, also: ['module', 'moduleEdit'] },
  { page: 'lessons', label: 'Lessons', hint: 'Single labs with graded tasks', icon: <BookOpen size={16} />, also: ['lesson', 'lessonEdit'] },
  { page: 'troubleshooting', label: 'Troubleshooting', hint: 'Chaos breaks a working network — you find and fix it', icon: <Siren size={16} />, also: ['scenario'] },
];
const LEARNING_PAGES = LEARNING.flatMap(l => (l.page ? [l.page, ...l.also] : []));
const NAV_SETUP: { page: Page; label: string }[] = [{ page: 'templates', label: 'Templates' }, { page: 'images', label: 'Images' }];

export default function App() {
  const { isAuthenticated, isLoading, checkAuth, user, logout } = useAuthStore();
  const { page, setPage, bootstrap, currentTopologyId, features } = useLabStore();
  const learning = features.learning_enabled;
  // The add-on was switched off while one of its pages was open.
  const shown: Page = !learning && LEARNING_PAGES.includes(page) ? 'labs' : page;

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
          {NAV_MAIN.map(n => <NavBtn key={n.page} on={shown === n.page || !!n.also?.includes(shown)} onClick={() => setPage(n.page)}>{n.label}</NavBtn>)}
          {learning && <LearningMenu page={shown} onPick={setPage} />}
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
          : shown === 'templates' ? <TemplatesPage />
          : shown === 'images' ? <ImagesPage />
          : shown === 'network' ? <NetworkPage />
          : shown === 'users' ? <div className="max-w-4xl mx-auto p-6"><UserManagementPanel /></div>
          : shown === 'lessons' ? <LessonsPage />
          : shown === 'lesson' ? <LessonPage />
          : shown === 'lessonEdit' ? <LessonEditor />
          : shown === 'modules' ? <ModulesPage />
          : shown === 'module' ? <ModulePage />
          : shown === 'moduleEdit' ? <ModuleEditor />
          : shown === 'troubleshooting' ? <TroubleshootingPage />
          : shown === 'scenario' ? <ScenarioPage />
          : <LabsHome />}
      </div>
    </div>
  );
}

function LearningMenu({ page, onPick }: { page: Page; onPick: (p: Page) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('mousedown', close);
    window.addEventListener('keydown', esc);
    return () => { window.removeEventListener('mousedown', close); window.removeEventListener('keydown', esc); };
  }, [open]);
  const current = LEARNING.find(l => l.page && (l.page === page || l.also.includes(page)));
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(o => !o)} aria-haspopup="menu" aria-expanded={open}
        className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-colors ${current || open ? 'bg-white/10 text-white' : 'text-slate-300 hover:text-white'}`}>
        <GraduationCap size={16} /> Learning{current ? <span className="text-slate-400 font-normal">· {current.label}</span> : null}
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div role="menu" className="absolute left-0 top-full mt-1.5 w-72 z-50 rounded-xl bg-white border border-slate-200 shadow-xl p-1.5">
          {LEARNING.map(l => {
            const on = !!l.page && (l.page === page || l.also.includes(page));
            return (
              <button key={l.label} role="menuitem" disabled={!l.page}
                onClick={() => { if (l.page) { onPick(l.page); setOpen(false); } }}
                className={`w-full flex items-start gap-3 px-3 py-2.5 rounded-lg text-left ${!l.page ? 'opacity-55 cursor-default' : on ? 'bg-blue-50' : 'hover:bg-slate-50'}`}>
                <span className={`mt-0.5 ${on ? 'text-blue-700' : 'text-slate-500'}`}>{l.icon}</span>
                <span className="min-w-0">
                  <span className={`flex items-center gap-2 text-[14px] font-semibold ${on ? 'text-blue-800' : 'text-slate-900'}`}>
                    {l.label}{!l.page && <span className="px-1.5 py-px rounded bg-slate-100 text-[10.5px] font-medium text-slate-500">Soon</span>}
                  </span>
                  <span className="block text-[12px] text-slate-500 leading-snug">{l.hint}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
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

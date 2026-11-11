import { useEffect } from 'react';
import { Keyboard, X } from 'lucide-react';
import type { Shortcut } from '../utils/hotkeys';

export default function KeyboardShortcutsModal({ title, shortcuts, onClose }: {
  title: string;
  shortcuts: Shortcut[];
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === '?') { e.preventDefault(); e.stopImmediatePropagation(); onClose(); return; }
      // Keep the canvas hotkeys (pan, view switch, ...) from firing behind the modal.
      if (!e.ctrlKey && !e.metaKey && e.key !== 'Tab') e.stopImmediatePropagation();
    };
    // Capture phase so closing with "?" doesn't also reopen it via the canvas handler.
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-surface-900 border border-surface-700 rounded-lg shadow-2xl w-[460px] max-w-[calc(100vw-32px)] max-h-[80vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700">
          <div className="flex items-center gap-2 text-sm font-semibold text-surface-100">
            <Keyboard size={15} /> {title}
          </div>
          <button onClick={onClose} className="text-surface-400 hover:text-surface-100" title="Close (Esc)">
            <X size={15} />
          </button>
        </div>
        <table className="w-full text-[12px]">
          <tbody>
            {shortcuts.map(s => (
              <tr key={s.keys} className="border-b border-surface-800 last:border-0">
                <td className="px-4 py-2 text-surface-300">{s.action}</td>
                <td className="px-4 py-2 text-right whitespace-nowrap">
                  <kbd className="px-1.5 py-0.5 rounded bg-surface-800 border border-surface-600 text-surface-100 font-mono text-[11px]">{s.keys}</kbd>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { useEffect, useRef } from 'react';

export interface MenuItem {
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
  divider?: boolean;
}

export default function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('mousedown', close);
    window.addEventListener('keydown', esc);
    return () => { window.removeEventListener('mousedown', close); window.removeEventListener('keydown', esc); };
  }, [onClose]);
  return (
    <div ref={ref} style={{ left: x, top: y }}
      className="fixed z-50 min-w-[180px] py-1 rounded-lg bg-surface-800 border border-surface-600 shadow-2xl text-sm">
      {items.map((it, i) => it.divider
        ? <div key={i} className="my-1 border-t border-surface-700" />
        : (
          <button key={i} disabled={it.disabled}
            onClick={() => { it.onClick(); onClose(); }}
            className={`w-full text-left px-3 py-1.5 disabled:opacity-40 disabled:cursor-not-allowed
              ${it.danger ? 'text-red-300 hover:bg-red-900/40' : 'text-surface-100 hover:bg-surface-700'}`}>
            {it.label}
          </button>
        ))}
    </div>
  );
}

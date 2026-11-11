import { Fragment, useEffect, useRef, useState } from 'react';

/** Menus are laid out in three sections — view, then create/edit, then
 *  delete/remove — separated by dividers. */
export type MenuGroup = 'view' | 'edit' | 'delete';
const GROUP_ORDER: MenuGroup[] = ['view', 'edit', 'delete'];

export interface MenuItem {
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
  keepOpen?: boolean;
  /** Flyout submenu, opened on hover (or Enter / →). The item's own onClick
   *  is ignored when it has children. */
  children?: MenuItem[];
  /** Section; defaults to 'delete' for danger items, else 'view'. */
  group?: MenuGroup;
  /** Highlight as a primary action (theme blue). */
  accent?: boolean;
}

export const menuItemGroup = (it: MenuItem): MenuGroup => it.group ?? (it.danger ? 'delete' : 'view');

/** Read-only menus (protected topology, not in edit mode) keep only the
 *  view section. */
export const viewOnlyItems = (items: MenuItem[]): MenuItem[] => items.filter(it => menuItemGroup(it) === 'view');

interface ContextMenuProps {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
  /** Highlight the first item on open (keyboard-opened menus). */
  highlightFirst?: boolean;
}

const firstEnabled = (items: MenuItem[]) => items.find(it => !it.disabled)?.label ?? null;

export default function ContextMenu({ x, y, items: rawItems, onClose, highlightFirst }: ContextMenuProps) {
  // Stable-order the items into their sections.
  const items = GROUP_ORDER.flatMap(g => rawItems.filter(it => menuItemGroup(it) === g));
  const ref = useRef<HTMLDivElement>(null);
  // Highlight is tracked by label, not index, so an item list that changes
  // while open (e.g. a late-loading entry) can't shift Enter onto another item.
  const [hlLabel, setHlLabel] = useState<string | null>(highlightFirst ? firstEnabled(items) : null);
  // Open flyout (by parent label) and its highlighted child.
  const [subLabel, setSubLabel] = useState<string | null>(null);
  const [subHl, setSubHl] = useState<string | null>(null);
  const hl = hlLabel == null ? -1 : items.findIndex(it => it.label === hlLabel && !it.disabled);
  const setHl = (i: number) => setHlLabel(items[i]?.label ?? null);
  const subItems = items.find(it => it.label === subLabel)?.children ?? null;
  const activate = (item: MenuItem, fromKey = false) => {
    if (item.disabled) return;
    if (item.children) {
      setSubLabel(item.label);
      setSubHl(fromKey ? firstEnabled(item.children) : null);
      return;
    }
    item.onClick();
    if (!item.keepOpen) onClose();
  };

  // Keyboard: Up/Down move (skipping disabled items), Enter / → opens a
  // flyout or picks, ← / Esc closes the flyout (Esc again closes the menu).
  // Capture phase + stopPropagation so the canvas hotkeys (arrow-key pan,
  // etc.) don't also fire while the menu is open.
  const stateRef = useRef({ items, hl, activate, onClose, subItems, subHl });
  stateRef.current = { items, hl, activate, onClose, subItems, subHl };
  useEffect(() => {
    const move = (list: MenuItem[], cur: number, step: number) => {
      const n = list.length;
      let i = cur;
      for (let k = 0; k < n; k++) {
        i = i < 0 ? (step > 0 ? 0 : n - 1) : (i + step + n) % n;
        if (!list[i].disabled) return list[i].label;
      }
      return null;
    };
    const onKey = (e: KeyboardEvent) => {
      const { items, hl, activate, onClose, subItems, subHl } = stateRef.current;
      const handled = () => { e.preventDefault(); e.stopPropagation(); };
      if (subItems) {
        const si = subItems.findIndex(it => it.label === subHl && !it.disabled);
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          handled();
          setSubHl(move(subItems, si, e.key === 'ArrowDown' ? 1 : -1));
        } else if (e.key === 'Enter') {
          if (si < 0) return;
          handled();
          activate(subItems[si]);
        } else if (e.key === 'ArrowLeft' || e.key === 'Escape') {
          handled();
          setSubLabel(null);
        }
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        handled();
        const next = move(items, hl, e.key === 'ArrowDown' ? 1 : -1);
        if (next) setHlLabel(next);
      } else if (e.key === 'Enter' || (e.key === 'ArrowRight' && items[hl]?.children)) {
        if (hl < 0) return;
        handled();
        activate(items[hl], true);
      } else if (e.key === 'Escape') {
        handled();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as HTMLElement)) {
        onClose();
      }
    };
    // Capture phase: React Flow's pane stops mousedown propagation (d3-zoom),
    // so a bubbling listener never hears clicks on a canvas.
    document.addEventListener('mousedown', handler, true);
    return () => document.removeEventListener('mousedown', handler, true);
  }, [onClose]);

  // Open flyouts to the left when there's no room on the right.
  const flyLeft = x + 450 > window.innerWidth;  // menu (≤200) + gap + flyout (≤240)

  const itemClass = (item: MenuItem, highlighted: boolean) => `w-full text-left px-3 py-1.5 text-xs transition-colors ${
    item.disabled
      ? 'text-surface-600 cursor-not-allowed'
      : item.danger
        ? `text-red-400 hover:bg-red-900/20 ${highlighted ? 'bg-red-900/20' : ''}`
        : item.accent
        ? `text-primary-300 font-medium hover:bg-primary-900/40 ${highlighted ? 'bg-primary-900/40' : ''}`
        : `text-surface-200 hover:bg-surface-700 ${highlighted ? 'bg-surface-700' : ''}`
  }`;

  return (
    <div
      ref={ref}
      className="fixed z-[100] bg-surface-800 border border-surface-600 rounded-lg shadow-xl py-1 min-w-[120px] max-w-[200px] fade-in"
      style={{ left: x, top: y }}
    >
      {items.map((item, i) => (
        <Fragment key={i}>
        {i > 0 && menuItemGroup(item) !== menuItemGroup(items[i - 1]) && (
          <div className="border-t border-surface-600 my-1" />
        )}
        <div className="relative">
          <button
            className={`${itemClass(item, i === hl || item.label === subLabel)} ${item.children ? 'flex items-center justify-between gap-3' : ''}`}
            onMouseEnter={() => {
              if (item.disabled) return;
              setHl(i);
              setSubLabel(item.children ? item.label : null);
              setSubHl(null);
            }}
            onClick={() => activate(item)}
            disabled={item.disabled}
          >
            <span className="truncate">{item.label}</span>
            {item.children && <span className="text-surface-500">▸</span>}
          </button>
          {item.children && item.label === subLabel && (
            <div
              className={`absolute top-0 ${flyLeft ? 'right-full mr-1' : 'left-full ml-1'} bg-surface-800 border border-surface-600 rounded-lg shadow-xl py-1 min-w-[160px] max-w-[240px] max-h-[60vh] overflow-y-auto`}
              style={{ marginTop: -5 }}
            >
              {item.children.map((child, j) => (
                <button
                  key={j}
                  className={itemClass(child, child.label === subHl)}
                  onMouseEnter={() => { if (!child.disabled) setSubHl(child.label); }}
                  onClick={() => activate(child)}
                  disabled={child.disabled}
                  title={child.label}
                >
                  <span className="block truncate">{child.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        </Fragment>
      ))}
    </div>
  );
}

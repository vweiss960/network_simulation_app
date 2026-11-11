// Shared helpers for the canvas navigation hotkeys (logical + geographic).

// Inputs that don't take typed text: a focused checkbox (e.g. an overlay
// toggle) must not swallow the navigation hotkeys.
const NON_TEXT_INPUTS = new Set(['checkbox', 'button', 'submit', 'reset', 'color', 'file', 'image']);

/** True when the key event comes from a text field, so a hotkey must not fire. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const t = target as HTMLElement | null;
  if (!t) return false;
  if (t.tagName === 'INPUT') return !NON_TEXT_INPUTS.has((t as HTMLInputElement).type);
  return t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable;
}

/** True when Space/Enter on the target activates it (button, checkbox, link). */
export function isActivationTarget(target: EventTarget | null): boolean {
  const t = target as HTMLElement | null;
  return !!t?.closest?.('button, a[href], input, summary, [role="button"]');
}

/** Pan direction for arrow keys and w/a/s/d, or null for any other key. */
export function panDirection(key: string): [number, number] | null {
  switch (key) {
    case 'ArrowLeft': case 'a': case 'A': return [-1, 0];
    case 'ArrowRight': case 'd': case 'D': return [1, 0];
    case 'ArrowUp': case 'w': case 'W': return [0, -1];
    case 'ArrowDown': case 's': case 'S': return [0, 1];
    default: return null;
  }
}

/** Watches for two right-clicks in quick succession at about the same spot. */
export function makeRightDoubleClickDetector(windowMs = 400, slopPx = 8) {
  let last: { t: number; x: number; y: number } | null = null;
  return (e: MouseEvent): boolean => {
    const now = performance.now();
    const hit = !!last && now - last.t < windowMs &&
      Math.abs(e.clientX - last.x) < slopPx && Math.abs(e.clientY - last.y) < slopPx;
    last = hit ? null : { t: now, x: e.clientX, y: e.clientY };
    return hit;
  };
}

export type Shortcut = { keys: string; action: string };

/** Window event fired by the F ("full screen") hotkey; views close their own
 *  menus / popovers when they hear it. */
export const CLOSE_MENUS_EVENT = 'phanero:close-menus';

export const LOGICAL_SHORTCUTS: Shortcut[] = [
  { keys: '?', action: 'Show this list of keyboard shortcuts' },
  { keys: 'Arrow keys, or W A S D', action: 'Move the topology left, right, up and down' },
  { keys: '/', action: 'Search for a node' },
  { keys: 'Page Up / Page Down', action: 'Zoom in or out' },
  { keys: 'R', action: 'Reset the view' },
  { keys: 'F', action: 'Full screen: close the side panel and any open menus' },
  { keys: 'Double click (left)', action: 'Zoom toward cursor location' },
  { keys: 'Double click (right)', action: 'Zoom away from cursor location' },
  { keys: 'O', action: 'Switch between logical and geographic view' },
  { keys: 'E', action: 'Enter edit mode (protected topology, nothing selected)' },
  { keys: 'N', action: 'Open the right-click menu (↑↓ to choose, Enter to select)' },
  { keys: 'E (node selected)', action: 'Edit the selected node' },
  { keys: 'P (node selected)', action: "Open the node's patch panels, or create one" },
  { keys: 'L (node selected)', action: "Select the node's links (press again to cycle)" },
  { keys: 'E (link selected)', action: 'Edit the selected link' },
  { keys: 'Ctrl + C / Ctrl + V', action: 'Copy the selected node(s) / paste at the cursor (edit mode)' },
  { keys: 'Ctrl + D', action: 'Duplicate the selected node(s) (edit mode)' },
  { keys: 'Ctrl + L', action: 'Toggle link labels' },
  { keys: 'Ctrl + S', action: 'Toggle link width by speed' },
  { keys: 'Ctrl + U', action: 'Toggle link color by utilization' },
  { keys: 'Ctrl + Shift + S', action: 'Show / hide all satellite links' },
];

export const GEO_SHORTCUTS: Shortcut[] = [
  { keys: '?', action: 'Show this list of keyboard shortcuts' },
  { keys: 'Arrow keys, or W A S D', action: 'Move the globe (pan the view)' },
  { keys: '/', action: 'Search for a node' },
  { keys: 'L', action: 'Search for a location or address' },
  { keys: 'N', action: 'Return to north-facing view' },
  { keys: 'Page Up / Page Down', action: 'Zoom in or out' },
  { keys: 'R', action: 'Reset the view' },
  { keys: 'F', action: 'Full screen: close the side panel, layer groups and any open menus' },
  { keys: 'Shift + arrow keys', action: 'Rotate the globe (left/right) and tilt (up/down)' },
  { keys: 'Space', action: 'Stop the globe and camera from moving' },
  { keys: 'U', action: 'Return to top-down view' },
  { keys: 'Shift + Page Up / Page Down', action: 'Raise or lower camera altitude (fine steps)' },
  { keys: 'Double click (left)', action: 'Zoom toward cursor location' },
  { keys: 'Double click (right)', action: 'Zoom away from cursor location' },
  { keys: 'O', action: 'Switch between logical and geographic view' },
  { keys: 'E', action: 'Enter edit mode (protected topology), then toggle Edit positions on / off' },
  { keys: 'S (node selected)', action: 'Snap the node to the location search pin (Edit positions on)' },
  { keys: 'Ctrl + Shift + S', action: 'Show / hide all satellite links' },
];

import { useEffect, useState } from 'react';

/** Keeps a panel mounted while it animates out. `mounted`: render it;
 *  `visible`: apply its "in" styles. Opening mounts it in the "out" state and
 *  flips `visible` two frames later so the CSS transition runs. */
export function usePresence(show: boolean, ms = 280) {
  const [mounted, setMounted] = useState(show);
  const [visible, setVisible] = useState(show);
  useEffect(() => {
    if (show) {
      setMounted(true);
      let inner = 0;
      const outer = requestAnimationFrame(() => { inner = requestAnimationFrame(() => setVisible(true)); });
      return () => { cancelAnimationFrame(outer); cancelAnimationFrame(inner); };
    }
    setVisible(false);
    const t = setTimeout(() => setMounted(false), ms);
    return () => clearTimeout(t);
  }, [show, ms]);
  return { mounted, visible };
}

/** Remembers the last non-null value, so a closing panel keeps showing what
 *  it showed while it animates out. */
export function useLast<T>(value: T | null): T | null {
  const [last, setLast] = useState<T | null>(value);
  if (value !== null && value !== last) setLast(value);
  return value ?? last;
}

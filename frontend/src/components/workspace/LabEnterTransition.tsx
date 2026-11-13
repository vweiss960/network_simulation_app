/**
 * Entering a lab: the lab's real canvas is revealed through a window that
 * grows out of the clicked lab card (a clip-path on the canvas area, from
 * the card's rectangle to the full area). What grows *is* the canvas — its
 * own dots, its own zoom — so there's nothing to fade into afterwards.
 *
 * A frame (rounded corners, border, lifted shadow — the card's look at the
 * end of the Labs page exit) grows with the window and dissolves on the way,
 * so the card turns into the lab instead of blinking out. It's portalled to
 * <body> because the clip-path would clip it too.
 *
 * Web Animations API with explicit keyframes, so render timing can't skip it.
 * Skipped when the OS asks for reduced motion.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLabStore } from '../../stores/useLabStore';

const GROW_MS = 750;
// Ease-in-out with a soft start and a long settle — calmer than a snap-out.
const EASE = 'cubic-bezier(.45,.05,.25,1)';
const CARD_SHADOW = '0 14px 34px rgb(15 23 42 / 0.16)';

/** Render inside the canvas area (position: relative); it animates that area. */
export default function LabEnterTransition() {
  const { enterFrom, clearEnter } = useLabStore();
  // Its ref is set before this component's layout effect runs (a parent's isn't).
  const anchor = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const [showFrame, setShowFrame] = useState(false);

  useLayoutEffect(() => {
    if (!enterFrom) return;
    const area = anchor.current?.parentElement;
    if (!area || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) { clearEnter(); return; }
    setShowFrame(true);
  }, [enterFrom]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    const area = anchor.current?.parentElement;
    const fr = frame.current;
    if (!showFrame || !enterFrom || !area || !fr) return;
    const b = area.getBoundingClientRect();
    const px = (n: number) => `${Math.max(0, Math.round(n))}px`;
    const from = `inset(${px(enterFrom.y - b.top)} ${px(b.right - enterFrom.x - enterFrom.w)} `
      + `${px(b.bottom - enterFrom.y - enterFrom.h)} ${px(enterFrom.x - b.left)} round 12px)`;
    const opts: KeyframeAnimationOptions = { duration: GROW_MS, easing: EASE };
    const clip = area.animate([{ clipPath: from }, { clipPath: 'inset(0px 0px 0px 0px round 0px)' }], opts);
    const box = fr.animate([
      { left: `${enterFrom.x}px`, top: `${enterFrom.y}px`, width: `${enterFrom.w}px`, height: `${enterFrom.h}px`,
        borderRadius: '12px', borderColor: 'rgb(219 225 234 / 1)', boxShadow: CARD_SHADOW },
      { offset: 0.55, borderColor: 'rgb(219 225 234 / 0.35)', boxShadow: '0 10px 28px rgb(15 23 42 / 0.06)' },
      { left: `${b.left}px`, top: `${b.top}px`, width: `${b.width}px`, height: `${b.height}px`,
        borderRadius: '0px', borderColor: 'rgb(219 225 234 / 0)', boxShadow: '0 0 0 rgb(15 23 42 / 0)' },
    ], { ...opts, fill: 'forwards' });
    clip.finished.then(() => { setShowFrame(false); clearEnter(); }).catch(() => { /* cancelled */ });
    return () => { clip.cancel(); box.cancel(); };
  }, [showFrame]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div ref={anchor} className="hidden" aria-hidden />
      {showFrame && enterFrom && createPortal(
        <div ref={frame} aria-hidden className="fixed z-[70] pointer-events-none border"
          style={{ left: enterFrom.x, top: enterFrom.y, width: enterFrom.w, height: enterFrom.h,
                   borderRadius: 12, borderColor: 'rgb(219 225 234)', boxShadow: CARD_SHADOW }} />,
        document.body)}
    </>
  );
}

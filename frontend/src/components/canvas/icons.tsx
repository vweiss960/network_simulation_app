/**
 * Classic network-diagram symbols (router circle with in/out arrows, switch
 * box with crossing arrows, monitor, server, brick-wall firewall, cloud).
 * Drawn in currentColor so they work on the light canvas and the dark
 * palette alike. `fill` paints the body (canvas); `dashed` outlines a device
 * that isn't deployed yet.
 */
import type { ReactNode } from 'react';

export interface SymbolProps {
  size?: number;
  className?: string;
  fill?: string;          // body fill (default: none)
  dashed?: boolean;
}

type Draw = (p: { fill: string; dash?: string }) => ReactNode;

/** Arrow from (x1,y1) to (x2,y2) with a head at the end. */
function arrow(x1: number, y1: number, x2: number, y2: number, head = 5): ReactNode {
  const a = Math.atan2(y2 - y1, x2 - x1);
  const h1 = [x2 - head * Math.cos(a - Math.PI / 4), y2 - head * Math.sin(a - Math.PI / 4)];
  const h2 = [x2 - head * Math.cos(a + Math.PI / 4), y2 - head * Math.sin(a + Math.PI / 4)];
  return (
    <g key={`${x1}${y1}${x2}${y2}`}>
      <line x1={x1} y1={y1} x2={x2} y2={y2} />
      <polyline points={`${h1[0]},${h1[1]} ${x2},${y2} ${h2[0]},${h2[1]}`} />
    </g>
  );
}

const DRAW: Record<string, Draw> = {
  router: ({ fill, dash }) => (
    <>
      <circle cx="24" cy="24" r="20" fill={fill} strokeDasharray={dash} />
      {arrow(12.5, 12.5, 20.5, 20.5)}
      {arrow(35.5, 35.5, 27.5, 27.5)}
      {arrow(27.5, 20.5, 35.5, 12.5)}
      {arrow(20.5, 27.5, 12.5, 35.5)}
    </>
  ),
  switch: ({ fill, dash }) => (
    <>
      <rect x="4" y="8" width="40" height="32" rx="5" fill={fill} strokeDasharray={dash} />
      {arrow(11, 19, 26, 19)}
      {arrow(22, 19, 37, 19)}
      {arrow(37, 29, 22, 29)}
      {arrow(26, 29, 11, 29)}
    </>
  ),
  host: ({ fill, dash }) => (
    <>
      <rect x="6" y="8" width="36" height="25" rx="2.5" fill={fill} strokeDasharray={dash} />
      <rect x="10" y="12" width="28" height="17" rx="1" fill="currentColor" fillOpacity="0.08" stroke="none" />
      <line x1="24" y1="33" x2="24" y2="39" />
      <line x1="16" y1="40" x2="32" y2="40" />
    </>
  ),
  workstation: ({ fill, dash }) => (
    <>
      <rect x="6" y="8" width="36" height="25" rx="2.5" fill={fill} strokeDasharray={dash} />
      <polyline points="13,15 18,20 13,25" />
      <line x1="21" y1="26" x2="29" y2="26" />
      <line x1="24" y1="33" x2="24" y2="39" />
      <line x1="16" y1="40" x2="32" y2="40" />
    </>
  ),
  server: ({ fill, dash }) => (
    <>
      <rect x="12" y="4" width="24" height="40" rx="2.5" fill={fill} strokeDasharray={dash} />
      <line x1="12" y1="14" x2="36" y2="14" />
      <line x1="12" y1="24" x2="36" y2="24" />
      <circle cx="30" cy="9" r="1" fill="currentColor" />
      <circle cx="30" cy="19" r="1" fill="currentColor" />
      <line x1="17" y1="34" x2="31" y2="34" />
    </>
  ),
  firewall: ({ fill, dash }) => (
    <>
      <rect x="5" y="9" width="38" height="30" rx="2" fill={fill} strokeDasharray={dash} />
      <line x1="5" y1="19" x2="43" y2="19" />
      <line x1="5" y1="29" x2="43" y2="29" />
      <line x1="17" y1="9" x2="17" y2="19" /><line x1="31" y1="9" x2="31" y2="19" />
      <line x1="11" y1="19" x2="11" y2="29" /><line x1="24" y1="19" x2="24" y2="29" /><line x1="37" y1="19" x2="37" y2="29" />
      <line x1="17" y1="29" x2="17" y2="39" /><line x1="31" y1="29" x2="31" y2="39" />
    </>
  ),
  cloud: ({ fill, dash }) => (
    <path fill={fill} strokeDasharray={dash}
      d="M14 37h21a8 8 0 0 0 1.2-15.9A11 11 0 0 0 15.3 18.4 9.5 9.5 0 0 0 14 37z" />
  ),
};

/** Outline shape of each symbol, for the selection ring. */
export const SYMBOL_SHAPE: Record<string, 'circle' | 'rounded'> = { router: 'circle' };

function makeSymbol(name: string) {
  const draw = DRAW[name];
  return function NetworkSymbol({ size = 24, className = '', fill = 'none', dashed = false }: SymbolProps) {
    return (
      <svg width={size} height={size} viewBox="0 0 48 48" className={className} fill="none" stroke="currentColor"
        strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {draw({ fill, dash: dashed ? '4 3.5' : undefined })}
      </svg>
    );
  };
}

export type SymbolComponent = ReturnType<typeof makeSymbol>;

export const DEVICE_ICONS: Record<string, SymbolComponent> = Object.fromEntries(
  Object.keys(DRAW).map(k => [k, makeSymbol(k)]),
);

export const deviceIcon = (icon: string): SymbolComponent => DEVICE_ICONS[icon] ?? DEVICE_ICONS.router;

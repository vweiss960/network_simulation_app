import type { Device } from '../types';

/** A traffic generator node: a Linux container running plabs-traffic. */
export const isTrafficNode = (n?: Pick<Device, 'kind' | 'image_ref'> | null) =>
  !!n && n.kind === 'linux' && (n.image_ref || '').includes('plabs/traffic');

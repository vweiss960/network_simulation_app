import { Laptop, Monitor, Network, Router, Server, Shield, type LucideIcon } from 'lucide-react';

export const DEVICE_ICONS: Record<string, LucideIcon> = {
  router: Router, switch: Network, host: Monitor, workstation: Laptop, firewall: Shield, server: Server,
};

export const deviceIcon = (icon: string): LucideIcon => DEVICE_ICONS[icon] ?? Router;

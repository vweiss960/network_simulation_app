import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server: proxies /api (incl. console WebSockets) to the backend.
// PLABS_BACKEND overrides the target (default: native backend on 127.0.0.1:8100).
const backend = process.env.PLABS_BACKEND || 'http://127.0.0.1:8100';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5174,
    allowedHosts: true,
    proxy: { '/api': { target: backend, changeOrigin: true, ws: true } },
  },
});

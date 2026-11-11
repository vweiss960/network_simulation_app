import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: ['u-dev-docker-01.mesh1labs.com', 'phanero.mesh1labs.com'],
    hmr: {
      // HMR goes through nginx on the client's HTTPS port
      clientPort: 3000,
      protocol: 'wss',
    },
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
    },
  },
});

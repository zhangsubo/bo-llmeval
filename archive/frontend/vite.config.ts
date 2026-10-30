import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8081,
    proxy: {
      '/api': {
        target: process.env.EVALPLAT_API_TARGET || 'http://localhost:18002',
        changeOrigin: true,
      },
    },
  },
});

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served same-origin by the STAC API at "/" in production. In dev, proxy the
// API prefix to a locally-running stac-api (docker compose: localhost:8081).
export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    port: 5173,
    proxy: {
      '/api/v1': 'http://localhost:8081',
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})

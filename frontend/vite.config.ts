import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies to the FastAPI backend so the browser sees one origin.
// No path rewrite: the backend owns /api/* and /ws/* as written in PLAN.md §10.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})

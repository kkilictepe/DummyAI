import { defineConfig } from 'vitest/config'
import type { ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'

// Backend target for the dev proxy only (Node-side, never shipped to the browser).
const DEV_BACKEND_URL = process.env.DEV_BACKEND_URL ?? 'http://localhost:8000'

// SSE-safe proxy: force identity encoding so a gzip layer can't buffer the event
// stream, and never set timeout/proxyTimeout (http-proxy pipes chunks unbuffered
// by default). Symptom of a buffering regression: all events arrive at once at the end.
const sseSafe: ProxyOptions = {
  target: DEV_BACKEND_URL,
  changeOrigin: true,
  configure: (proxy) => {
    proxy.on('proxyReq', (proxyReq) => {
      proxyReq.setHeader('accept-encoding', 'identity')
    })
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // /agui/<flow> -> backend POST /<flow>  (AG-UI SSE). The prefix keeps SPA route
      // GETs (e.g. a browser refresh on /copilot) out of the proxy.
      '/agui': {
        ...sseSafe,
        rewrite: (path) => path.replace(/^\/agui/, ''),
      },
      // /api/<path> -> backend /<path>  (REST, e.g. /api/health -> /health).
      '/api': {
        ...sseSafe,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
    css: false,
  },
})

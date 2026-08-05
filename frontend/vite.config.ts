import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const MASTER_API_TARGET = 'http://127.0.0.1:8787'
const READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

function isMasterGatewayTarget(target: string): boolean {
  try {
    const url = new URL(target)
    const host = url.hostname.toLowerCase()
    return url.protocol === 'http:'
      && ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(host)
      && (url.port || '80') === '8787'
  } catch {
    return false
  }
}

function readonlyMasterApi(target: string): Plugin {
  return {
    name: 'readonly-master-api-preview',
    enforce: 'pre',
    configureServer(server) {
      if (!isMasterGatewayTarget(target)) return
      server.middlewares.use((request, response, next) => {
        const pathname = request.url?.split('?', 1)[0] || ''
        if (pathname === '/api' || pathname.startsWith('/api/')) {
          const method = request.method?.toUpperCase() || 'GET'
          if (!READ_METHODS.has(method)) {
            response.statusCode = 403
            response.setHeader('Content-Type', 'application/json')
            response.end(JSON.stringify({
              reason_code: 'preview_readonly_master_api',
              message: 'Worktree preview blocks API writes to the master gateway.',
            }))
            return
          }
        }
        next()
      })
    },
  }
}

export default defineConfig(() => {
  const apiTarget = process.env.VITE_API_TARGET?.trim() || MASTER_API_TARGET
  // The updater builds into a sibling staging directory, then activates that
  // complete output only after Python smoke checks pass.
  const outputDir = process.env.VITE_BUILD_OUT_DIR?.trim()
    || path.resolve(__dirname, '../aletheia/dashboard_static')
  return {
    plugins: [react(), tailwindcss(), readonlyMasterApi(apiTarget)],
    build: {
      outDir: outputDir,
      emptyOutDir: true,
      manifest: true,
      rollupOptions: {
        output: {
          manualChunks: {
            recharts: ['recharts'],
          },
        },
      },
    },
    server: {
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
      },
    },
  }
})

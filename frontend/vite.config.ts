import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, '../aletheia/dashboard_static'),
    // Keep the previous hashed assets while building. Emptying this directory
    // creates a short window where a gateway restart can mount no dashboard and
    // serve API-only 404s at "/".
    emptyOutDir: false,
    manifest: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8787',
    },
  },
})

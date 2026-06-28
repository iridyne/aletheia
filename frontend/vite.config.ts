import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [
    react(),
  ],
  build: {
    outDir: path.resolve(__dirname, '../aletheia/dashboard_static'),
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      output: {
        // Fixed filenames — avoid content-hash renames that cause git merge conflicts
        // when two branches each build independently. Cache busting via ETag (304).
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
        manualChunks: {
          recharts: ['recharts'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8787',
    },
  },
})

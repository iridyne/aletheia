import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // Vitest stubs CSS by default, which makes `import CSS from './x.css?raw'`
    // resolve to an empty string — a token-coverage test would then silently
    // pass on no input. Processing CSS keeps ?raw returning real file contents.
    css: true,
  },
})

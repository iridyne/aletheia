import { defineConfig, devices } from '@playwright/test'

// Point at a running instance: dev server or the Python backend serving dashboard_static.
// Override: PERF_BASE_URL=http://localhost:8787 npx playwright test --config tests/perf/playwright.config.ts
const BASE_URL = process.env.PERF_BASE_URL ?? 'http://localhost:5173'

export default defineConfig({
  testDir: './',
  timeout: 20_000,
  retries: 1,
  use: {
    baseURL: BASE_URL,
    headless: true,
    // Capture a screenshot on failure for debugging blank-screen issues.
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})

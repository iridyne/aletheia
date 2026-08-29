/**
 * Browser-level load test.
 *
 * For each page: after navigation, within INDICATOR_DEADLINE_MS the main area
 * must either show real content OR a visible loading indicator. A blank body
 * at that checkpoint means the page has no loading state and will look frozen.
 *
 * Setup (one-time):
 *   cd frontend
 *   npm install --save-dev @playwright/test
 *   npx playwright install chromium
 *
 * Run against dev server (localhost:5173):
 *   npx playwright test --config tests/perf/playwright.config.ts
 *
 * Run against production backend (localhost:8787):
 *   PERF_BASE_URL=http://localhost:8787 npx playwright test --config tests/perf/playwright.config.ts
 */
import { test, expect } from '@playwright/test'

// A page must show content OR a loading indicator within this many ms.
const INDICATOR_DEADLINE_MS = 150

// Soft budget: pages that exceed this get a console warning but don't fail the test.
const SLOW_PAGE_WARN_MS = 1_000

const PAGES = [
  { path: '/',                 name: 'Overview' },
  { path: '/agent-activity',   name: 'AgentActivity' },
  { path: '/learning',         name: 'Learning' },
  { path: '/blotter',          name: 'Blotter' },
  { path: '/trades',           name: 'TradesPositions' },
  { path: '/charts',           name: 'Charts' },
  { path: '/settings',         name: 'Settings' },
  { path: '/risk',             name: 'RiskControls' },
  { path: '/news',             name: 'NewsIntel' },
  { path: '/command-center',   name: 'CommandCenter' },
  { path: '/serenity-lens',    name: 'SerenityLens' },
  { path: '/prediction-markets', name: 'PredictionLens' },
  { path: '/universe',         name: 'Universe' },
  { path: '/usage',            name: 'Usage' },
]

for (const page of PAGES) {
  test(`${page.name} shows content or loading indicator within ${INDICATOR_DEADLINE_MS}ms`, async ({ page: pw }) => {
    const t0 = Date.now()
    await pw.goto(page.path, { waitUntil: 'domcontentloaded' })
    await pw.waitForTimeout(INDICATOR_DEADLINE_MS)

    const feedback = await pw.evaluate(() => {
      const main = document.querySelector('.main')
      if (!main) return { hasContent: false, hasIndicator: false, textLen: 0 }
      const text = (main.textContent ?? '').trim()
      const hasIndicator =
        main.querySelector('[role="status"]') !== null ||
        main.querySelector('.route-skeleton') !== null ||
        /loading/i.test(text)
      const hasContent = text.length > 40 && !/^\s*$/.test(text)
      return { hasContent, hasIndicator, textLen: text.length }
    })

    expect(
      feedback.hasContent || feedback.hasIndicator,
      `${page.name}: blank body ${INDICATOR_DEADLINE_MS}ms after navigation ` +
      `(no content, no loading indicator). ` +
      `Add isPending/isLoading guards or a skeleton state.`
    ).toBe(true)

    // Wait for the page to settle, then record total time.
    await pw.waitForLoadState('networkidle').catch(() => {})
    const elapsed = Date.now() - t0
    test.info().annotations.push({ type: 'load-time', description: `${elapsed}ms` })

    if (elapsed > SLOW_PAGE_WARN_MS) {
      console.warn(`⚠  ${page.name}: ${elapsed}ms total (budget: ${SLOW_PAGE_WARN_MS}ms)`)
    }
  })
}

import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'

import { TradeInsightsScope } from './TradeInsightsPanel'

it('labels complete and truncated Trade Insights scopes honestly', () => {
  const complete = renderToStaticMarkup(<TradeInsightsScope data={{
    since: '2026-06-22', source_raw_count: 9_999, loaded_raw_count: 9_999,
    loaded_count: 9_000, omitted_count: 0, cohort_complete: true,
    loaded_from: '2026-06-22T00:00:00Z', loaded_through: '2026-09-03T00:00:00Z',
    eligible_outcome_count: 9_000, excluded_outcome_count: 999,
  }} />)
  const truncated = renderToStaticMarkup(<TradeInsightsScope data={{
    since: '2026-06-22', source_raw_count: 10_001, loaded_raw_count: 10_000,
    loaded_count: 9_999, omitted_count: 1, cohort_complete: false,
    loaded_from: '2026-06-23T00:00:00Z', loaded_through: '2026-09-03T00:00:00Z',
    eligible_outcome_count: 10_000, excluded_outcome_count: 1,
  }} />)

  expect(complete).toContain('Since 2026-06-22')
  expect(complete).not.toContain('omitted')
  expect(truncated).toContain('role="status"')
  expect(truncated).toContain('latest 10,000 raw close events')
  expect(truncated).toContain('1 earlier event omitted')
  expect(truncated).toContain('2026-06-23T00:00:00Z')
  expect(truncated).not.toContain('Since 2026-06-22')
})

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { UsageInsightsResponse } from '../api/client'
import Usage from './Usage'

const insights: UsageInsightsResponse = {
  window: { days: 7, started_at: '2026-08-03T00:00:00Z', ended_at: '2026-08-10T00:00:00Z', as_of: '2026-08-10T00:00:00Z', timezone: 'UTC' },
  coverage: {
    status: 'partial', reason: 'Legacy rows have no duration.',
    axes: [
      { id: 'pricing', label: 'Pricing', status: 'available', coverage_pct: 80, reason: null },
      { id: 'tool_timing', label: 'Tool timing', status: 'not_available', coverage_pct: null, reason: 'Legacy rows are unknown.' },
    ],
  },
  token_mix: { status: 'available', reason: null, input_tokens: 100, output_tokens: 50, reasoning_tokens: 0, cached_tokens: 0, event_count: 1, field_coverage_pct: { input_tokens: 100, output_tokens: 100, reasoning_tokens: 100, cached_tokens: 100 } },
  pricing: { status: 'available', reason: null, known_cost_usd: 0.1, estimated_cost_usd: 0, unknown_event_count: 0, pricing_coverage_pct: 100, gaps: [] },
  attribution: { status: 'not_available', reason: 'No runs.', rows: [] },
  tools: { status: 'not_available', reason: 'No tools.', rows: [], timing_coverage_pct: null, pricing_coverage_pct: null },
  reliability: {
    status: 'not_available', reason: 'No sessions.', logical: {}, physical: {},
    current_model_cooldown: {
      status: 'available', reason: 'Current in-process state; it is not a historical cooldown ledger.',
      current: true, fact_scope: 'current_in_process', as_of: '2026-08-10T00:00:00Z',
      rows: [{ provider: 'test', model: 'model', cooling_down: true }], cooling_down_count: 1,
    },
  },
  budgets: { status: 'not_available', reason: 'No budgets.', rows: [] },
  trends: {
    status: 'not_available', reason: 'No trend.', daily: [], baseline: null,
    trace_coverage: { status: 'not_available', reason: 'No trace.', observed_zero_day_count: 0 },
  },
  research_yield: { status: 'not_available', reason: 'No artifacts.', artifacts: [], lineage: [] },
}

describe('Usage', () => {
  it('renders coverage text alongside the radar and preserves unavailable data', () => {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } })
    client.setQueryData(['usage-insights', 7], insights)

    const html = renderToStaticMarkup(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <Usage />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(html).toContain('Coverage radar')
    expect(html).toContain('Coverage details (text equivalent)')
    expect(html).toContain('Legacy rows are unknown.')
    expect(html).toContain('N/A')
    expect(html).toContain('Research yield (last 7 days)')
    expect(html).toContain('Current model health')
    expect(html).toContain('this is not last 7 days data')
    expect(html).toContain('Trend trace coverage')
  })
})

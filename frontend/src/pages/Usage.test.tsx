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
  token_mix: { status: 'available', reason: null, input_tokens: 100, output_tokens: 50, total_tokens: 150, reasoning_tokens: 0, cached_tokens: 0, event_count: 1, field_coverage_pct: { input_tokens: 100, output_tokens: 100, reasoning_tokens: 100, cached_tokens: 100 } },
  pricing: { status: 'available', reason: null, known_cost_usd: 0.1, estimated_cost_usd: 0, unknown_event_count: 0, pricing_coverage_pct: 100, gaps: [] },
  attribution: { status: 'not_available', reason: 'No runs.', rows: [] },
  tools: { status: 'not_available', reason: 'No tools.', rows: [], timing_coverage_pct: null, pricing_coverage_pct: null },
  reliability: {
    status: 'partial', reason: 'One attempt failed.', logical: {}, physical: { error_categories: [{ error_type: 'timeout', event_count: 2 }] },
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
  it('renders the shared skeleton while the initial Usage query is pending', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const html = renderToStaticMarkup(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <Usage />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(html).toContain('usage-section skeleton')
    expect(html).toContain('aria-label="Loading consumption and spend"')
    expect(html).toContain('aria-label="Loading usage sections"')
    expect(html).toContain('aria-busy="true"')
    expect(html).not.toContain('Coverage radar')
  })

  it('renders the summary while later progressive groups remain pending', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
    client.setQueryData(['usage-insights', 1, 'summary'], insights)

    const html = renderToStaticMarkup(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <Usage />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(html).toContain('Consumption &amp; spend · last 7 days')
    expect(html).toContain('Token composition')
    expect(html).toContain('Cost provenance')
    expect(html).toContain('Reliability · last 7 days')
    expect(html).toContain('Current model cooldown')
    expect(html).toContain('test')
    expect(html).toContain('timeout: 2')
    expect(html).toContain('usage-section skeleton')
    expect(html).not.toContain('Research yield · last 7 days')
  })

  it('renders consumption first and preserves honest coverage states', () => {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } })
    client.setQueryData(['usage-insights', 1, 'summary'], insights)
    client.setQueryData(['usage-insights', 1, 'operations'], insights)
    client.setQueryData(['usage-insights', 1, 'analysis'], insights)

    const html = renderToStaticMarkup(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <Usage />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(html.indexOf('Consumption &amp; spend')).toBeLessThan(html.indexOf('Data coverage &amp; gaps'))
    expect(html).toContain('Total tokens')
    expect(html).toContain('Token composition')
    expect(html).toContain('Legacy rows are unknown.')
    expect(html).toContain('N/A')
    expect(html).toContain('Research yield · last 7 days')
    expect(html).toContain('aria-pressed="true">1d')
    expect(html).toContain('Reliability · last 7 days')
    expect(html).toContain('Budgets · last 7 days')
    expect(html.indexOf('Reliability · last 7 days')).toBeLessThan(html.indexOf('Budgets · last 7 days'))
    expect(html).not.toContain('Coverage radar')
  })

  it('paginates every insight table instead of rendering every row into the page', () => {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } })
    const manyArtifacts = Array.from({ length: 100 }, (_, index) => ({
      run_id: `run-${index}`,
      agent_name: 'paper_trader',
      artifact_type: 'filter_decision',
      artifact_count: 1,
    }))
    client.setQueryData(['usage-insights', 1, 'summary'], insights)
    client.setQueryData(['usage-insights', 1, 'operations'], insights)
    client.setQueryData(['usage-insights', 1, 'analysis'], {
      ...insights,
      research_yield: { ...insights.research_yield, artifacts: manyArtifacts },
    })

    const html = renderToStaticMarkup(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <Usage />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(html).toContain('Produced artifacts (100)')
    expect(html).toContain('run-24')
    expect(html).not.toContain('run-25')
    expect(html).toContain('1 / 4')
    expect(html).toContain('usage-table-card')
  })
})

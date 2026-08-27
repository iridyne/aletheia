import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { UsageOperationsResponse, UsageSummaryResponse } from '../api/client'
import Usage from './Usage'

const summary: UsageSummaryResponse = {
  contract_version: 2,
  window: { days: 1, started_at: '2026-08-26T00:00:00Z', ended_at: '2026-08-27T00:00:00Z', updated_at: '2026-08-27T00:00:00Z', timezone: 'UTC', granularity: 'hour' },
  overview: { cost_usd: null, total_tokens: 1_590_000, llm_calls: 10, success_rate_pct: 90, avg_cost_per_call_usd: null },
  comparison: { complete: false },
  token_composition: { input_tokens: 1_000_000, output_tokens: 590_000, cache_read_tokens: 0 },
  trend: [{ started_at: '2026-08-26T00:00:00Z', cost_usd: null, input_tokens: 100, output_tokens: 50, llm_calls: 1 }],
}

const operations: UsageOperationsResponse = {
  contract_version: 2,
  window: summary.window,
  breakdowns: {
    agents: [{ agent: 'Unattributed', runs: 0, calls: 10, success_rate_pct: 90, tokens: 1_590_000, cost_usd: null, p95_latency_ms: 1234 }],
    models: [], providers: [], tools: [],
  },
  reliability: { attempts: 12, errors: 1, retries: 2, fallbacks: 1, p50_latency_ms: 500, p95_latency_ms: 1234, error_categories: [{ category: 'timeout', count: 1 }] },
  active_cooldowns: [],
  budgets: [],
}

function render(summaryData?: UsageSummaryResponse, operationsData?: UsageOperationsResponse) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  if (summaryData) client.setQueryData(['usage-insights', 1, 'summary'], summaryData)
  if (operationsData) client.setQueryData(['usage-insights', 1, 'operations'], operationsData)
  return renderToStaticMarkup(<MemoryRouter><QueryClientProvider client={client}><Usage /></QueryClientProvider></MemoryRouter>)
}

describe('Usage v2', () => {
  it('uses stable skeletons while the selected window loads', () => {
    const html = render()
    expect(html).toContain('Loading usage overview')
    expect(html).toContain('Loading breakdown and reliability')
    expect(html).toContain('aria-busy="true"')
  })

  it('renders the five fixed KPIs and honest unavailable values', () => {
    const html = render(summary, operations)
    for (const label of ['Cost', 'Total tokens', 'LLM calls', 'Success rate', 'Avg cost / call']) expect(html).toContain(label)
    expect(html).toContain('title="1,590,000"')
    expect(html).toContain('N/A')
  })

  it('shows observed zero token subtypes and hides never-provided subtypes', () => {
    const html = render(summary, operations)
    expect(html).toContain('Cache read')
    expect(html).toContain('>0</strong>')
    expect(html).not.toContain('Cache write')
    expect(html).not.toContain('Reasoning</span>')
  })

  it('uses fixed breakdown columns and keeps missing attribution reconcilable', () => {
    const html = render(summary, operations)
    expect(html).toContain('Group by')
    expect(html).toContain('Unattributed')
    expect(html).toContain('P95 latency')
    expect(html).toContain('aria-pressed="true">Agent')
  })

  it('renders exception-only reliability and omits empty budget and cooldown sections', () => {
    const html = render(summary, operations)
    expect(html).toContain('Errors by category')
    expect(html).toContain('timeout')
    expect(html).not.toContain('Active cooldowns')
    expect(html).not.toContain('<h2>Budget</h2>')
  })

  it('removes engineering-only Usage language and maintenance actions', () => {
    const html = render(summary, operations)
    for (const removed of ['Estimated', 'Known', 'Unknown events', 'Partial', 'Data coverage', 'Pricing gaps', 'Research yield', 'Refresh pricing']) expect(html).not.toContain(removed)
  })
})

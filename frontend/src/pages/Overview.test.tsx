// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Overview, { isGoLiveScorecard, operatorMutationError, SetupNotice } from './Overview'
import type { CollectorHealthReport, MarketDataCoverageReport } from '../types'

vi.mock('../components/PnLZone', () => ({ default: () => null }))
vi.mock('../components/AgentFeed', () => ({ default: () => null }))

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('Overview', () => {
  it.each([true, false, null, undefined])('preserves unknown model setup status: %s', required => {
    const markup = renderToStaticMarkup(<MemoryRouter><SetupNotice required={required} /></MemoryRouter>)
    expect(markup.includes('NO MODEL CONFIGURED')).toBe(required === true)
    expect(markup.includes('MODEL SETUP STATUS UNAVAILABLE')).toBe(required === null)
  })
  it('keeps operator rejection identity and rejects invalid qualification evidence', () => {
    expect(operatorMutationError('Pause gateway failed', new Error('backend 503'))).toBe('Pause gateway failed: backend 503')
    expect(operatorMutationError('Resume gateway failed', null)).toBe('Resume gateway failed: request failed')
    expect(isGoLiveScorecard({ traffic_light: 'ready' })).toBe(false)
  })

  it('distinguishes collector outcomes and expands current coverage separately from inventory by keyboard', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
    const health: CollectorHealthReport = {
      status: 'attention', domain_count: 6, recorded_count: 5, completed_count: 3,
      degraded_count: 0, transient_problem_count: 0, infrastructure_incident: null,
      domains: ['success', 'partial', 'empty', 'not_configured', 'disabled', 'never_run'].map(status => ({
        domain: status === 'empty' ? 'derivatives_history' : status, last_status: status,
        display_status: status, cadence_sec: 600, age_sec: 1, overdue: false, degraded: false,
        attributed_cause: null, display_severity: status === 'success' ? 'ok' : 'muted',
        last_error_code: status === 'empty' ? 'no_derivatives_history' : null,
      })),
    }
    const coverage: MarketDataCoverageReport = {
      status: 'attention', generated_at: '2026-09-06T00:00:00Z', domain_count: 1,
      fresh_count: 0, problem_count: 1, critical_problem_count: 1,
      domains: [{
        domain: 'funding', label: 'Funding', state: 'empty', critical: true,
        scope: 'current_targets', target_count: 2, missing_target_count: 2,
        row_count: 0, symbol_count: 0, source_counts: {}, newest_observed_at: null,
        age_sec: null, max_age_sec: 600, fresh_row_count: 0, stale_row_count: 0,
        unavailable_row_count: 0, freshness_state_counts: {},
        inventory: { state: 'invalid', row_count: 45, symbol_count: 3, source_counts: {},
          newest_observed_at: null, age_sec: null, fresh_row_count: 0, stale_row_count: 0,
          unavailable_row_count: 45, freshness_state_counts: { invalid: 45 } },
      }],
    }
    client.setQueryData(['ui-shell-bootstrap'], { connectivity: { state: 'unknown', reason: 'No recent probe' }, exchange: { status: 'not_configured', data_exchange: 'okx', execution_exchange: null } })
    client.setQueryData(['ui-overview-bootstrap'], {})
    client.setQueryData(['collector-health'], health)
    client.setQueryData(['market-data-coverage'], coverage)
    const host = document.createElement('div')
    document.body.append(host)
    const root = createRoot(host)
    try {
      await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><Overview /></QueryClientProvider></MemoryRouter>))
      const exchange = [...host.querySelectorAll<HTMLElement>('[role="button"]')].find(row => row.textContent?.startsWith('Exchange'))!
      expect(exchange.textContent).toContain('Not configured')
      expect(exchange.textContent).not.toContain('okx')
      const collector = [...host.querySelectorAll<HTMLElement>('[role="button"]')].find(row => row.textContent?.startsWith('Collectors'))!
      expect(collector.textContent).toContain('Attention')
      expect(collector.getAttribute('aria-expanded')).toBe('false')
      await act(async () => collector.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
      expect(collector.getAttribute('aria-expanded')).toBe('true')
      expect(host.textContent).toContain('Collector diagnostics')
      const coverageRow = [...host.querySelectorAll<HTMLElement>('[role="button"]')].find(row => row.textContent?.startsWith('Data coverage'))!
      await act(async () => coverageRow.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true })))
      expect(coverageRow.getAttribute('aria-expanded')).toBe('true')
      expect(collector.getAttribute('aria-expanded')).toBe('false')
      expect(host.textContent).toContain('2 current targets · 2 missing targets')
      expect(host.textContent).toContain('Historical inventory (all stored rows): invalid')
      expect(host.textContent).toContain('0 current rows')
    } finally {
      await act(async () => root.unmount())
      client.clear()
      host.remove()
    }
  })
})

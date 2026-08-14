import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { UniverseCurrentResponse } from '../types'
import Universe from './Universe'

const current: UniverseCurrentResponse = {
  enabled: true,
  fallback: false,
  stale: false,
  stale_count: 0,
  snapshot: {
    id: 'snapshot-1234', created_at: '2026-08-13T00:00:00Z', market_type: 'spot', policy_id: null,
    status: 'active', expires_at: '2026-08-13T01:00:00Z', summary: JSON.stringify({ scanned: 120, filtered: 96 }),
  },
  members: [{
    id: 'member-1', snapshot_id: 'snapshot-1234', asset_id: 'avax', symbol: 'AVAX/USDT', rank: 1,
    total_score: 71.2, opportunity_score: 61, tradability_score: 87, liquidity_score: 88,
    estimated_cost: 2.1, crowding_risk: 11, late_entry_penalty: 3, scanner_family: 'momentum',
    quote_volume_24h: 18_400_000, spread_bps: 4.2, ttl_sec: 3600, expires_at: '2026-08-13T01:00:00Z',
    inclusion_reason: JSON.stringify(['RS24h leads BTC']), risk_flags: '[]', execution_eligible: 1,
  }],
}


function renderUniverse() {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
  client.setQueryData(['universe-current'], current)
  return renderToStaticMarkup(<MemoryRouter><QueryClientProvider client={client}><Universe /></QueryClientProvider></MemoryRouter>)
}

describe('Universe Board', () => {
  it('prioritizes pool health, allocation, and actionable candidate context', () => {
    const html = renderUniverse()
    expect(html).toContain('Executable')
    expect(html).toContain('Scanned')
    expect(html).toContain('Allocation')
    expect(html).toContain('Why in pool')
    expect(html).toContain('RS24h leads BTC')
    expect(html).toContain('Market quality')
    expect(html).toContain('Decision state')
    expect(html).not.toContain('Net Scanner Calibration')
  })
})

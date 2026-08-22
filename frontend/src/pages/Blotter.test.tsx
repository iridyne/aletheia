import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

type QueryMode = 'error' | 'success' | 'stale'

const queryState = vi.hoisted(() => ({
  funnel: 'error' as QueryMode,
  proposals: 'error' as QueryMode,
  decisions: 'error' as QueryMode,
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => {
    const key = queryKey[0]
    const mode = key === 'opportunity-funnel-snapshot' ? queryState.funnel
      : key === 'proposals' ? queryState.proposals
      : key === 'operator-decisions' ? queryState.decisions
      : 'success'
    const successData = key === 'opportunity-funnel-snapshot'
      ? { current: { counts: { open_opportunities: 0 }, ranker: { counts: { carrier_active: 0 } } } }
      : key === 'proposals' ? []
      : key === 'operator-decisions' ? { items: [], counts: { trade: 0 } }
      : null
    return {
      data: mode === 'success' || mode === 'stale' ? successData : undefined,
      isPending: false,
      isError: mode === 'error' || mode === 'stale',
    }
  },
}))

vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams()],
  useNavigate: () => vi.fn(),
}))

vi.mock('../components/OpportunityDetailDrawer', () => ({
  default: () => null,
}))

vi.mock('../components/DecisionTraceDrawer', () => ({
  DecisionTraceDrawer: () => null,
}))

import { CandidateLedger, StatusTokenStrip } from './Blotter'

describe('Blotter query state honesty', () => {
  beforeEach(() => {
    queryState.funnel = 'error'
    queryState.proposals = 'error'
    queryState.decisions = 'error'
  })

  it('StatusTokenStrip: failed reads render "—" rather than numeric zero', () => {
    const html = renderToStaticMarkup(<StatusTokenStrip />)
    expect(html).toContain('—')
    expect(html).not.toMatch(/<div[^>]*class="[^"]*bl-token-count[^"]*"[^>]*>0<\/div>/)
  })

  it('CandidateLedger: failed initial read is unavailable, not "No terminal decisions"', () => {
    const html = renderToStaticMarkup(<CandidateLedger />)
    expect(html).toContain('unavailable')
    expect(html).not.toContain('No terminal decisions')
  })

  it('CandidateLedger: successful empty response shows "No terminal decisions"', () => {
    queryState.decisions = 'success'
    const html = renderToStaticMarkup(<CandidateLedger />)
    expect(html).toContain('No terminal decisions')
    expect(html).not.toContain('unavailable')
  })
})

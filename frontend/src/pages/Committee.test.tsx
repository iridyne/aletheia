import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { CommitteeOverviewResponse } from '../api/client'
import { CommitteeWorkbench, CommitteeOpinionDetail } from './Committee'

/**
 * AI Jury workbench honesty: the committee surface is advisory explainability
 * data, so every failure/absence shape must render "not available"/honest
 * notes — never invented verdicts, never benign zeros.
 */

function evaluation(overrides: Record<string, unknown> = {}) {
  return {
    id: 'cr-1',
    created_at: new Date().toISOString(),
    opportunity_id: 'opp-1',
    symbol: 'BTC/USDT',
    decision_id: 'dec-1',
    direction: 'long',
    confidence: 0.62,
    explainability_score: 0.71,
    blocked: false,
    model_used: 'primary-model',
    fallback_used: false,
    indicators: { rsi: 48 },
    agent_opinions: [{
      agent: 'LLM',
      direction: 'long',
      confidence: 0.62,
      reasoning: 'volume expansion above range',
      evidence: [{ indicator: 'RSI', value: 'RSI 48', contribution: 10 }],
    }],
    risks: ['funding could spike'],
    ...overrides,
  }
}

function okResponse(evaluations: ReturnType<typeof evaluation>[]): CommitteeOverviewResponse {
  return {
    status: 'ok',
    window: { count: evaluations.length, limit: 200, symbol: null },
    summary: {
      total: evaluations.length,
      direction_mix: {},
      avg_confidence: null,
      avg_explainability: null,
      blocked_count: 0,
      fallback_count: 0,
      models: {},
      symbol_count: 0,
      latest_at: null,
    },
    evaluations: evaluations as CommitteeOverviewResponse['evaluations'],
  }
}

describe('AI Jury workbench honesty', () => {
  it('renders read failures as unavailable, not as an empty verdict list', () => {
    const html = renderToStaticMarkup(
      <CommitteeWorkbench isLoading={false} isError data={undefined} />,
    )
    expect(html).toContain('Not available')
    expect(html).toContain('could not be read')
    expect(html).not.toContain('No committee evaluations')
  })

  it('renders backend read_failed status with the honest reason', () => {
    const html = renderToStaticMarkup(
      <CommitteeWorkbench
        isLoading={false}
        isError={false}
        data={{ status: 'read_failed', reason: 'database locked', evaluations: [] }}
      />,
    )
    expect(html).toContain('Not available')
    expect(html).toContain('database locked')
  })

  it('renders initial reads as loading rather than an empty page', () => {
    const html = renderToStaticMarkup(
      <CommitteeWorkbench isLoading isError={false} data={undefined} />,
    )
    expect(html).toContain('Loading committee evaluations')
    expect(html).not.toContain('No committee evaluations')
  })

  it('labels an empty ledger honestly instead of fabricating activity', () => {
    const html = renderToStaticMarkup(
      <CommitteeWorkbench isLoading={false} isError={false} data={okResponse([])} />,
    )
    expect(html).toContain('No committee evaluations recorded yet')
    expect(html).toContain('committee_reports')
  })

  it('shows conviction as a percentage of the 0-1 value and surfaces detail fields', () => {
    const html = renderToStaticMarkup(
      <CommitteeWorkbench isLoading={false} isError={false} data={okResponse([evaluation()])} />,
    )
    // Confidence 0.62 must render as 62% — the historical pitfall was
    // displaying the raw 0-1 value without ×100.
    expect(html).toContain('62%')
    expect(html).not.toContain('>0.62%')
    expect(html).toContain('BTC/USDT')
    expect(html).toContain('Long')
    expect(html).toContain('primary-model')
    // ES stays a raw 0-1 score (drawer convention).
    expect(html).toContain('0.71')
  })

  it('flags blocked and fallback rows explicitly', () => {
    const html = renderToStaticMarkup(
      <CommitteeWorkbench
        isLoading={false}
        isError={false}
        data={okResponse([evaluation({ id: 'cr-b', blocked: true, fallback_used: true, direction: 'neutral' })])}
      />,
    )
    expect(html).toContain('blocked')
    expect(html).toContain('fallback')
    expect(html).toContain('Neutral')
  })

  it('renders opinion detail with reasoning, evidence and risk warnings; absence stays honest', () => {
    const rich = renderToStaticMarkup(<CommitteeOpinionDetail ev={evaluation() as any} />)
    expect(rich).toContain('volume expansion above range')
    expect(rich).toContain('RSI 48')
    expect(rich).toContain('Risk warnings')
    expect(rich).toContain('funding could spike')
    expect(rich).toContain('rsi: 48')

    const bare = renderToStaticMarkup(
      <CommitteeOpinionDetail
        ev={evaluation({ id: 'cr-b', agent_opinions: [], risks: [], indicators: {} }) as any}
      />,
    )
    expect(bare).toContain('No agent opinions were persisted')
    expect(bare).not.toContain('Risk warnings')
  })
})

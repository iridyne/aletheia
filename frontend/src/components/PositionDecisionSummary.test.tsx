// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { Position } from '../types'
import { PositionDecisionSummary } from './PositionDecisionSummary'

function makePosition(overrides: Partial<Position> = {}): Position {
  return {
    id: 'pos-1',
    symbol: 'BTC/USDT',
    market_type: 'spot',
    direction: 'long',
    quantity: 0.5,
    entry_price: 60000,
    current_price: 61000,
    unrealized_pnl: 500,
    realized_pnl: null,
    execution_mode: 'paper_internal',
    strategy_id: null,
    status: 'open',
    stop_price: 58000,
    take_profit_price: 65000,
    close_reason: null,
    opened_at: '2026-01-15T10:00:00Z',
    closed_at: null,
    decision_reasoning: null,
    ...overrides,
  }
}

let root: Root | undefined
let container: HTMLElement

beforeEach(() => {
  container = document.createElement('div')
  root = createRoot(container)
})
afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  root = undefined
})

describe('PositionDecisionSummary — readable projection', () => {
  it('renders thesis, conviction, supporting/opposing signals and primary risk', async () => {
    const meta = {
      conviction: 72,
      supporting_signals: ['Strong momentum', 'Volume confirmation'],
      opposing_signals: ['Overbought RSI'],
      bear_case: 'Bull trap if 60k rejected on retest.',
    }
    const raw = `Momentum breakout with volume confirmation.\n---\n${JSON.stringify(meta)}`
    await act(async () => root?.render(
      <PositionDecisionSummary position={makePosition({ decision_reasoning: raw })} />,
    ))
    const text = container.textContent || ''
    expect(text).toContain('Momentum breakout with volume confirmation.')
    expect(text).toContain('conviction')
    expect(text).toContain('72')
    expect(text).toContain('Long')
    expect(text).toContain('Strong momentum')
    expect(text).toContain('Overbought RSI')
    expect(text).toContain('Bull trap if 60k rejected')
    // Raw JSON must NOT appear anywhere in the rendered output.
    expect(text).not.toContain('"conviction"')
    expect(text).not.toContain('---')
  })

  it('renders debate summary only when debate_ran is true', async () => {
    const raw = 'Thesis.\n---\n' + JSON.stringify({
      debate_ran: true, debate_initial_conviction: 55, debate_bull: 68, debate_bear: 42,
    })
    await act(async () => root?.render(
      <PositionDecisionSummary position={makePosition({ decision_reasoning: raw })} />,
    ))
    const text = container.textContent || ''
    expect(text).toContain('debate')
    expect(text).toContain('bull')
    expect(text).toContain('68')

    const noDebate = 'Thesis.\n---\n' + JSON.stringify({ debate_ran: false })
    await act(async () => root?.render(
      <PositionDecisionSummary position={makePosition({ decision_reasoning: noDebate })} />,
    ))
    expect(container.textContent).not.toContain('debate ·')
  })

  it('handles legacy records with no JSON tail — thesis-only, no crash', async () => {
    await act(async () => root?.render(
      <PositionDecisionSummary position={makePosition({
        decision_reasoning: 'Older row: pure text reasoning, no metadata.',
      })} />,
    ))
    expect(container.textContent).toContain('Older row: pure text reasoning')
  })

  it('handles malformed JSON gracefully with an inline hint (thesis preserved)', async () => {
    await act(async () => root?.render(
      <PositionDecisionSummary position={makePosition({
        decision_reasoning: 'Reasoning here.\n---\n{corrupt json,,',
      })} />,
    ))
    const text = container.textContent || ''
    expect(text).toContain('Reasoning here.')
    expect(text).toContain('Structured decision metadata unavailable')
  })
})

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { closeResultNotice, closeResultQueryKeys, pnlCell } from './TradesPositions'

describe('pnlCell', () => {
  it('renders zero as neutral break-even', () => {
    const html = renderToStaticMarkup(pnlCell(0))

    expect(html).toContain('class="mono"')
    expect(html).toContain('0.00')
    expect(html).not.toContain('pnl-positive')
    expect(html).not.toContain('pnl-negative')
    expect(html).not.toContain('+0.00')
  })

  it('keeps positive, negative, and unavailable states distinct', () => {
    expect(renderToStaticMarkup(pnlCell(1))).toContain('pnl-positive')
    expect(renderToStaticMarkup(pnlCell(1))).toContain('+1.00')
    expect(renderToStaticMarkup(pnlCell(-1))).toContain('pnl-negative')
    expect(renderToStaticMarkup(pnlCell(-1))).toContain('-1.00')
    expect(renderToStaticMarkup(pnlCell(null))).toContain('text-muted')
    expect(renderToStaticMarkup(pnlCell(null))).toContain('—')
  })
})

describe('closeResultNotice', () => {
  const position = { symbol: 'BTC/USDT', market_type: 'usdt_futures' }

  it('claims terminal closure only after materialization is confirmed', () => {
    expect(closeResultNotice(position, { closed: true })).toEqual({
      kind: 'closed',
      message: 'BTCUSDT closed',
    })
  })

  it('keeps accepted and partially filled closes non-terminal', () => {
    expect(closeResultNotice(position, {
      closed: false,
      status: 'submitted',
      order_id: 'order-1',
    })).toEqual({
      kind: 'pending',
      message: 'BTCUSDT close submitted — awaiting confirmation (submitted, order order-1)',
    })
    expect(closeResultNotice(position, {
      closed: false,
      status: 'partially_filled',
      exchange_order_id: 'venue-1',
    })).toEqual({
      kind: 'pending',
      message: 'BTCUSDT close partially filled — residual exposure remains (partially_filled, order venue-1)',
    })
    expect(closeResultNotice(position, {
      closed: false,
      status: 'materialization_missing',
    })).toEqual({
      kind: 'pending',
      message: 'BTCUSDT fill acknowledged — awaiting reconciliation (materialization_missing)',
    })
    expect(closeResultNotice(position, {
      closed: false,
      status: 'submitted',
      already_pending: true,
    }).message).toContain('close already pending')
  })

  it('surfaces rejected and ambiguous acknowledgements as errors', () => {
    expect(closeResultNotice(position, { closed: false, status: 'submission_rejected' }).kind).toBe('error')
    expect(closeResultNotice(position, { closed: false, status: 'cancelled' }).kind).toBe('error')
    expect(closeResultNotice(position, { closed: false }).kind).toBe('error')
    expect(closeResultNotice(position, {}).kind).toBe('error')
    expect(closeResultNotice(position, null).kind).toBe('error')
  })

  it('refreshes every projection affected by close convergence', () => {
    expect(closeResultQueryKeys).toEqual([
      ['positions'],
      ['trades'],
      ['positions-reconciliation'],
      ['portfolio'],
    ])
  })
})

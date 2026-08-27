import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { pnlCell } from './TradesPositions'

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

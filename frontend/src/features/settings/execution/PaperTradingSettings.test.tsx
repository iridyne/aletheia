import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { CapitalEventsCard, PaperAccountCard, TradingParamsCard } from './PaperTradingSettings'
import SRC from './PaperTradingSettings.tsx?raw'

describe('PaperTradingSettings', () => {
  it('preserves zero cash and local position-management controls', () => {
    const client = new QueryClient()
    client.setQueryData(['paper-account'], {
      cash_balance_usdt: 0,
      reserved_margin_usdt: 0,
      available_balance_usdt: 0,
      equity_usdt: 0,
      events: [],
    })
    const html = renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <PaperAccountCard qc={client} />
      </QueryClientProvider>,
    )

    expect(html).toContain('0.00 USDT')
    expect(html).toContain('Setting cash to 0 is preserved')
    expect(html).toContain('Close local spot positions')
    expect(html).toContain('Close all local positions')
    expect(SRC).toContain("if (confirm(`Set local paper cash balance to ${value.toFixed(2)} USDT?`))")
  })

  it('keeps capital events and paper parameter controls composed', () => {
    const client = new QueryClient()
    client.setQueryData(['capital-events'], [])
    client.setQueryData(['capital-net-deposits'], { net_deposits_usdt: 0 })
    const html = renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <CapitalEventsCard />
        <TradingParamsCard settings={{ trading: {} }} qc={client} onDirty={() => undefined} />
      </QueryClientProvider>,
    )

    expect(html).toContain('Net deposits')
    expect(html).toContain('Default stop-loss (%)')
    expect(html).toContain('Trailing Stop &amp; Breakeven')
    expect(html).toContain('Persona-Based Sizing')
    expect(SRC).toContain('Legacy sizing scale')
    expect(SRC).toContain('not a calibrated Kelly fraction')
    expect(html).toContain('Market-Aware Position Review')
  })
})

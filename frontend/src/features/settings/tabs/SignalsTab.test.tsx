// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SignalsTab } from './SignalsTab'
import SRC from './SignalsTab.tsx?raw'

function renderSignalsTab(marketData: { observed_equities?: string[]; observed_stocks?: string[]; observed_stock_tokens?: string[]; observed_xstocks?: string[] } = {}) {
  const client = new QueryClient()
  client.setQueryData(['prediction-status'], { enabled: true })
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <SignalsTab
        settings={{ market_data: marketData, exchange: {}, news: { enabled: true, feeds: [] } }}
        qc={client}
        onDirty={() => undefined}
      />
    </QueryClientProvider>,
  )
}

describe('SignalsTab', () => {
  it('preserves section order and deep-link anchors', () => {
    const html = renderSignalsTab()
    const ids = ['market-data-sources', 'macro-sentiment-signals', 'news-feeds', 'prediction-markets', 'observed-us-equities']
    let previous = -1
    for (const id of ids) {
      const position = html.indexOf(`id="settings-section-${id}"`)
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
  })

  it('keeps every Signals domain component composed', () => {
    const html = renderSignalsTab()

    expect(SRC).toContain('<MarketDataCard settings={settings} qc={qc} onDirty={onDirty} />')
    expect(html).toContain('Fear &amp; Greed Index')
    expect(html).toContain('News ingestion')
    expect(html).toContain('Prediction observer')
    expect(html).toContain('No tickers — equity_observer will not run.')
  })

  it('names each news-feed switch from its sibling feed identity', () => {
    expect(SRC).toContain('aria-label={`Enable ${f.name}`}')
  })

  it('exposes only the canonical equity-list editor', () => {
    const html = renderSignalsTab()
    expect(html).toContain('Observed US Equities')
    expect(html).not.toContain('Observed stocks')
    expect(SRC).not.toContain('observed_stocks: parseCsvList')
    expect(SRC).toContain('api.updateEquities(draft)')
  })

  it('renders canonical tickers and honors an empty list without legacy fallback', () => {
    const empty = renderSignalsTab({ observed_equities: [], observed_stocks: ['TSLA'] })
    expect(empty).not.toContain('Remove TSLA')
    const canonical = renderSignalsTab({ observed_equities: ['AAPL'], observed_stocks: ['TSLA'] })
    expect(canonical).toContain('Remove AAPL')
    expect(canonical).not.toContain('Remove TSLA')
  })

  it('shows merged token inputs without writing the legacy field', async () => {
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const container = document.createElement('div')
    const root = createRoot(container)
    const client = new QueryClient()
    try {
      await act(async () => root.render(
        <QueryClientProvider client={client}>
          <SignalsTab sectionId="market-data-sources" qc={client} onDirty={() => undefined}
            settings={{ market_data: {
              observed_stock_tokens: ['rNVDA/USDT', 'TSLAON/USDT'],
            } }} />
        </QueryClientProvider>,
      ))
      await act(async () => container.querySelector('button')!.click())
      const input = container.querySelector<HTMLInputElement>('input[placeholder="rNVDA/USDT, rTSLA/USDT, AAPLON/USDT"]')
      expect(input?.value).toBe('rNVDA/USDT, TSLAON/USDT')
      expect(SRC).not.toContain('observed_xstocks:')
    } finally {
      await act(async () => root.unmount())
      client.clear()
    }
  })
})

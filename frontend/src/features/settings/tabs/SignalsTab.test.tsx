import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SignalsTab } from './SignalsTab'
import SRC from './SignalsTab.tsx?raw'

function renderSignalsTab(feeds: Array<{ name: string; url: string; source: string; enabled: boolean }> = []) {
  const client = new QueryClient()
  client.setQueryData(['prediction-status'], { enabled: true })
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <SignalsTab
        settings={{ market_data: {}, exchange: {}, news: { enabled: true, feeds } }}
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
    const html = renderSignalsTab([
      { name: 'Reuters', url: 'https://example.test/reuters', source: 'wire', enabled: true },
    ])

    expect(html).toContain('aria-label="Enable Reuters"')
  })
})

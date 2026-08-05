import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RiskTab } from './RiskTab'
import SRC from './RiskTab.tsx?raw'

function renderRiskTab() {
  const client = new QueryClient()
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <RiskTab
        settings={{ universe: {}, event_triggers: {}, risk: { symbol_filter_mode: 'blacklist', symbol_list: [] }, market_data: {} }}
        qc={client}
        onDirty={() => undefined}
      />
    </QueryClientProvider>,
  )
}

describe('RiskTab', () => {
  it('preserves section order and the Universe deep-link anchor', () => {
    const html = renderRiskTab()
    const ids = ['universe', 'event-triggers', 'tradable-symbols', 'risk-limits']
    let previous = -1
    for (const id of ids) {
      const position = html.indexOf(`id="settings-section-${id}"`)
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
  })

  it('keeps every risk domain component and safety control composed', () => {
    const html = renderRiskTab()

    expect(SRC).toContain('<UniverseConfigCard settings={settings} qc={qc} onDirty={onDirty} />')
    expect(SRC).toContain('<EventTriggerCard settings={settings} qc={qc} />')
    expect(html).toContain('Empty — all symbols are tradable.')
    expect(html).toContain('Max daily loss')
    expect(html).toContain('Kill switch auto-trigger (live)')
    expect(SRC).toContain('<RiskPolicyProfiles risk={risk} qc={qc} />')
  })
})

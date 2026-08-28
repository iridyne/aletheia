import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ExecutionTab } from './ExecutionTab'
import SRC from './ExecutionTab.tsx?raw'

function renderExecutionTab(
  paperMode = 'paper_internal',
  utaReadBoundaries: Record<string, 'legacy' | 'shadow_read' | 'uta_v1'> = {},
) {
  const client = new QueryClient()
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <ExecutionTab
        status={{ live_state: 'disabled' }}
        settings={{
          exchange: {
            paper_execution_mode: paperMode,
            enabled_markets: {},
            uta_read_boundaries: utaReadBoundaries,
          },
          market_data: {},
          trading: {},
        }}
        qc={client}
        onDirty={() => undefined}
      />
    </QueryClientProvider>,
  )
}

describe('ExecutionTab', () => {
  it('preserves section order and conditional local-paper anchor', () => {
    const html = renderExecutionTab()
    const ids = ['exchange-connection', 'trading-mode', 'paper-trading-parameters', 'capital-events', 'local-paper-account']
    let previous = -1
    for (const id of ids) {
      const position = html.indexOf(`id="settings-section-${id}"`)
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
  })

  it('keeps exchange, live authority, and paper components composed', () => {
    const html = renderExecutionTab()

    expect(SRC).toContain('<ExchangeConnectionCard qc={qc} onDirty={onDirty} />')
    expect(html).toContain('Experimental Live authority state')
    expect(SRC).toContain('此操作只记录 live_limited 权限前置状态，不会把新 Proposal 路由到实盘')
    expect(SRC).toContain('当前自主 Proposal 仍使用 Paper execution mode 中选择的模拟执行通道')
    expect(SRC).toContain('只有未来独立完成并验证 live Proposal 路由后，真实资金风险才会出现')
    expect(SRC).toContain('<TradingParamsCard settings={settings} qc={qc} onDirty={onDirty} />')
    expect(SRC).toContain('<CapitalEventsCard onDirty={onDirty} />')
    expect(SRC).toContain('<PaperAccountCard qc={qc} onDirty={onDirty} />')
  })

  it('exposes legacy rollback and shadow-read selection for external accounts', () => {
    const html = renderExecutionTab('okx_demo', { okx_demo: 'shadow_read' })

    expect(html).toContain('UTA read backend')
    expect(html).toContain('Legacy (authoritative)')
    expect(html).toContain('UTA shadow read')
    expect(SRC).toContain('uta_read_boundary: readBoundary')
    expect(SRC).toContain('<option value="uta_v1" disabled>')
  })

  it('keeps credential saves bound to an immutable target while pending', () => {
    expect(SRC).toContain('type CredentialSubmission = {')
    expect(SRC).toContain('verifyAndSave.mutate({ exchange, profile, apiKey, apiSecret, passphrase })')
    expect(SRC).toContain('disabled={verifyAndSave.isPending}')
    expect(SRC).toContain('variables.exchange, variables.profile')
    expect(SRC).toContain("if (apiKey === variables.apiKey) setApiKey('')")
  })
})

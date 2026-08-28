// @vitest-environment happy-dom
import { act } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { ExchangeModeRow, ExecutionTab } from './ExecutionTab'
import SRC from './ExecutionTab.tsx?raw'

vi.mock('../../../api/client', () => ({
  api: { setExchangeMode: vi.fn() },
}))

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>(res => { resolve = res })
  return { promise, resolve }
}

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

  it('keeps exchange mode writes serialized through the authoritative status refetch', async () => {
    const write = deferred()
    const refetch = deferred()
    vi.mocked(api.setExchangeMode).mockReturnValue(write.promise as any)
    const qc = { refetchQueries: vi.fn(() => refetch.promise) }
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    const renderRow = (exchangeMode: string) => root!.render(
      <QueryClientProvider client={client}>
        <ExchangeModeRow
          status={{ exchange_mode: exchangeMode }}
          settings={{ exchange: { paper_execution_mode: 'paper_internal' } }}
          qc={qc as any}
        />
      </QueryClientProvider>,
    )

    await act(async () => renderRow('live'))
    const select = host.querySelector('select') as HTMLSelectElement
    select.value = 'demo'
    await act(async () => select.dispatchEvent(new Event('change', { bubbles: true })))

    expect(api.setExchangeMode).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => {
      expect(select.value).toBe('demo')
      expect(select.disabled).toBe(true)
    })

    await act(async () => write.resolve())
    expect(qc.refetchQueries).toHaveBeenCalledWith({ queryKey: ['status'] }, { throwOnError: true })
    expect(select.disabled).toBe(true)

    await act(async () => {
      renderRow('demo')
      refetch.resolve()
      await refetch.promise
    })
    await vi.waitFor(() => expect(select.disabled).toBe(false))
    expect(select.value).toBe('demo')
    expect(api.setExchangeMode).toHaveBeenCalledTimes(1)
  })
})

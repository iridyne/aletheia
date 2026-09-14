// @vitest-environment happy-dom
import { act } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { ExchangeModeRow, ExecutionTab } from './ExecutionTab'

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
  paperMode = 'bitget_demo',
  client = new QueryClient(),
) {
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <ExecutionTab
        status={{ live_state: 'disabled' }}
        settings={{
          exchange: {
            paper_execution_mode: paperMode,
            enabled_markets: {},
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
  it.each([false, true])('reports unavailable credential status even with cached data=%s', cached => {
    const client = new QueryClient({ defaultOptions: { queries: { retryOnMount: false } } })
    const query = client.getQueryCache().build(client, { queryKey: ['bitgetCreds'] })
    query.setState({
      status: 'error', error: new Error('HTTP 404'),
      data: cached ? { demo: { api_key: { configured: true } } } : undefined,
    })
    const html = renderExecutionTab('bitget_demo', client)
    expect(html).toContain('not available')
    expect(html).not.toContain('not configured')
  })

  it('reports not configured only after a successful credential-status read', () => {
    const client = new QueryClient()
    client.setQueryData(['bitgetCreds'], {
      demo: { api_key: { configured: false } }, live: { api_key: { configured: false } },
    })
    expect(renderExecutionTab('bitget_demo', client)).toContain('not configured')
    expect(renderExecutionTab()).not.toContain('not configured')
  })

  it('preserves external-exchange section order without local account controls', () => {
    const html = renderExecutionTab()
    const ids = ['exchange-connection', 'trading-mode', 'paper-trading-parameters', 'capital-events']
    expect(html).not.toContain('Paper (internal)')
    expect(html).not.toContain('local-paper-account')
    let previous = -1
    for (const id of ids) {
      const position = html.indexOf(`id="settings-section-${id}"`)
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
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
          settings={{ exchange: { paper_execution_mode: 'bitget_demo' } }}
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

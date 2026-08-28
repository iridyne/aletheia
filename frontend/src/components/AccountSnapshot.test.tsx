// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { positionKeys } from '../lib/queryKeys'
import AccountSnapshot from './AccountSnapshot'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | undefined

afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  document.body.replaceChildren()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

async function flushQueries() {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 10))
  })
}

function renderSnapshot(client: QueryClient) {
  client.setQueryData(positionKeys.all, [])
  vi.spyOn(api, 'getPositions').mockResolvedValue([])
  const node = document.createElement('div')
  document.body.appendChild(node)
  root = createRoot(node)
  act(() => root!.render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AccountSnapshot />
      </QueryClientProvider>
    </MemoryRouter>,
  ))
  return node
}

function queryClient(staleTime = Infinity) {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime } },
  })
}

describe('AccountSnapshot portfolio read state', () => {
  it('labels an initial portfolio read failure as unavailable, not unconfigured', async () => {
    vi.spyOn(api, 'getPortfolio').mockRejectedValue(new Error('portfolio down'))

    const node = renderSnapshot(queryClient(0))
    await flushQueries()

    expect(node.textContent).toContain('portfolio read failed')
    expect(node.textContent).not.toContain('exchange not configured')
    expect(node.textContent).not.toContain('exchange · live')
    expect(node.textContent).not.toContain('unified · live')
  })

  it('keeps a successful unconfigured response labelled as exchange not configured', async () => {
    vi.spyOn(api, 'getPortfolio').mockResolvedValue({
      total_value_usdt: null,
      today_realized_pnl: null,
      exchange_sync_status: 'unconfigured',
    })

    const node = renderSnapshot(queryClient(0))
    await flushQueries()

    expect(node.textContent).toContain('exchange not configured')
    expect(node.textContent).not.toContain('portfolio read failed')
  })

  it('marks cached values as read-failed instead of live after a refetch failure', async () => {
    vi.spyOn(api, 'getPortfolio').mockRejectedValue(new Error('portfolio down'))
    const client = queryClient(0)
    const updatedAt = Date.now() - 65_000
    client.setQueryData(['portfolio'], {
      total_value_usdt: 1234,
      today_realized_pnl: 12,
      exchange_equity_usdt: 1234,
      exchange_sync_status: 'ok',
    }, { updatedAt })

    const node = renderSnapshot(client)
    await flushQueries()

    expect(node.textContent).toMatch(/cached · portfolio read failed · 6[5-9]s old/)
    expect(node.textContent).not.toContain('exchange · live')
    expect(node.textContent).not.toContain('unified · live')
  })
})

// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { CapitalEventsCard, TradingParamsCard } from './PaperTradingSettings'

let root: Root | undefined
let container: HTMLElement

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function button(text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button')).find(
    candidate => candidate.textContent === text,
  )
  expect(found).toBeDefined()
  return found as HTMLButtonElement
}

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  container?.remove()
  root = undefined
  vi.restoreAllMocks()
})

describe('PaperTradingSettings', () => {
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
    expect(html).toContain('Market-Aware Position Review')
  })
  it('distinguishes loading and failed capital reads from zero and retries both reads', async () => {
    const events = deferred<never>()
    const net = deferred<never>()
    vi.spyOn(api, 'getCapitalEvents').mockReturnValueOnce(events.promise).mockResolvedValue([])
    vi.spyOn(api, 'getNetDeposits').mockReturnValueOnce(net.promise).mockResolvedValue({ net_deposits_usdt: 0, first_event_at: null })
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    await act(async () => {
      root?.render(<QueryClientProvider client={client}><CapitalEventsCard /></QueryClientProvider>)
    })
    expect(container.textContent).toContain('Loading capital events')
    expect(container.textContent).toContain('Net deposits: loading')
    expect(container.querySelector('table')).toBeNull()
    await act(async () => {
      events.reject(new Error('events unavailable'))
      net.reject(new Error('net unavailable'))
    })
    await vi.waitFor(() => {
      expect(container.textContent).toContain('Capital events unavailable')
      expect(container.textContent).toContain('Net deposits: not available')
    })
    expect(container.textContent).not.toContain('0.00 USDT')
    expect(container.querySelector('table')).toBeNull()
    const retries = Array.from(container.querySelectorAll('button')).filter(candidate => candidate.textContent === 'Retry')
    expect(retries).toHaveLength(2)
    await act(async () => { retries.forEach(retry => retry.click()) })
    await vi.waitFor(() => {
      expect(container.textContent).toContain('Net deposits: 0.00 USDT')
      expect(container.textContent).not.toContain('Capital events unavailable')
      expect(container.textContent).not.toContain('Loading capital events')
    })
    client.clear()
  })

  it('appends older capital-event pages without losing newer rows', async () => {
    const firstPage = {
      events: [{ id: 'newer', detected_at: '2026-08-22T00:00:00Z', event_type: 'deposit', broker_id: 'manual', amount_usdt: 2, note: '' }],
      total: 2,
      has_more: true,
      next_cursor: { detected_at: '2026-08-22T00:00:00Z', id: 'newer' },
    }
    const secondPage = {
      events: [{ id: 'older', detected_at: '2026-08-21T00:00:00Z', event_type: 'withdraw', broker_id: 'manual', amount_usdt: 1, note: '' }],
      total: 2,
      has_more: false,
      next_cursor: null,
    }
    vi.spyOn(api, 'getCapitalEvents').mockImplementation(async params => (
      params?.before_id ? secondPage : firstPage
    ))
    vi.spyOn(api, 'getNetDeposits').mockResolvedValue({ net_deposits_usdt: 1, first_event_at: null })

    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    await act(async () => {
      root?.render(
        <QueryClientProvider client={new QueryClient()}>
          <CapitalEventsCard />
        </QueryClientProvider>,
      )
    })
    await vi.waitFor(() => {
      expect(container.querySelectorAll('tbody tr')).toHaveLength(1)
      expect(container.textContent).toContain('2026-08-22')
    })

    await act(async () => { button('Load older').click() })
    await vi.waitFor(() => {
      expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
      expect(container.textContent).toContain('2026-08-22')
      expect(container.textContent).toContain('2026-08-21')
    })
    expect(container.textContent).not.toContain('Load older')
  })

  it('records one capital event per submit while the first request is in flight (#1555)', async () => {
    const pending = deferred<never>()
    vi.spyOn(api, 'getCapitalEvents').mockResolvedValue({ events: [], total: 0, has_more: false, next_cursor: null })
    vi.spyOn(api, 'getNetDeposits').mockResolvedValue({ net_deposits_usdt: 0, first_event_at: null })
    const create = vi.spyOn(api, 'createCapitalEvent').mockReturnValue(pending.promise)

    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    await act(async () => {
      root?.render(
        <QueryClientProvider client={new QueryClient()}>
          <CapitalEventsCard />
        </QueryClientProvider>,
      )
    })
    const amount = container.querySelector('input[type="number"]') as HTMLInputElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(amount, '100')
      amount.dispatchEvent(new Event('input', { bubbles: true }))
    })

    await act(async () => { button('Record').click() })
    // A human double-click lands a macrotask later, after the pending render.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
    await act(async () => { button('Record').click() })

    expect(button('Record').disabled).toBe(true)
    expect(create).toHaveBeenCalledTimes(1)
  })

})

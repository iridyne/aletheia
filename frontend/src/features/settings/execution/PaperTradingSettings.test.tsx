// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { ConfirmProvider } from '../../../components/ui'
import { CapitalEventsCard, PaperAccountCard, TradingParamsCard } from './PaperTradingSettings'
import SRC from './PaperTradingSettings.tsx?raw'

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

function changeInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function button(text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button')).find(
    candidate => candidate.textContent === text,
  )
  expect(found).toBeDefined()
  return found as HTMLButtonElement
}

function lastButton(text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button'))
    .filter(candidate => candidate.textContent === text)
    .at(-1)
  expect(found).toBeDefined()
  return found as HTMLButtonElement
}

function input(placeholder: string): HTMLInputElement {
  const found = container.querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`)
  expect(found).not.toBeNull()
  return found as HTMLInputElement
}

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

async function renderPaperAccountCard(client = new QueryClient()) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  client.setQueryData(['paper-account'], {
    cash_balance_usdt: 100,
    reserved_margin_usdt: 0,
    available_balance_usdt: 100,
    equity_usdt: 100,
    events: [],
  })
  vi.spyOn(api, 'getPaperAccount').mockResolvedValue({
    cash_balance_usdt: 100,
    reserved_margin_usdt: 0,
    available_balance_usdt: 100,
    equity_usdt: 100,
    events: [],
  })
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  await act(async () => {
    root?.render(
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <PaperAccountCard qc={client} />
        </ConfirmProvider>
      </QueryClientProvider>,
    )
  })
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  container?.remove()
  root = undefined
  vi.restoreAllMocks()
})

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
    expect(SRC).toContain('const confirm = useConfirm()')
    expect(SRC).toContain('title: \'Set local paper cash balance?\'')
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

  it('serializes local paper account actions behind one pending gate', async () => {
    const setPending = deferred<{ ok: boolean }>()
    const closePending = deferred<{ closed_position_ids: string[] }>()
    vi.spyOn(api, 'setPaperAccountBalance').mockReturnValue(setPending.promise)
    vi.spyOn(api, 'closeAllPaperPositions').mockReturnValue(closePending.promise)
    await renderPaperAccountCard()

    changeInput(input('200'), '200')
    await act(async () => { button('Set balance').click() })
    await act(async () => { lastButton('Set balance').click() })
    await flush()
    expect(api.setPaperAccountBalance).toHaveBeenCalledWith(200, '')
    expect(button('Add funds').disabled).toBe(true)
    expect(button('Remove funds').disabled).toBe(true)
    expect(button('Close local spot positions').disabled).toBe(true)
    expect(button('Close all local positions').disabled).toBe(true)

    await act(async () => { setPending.resolve({ ok: true }) })
    await flush()
    await act(async () => { button('Close all local positions').click() })
    await act(async () => { button('Close positions').click() })
    await flush()
    expect(api.closeAllPaperPositions).toHaveBeenCalledTimes(1)
    expect(button('Add funds').disabled).toBe(true)
    expect(button('Remove funds').disabled).toBe(true)
    expect(button('Set balance').disabled).toBe(true)

    await act(async () => { closePending.resolve({ closed_position_ids: [] }) })
  })

  it('clears only the unchanged cash draft owned by the completed request', async () => {
    const first = deferred<{ ok: boolean }>()
    const second = deferred<{ ok: boolean }>()
    const credit = vi.spyOn(api, 'creditPaperAccount')
    credit.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    await renderPaperAccountCard()

    changeInput(input('200'), '100')
    changeInput(input('Reason (shown to agent)'), 'draft A')
    await act(async () => { button('Add funds').click() })
    expect(credit).toHaveBeenLastCalledWith(100, 'draft A')
    await act(async () => { first.resolve({ ok: true }) })
    await flush()
    expect(input('200').value).toBe('')
    expect(input('Reason (shown to agent)').value).toBe('')

    changeInput(input('200'), '200')
    changeInput(input('Reason (shown to agent)'), 'draft B')
    await act(async () => { button('Add funds').click() })
    expect(credit).toHaveBeenLastCalledWith(200, 'draft B')
    changeInput(input('200'), '300')
    changeInput(input('Reason (shown to agent)'), 'draft C')
    await act(async () => { second.resolve({ ok: true }) })
    await flush()
    expect(input('200').value).toBe('300')
    expect(input('Reason (shown to agent)').value).toBe('draft C')
  })

  it('preserves a failed cash draft and releases the shared pending gate', async () => {
    const pending = deferred<{ ok: boolean }>()
    vi.spyOn(api, 'debitPaperAccount').mockReturnValue(pending.promise)
    await renderPaperAccountCard()

    changeInput(input('200'), '25')
    changeInput(input('Reason (shown to agent)'), 'keep this')
    await act(async () => { button('Remove funds').click() })
    await flush()
    expect(api.debitPaperAccount).toHaveBeenCalledWith(25, 'keep this')
    expect(button('Close all local positions').disabled).toBe(true)

    await act(async () => { pending.reject(new Error('nope')) })
    await flush()
    expect(input('200').value).toBe('25')
    expect(input('Reason (shown to agent)').value).toBe('keep this')
    expect(button('Close all local positions').disabled).toBe(false)
    expect(container.textContent).toContain('nope')
  })
})

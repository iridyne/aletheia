// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Position } from '../types'
import { PositionActions, positionChartHref } from './PositionActions'
import { ToastProvider } from './ui/Toast'

function makePosition(overrides: Partial<Position> = {}): Position {
  return {
    id: 'pos-abc',
    symbol: 'ETH/USDT',
    market_type: 'perp',
    direction: 'long',
    quantity: 1.25,
    entry_price: 3200,
    current_price: 3350,
    unrealized_pnl: 187.5,
    realized_pnl: null,
    execution_mode: 'paper_internal',
    strategy_id: 'strat-1',
    status: 'open',
    stop_price: 3100,
    take_profit_price: 3600,
    close_reason: null,
    opened_at: '2026-01-15T10:00:00Z',
    closed_at: null,
    decision_reasoning: 'Thesis line.\n---\n{"conviction":72,"supporting_signals":["a"]}',
    ...overrides,
  }
}

let root: Root | undefined
let container: HTMLElement

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  container.remove()
  root = undefined
  vi.restoreAllMocks()
})

describe('positionChartHref — position-aware deep link', () => {
  it('preserves symbol, market_type and position identity in the URL', () => {
    const href = positionChartHref(makePosition())
    expect(href.startsWith('/charts?')).toBe(true)
    const params = new URLSearchParams(href.slice('/charts?'.length))
    expect(params.get('symbol')).toBe('ETH/USDT')
    expect(params.get('market_type')).toBe('perp')
    expect(params.get('position_id')).toBe('pos-abc')
  })

  it('defaults market_type to spot when missing', () => {
    const href = positionChartHref(makePosition({ market_type: '' }))
    expect(new URLSearchParams(href.slice('/charts?'.length)).get('market_type')).toBe('spot')
  })
})

describe('PositionActions — Copy Full JSON', () => {
  it('renders a "View on Chart" anchor pointing at the deep-link URL', async () => {
    const position = makePosition()
    await act(async () => root?.render(
      <MemoryRouter>
        <ToastProvider>
          <PositionActions position={position} />
        </ToastProvider>
      </MemoryRouter>,
    ))
    const link = container.querySelector('a[aria-label="Open position on chart"]') as HTMLAnchorElement
    expect(link).not.toBeNull()
    expect(link.getAttribute('href')).toBe(positionChartHref(position))
  })

  it('copies the full source payload (all fields, including decision_reasoning)', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    const position = makePosition()
    await act(async () => root?.render(
      <MemoryRouter>
        <ToastProvider>
          <PositionActions position={position} />
        </ToastProvider>
      </MemoryRouter>,
    ))
    const btn = Array.from(container.querySelectorAll('button')).find(
      b => b.textContent?.includes('Copy Full JSON'),
    ) as HTMLButtonElement
    expect(btn).toBeTruthy()
    await act(async () => { btn.click() })

    expect(writeText).toHaveBeenCalledTimes(1)
    const payload = writeText.mock.calls[0][0] as string
    // Must be pretty-printed JSON, not a summary — full audit fields survive.
    expect(payload).toBe(JSON.stringify(position, null, 2))
    const parsed = JSON.parse(payload)
    expect(parsed.decision_reasoning).toBe(position.decision_reasoning)
    expect(parsed.id).toBe(position.id)
    expect(parsed.opened_at).toBe(position.opened_at)
    expect(parsed.entry_price).toBe(position.entry_price)
  })

  it('surfaces a toast on clipboard failure instead of silently swallowing', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('blocked')) },
    })

    await act(async () => root?.render(
      <MemoryRouter>
        <ToastProvider>
          <PositionActions position={makePosition()} />
        </ToastProvider>
      </MemoryRouter>,
    ))
    const btn = Array.from(container.querySelectorAll('button')).find(
      b => b.textContent?.includes('Copy Full JSON'),
    ) as HTMLButtonElement
    await act(async () => { btn.click() })

    // Toast container renders the error into the DOM.
    await act(async () => { await Promise.resolve() })
    expect(document.querySelector('.toast-error')?.textContent).toContain('Could not copy')
  })
})

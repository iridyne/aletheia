// @vitest-environment happy-dom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Position, UniverseCurrentResponse } from '../types'

vi.mock('../charts/PriceChart', () => ({
  default: ({ symbol, marketType }: { symbol: string; marketType: string }) => <div data-testid="price-chart" data-symbol={symbol} data-market={marketType}>Chart canvas</div>,
}))
vi.mock('../components/MacroBar', () => ({ default: () => <div>Macro context</div> }))

import Charts from './Charts'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const universe: UniverseCurrentResponse = {
  enabled: true,
  fallback: false,
  stale: false,
  stale_count: 0,
  snapshot: {
    id: 'snapshot', created_at: '2026-08-13T00:00:00Z', market_type: 'spot', policy_id: null,
    status: 'active', expires_at: null, summary: '{}',
  },
  members: [{
    id: 'btc-member', snapshot_id: 'snapshot', asset_id: 'btc', symbol: 'BTC/USDT', rank: 1,
    total_score: 90, opportunity_score: 90, tradability_score: 90, liquidity_score: 90,
    estimated_cost: 1, crowding_risk: 1, late_entry_penalty: 1, scanner_family: 'momentum',
    quote_volume_24h: 1, spread_bps: 1, ttl_sec: 3600, expires_at: '2026-08-13T01:00:00Z',
    inclusion_reason: '[]', risk_flags: '[]', execution_eligible: 1,
  }],
}

const positions: Position[] = [{
  id: 'eth-position', symbol: 'ETH/USDT', market_type: 'perp', direction: 'long', quantity: 1,
  entry_price: 100, current_price: 105, unrealized_pnl: 5, realized_pnl: null,
  execution_mode: 'paper', strategy_id: null, status: 'open', stop_price: 90,
  take_profit_price: 120, close_reason: null, opened_at: '2026-08-13T00:00:00Z', closed_at: null,
  liquidation_price: 70,
}]

function makeClient() {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
  client.setQueryData(['universe-current'], universe)
  client.setQueryData(['market-snapshots'], [
    { id: 'btc-snapshot', symbol: 'BTC/USDT', market_type: 'spot', price: 62_000, volume_24h: 1, change_pct_24h: 2.4, snapshot_at: '2026-08-13T00:00:00Z' },
  ])
  client.setQueryData(['positions'], positions)
  client.setQueryData(['market-opportunity-instruments'], [
    { symbol: 'SOL/USDT', market: 'perp', total_count: 1, open_count: 1, latest_created_at: '2026-08-13T00:00:00Z', latest_status: 'open', latest_direction_bias: 'long' },
    { symbol: 'BTC/USDT', market: 'spot', total_count: 1, open_count: 1, latest_created_at: '2026-08-13T00:00:00Z', latest_status: 'open', latest_direction_bias: 'short' },
  ])
  client.setQueryData(['market-opportunities', 'BTC/USDT', 'spot'], [])
  client.setQueryData(['market-opportunities', 'ETH/USDT', 'perp'], [])
  client.setQueryData(['market-opportunities', 'SOL/USDT', 'perp'], [])
  client.setQueryData(['trades'], [])
  client.setQueryData(['opportunities'], [])
  client.setQueryData(['news'], [])
  client.setQueryData(['prediction-markets'], [])
  return client
}

let root: Root | undefined
afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  document.body.innerHTML = ''
})
function renderCharts() {
  const node = document.createElement('div')
  document.body.appendChild(node)
  root = createRoot(node)
  act(() => root!.render(<MemoryRouter initialEntries={['/charts']}><QueryClientProvider client={makeClient()}><Charts /></QueryClientProvider></MemoryRouter>))
  return node
}

function buttonContaining(node: ParentNode, text: string) {
  const button = [...node.querySelectorAll('button')].find(value => value.textContent?.includes(text)) as HTMLButtonElement | undefined
  expect(button, `button containing ${text}`).toBeTruthy()
  return button!
}


function input(element: HTMLInputElement, value: string) {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    setter?.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function click(element: HTMLElement) {
  act(() => element.click())
}

describe('Markets workbench', () => {
  it('renders decision context, grouped vertical navigation, chart controls, and secondary intelligence in order', () => {
    const node = renderCharts()
    const text = node.textContent || ''
    const chart = node.querySelector('[data-testid="price-chart"]')!
    const macro = [...node.querySelectorAll('div')].find(element => element.textContent === 'Macro context')!
    const tabs = node.querySelector('[role="tablist"]')!
    expect(text.indexOf('Markets')).toBeLessThan(text.indexOf('Choose instrument'))
    expect(text.indexOf('Find instrument')).toBeLessThan(text.indexOf('Chart canvas'))
    expect(chart.compareDocumentPosition(macro) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(macro.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(text).toContain('Universe1')
    expect(text).toContain('Positions1')
    expect(text).toContain('Opportunities2')
    expect(text).toContain('Estimated from visible OHLCV · 70% value area')
    expect(node.querySelectorAll('.market-overlay-controls input')).toHaveLength(4)
    expect([...node.querySelectorAll('.market-instrument-form label')].map(label => label.textContent)).toEqual(expect.arrayContaining(['Symbol', 'Exchange', expect.stringContaining('Market')]))
    expect(text).not.toMatch(/Hyperliquid liquidation wall|Position X|\bBuy\b|\bSell\b/)
  })

  it('filters locally, switches exact identity, and marks duplicate identity rows active', () => {
    const node = renderCharts()
    const search = node.querySelector('.market-instrument-rail input[type="search"]') as HTMLInputElement
    const chart = node.querySelector('[data-testid="price-chart"]')
    input(search, 'eth perp')
    expect(node.querySelectorAll('.market-instrument-rail .market-instrument-row')).toHaveLength(1)
    expect(node.textContent).toContain('ETHUSDT')
    input(search, '')
    click(buttonContaining(node.querySelector('.market-instrument-rail')!, 'ETHUSDT'))
    expect(chart?.getAttribute('data-symbol')).toBe('ETH/USDT')
    expect(chart?.getAttribute('data-market')).toBe('perp')
    expect(buttonContaining(node.querySelector('.market-instrument-rail')!, 'ETHUSDT').getAttribute('aria-pressed')).toBe('true')
  })

  it('focus-manages the floating picker and closes it after selecting an instrument', () => {
    const node = renderCharts()
    const trigger = buttonContaining(node, 'Choose instrument')
    trigger.focus()
    click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    let dialog = node.querySelector('[role="dialog"]') as HTMLElement
    expect(dialog).toBeTruthy()
    click(buttonContaining(dialog, 'Close'))
    expect(node.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(trigger)

    click(trigger)
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(node.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(trigger)

    click(trigger)
    dialog = node.querySelector('[role="dialog"]') as HTMLElement
    click(buttonContaining(dialog, 'SOLUSDT'))
    expect(node.querySelector('[role="dialog"]')).toBeNull()
    expect(node.querySelector('[data-testid="price-chart"]')?.getAttribute('data-symbol')).toBe('SOL/USDT')

    click(trigger)
    click(node.querySelector('.market-instrument-backdrop') as HTMLElement)
    expect(node.querySelector('[role="dialog"]')).toBeNull()
  })
})

// @vitest-environment happy-dom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Position, UniverseCurrentResponse } from '../types'
import type * as PriceChartModel from '../charts/priceChartModel'
import type { ChartSettings } from '../charts/priceChartModel'

vi.mock('../charts/PriceChart', async () => {
  const model = await vi.importActual<typeof PriceChartModel>('../charts/priceChartModel')
  return {
    DEFAULT_CHART_SETTINGS: model.DEFAULT_CHART_SETTINGS,
    MAIN_INDICATOR_IDS: model.MAIN_INDICATOR_IDS,
    SUBCHART_INDICATOR_IDS: model.SUBCHART_INDICATOR_IDS,
    MAX_INDICATORS_PER_ZONE: model.MAX_INDICATORS_PER_ZONE,
    default: ({ symbol, marketType, settings, timeframe }: { symbol: string; marketType: string; timeframe?: string; settings: ChartSettings }) => (
      <div
        data-testid="price-chart"
        data-symbol={symbol}
        data-market={marketType}
        data-timeframe={timeframe}
        data-main-indicators={settings.indicators.main.join(',')}
        data-subchart-indicators={settings.indicators.subcharts.join(',')}
        data-scale-mode={settings.scale.mode}
        data-timezone={settings.view.timezone}
      >Chart canvas</div>
    ),
  }
})
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
  client.setQueryData(['config-default-exchange'], { exchange: 'bitget' })
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
    expect(node.querySelector('.market-chart-legend')).toBeNull()
    expect(node.querySelectorAll('.market-overlay-controls input')).toHaveLength(0)
    expect(text).toContain('Chart settings')
    expect(text).not.toMatch(/KLineChart|TradingView Lite/)
    expect(text).not.toMatch(/Hyperliquid liquidation wall|Position X|\bBuy\b|\bSell\b/)
  })

  it('opens chart settings as a centered modal covering View, Price scale, Overlays, Main chart indicators, and Subcharts', () => {
    const node = renderCharts()
    click(buttonContaining(node, 'Chart settings'))
    const modal = node.querySelector('.chart-settings-modal') as HTMLElement
    expect(modal).toBeTruthy()
    expect(modal.getAttribute('role')).toBe('dialog')
    expect(modal.getAttribute('aria-modal')).toBe('true')
    const titleId = modal.getAttribute('aria-labelledby')
    expect(titleId && modal.querySelector(`#${titleId}`)?.textContent).toBe('Chart settings')
    const selects = [...modal.querySelectorAll('select')]
    expect(selects.map(select => select.closest('label')?.querySelector('span')?.textContent)).toEqual([
      'Crosshair', 'Timezone', 'Scale',
    ])
    expect([...modal.querySelectorAll('input[type="checkbox"]')].map(input => input.closest('label')?.textContent?.trim())).toEqual([
      'Horizontal grid', 'Vertical grid', 'Auto scale', 'Show right-axis labels',
      'Volume', 'Estimated profile', 'Position levels', 'Signals',
      'EMA 20', 'EMA 50', 'Bollinger Bands 20/2',
      'RSI 14', 'MACD 12/26/9', 'Stochastic 14/3/3', 'ATR 14',
    ])
    const titles = [...modal.querySelectorAll('.chart-settings-section-title')].map(node => node.textContent?.trim())
    expect(titles).toContain('Main chart indicators · 0/3')
    expect(titles).toContain('Subcharts · 1/3')
    expect(buttonContaining(modal, 'Restore defaults')).toBeTruthy()
  })

  const modalCheckbox = (modal: Element, label: string) =>
    [...modal.querySelectorAll('input[type="checkbox"]')].find(
      input => input.closest('label')?.textContent?.trim() === label,
    ) as HTMLInputElement | undefined

  it('drives the two-zone indicator selector, enforces the 3/3 cap, and persists arrays through localStorage', () => {
    window.localStorage.removeItem('aletheia.charts.settings.v1')
    const node = renderCharts()
    const chart = node.querySelector('[data-testid="price-chart"]')!
    expect(chart.getAttribute('data-main-indicators')).toBe('')
    expect(chart.getAttribute('data-subchart-indicators')).toBe('rsi')
    click(buttonContaining(node, 'Chart settings'))
    const modal = node.querySelector('.chart-settings-modal') as HTMLElement

    // Subcharts: RSI already on; add MACD then Stochastic to reach 3/3; ATR disabled.
    act(() => modalCheckbox(modal, 'MACD 12/26/9')!.click())
    act(() => modalCheckbox(modal, 'Stochastic 14/3/3')!.click())
    expect(chart.getAttribute('data-subchart-indicators')).toBe('rsi,macd,stochastic')
    expect(modalCheckbox(modal, 'ATR 14')!.disabled).toBe(true)

    // Remove RSI → ATR becomes enabled; enabling ATR gives macd,stochastic,atr in insertion order.
    act(() => modalCheckbox(modal, 'RSI 14')!.click())
    expect(modalCheckbox(modal, 'ATR 14')!.disabled).toBe(false)
    act(() => modalCheckbox(modal, 'ATR 14')!.click())
    expect(chart.getAttribute('data-subchart-indicators')).toBe('macd,stochastic,atr')

    // Main indicators are an independent 3/3 zone.
    act(() => modalCheckbox(modal, 'EMA 20')!.click())
    act(() => modalCheckbox(modal, 'EMA 50')!.click())
    act(() => modalCheckbox(modal, 'Bollinger Bands 20/2')!.click())
    expect(chart.getAttribute('data-main-indicators')).toBe('ema20,ema50,bollinger')
    expect(chart.getAttribute('data-subchart-indicators')).toBe('macd,stochastic,atr')

    const stored = JSON.parse(window.localStorage.getItem('aletheia.charts.settings.v1') || '{}')
    expect(stored.indicators.main).toEqual(['ema20', 'ema50', 'bollinger'])
    expect(stored.indicators.subcharts).toEqual(['macd', 'stochastic', 'atr'])

    click(buttonContaining(modal, 'Restore defaults'))
    expect(chart.getAttribute('data-main-indicators')).toBe('')
    expect(chart.getAttribute('data-subchart-indicators')).toBe('rsi')
  })

  it('migrates legacy rsi.enabled localStorage into the new subcharts array', () => {
    window.localStorage.setItem(
      'aletheia.charts.settings.v1',
      JSON.stringify({ indicators: { rsi: { enabled: false, length: 14 } } }),
    )
    let node = renderCharts()
    expect(node.querySelector('[data-testid="price-chart"]')?.getAttribute('data-subchart-indicators')).toBe('')
    act(() => root?.unmount())
    root = undefined
    document.body.innerHTML = ''

    window.localStorage.setItem(
      'aletheia.charts.settings.v1',
      JSON.stringify({ indicators: { rsi: { enabled: true, length: 14 } } }),
    )
    node = renderCharts()
    expect(node.querySelector('[data-testid="price-chart"]')?.getAttribute('data-subchart-indicators')).toBe('rsi')
  })

  it('collapses instrument rail groups independently, hides members, and restores state after search', () => {
    const node = renderCharts()
    const rail = node.querySelector('.market-instrument-rail') as HTMLElement
    const groupTitle = (title: string) =>
      [...rail.querySelectorAll('button.market-instrument-group-title')].find(
        button => button.textContent?.includes(title),
      ) as HTMLButtonElement

    const universeHeader = groupTitle('Universe')
    const positionsHeader = groupTitle('Positions')
    expect(universeHeader.getAttribute('aria-expanded')).toBe('true')
    expect(positionsHeader.getAttribute('aria-expanded')).toBe('true')

    // Collapse Universe; positions still expanded.
    click(universeHeader)
    const universeBodyId = universeHeader.getAttribute('aria-controls')!
    const universeBody = rail.querySelector(`#${universeBodyId}`) as HTMLElement
    expect(groupTitle('Universe').getAttribute('aria-expanded')).toBe('false')
    expect(universeBody.hidden).toBe(true)
    expect(groupTitle('Positions').getAttribute('aria-expanded')).toBe('true')

    // Search enters single Search results mode; Universe still collapsed after clearing.
    const search = rail.querySelector('input[type="search"]') as HTMLInputElement
    input(search, 'BTC')
    expect(rail.textContent).toContain('Search results')
    input(search, '')
    expect(groupTitle('Universe').getAttribute('aria-expanded')).toBe('false')

    // Re-expand Universe.
    click(groupTitle('Universe'))
    expect(groupTitle('Universe').getAttribute('aria-expanded')).toBe('true')
  })

  it('replaces rail groups with inline search results, no popup, and restores groups after selection', () => {
    const node = renderCharts()
    const rail = node.querySelector('.market-instrument-rail') as HTMLElement
    const search = rail.querySelector('input[type="search"]') as HTMLInputElement
    const chart = node.querySelector('[data-testid="price-chart"]')

    act(() => search.focus())
    expect(document.querySelector('.market-instrument-popup')).toBeNull()
    expect(rail.textContent).toContain('Universe')
    expect(rail.textContent).toContain('Positions')
    expect(rail.textContent).toContain('Opportunities')

    input(search, 'BTC')
    expect(document.querySelector('.market-instrument-popup')).toBeNull()
    expect(rail.textContent).toContain('Search results')
    const firstRow = rail.querySelector('.market-instrument-row') as HTMLButtonElement
    expect(firstRow).toBeTruthy()
    const rowText = firstRow.textContent || ''
    expect(rowText).toMatch(/BTCUSDT/)
    expect(rowText).toMatch(/perp|spot/)
    expect(rowText).toContain('Bitcoin')
    expect(rowText).toContain('bitget')

    click(firstRow)
    expect(chart?.getAttribute('data-symbol')).toBe('BTC/USDT:USDT')
    expect(chart?.getAttribute('data-market')).toBe('perp')
    expect(search.value).toBe('')
    expect(rail.textContent).toContain('Universe')
    expect(rail.textContent).not.toContain('Search results')
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

  it('opens the timeframe overflow menu with grouped periods and applies the chosen value', () => {
    const node = renderCharts()
    const overflow = node.querySelector('.market-tf-overflow') as HTMLButtonElement
    expect(overflow).toBeTruthy()
    click(overflow)
    const menu = document.querySelector('[role="menu"]') as HTMLElement
    expect(menu).toBeTruthy()
    const groupTitles = [...menu.querySelectorAll('.market-tf-menu-group-title')].map(node => node.textContent)
    expect(groupTitles).toEqual(['Minutes', 'Hours', 'Days'])
    const items = [...menu.querySelectorAll('.market-tf-menu-item')].map(item => item.textContent)
    expect(items).toContain('1m')
    expect(items).toContain('4h')
    click([...menu.querySelectorAll('.market-tf-menu-item')].find(node => node.textContent === '1m') as HTMLButtonElement)
    expect(document.querySelector('[role="menu"]')).toBeNull()
    expect(node.querySelector('[data-testid="price-chart"]')?.getAttribute('data-timeframe')).toBe('1m')
    expect((node.querySelector('.market-tf-overflow') as HTMLElement).textContent).toBe('1m')
  })

})

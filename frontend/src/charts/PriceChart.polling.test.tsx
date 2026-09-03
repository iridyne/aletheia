// @vitest-environment happy-dom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const series = () => ({
    applyOptions: vi.fn(), createPriceLine: vi.fn(() => ({})), priceScale: () => ({ applyOptions: vi.fn() }),
    removePriceLine: vi.fn(), setData: vi.fn(), setMarkers: vi.fn(),
  })
  const timeScale = {
    fitContent: vi.fn(), getVisibleLogicalRange: vi.fn(() => ({ from: 20, to: 30 })),
    setVisibleLogicalRange: vi.fn(), subscribeVisibleLogicalRangeChange: vi.fn(),
    unsubscribeVisibleLogicalRangeChange: vi.fn(),
  }
  return {
    createChart: vi.fn(() => ({
      addCandlestickSeries: vi.fn(series), addHistogramSeries: vi.fn(series), addLineSeries: vi.fn(series),
      applyOptions: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }), remove: vi.fn(),
      subscribeClick: vi.fn(), timeScale: () => timeScale, unsubscribeClick: vi.fn(),
    })),
    getOhlcv: vi.fn(),
    getOhlcvWithMeta: vi.fn(),
  }
})

vi.mock('lightweight-charts', () => ({
  createChart: mocks.createChart,
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Magnet: 1, Normal: 0, Hidden: 2 },
  LineStyle: { Solid: 0, Dashed: 2, Dotted: 1 },
  PriceScaleMode: { Normal: 0, Logarithmic: 1, Percentage: 2, IndexedTo100: 3 },
}))

vi.mock('../api/client', () => ({
  api: { getOhlcv: mocks.getOhlcv, getOhlcvWithMeta: mocks.getOhlcvWithMeta },
}))

import PriceChart, { DEFAULT_CHART_SETTINGS, type Bar } from './PriceChart'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const response = {
  candles: [{ time: 1, open: 1, high: 1, low: 1, close: 1, volume: 1 } satisfies Bar],
  meta: { source: 'test', instrument: 'BTC/USDT', asOf: null, ageMs: 0, stale: false },
}
const settings = {
  ...DEFAULT_CHART_SETTINGS,
  indicators: { ...DEFAULT_CHART_SETTINGS.indicators, subcharts: [] },
}

let host: HTMLDivElement
let root: Root

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  vi.useRealTimers()
  vi.clearAllMocks()
})

it.each(['resolve', 'reject'] as const)('single-flights live polls and unlocks after %s', async outcome => {
  vi.useFakeTimers()
  let settle!: (value: typeof response) => void
  let fail!: (reason: Error) => void
  const pending = new Promise<typeof response>((resolve, reject) => { settle = resolve; fail = reject })
  mocks.getOhlcvWithMeta.mockResolvedValue(response).mockResolvedValueOnce(response).mockReturnValueOnce(pending)
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)

  await act(async () => root.render(<PriceChart symbol="BTC/USDT" settings={settings} />))
  await act(async () => { vi.advanceTimersByTime(20_000) })
  expect(mocks.getOhlcvWithMeta).toHaveBeenCalledTimes(2)

  if (outcome === 'resolve') settle(response)
  else fail(new Error('temporary poll failure'))
  await act(async () => {})
  await act(async () => { vi.advanceTimersByTime(10_000) })
  expect(mocks.getOhlcvWithMeta).toHaveBeenCalledTimes(3)
})

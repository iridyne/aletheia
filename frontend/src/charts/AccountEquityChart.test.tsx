// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountEquityChart from './AccountEquityChart'

const mocks = vi.hoisted(() => ({
  createChart: vi.fn(),
  rawSetData: vi.fn(),
  rawUpdate: vi.fn(),
  rawSetMarkers: vi.fn(),
  adjustedSetData: vi.fn(),
  adjustedUpdate: vi.fn(),
  remove: vi.fn(),
  fitContent: vi.fn(),
}))

vi.mock('lightweight-charts', () => ({
  ColorType: { Solid: 'Solid' },
  createChart: mocks.createChart,
}))

describe('AccountEquityChart', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.createChart.mockReturnValue({
      addAreaSeries: () => ({
        setData: mocks.rawSetData,
        update: mocks.rawUpdate,
        setMarkers: mocks.rawSetMarkers,
      }),
      addLineSeries: () => ({
        setData: mocks.adjustedSetData,
        update: mocks.adjustedUpdate,
      }),
      timeScale: () => ({ fitContent: mocks.fitContent }),
      applyOptions: vi.fn(),
      remove: mocks.remove,
    })
  })

  it('keeps one chart instance and incrementally updates the provisional tail', async () => {
    const client = new QueryClient()
    client.setQueryData(['account-equity-history', 30], {
      source: 'local_paper_ledger',
      state: 'available',
      points: [{ time: '2026-08-07T12:00:00Z', equity_usdt: 100 }],
      adjusted_points: [],
      capital_flow_markers: [],
      as_of: '2026-08-07T12:00:00Z',
    })
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const renderChart = (equity: number) => (
      <QueryClientProvider client={client}>
        <AccountEquityChart
          days={30}
          currentPoint={{ time: '2026-08-07T12:00:10Z', equity_usdt: equity }}
        />
      </QueryClientProvider>
    )

    await act(async () => root.render(renderChart(101)))
    await act(async () => root.render(renderChart(102)))

    expect(mocks.createChart).toHaveBeenCalledTimes(1)
    expect(mocks.rawSetData).toHaveBeenCalledTimes(1)
    expect(mocks.rawUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ value: 102 }))

    await act(async () => root.unmount())
    expect(mocks.remove).toHaveBeenCalledTimes(1)
    container.remove()
  })
})

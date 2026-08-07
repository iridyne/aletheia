import { useEffect, useMemo, useRef } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { chartTheme } from './theme'
import { isoToEpochSeconds } from '../lib/format'

interface CurrentEquityPoint {
  time: string
  equity_usdt: number
}

interface Props {
  days?: number
  currentPoint?: CurrentEquityPoint | null
}

function uniquePoints(rows: LineData<Time>[]): LineData<Time>[] {
  const seen = new Set<number>()
  return rows.filter(row => {
    const time = Number(row.time)
    if (seen.has(time)) return false
    seen.add(time)
    return true
  })
}

/** The Overview's single user-facing account-value series. */
export default function AccountEquityChart({ days, currentPoint }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const rawSeriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const adjustedSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const historyLastTimeRef = useRef<number | null>(null)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['account-equity-history', days],
    queryFn: () => api.getAccountEquityHistory(days),
    // Durable history is normally invalidated by the portfolio_snapshots WS
    // fingerprint. This remains only as a low-cost fallback if WS is offline.
    refetchInterval: 60_000,
    staleTime: 58_000,
  })

  const rows = useMemo(() => uniquePoints((data?.points ?? [])
    .map(point => {
      const time = isoToEpochSeconds(point.time)
      return time === null ? null : { time: time as Time, value: point.equity_usdt }
    })
    .filter((row): row is LineData<Time> => row !== null)), [data?.points])

  const adjustedRows = useMemo(() => uniquePoints((data?.adjusted_points ?? [])
    .map(point => {
      const time = isoToEpochSeconds(point.time)
      return time === null ? null : { time: time as Time, value: point.adjusted_equity_usdt }
    })
    .filter((row): row is LineData<Time> => row !== null)), [data?.adjusted_points])

  const markers = useMemo(() => (data?.capital_flow_markers ?? [])
    .map(event => {
      const eventTime = isoToEpochSeconds(event.time)
      if (eventTime === null) return null
      const anchor = rows.find(row => Number(row.time) >= eventTime)
      if (!anchor) return null
      const deposit = event.event_type === 'deposit'
      return {
        time: anchor.time,
        position: deposit ? 'belowBar' as const : 'aboveBar' as const,
        color: deposit ? '#a3e635' : '#fb7185',
        shape: deposit ? 'arrowUp' as const : 'arrowDown' as const,
        text: `${deposit ? 'Deposit' : 'Withdrawal'} ${event.amount_usdt.toFixed(2)}`,
      }
    })
    .filter((marker): marker is NonNullable<typeof marker> => marker !== null), [data?.capital_flow_markers, rows])

  // Create one high-DPI Canvas chart for the component lifetime. Data refreshes
  // update the existing series instead of destroying the chart, resetting the
  // viewport, and fitting the whole range on every tick.
  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      layout: chartTheme.layout,
      grid: chartTheme.grid,
      width: containerRef.current.clientWidth,
      height: 220,
      timeScale: { timeVisible: true, rightOffset: 2 },
      rightPriceScale: { borderColor: chartTheme.borderColor },
      crosshair: { vertLine: { labelBackgroundColor: '#22d3ee' } },
    })
    const rawSeries = chart.addAreaSeries({
      lineColor: chartTheme.lineColor,
      topColor: chartTheme.areaTopColor,
      bottomColor: chartTheme.areaBottomColor,
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
    })
    const adjustedSeries = chart.addLineSeries({
      color: '#a3e635',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    chartRef.current = chart
    rawSeriesRef.current = rawSeries
    adjustedSeriesRef.current = adjustedSeries

    const onResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      rawSeriesRef.current = null
      adjustedSeriesRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [])

  // Full replacement is reserved for durable-history or selected-range changes.
  useEffect(() => {
    const rawSeries = rawSeriesRef.current
    const adjustedSeries = adjustedSeriesRef.current
    if (!rawSeries || !adjustedSeries) return
    rawSeries.setData(rows)
    rawSeries.setMarkers(markers)
    adjustedSeries.setData(markers.length > 0 ? adjustedRows : [])
    historyLastTimeRef.current = rows.length ? Number(rows[rows.length - 1].time) : null
    if (rows.length) chartRef.current?.timeScale().fitContent()
  }, [rows, adjustedRows, markers])

  // The current portfolio reading is provisional: it is rendered immediately
  // but not persisted here. The next durable snapshot replaces it naturally.
  useEffect(() => {
    const rawSeries = rawSeriesRef.current
    if (!rawSeries || !currentPoint || !Number.isFinite(currentPoint.equity_usdt)) return
    const observedTime = isoToEpochSeconds(currentPoint.time)
    if (observedTime === null) return
    const historyLast = historyLastTimeRef.current
    const tailTime = historyLast !== null && observedTime <= historyLast
      ? historyLast + 1
      : observedTime
    rawSeries.update({ time: tailTime as Time, value: currentPoint.equity_usdt })

    if (markers.length > 0 && adjustedSeriesRef.current) {
      const adjustedPoints = data?.adjusted_points ?? []
      const latestAdjustment = adjustedPoints.length
        ? adjustedPoints[adjustedPoints.length - 1].cumulative_net_flow_usdt
        : 0
      adjustedSeriesRef.current.update({
        time: tailTime as Time,
        value: currentPoint.equity_usdt - latestAdjustment,
      })
    }
    if (rows.length === 0) chartRef.current?.timeScale().fitContent()
  }, [currentPoint, data?.adjusted_points, markers.length, rows])

  const hasCurrentPoint = currentPoint != null && Number.isFinite(currentPoint.equity_usdt)
  const hasVisibleData = rows.length > 0 || hasCurrentPoint

  return (
    <div style={{ position: 'relative', minHeight: 220 }}>
      <div ref={containerRef} style={{ width: '100%' }} />
      {!hasVisibleData && isLoading && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0.5 }}>
          Loading account equity...
        </div>
      )}
      {!hasVisibleData && isError && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          <p className="hint" style={{ color: '#ff5d6c' }}>Failed to load account equity.</p>
          <button className="time-range-btn" onClick={() => refetch()}>Retry</button>
        </div>
      )}
      {!hasVisibleData && !isLoading && !isError && (
        <p className="hint">No account-equity reading is available yet.</p>
      )}
      {hasVisibleData && (data?.capital_flow_markers?.length ?? 0) > 0 && (
        <p className="hint" style={{ marginTop: 6 }}>
          Cyan: raw account equity · Green: cash-flow-adjusted equity
        </p>
      )}
    </div>
  )
}

import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { chartTheme } from './theme'
import { isoToEpochSeconds } from '../lib/format'

interface Props {
  days?: number
}

/** The Overview's single user-facing account-value series. */
export default function AccountEquityChart({ days }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['account-equity-history', days],
    queryFn: () => api.getAccountEquityHistory(days),
    refetchInterval: 60_000,
    staleTime: 58_000,
  })
  const rows = (data?.points ?? [])
    .map(point => {
      const time = isoToEpochSeconds(point.time)
      return time === null ? null : { time: time as Time, value: point.equity_usdt }
    })
    .filter((row): row is { time: Time; value: number } => row !== null)
  const adjustedRows = (data?.adjusted_points ?? [])
    .map(point => {
      const time = isoToEpochSeconds(point.time)
      return time === null ? null : { time: time as Time, value: point.adjusted_equity_usdt }
    })
    .filter((row): row is { time: Time; value: number } => row !== null)

  useEffect(() => {
    if (!containerRef.current || rows.length === 0) return
    const chart = createChart(containerRef.current, {
      layout: chartTheme.layout,
      grid: chartTheme.grid,
      width: containerRef.current.clientWidth,
      height: 220,
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart
    const series = chart.addLineSeries({ color: '#22d3ee', lineWidth: 2 })
    const seen = new Set<number>()
    series.setData(rows.filter(row => {
      const time = row.time as number
      if (seen.has(time)) return false
      seen.add(time)
      return true
    }))
    const markers = (data?.capital_flow_markers ?? [])
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
      .filter((marker): marker is NonNullable<typeof marker> => marker !== null)
    series.setMarkers(markers)
    if ((data?.capital_flow_markers?.length ?? 0) > 0) {
      const adjustedSeries = chart.addLineSeries({ color: '#a3e635', lineWidth: 2 })
      const adjustedSeen = new Set<number>()
      adjustedSeries.setData(adjustedRows.filter(row => {
        const time = row.time as number
        if (adjustedSeen.has(time)) return false
        adjustedSeen.add(time)
        return true
      }))
    }
    chart.timeScale().fitContent()
    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [rows, adjustedRows, data?.capital_flow_markers?.length])

  if (isLoading) return <div style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0.5 }}>Loading account equity...</div>
  if (isError) return (
    <div style={{ height: 220, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
      <p className="hint" style={{ color: '#ff5d6c' }}>Failed to load account equity.</p>
      <button className="time-range-btn" onClick={() => refetch()}>Retry</button>
    </div>
  )
  if (rows.length === 0) return <p className="hint">No account-equity snapshot yet. The curve starts only after the ledger has a complete account reading.</p>
  return (
    <div>
      {(data?.capital_flow_markers?.length ?? 0) > 0 && (
        <p className="hint" style={{ marginBottom: 6 }}>
          Cyan: raw account equity · Green: cash-flow-adjusted equity
        </p>
      )}
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}

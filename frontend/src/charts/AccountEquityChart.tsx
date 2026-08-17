import { useEffect, useRef } from 'react'
import { createChart, PriceScaleMode, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { chartTheme } from './theme'
import { fmtNum, fmtSigned, isoToEpochSeconds } from '../lib/format'

interface Props {
  days?: number
}

/** The Overview's single user-facing account-value series. */
export default function AccountEquityChart({ days }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['account-equity-history', days],
    queryFn: ({ signal }) => api.getAccountEquityHistory(days, 500, signal),
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
      rightPriceScale: { mode: PriceScaleMode.Logarithmic },
    })
    const series = chart.addLineSeries({ color: chartTheme.accountEquityRaw, lineWidth: 2 })
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
          id: event.id,
          time: anchor.time,
          position: deposit ? 'belowBar' as const : 'aboveBar' as const,
          color: deposit ? chartTheme.capitalDeposit : chartTheme.capitalWithdrawal,
          shape: deposit ? 'arrowUp' as const : 'arrowDown' as const,
          size: 1,
        }
      })
      .filter((marker): marker is NonNullable<typeof marker> => marker !== null)
    series.setMarkers(markers)
    const markerDetails = new Map((data?.capital_flow_markers ?? []).map(event => [event.id, event]))
    chart.subscribeCrosshairMove(param => {
      const tooltip = tooltipRef.current
      const event = markerDetails.get(String(param.hoveredObjectId ?? ''))
      if (!tooltip || !event || !param.point) {
        if (tooltip) tooltip.style.display = 'none'
        return
      }
      const label = event.change_type === 'manual_set_balance'
        ? 'Balance set'
        : event.signed_amount_usdt > 0 ? 'Balance increase' : 'Balance decrease'
      const balance = event.balance_before_usdt == null || event.balance_after_usdt == null
        ? ''
        : `\n${fmtNum(event.balance_before_usdt)} → ${fmtNum(event.balance_after_usdt)} USDT`
      tooltip.textContent = `${label}: ${fmtSigned(event.signed_amount_usdt)} USDT${balance}${event.note ? `\n${event.note}` : ''}`
      tooltip.style.display = 'block'
      tooltip.style.left = `${Math.max(8, Math.min(param.point.x + 12, containerRef.current!.clientWidth - 230))}px`
      tooltip.style.top = `${Math.max(8, param.point.y - 54)}px`
    })
    if ((data?.capital_flow_markers?.length ?? 0) > 0) {
      const adjustedSeries = chart.addLineSeries({ color: chartTheme.accountEquityAdjusted, lineWidth: 2 })
      const adjustedSeen = new Set<number>()
      adjustedSeries.setData(adjustedRows.filter(row => {
        const time = row.time as number
        if (adjustedSeen.has(time)) return false
        adjustedSeen.add(time)
        return true
      }))
    }
    chart.timeScale().fitContent()
    const resizeObserver = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width
      if (width) chart.applyOptions({ width })
    })
    resizeObserver.observe(containerRef.current)
    return () => { resizeObserver.disconnect(); chart.remove() }
  }, [rows, adjustedRows, data?.capital_flow_markers])

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
      <div style={{ position: 'relative' }}>
        <div ref={containerRef} style={{ width: '100%' }} />
        <div ref={tooltipRef} role="tooltip" style={{
          display: 'none', position: 'absolute', zIndex: 2, width: 210,
          padding: '8px 10px', border: `1px solid ${chartTheme.borderColor}`,
          borderRadius: 6, background: '#0d1117', color: '#e6edf3',
          fontSize: 12, lineHeight: 1.45, whiteSpace: 'pre-line', pointerEvents: 'none',
        }} />
      </div>
    </div>
  )
}

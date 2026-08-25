import { useEffect, useMemo, useRef } from 'react'
import { createChart, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { chartTheme } from './theme'
import { isoToEpochSeconds } from '../lib/format'

interface Props {
  days?: number
  // #1939: scope the curve to the selected execution_mode so the realized
  // series never combines paper/demo/live populations under one label.
  executionMode?: string
}

function toRows(points: ReadonlyArray<{ time: string; cumulative_pnl: number }>) {
  return points
    .map(p => ({ time: isoToEpochSeconds(p.time), value: p.cumulative_pnl }))
    .filter((r): r is { time: number; value: number } => r.time !== null)
    .map(r => ({ time: r.time as Time, value: r.value }))
}

export default function PnLChart({ days, executionMode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['equity-curve', days, executionMode ?? 'all'],
    queryFn: () => api.getEquityCurve(days, executionMode),
    refetchInterval: 10_000,
  })

  const genuineRows = useMemo(() => toRows(data?.genuine_points ?? []), [data?.genuine_points])
  const hasData = genuineRows.length > 0

  useEffect(() => {
    const container = containerRef.current
    if (!hasData || !container) return
    const chart = createChart(container, {
      layout: chartTheme.layout,
      grid: chartTheme.grid,
      width: container.clientWidth,
      height: 220,
      timeScale: { timeVisible: true },
    })

    const seen = new Set<number>()
    const unique = (rows: typeof genuineRows) =>
      rows.filter(r => { const k = r.time as number; if (seen.has(k)) return false; seen.add(k); return true })

    const series = chart.addBaselineSeries({
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#2ee6a8',
      topFillColor1: 'rgba(46, 230, 168, 0.18)',
      topFillColor2: 'rgba(46, 230, 168, 0.02)',
      bottomLineColor: '#ff5d6c',
      bottomFillColor1: 'rgba(255, 93, 108, 0.02)',
      bottomFillColor2: 'rgba(255, 93, 108, 0.18)',
      lineWidth: 2,
    })
    series.setData(unique(genuineRows))
    chart.timeScale().fitContent()

    const resizeObserver = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width
      if (width) chart.applyOptions({ width })
    })
    resizeObserver.observe(container)
    return () => { resizeObserver.disconnect(); chart.remove() }
  }, [data, days, genuineRows, hasData])

  if (isLoading) return <div className="skeleton-bar" style={{ height: 220, width: '100%', borderRadius: 6 }} />

  if (isError) {
    return (
      <div style={{ height: 220, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
        <p className="hint" style={{ color: '#ff5d6c' }}>Failed to load PnL curve.</p>
        <button className="time-range-btn" onClick={() => refetch()}>Retry</button>
      </div>
    )
  }

  return (
    <div>
      {hasData
        ? <div ref={containerRef} style={{ width: '100%' }} />
        : <p className="hint">No verified strategy outcomes yet. Raw account events may still be excluded or lack economic evidence.</p>}
    </div>
  )
}

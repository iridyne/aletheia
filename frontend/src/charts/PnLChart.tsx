import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { chartTheme } from './theme'
import { isoToEpochSeconds } from '../lib/format'

interface Props {
  days?: number
}

export default function PnLChart({ days }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['equity-curve', days],
    queryFn: () => api.getEquityCurve(days),
  })

  const points = data?.points ?? []
  const currentUnrealized = data?.current_unrealized ?? 0

  // Backend closed_at is tz-aware UTC (`...+00:00`). isoToEpochSeconds normalizes
  // it correctly — the old inline `endsWith('Z') ? : +'Z'` turned `+00:00` into an
  // invalid `+00:00Z` → NaN → every point dropped → permanent "no data".
  const rows = points
    .map(p => ({ time: isoToEpochSeconds(p.time), value: p.cumulative_pnl }))
    .filter((r): r is { time: number; value: number } => r.time !== null)
    .map(r => ({ time: r.time as Time, value: r.value }))

  // Append current unrealized as the latest point if there are open positions
  const chartData = currentUnrealized !== 0 && rows.length > 0
    ? [...rows, { time: (Math.floor(Date.now() / 1000)) as Time, value: rows[rows.length - 1].value + currentUnrealized }]
    : rows
  const hasData = chartData.length > 0

  useEffect(() => {
    // No data → the container div isn't rendered (see below), so don't create a
    // chart. This also avoids leaving an empty 220px canvas as dead whitespace.
    if (!hasData || !containerRef.current) return
    const chart = createChart(containerRef.current, {
      layout: chartTheme.layout,
      grid: chartTheme.grid,
      width: containerRef.current.clientWidth,
      height: 220,
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart

    const seen = new Set<number>()
    const unique = chartData.filter(r => { const k = r.time as number; if (seen.has(k)) return false; seen.add(k); return true })

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
    series.setData(unique)
    chart.timeScale().fitContent()

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [chartData.length, days, hasData])

  if (isLoading) {
    return <div style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0.5 }}>Loading PnL curve...</div>
  }

  // Distinguish a failed request from genuinely-empty data. Without this, a
  // network/proxy failure (data === undefined) silently renders the same
  // "no trades" empty state as a real zero-trade account — masking an error
  // as a benign empty curve. See feedback: onError must always be handled.
  if (isError) {
    return (
      <div style={{ height: 220, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
        <p className="hint" style={{ color: '#ff5d6c' }}>Failed to load PnL curve.</p>
        <button className="time-range-btn" onClick={() => refetch()}>Retry</button>
      </div>
    )
  }

  // Empty state: just one line, no chart container — no 220px dead whitespace.
  if (!hasData) {
    return <p className="hint">No trades closed yet — curve appears after the first position closes.</p>
  }

  // Caveat must stay honest: the line is cumulative *realized* PnL, but when
  // positions are open we append one extra end point that folds in current
  // unrealized PnL (see chartData above) — so the last point is realized+open,
  // not pure realized. Spell that out instead of labelling the whole thing "realized".
  const realizedTxt = data?.total_realized !== undefined
    ? `${data.total_realized >= 0 ? '+' : ''}${data.total_realized.toFixed(2)} USDT`
    : ''
  const openTxt = currentUnrealized !== 0
    ? ` · last point incl. open ${currentUnrealized >= 0 ? '+' : ''}${currentUnrealized.toFixed(2)}`
    : ''
  return (
    <div>
      <p className="metric-caveat" style={{ marginBottom: '4px' }}>
        cumulative realized PnL (trade-by-trade){realizedTxt && ` · ${realizedTxt}`}{openTxt}
      </p>
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}

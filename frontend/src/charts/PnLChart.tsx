import { useEffect, useRef, useState } from 'react'
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
    refetchInterval: 10_000,
  })

  const rawPoints = data?.raw_recorded_points ?? []
  const genuinePoints = data?.genuine_points ?? []
  const currentUnrealized = data?.current_unrealized ?? 0
  const excludedOutcomeCount = data?.excluded_outcome_count ?? 0

  // Backend closed_at is tz-aware UTC (`...+00:00`). isoToEpochSeconds normalizes
  // it correctly — the old inline `endsWith('Z') ? : +'Z'` turned `+00:00` into an
  // invalid `+00:00Z` → NaN → every point dropped → permanent "no data".
  const toRows = (points: typeof rawPoints) => points
    .map(p => ({ time: isoToEpochSeconds(p.time), value: p.cumulative_pnl }))
    .filter((r): r is { time: number; value: number } => r.time !== null)
    .map(r => ({ time: r.time as Time, value: r.value }))
  const rawRows = toRows(rawPoints)
  const genuineRows = toRows(genuinePoints)
  const hasData = rawRows.length > 0 || genuineRows.length > 0

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

    const unique = (rows: typeof rawRows) => {
      const seen = new Set<number>()
      return rows.filter(r => { const k = r.time as number; if (seen.has(k)) return false; seen.add(k); return true })
    }

    if (rawRows.length > 0) {
      const rawSeries = chart.addLineSeries({ color: '#7b8494', lineWidth: 1 })
      rawSeries.setData(unique(rawRows))
    }

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

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [data, days, hasData])

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

  const realizedTxt = data?.total_realized != null
    ? `${data.total_realized >= 0 ? '+' : ''}${data.total_realized.toFixed(2)} USDT`
    : 'unavailable'
  const [hintOpen, setHintOpen] = useState(false)
  const showHint = rawRows.length > 0 && genuineRows.length === 0
  const hintText = `No verified strategy outcomes yet. ${excludedOutcomeCount} recorded closes do not meet the current verification requirements; the gray line remains raw account history only.`

  return (
    <div>
      <p className="metric-caveat" style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{ color: '#7b8494' }}>Raw recorded account events</span>
        {' · '}
        <span style={{ color: '#2ee6a8' }}>Verified strategy curve</span>
        {showHint && (
          <span
            style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}
            onMouseEnter={() => setHintOpen(true)}
            onMouseLeave={() => setHintOpen(false)}
          >
            <span style={{
              cursor: 'default', color: 'var(--faint)', fontSize: '0.68rem',
              lineHeight: 1, border: '1px solid var(--border)', borderRadius: '50%',
              width: '14px', height: '14px', display: 'inline-flex', alignItems: 'center',
              justifyContent: 'center', fontFamily: 'var(--mono)',
            }}>i</span>
            {hintOpen && (
              <span style={{
                position: 'absolute', bottom: 'calc(100% + 6px)', left: '50%',
                transform: 'translateX(-50%)',
                background: 'var(--surface-2)', border: '1px solid var(--border-2)',
                borderRadius: 'var(--r-sm)', padding: '6px 10px',
                minWidth: '260px', maxWidth: '340px',
                boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
                color: 'var(--muted)', fontSize: '0.72rem', lineHeight: '1.4',
                whiteSpace: 'normal', zIndex: 50, pointerEvents: 'none',
              }}>
                {hintText}
              </span>
            )}
          </span>
        )}
      </p>
      <p className="metric-caveat" style={{ marginBottom: '4px' }}>
        verified realized: {realizedTxt} · current unrealized (separate): {currentUnrealized >= 0 ? '+' : ''}{currentUnrealized.toFixed(2)} USDT
      </p>
      {hasData
        ? <div ref={containerRef} style={{ width: '100%' }} />
        : <p className="hint">No verified strategy outcomes yet. Raw account events may still be excluded or lack economic evidence.</p>}
    </div>
  )
}

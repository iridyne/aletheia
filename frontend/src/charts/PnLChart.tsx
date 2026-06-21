import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { chartTheme } from './theme'

interface Props {
  days?: number
}

export default function PnLChart({ days }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const { data = [] } = useQuery({
    queryKey: ['portfolio-history', days],
    queryFn: () => api.getPortfolioHistory(days),
  })

  const allRows = data as any[]
  const hasExchangeData = allRows.some((s: any) => s.exchange_equity_usdt != null)

  const rows = allRows
    .filter((s: any) => !hasExchangeData || s.exchange_equity_usdt != null)
    .map((s: any) => {
      const raw = s.snapshot_at ?? ''
      const t = Math.floor(new Date(raw.endsWith('Z') ? raw : raw.replace(/\+00:00$/, 'Z')).getTime() / 1000)
      const value = s.exchange_equity_usdt != null
        ? s.exchange_equity_usdt
        : (s.unrealized_pnl ?? 0) + (s.realized_pnl ?? 0)
      return { time: t as Time, value }
    })
    .filter(r => !Number.isNaN(r.time))

  const sourceLabel = hasExchangeData ? 'exchange balance' : 'paper PnL (internal)'

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      layout: chartTheme.layout,
      grid: chartTheme.grid,
      width: containerRef.current.clientWidth,
      height: 220,
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart

    if (rows.length) {
      const seen = new Set<number>()
      const unique = rows.filter(r => { const k = r.time as number; if (seen.has(k)) return false; seen.add(k); return true })

      const baseValue = unique[0]?.value ?? 0
      const series = chart.addBaselineSeries({
        baseValue: { type: 'price', price: baseValue },
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
    }

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [rows.length, data])

  return (
    <div>
      {rows.length === 0
        ? <p className="hint">No portfolio history yet — first snapshot appears within 10 min.</p>
        : <p className="metric-caveat" style={{ marginBottom: '4px' }}>{sourceLabel}</p>
      }
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}

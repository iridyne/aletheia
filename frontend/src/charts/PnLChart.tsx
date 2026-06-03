import { useEffect, useRef } from 'react'
import { createChart, ColorType, type IChartApi, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function PnLChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const { data = [] } = useQuery({ queryKey: ['portfolio-history'], queryFn: api.getPortfolioHistory })

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: '#1a1f26' }, textColor: '#e8e6e3' },
      grid: { vertLines: { color: '#2a2f35' }, horzLines: { color: '#2a2f35' } },
      width: containerRef.current.clientWidth,
      height: 220,
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart
    const series = chart.addLineSeries({ color: '#58a6ff', lineWidth: 2 })

    const rows = (data as any[])
      .map((s: any) => {
        const t = Math.floor(new Date(s.snapshot_at + 'Z').getTime() / 1000)
        return { time: t as Time, value: (s.unrealized_pnl ?? 0) + (s.realized_pnl ?? 0) }
      })
      .filter(r => !Number.isNaN(r.time))
      .sort((a, b) => (a.time as number) - (b.time as number))

    if (rows.length) {
      // de-dup identical timestamps (lightweight-charts requires strictly ascending)
      const seen = new Set<number>()
      const unique = rows.filter(r => { const k = r.time as number; if (seen.has(k)) return false; seen.add(k); return true })
      series.setData(unique)
      chart.timeScale().fitContent()
    }

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [data])

  return (
    <div>
      {(data as any[]).length === 0 && <p className="hint">No portfolio history yet.</p>}
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}

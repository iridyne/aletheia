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
  const { data, isLoading } = useQuery({
    queryKey: ['equity-curve', days],
    queryFn: () => api.getEquityCurve(days),
  })

  const points = data?.points ?? []
  const currentUnrealized = data?.current_unrealized ?? 0

  const rows = points.map(p => {
    const t = Math.floor(new Date(p.time.endsWith('Z') ? p.time : p.time + 'Z').getTime() / 1000)
    return { time: t as Time, value: p.cumulative_pnl }
  }).filter(r => !Number.isNaN(r.time))

  // Append current unrealized as the latest point if there are open positions
  const chartData = currentUnrealized !== 0 && rows.length > 0
    ? [...rows, { time: (Math.floor(Date.now() / 1000)) as Time, value: rows[rows.length - 1].value + currentUnrealized }]
    : rows

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

    if (chartData.length > 0) {
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
    }

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [chartData.length, days])

  if (isLoading) {
    return <div style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0.5 }}>Loading equity curve...</div>
  }

  return (
    <div>
      {chartData.length === 0
        ? <p className="hint">No trades closed yet — curve appears after the first position closes.</p>
        : <p className="metric-caveat" style={{ marginBottom: '4px' }}>
            cumulative realized PnL (trade-by-trade) · {data?.total_realized !== undefined ? `${data.total_realized >= 0 ? '+' : ''}${data.total_realized.toFixed(2)} USDT` : ''}
          </p>
      }
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}

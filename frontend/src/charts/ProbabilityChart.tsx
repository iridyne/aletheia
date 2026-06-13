import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type Time } from 'lightweight-charts'
import { chartTheme } from './theme'
import type { ProbabilitySnapshot } from '../types'

interface Props {
  data: ProbabilitySnapshot[]
  height?: number
}

export default function ProbabilityChart({ data, height = 200 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...chartTheme.layout,
      width: containerRef.current.clientWidth,
      height,
      grid: chartTheme.grid,
      rightPriceScale: { borderColor: '#232c36' },
      timeScale: { borderColor: '#232c36', timeVisible: true },
    })
    chartRef.current = chart

    const series = chart.addAreaSeries({
      lineColor: '#22d3ee',
      topColor: 'rgba(34, 211, 238, 0.3)',
      bottomColor: 'rgba(34, 211, 238, 0.02)',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (v: number) => `${(v * 100).toFixed(1)}%` },
    })

    const points = data
      .filter(p => p.ts && p.yes_probability != null)
      .map(p => ({
        time: (new Date(p.ts).getTime() / 1000) as Time,
        value: p.yes_probability,
      }))

    if (points.length > 0) {
      series.setData(points)
      chart.timeScale().fitContent()
    }

    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width
      if (w) chart.applyOptions({ width: w })
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
    }
  }, [data, height])

  if (!data.length) {
    return <div className="pred-chart-empty">No probability history yet — data accumulates with each observer run.</div>
  }

  return <div ref={containerRef} className="pred-chart" />
}

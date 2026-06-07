import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { chartTheme } from './theme'

export interface TradeMarker { time: number; side: 'buy' | 'sell'; price: number; id?: string }

export default function PriceChart({ symbol, timeframe = '1h', markers = [], onMarkerClick }: {
  symbol: string; timeframe?: string; markers?: TradeMarker[]; onMarkerClick?: (id: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  const { data, isError } = useQuery({
    queryKey: ['ohlcv', symbol, timeframe],
    queryFn: () => api.getOhlcv(symbol, timeframe),
  })

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      layout: chartTheme.layout,
      grid: chartTheme.grid,
      width: containerRef.current.clientWidth,
      height: 320,
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart
    const series = chart.addCandlestickSeries({
      upColor: chartTheme.upColor, downColor: chartTheme.downColor, borderVisible: false,
      wickUpColor: chartTheme.upColor, wickDownColor: chartTheme.downColor,
    })

    const candles = Array.isArray(data) ? data : []
    if (candles.length) {
      series.setData(candles.map((c: any) => ({
        time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close,
      })))
      if (markers.length) {
        series.setMarkers(markers.map(m => ({
          time: m.time as Time,
          position: m.side === 'buy' ? 'belowBar' : 'aboveBar',
          color: m.side === 'buy' ? chartTheme.markerBuy : chartTheme.markerSell,
          shape: m.side === 'buy' ? 'arrowUp' : 'arrowDown',
          text: m.side.toUpperCase(),
        })))
      }
      chart.timeScale().fitContent()
    }

    if (onMarkerClick && markers.length) {
      chart.subscribeClick((param) => {
        if (!param.time) return
        const clickTime = param.time as number
        const matched = markers.find(m => m.time === clickTime && m.id)
        if (matched?.id) onMarkerClick(matched.id)
      })
    }

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [data, markers, onMarkerClick])

  return (
    <div>
      {isError && <p className="hint">Could not load market data (configure exchange / network).</p>}
      {Array.isArray(data) && data.length === 0 && <p className="hint">No candle data yet.</p>}
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}

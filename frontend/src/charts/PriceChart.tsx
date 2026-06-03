import { useEffect, useRef } from 'react'
import { createChart, ColorType, type IChartApi, type Time } from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface TradeMarker { time: number; side: 'buy' | 'sell'; price: number }

export default function PriceChart({ symbol, timeframe = '1h', markers = [] }: {
  symbol: string; timeframe?: string; markers?: TradeMarker[]
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
      layout: { background: { type: ColorType.Solid, color: '#1a1f26' }, textColor: '#e8e6e3' },
      grid: { vertLines: { color: '#2a2f35' }, horzLines: { color: '#2a2f35' } },
      width: containerRef.current.clientWidth,
      height: 320,
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart
    const series = chart.addCandlestickSeries({
      upColor: '#3fb950', downColor: '#f85149', borderVisible: false,
      wickUpColor: '#3fb950', wickDownColor: '#f85149',
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
          color: m.side === 'buy' ? '#3fb950' : '#f85149',
          shape: m.side === 'buy' ? 'arrowUp' : 'arrowDown',
          text: m.side.toUpperCase(),
        })))
      }
      chart.timeScale().fitContent()
    }

    const onResize = () => chart.applyOptions({ width: containerRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.remove() }
  }, [data, markers])

  return (
    <div>
      {isError && <p className="hint">Could not load market data (configure exchange / network).</p>}
      {Array.isArray(data) && data.length === 0 && <p className="hint">No candle data yet.</p>}
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}

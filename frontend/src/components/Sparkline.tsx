import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

interface SparklineProps {
  symbol: string
  width?: number
  height?: number
}

export default function Sparkline({ symbol, width = 80, height = 24 }: SparklineProps) {
  const { data } = useQuery({
    queryKey: ['sparkline', symbol],
    queryFn: () => api.getOhlcv(symbol, '1h', 24),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const candles = data || []
  if (candles.length < 2) return null

  const closes = candles.map(candle => candle.close)

  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const range = max - min || 1

  const pad = 2
  const w = width - pad * 2
  const h = height - pad * 2

  const points = closes.map((v, i) => {
    const x = pad + (i / (closes.length - 1)) * w
    const y = pad + h - ((v - min) / range) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  const trend = closes[closes.length - 1] >= closes[0]
  const color = trend ? 'var(--green)' : 'var(--red)'

  return (
    <svg width={width} height={height} className="sparkline" aria-hidden="true">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

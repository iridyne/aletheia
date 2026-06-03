import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import PriceChart from '../charts/PriceChart'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d']

export default function Charts() {
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [timeframe, setTimeframe] = useState('1h')
  const { data: trades = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })

  // Build trade markers for this symbol from filled orders
  const markers = (trades as any[])
    .filter(o => o.symbol === symbol && o.filled_at)
    .map(o => ({
      time: Math.floor(new Date(o.filled_at + 'Z').getTime() / 1000),
      side: (o.direction === 'long' || o.direction === 'buy' ? 'buy' : 'sell') as 'buy' | 'sell',
      price: o.price || 0,
    }))
    .filter(m => !Number.isNaN(m.time))

  return (
    <div>
      <h2 className="page-title">Charts</h2>
      <div className="card">
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <select value={symbol} onChange={e => setSymbol(e.target.value)}>
            {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <PriceChart symbol={symbol} timeframe={timeframe} markers={markers} />
        <p className="hint">Green/red arrows mark filled paper trades on {symbol}.</p>
      </div>
    </div>
  )
}

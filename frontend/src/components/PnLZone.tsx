import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import MetricTile from './MetricTile'
import PnLChart from '../charts/PnLChart'
import { fmtNum, fmtSigned, useDecimals } from '../lib/format'

/** PnL / Account zone — hero account value (exchange_equity_usdt when synced) + context tiles + equity curve. */
export default function PnLZone() {
  const navigate = useNavigate()
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })

  const exchangeMode = String((status as any)?.exchange_mode || 'demo')

  useDecimals() // re-render when the display-decimals setting changes
  const toneOf = (n: number | null) => (n == null ? 'muted' : n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral')

  const p = portfolio as any
  // Prefer real exchange balance; fall back to local managed notional
  const exchangeEquity = p?.exchange_equity_usdt
  const syncStatus: string = p?.exchange_sync_status ?? 'unconfigured'
  const totalRaw = exchangeEquity ?? p?.total_value_usdt
  const totalValue = fmtNum(totalRaw)
  const syncCaveat =
    exchangeEquity != null
      ? syncStatus === 'stale' ? 'data stale' : 'live'
      : syncStatus === 'unconfigured' ? 'exchange not configured'
      : syncStatus === 'error' ? 'sync error'
      : 'exchange sync pending'

  const todayRaw = p?.today_pnl
  const todayNum = todayRaw == null ? null : Number(todayRaw)
  const dayPct = todayNum != null && totalRaw ? (todayNum / Number(totalRaw)) * 100 : null

  const unrealRaw = p?.unrealized_pnl
  const unrealNum = unrealRaw == null ? null : Number(unrealRaw)
  const realRaw = p?.realized_pnl
  const realNum = realRaw == null ? null : Number(realRaw)

  const exposure = (p?.exposure_by_symbol || {}) as Record<string, number>
  const exposureTotal = Object.values(exposure).reduce((a, b) => a + Number(b || 0), 0)
  const exposureParts = Object.entries(exposure)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([sym, val]) => {
      const pct = exposureTotal > 0 ? Math.round((Number(val) / exposureTotal) * 100) : 0
      return `${sym.replace('USDT', '')} ${pct}%`
    })
  const positionsCount = Number(p?.positions_count ?? 0)

  return (
    <div className="pnl-zone">
      <div className="pnl-cluster">
        {/* Hero — account value (dominant) */}
        <div className="card pnl-hero">
          <div className="metric-label">Account Value</div>
          <div className={`pnl-hero-value${totalRaw == null ? ' is-muted' : ''}`}>
            {totalValue} <span className="hero-unit">USDT</span>
          </div>
          <div className="metric-caveat">{exchangeMode} · {syncCaveat}</div>
        </div>

        {/* Context tiles */}
        <div className="pnl-tiles">
          <MetricTile
            label="Today PnL"
            value={todayNum == null ? 'not available' : fmtSigned(todayNum)}
            tone={toneOf(todayNum)}
            context={todayNum == null ? 'no data' : dayPct != null ? `${fmtSigned(dayPct)}% of managed` : 'today'}
          />
          <MetricTile
            label="Unrealized"
            value={fmtSigned(unrealNum)}
            tone={toneOf(unrealNum)}
            context="open positions"
          />
          <MetricTile
            label="Realized"
            value={fmtSigned(realNum)}
            tone={toneOf(realNum)}
            context="all-time"
          />
          <MetricTile
            label="Exposure"
            value={positionsCount === 0 ? 'flat' : `${positionsCount} pos`}
            tone={positionsCount > 0 ? 'neutral' : 'muted'}
            context={exposureParts.length ? exposureParts.slice(0, 3).join(' · ') : 'no open exposure'}
            onClick={() => navigate('/trades')}
          />
        </div>
      </div>

      {/* Equity curve — TradingView-flavored, full width */}
      <div className="card pnl-curve">
        <div className="flex items-center justify-between mb-2">
          <h3>Equity Curve</h3>
          <span className="text-muted text-sm mono">cumulative PnL</span>
        </div>
        <PnLChart />
      </div>
    </div>
  )
}

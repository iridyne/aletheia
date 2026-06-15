import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import MetricTile from './MetricTile'
import PnLChart from '../charts/PnLChart'
import { fmtNum, fmtSigned, useDecimals } from '../lib/format'

// Palette for pie slices — cycles if more than 6 coins (rare)
const SLICE_COLORS = ['#00e5ff', '#7c3aed', '#f59e0b', '#10b981', '#f43f5e', '#3b82f6']

interface AssetItem { coin: string; usdt_value: number; pct: number }

function AssetPie({ items }: { items: AssetItem[] }) {
  if (!items.length) return null
  const r = 28; const cx = 32; const cy = 32
  let angle = -Math.PI / 2  // start at top
  const slices = items.map((item, i) => {
    const sweep = (item.pct / 100) * 2 * Math.PI
    const x1 = cx + r * Math.cos(angle)
    const y1 = cy + r * Math.sin(angle)
    angle += sweep
    const x2 = cx + r * Math.cos(angle)
    const y2 = cy + r * Math.sin(angle)
    const large = sweep > Math.PI ? 1 : 0
    return (
      <path
        key={item.coin}
        d={`M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large},1 ${x2},${y2} Z`}
        fill={SLICE_COLORS[i % SLICE_COLORS.length]}
        opacity={0.85}
      />
    )
  })
  return (
    <svg width="64" height="64" viewBox="0 0 64 64" style={{ flexShrink: 0 }}>
      {slices}
      {/* inner hole */}
      <circle cx={cx} cy={cy} r={14} fill="var(--surface, #0d1117)" />
    </svg>
  )
}

function AssetTooltip({ items }: { items: AssetItem[] }) {
  return (
    <div className="asset-tooltip">
      {items.map((item, i) => (
        <div key={item.coin} className="asset-tooltip-row">
          <span className="asset-dot" style={{ background: SLICE_COLORS[i % SLICE_COLORS.length] }} />
          <span className="asset-coin">{item.coin}</span>
          <span className="asset-value">{fmtNum(item.usdt_value)} USDT</span>
          <span className="asset-pct">{item.pct.toFixed(1)}%</span>
        </div>
      ))}
    </div>
  )
}

/** PnL / Account zone — hero account value (exchange_equity_usdt when synced) + context tiles + equity curve. */
export default function PnLZone() {
  const navigate = useNavigate()
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const [hovered, setHovered] = useState(false)

  const exchangeMode = String((status as any)?.exchange_mode || 'demo')

  useDecimals()
  const toneOf = (n: number | null) => (n == null ? 'muted' : n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral')

  const p = portfolio as any
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

  const breakdown: AssetItem[] = p?.exchange_asset_breakdown ?? []

  const todayRaw = p?.today_pnl
  const todayNum = todayRaw == null ? null : Number(todayRaw)
  const dayPct = todayNum != null && totalRaw ? (todayNum / Number(totalRaw)) * 100 : null

  const unrealNum = p?.unrealized_pnl == null ? null : Number(p.unrealized_pnl)
  const realNum = p?.realized_pnl == null ? null : Number(p.realized_pnl)

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
        {/* Hero — account value + pie + hover tooltip */}
        <div className="card pnl-hero">
          <div
            className="pnl-hero-inner"
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
          >
            <div className="pnl-hero-text">
              <div className="metric-label">Account Value</div>
              <div className={`pnl-hero-value${totalRaw == null ? ' is-muted' : ''}`}>
                {totalValue} <span className="hero-unit">USDT</span>
              </div>
              <div className="metric-caveat">{exchangeMode} · {syncCaveat}</div>
            </div>
            {breakdown.length > 0 && (
              <div className="pnl-pie-wrap">
                <AssetPie items={breakdown} />
              </div>
            )}
          </div>
          {hovered && breakdown.length > 0 && <AssetTooltip items={breakdown} />}
        </div>

        {/* Context tiles */}
        <div className="pnl-tiles">
          <MetricTile
            label="Today PnL"
            value={todayNum == null ? 'not available' : fmtSigned(todayNum)}
            tone={toneOf(todayNum)}
            context={todayNum == null ? 'no data' : dayPct != null ? `${fmtSigned(dayPct)}% of managed` : undefined}
          />
          <MetricTile
            label="Unrealized"
            value={fmtSigned(unrealNum)}
            tone={toneOf(unrealNum)}
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

      {/* Equity curve */}
      <div className="card pnl-curve">
        <h3 className="mb-2">Equity Curve</h3>
        <PnLChart />
      </div>
    </div>
  )
}

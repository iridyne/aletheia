import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import MetricTile from './MetricTile'
import { fmtNum, fmtSigned, useDecimals } from '../lib/format'

export default function AccountSnapshot() {
  const navigate = useNavigate()
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })

  useDecimals() // re-render when the display-decimals setting changes
  const openPositions = (positions as any[]).filter(p => p.status === 'open')
  const pendingProposals = (proposals as any[]).filter(p => p.status === 'pending')

  const p = portfolio as any

  // total_value_usdt already prefers UTA unified > exchange_equity > local notional
  const syncStatus: string = p?.exchange_sync_status ?? 'unconfigured'
  const displayValue = fmtNum(p?.total_value_usdt)
  const syncCaveat =
    p?.uta_state != null
      ? 'unified · live'
      : p?.exchange_equity_usdt != null
        ? syncStatus === 'stale' ? 'exchange · data stale' : 'exchange · live'
      : syncStatus === 'not_applicable' ? 'local paper · no exchange'
      : syncStatus === 'unconfigured' ? 'exchange not configured'
        : syncStatus === 'error' ? 'exchange sync error'
        : 'exchange sync pending'

  // "Today PnL" prefers the real day figure. #56 fix (2026-07-08): the
  // backend's `today_pnl` field is a legacy misnomer — it's actually
  // `unrealized_pnl (lifetime, all open) + today_realized_pnl`, so an
  // open position's total drift shows up under "Today". Prefer the
  // pure-today value `today_realized_pnl` when present, fall back to
  // legacy `today_pnl`, then to unrealized (with caveat) as before.
  const todayRealizedRaw = (p as any)?.today_realized_pnl
  const todayRealizedComplete = (p as any)?.today_realized_pnl_complete !== false
  const legacyTodayPnl = p?.today_pnl
  const usingUnrealizedFallback = todayRealizedComplete && todayRealizedRaw == null && legacyTodayPnl == null && p?.unrealized_pnl != null
  const usingLegacyTodayFallback = todayRealizedComplete && todayRealizedRaw == null && legacyTodayPnl != null
  const todayPnlRaw = todayRealizedComplete
    ? todayRealizedRaw ?? legacyTodayPnl ?? p?.unrealized_pnl
    : null
  const todayPnlNum = todayPnlRaw == null ? null : Number(todayPnlRaw)
  const todayPnlText = fmtSigned(todayPnlNum)
  const todayPnlTone: 'positive' | 'negative' | 'muted' = todayPnlNum == null
    ? 'muted'
    : todayPnlNum > 0 ? 'positive' : todayPnlNum < 0 ? 'negative' : 'muted'

  return (
    <div className="account-snapshot">
      <MetricTile
        label="Total Value"
        value={`${displayValue} USDT`}
        caveat={syncCaveat}
      />
      <MetricTile
        label="Today PnL"
        value={`${todayPnlText} USDT`}
        tone={todayPnlTone}
        caveat={
          !todayRealizedComplete ? 'funding or close economics unavailable' :
          usingUnrealizedFallback ? 'unrealized · today figure unavailable' :
          // #56: the legacy today_pnl includes lifetime uPnL of open positions.
          // Surface that so the operator knows the number isn't pure today.
          usingLegacyTodayFallback ? 'includes open-position uPnL (legacy today_pnl)' :
          undefined
        }
      />
      <MetricTile
        label="Open Positions"
        value={openPositions.length}
        tone={openPositions.length > 0 ? 'neutral' : 'muted'}
        onClick={() => navigate('/trades')}
      />
      <MetricTile
        label="Pending Proposals"
        value={pendingProposals.length}
        tone={pendingProposals.length > 0 ? 'neutral' : 'muted'}
        onClick={() => navigate('/proposals')}
      />
    </div>
  )
}

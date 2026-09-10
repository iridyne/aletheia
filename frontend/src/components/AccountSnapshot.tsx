import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import MetricTile from './MetricTile'
import { fmtNum, fmtSigned } from '../lib/format'

export default function AccountSnapshot() {
  const navigate = useNavigate()
  const {
    data: portfolio,
    isError: portfolioError,
    dataUpdatedAt: portfolioUpdatedAt,
  } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const p = portfolio
  const portfolioAvailable = p != null
  const positionScopeAvailable = p?.execution_mode_status === 'resolved'
    && typeof p?.execution_mode === 'string'
    && p.execution_mode.trim() !== ''
  const openPositionsRaw = p?.positions_count == null ? null : Number(p.positions_count)
  const openPositions = positionScopeAvailable && Number.isFinite(openPositionsRaw)
    ? openPositionsRaw
    : null
  const cachedPortfolioAgeSec = portfolioAvailable && portfolioUpdatedAt > 0
    ? Math.max(0, Math.round((Date.now() - portfolioUpdatedAt) / 1000))
    : null
  const portfolioReadFailureCaveat = portfolioError
    ? portfolioAvailable
      ? `cached · portfolio read failed · ${cachedPortfolioAgeSec ?? 0}s old`
      : 'portfolio read failed'
    : null

  // The backend selects the available external account equity.
  const syncStatus: string = p?.exchange_sync_status ?? 'unconfigured'
  const utaStatus: string | undefined = p?.uta_state?.sync_status ?? undefined
  const displayValue = fmtNum(p?.total_value_usdt)
  const syncCaveat =
    portfolioReadFailureCaveat ??
    (p?.uta_state != null
      ? utaStatus === 'ok' ? 'unified · live' : utaStatus === 'stale' ? 'unified · data stale' : utaStatus === 'error' ? 'unified · sync error' : 'unified · not configured'
      : p?.exchange_equity_usdt != null
        ? syncStatus === 'stale' ? 'exchange · data stale' : 'exchange · live'
      : syncStatus === 'unconfigured' ? 'exchange not configured'
        : syncStatus === 'error' ? 'exchange sync error'
        : 'exchange sync pending')

  // "Today PnL" prefers the real day figure. #56 fix (2026-07-08): the
  // backend's `today_pnl` field is a legacy misnomer — it's actually
  // `unrealized_pnl (lifetime, all open) + today_realized_pnl`, so an
  // open position's total drift shows up under "Today". Prefer the
  // pure-today value `today_realized_pnl` when present, fall back to
  // legacy `today_pnl`, then to unrealized (with caveat) as before.
  const todayRealizedRaw = p?.today_realized_pnl
  const todayRealizedComplete = p?.today_realized_pnl_complete !== false
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
          portfolioReadFailureCaveat ??
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
        value={openPositions ?? 'not available'}
        tone={openPositions != null && openPositions > 0 ? 'neutral' : 'muted'}
        onClick={() => navigate('/trades')}
      />
    </div>
  )
}

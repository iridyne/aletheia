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

  // Prefer real exchange balance; fall back to local managed notional
  const exchangeEquity = p?.exchange_equity_usdt
  const syncStatus: string = p?.exchange_sync_status ?? 'unconfigured'
  const displayValue = fmtNum(exchangeEquity ?? p?.total_value_usdt)
  const syncCaveat =
    exchangeEquity != null
      ? syncStatus === 'stale' ? 'exchange · data stale' : 'exchange · live'
      : syncStatus === 'unconfigured' ? 'exchange not configured'
      : syncStatus === 'error' ? 'exchange sync error'
      : 'exchange sync pending'

  const todayPnlRaw = p?.today_pnl ?? p?.unrealized_pnl
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

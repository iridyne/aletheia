import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import MetricTile from './MetricTile'

export default function AccountSnapshot() {
  const navigate = useNavigate()
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })

  const openPositions = (positions as any[]).filter(p => p.status === 'open')
  const pendingProposals = (proposals as any[]).filter(p => p.status === 'pending')

  const totalValueRaw = (portfolio as any)?.total_value_usdt
  const totalValue = totalValueRaw == null
    ? '—'
    : `${Number(totalValueRaw).toLocaleString(undefined, { maximumFractionDigits: 2 })}`

  const todayPnlRaw = (portfolio as any)?.today_pnl ?? (portfolio as any)?.unrealized_pnl
  const todayPnlNum = todayPnlRaw == null ? null : Number(todayPnlRaw)
  const todayPnlText = todayPnlNum == null
    ? '—'
    : `${todayPnlNum >= 0 ? '+' : ''}${todayPnlNum.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  const todayPnlTone: 'positive' | 'negative' | 'muted' = todayPnlNum == null
    ? 'muted'
    : todayPnlNum > 0 ? 'positive' : todayPnlNum < 0 ? 'negative' : 'muted'

  return (
    <div className="account-snapshot">
      <MetricTile
        label="Total Value"
        value={`${totalValue} USDT`}
        caveat="aletheia-managed only · exchange sync pending"
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
        onClick={() => navigate('/positions')}
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

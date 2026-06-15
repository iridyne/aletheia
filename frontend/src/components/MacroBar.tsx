import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { fmtNum, fmtSigned } from '../lib/format'

const REGIME_TONE: Record<string, string> = {
  risk_on:  'macro-regime-on',
  neutral:  'macro-regime-neutral',
  risk_off: 'macro-regime-off',
}

const REGIME_LABEL: Record<string, string> = {
  risk_on:  'RISK ON',
  neutral:  'NEUTRAL',
  risk_off: 'RISK OFF',
}

function MacroCell({ label, value, change, suffix = '' }: {
  label: string; value: number | null | undefined
  change?: number | null; suffix?: string
}) {
  if (value == null) return null
  const tone = change == null ? '' : change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : ''
  return (
    <span className="macro-cell">
      <span className="macro-cell-label">{label}</span>
      <span className="macro-cell-value mono">{fmtNum(value)}{suffix}</span>
      {change != null && (
        <span className={`macro-cell-change mono ${tone}`}>{fmtSigned(change)}%</span>
      )}
    </span>
  )
}

export default function MacroBar() {
  const { data } = useQuery({
    queryKey: ['macro'],
    queryFn: api.getMacro,
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  if (!data?.available || !data?.data) {
    return (
      <div className="macro-bar macro-bar-pending">
        <span className="macro-bar-hint">Macro data pending — collector initializing</span>
      </div>
    )
  }

  const d = data.data
  const regime = d.risk_regime || 'neutral'

  return (
    <div className="macro-bar" role="status" aria-label="Global macro indicators">
      <span className={`macro-regime ${REGIME_TONE[regime] || 'macro-regime-neutral'}`}>
        {REGIME_LABEL[regime] || regime.toUpperCase()}
      </span>
      <span className="macro-divider" />
      {d.vix_close != null && (
        <span className="macro-cell">
          <span className="macro-cell-label">VIX</span>
          <span className={`macro-cell-value mono ${d.vix_close > 25 ? 'pnl-negative' : d.vix_close < 15 ? 'pnl-positive' : ''}`}>
            {fmtNum(d.vix_close)}
          </span>
        </span>
      )}
      <MacroCell label="DXY" value={d.dxy_close} />
      {d.btc_dominance != null && (
        <span className="macro-cell">
          <span className="macro-cell-label">BTC.D</span>
          <span className="macro-cell-value mono">{fmtNum(d.btc_dominance)}%</span>
        </span>
      )}
      {d.total_market_cap_usd != null && (
        <span className="macro-cell">
          <span className="macro-cell-label">TOTAL CAP</span>
          <span className="macro-cell-value mono">${fmtNum(d.total_market_cap_usd / 1e12)}T</span>
          {d.total_market_cap_change_24h_pct != null && (
            <span className={`macro-cell-change mono ${d.total_market_cap_change_24h_pct > 0 ? 'pnl-positive' : d.total_market_cap_change_24h_pct < 0 ? 'pnl-negative' : ''}`}>
              {fmtSigned(d.total_market_cap_change_24h_pct)}%
            </span>
          )}
        </span>
      )}
      <MacroCell label="SPY" value={d.spy_close} />
    </div>
  )
}

import { ReactNode } from 'react'

type Tone = 'neutral' | 'positive' | 'negative' | 'muted' | 'warning'

type Props = {
  label: string
  value: ReactNode
  tone?: Tone
  caveat?: string
  /** One short line of context under the value (OpenAlice style: every number explains itself). */
  context?: ReactNode
  onClick?: () => void
}

const TONE_CLASS: Record<Tone, string> = {
  neutral: '',
  positive: 'pnl-positive',
  negative: 'pnl-negative',
  muted: 'text-muted',
  warning: 'pnl-warning',
}

export default function MetricTile({ label, value, tone = 'neutral', caveat, context, onClick }: Props) {
  return (
    <div
      className={`card metric-tile${onClick ? ' clickable' : ''}`}
      onClick={onClick}
    >
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${TONE_CLASS[tone]}`}>{value}</div>
      {context && (
        <div className="metric-context" title={typeof context === 'string' ? context : undefined}>{context}</div>
      )}
      {caveat && <div className="metric-caveat">{caveat}</div>}
    </div>
  )
}

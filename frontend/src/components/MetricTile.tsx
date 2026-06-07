import { ReactNode } from 'react'

type Tone = 'neutral' | 'positive' | 'negative' | 'muted'

type Props = {
  label: string
  value: ReactNode
  tone?: Tone
  caveat?: string
  onClick?: () => void
}

const TONE_CLASS: Record<Tone, string> = {
  neutral: '',
  positive: 'pnl-positive',
  negative: 'pnl-negative',
  muted: 'text-muted',
}

export default function MetricTile({ label, value, tone = 'neutral', caveat, onClick }: Props) {
  return (
    <div
      className="card metric-tile"
      onClick={onClick}
      style={onClick ? { cursor: 'pointer' } : undefined}
    >
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${TONE_CLASS[tone]}`}>{value}</div>
      {caveat && <div className="metric-caveat">{caveat}</div>}
    </div>
  )
}

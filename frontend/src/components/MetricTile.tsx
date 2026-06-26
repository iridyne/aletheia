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
  // When clickable, render a real <button> so keyboard users (Tab/Enter/Space) and
  // screen readers can use the tile as a navigation control — DataTable rows already
  // do this; tiles were the gap (WCAG 2.1.1 keyboard, 4.1.2 name/role/value).
  // When non-clickable, stay a <div> (no false button affordance for SR).
  const Tag = onClick ? 'button' : 'div'
  const buttonProps = onClick
    ? { type: 'button' as const, onClick }
    : {}
  return (
    <Tag
      className={`card metric-tile${onClick ? ' clickable' : ''}`}
      {...buttonProps}
    >
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${TONE_CLASS[tone]}`}>{value}</div>
      {context && (
        <div className="metric-context" title={typeof context === 'string' ? context : undefined}>{context}</div>
      )}
      {caveat && <div className="metric-caveat">{caveat}</div>}
    </Tag>
  )
}

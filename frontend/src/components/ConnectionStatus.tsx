/**
 * Verified-Connection status row: shows the REAL state of a configured external
 * integration at-a-glance (reachable? which is default? resolved model/account?).
 * Pure presentational — composes only the existing badge/dot/hint CSS vocabulary,
 * no new classes. Reused by model providers, exchange profiles, telegram, etc.
 */

export type ConnState = 'green' | 'red' | 'yellow' | 'muted'

const BADGE: Record<ConnState, string> = {
  green: 'badge badge-green',
  red: 'badge badge-red',
  yellow: 'badge badge-yellow',
  muted: 'badge',
}
const DOT: Record<ConnState, string> = {
  green: 'dot dot-green',
  red: 'dot dot-red',
  yellow: 'dot dot-yellow',
  muted: 'dot dot-muted',
}

export default function ConnectionStatus({
  label, state, text, detail, isDefault,
}: {
  label: string
  state: ConnState
  text: string
  detail?: string
  isDefault?: boolean
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', padding: '0.3rem 0' }}>
      <span className={DOT[state]} style={{ flex: '0 0 auto', alignSelf: 'center' }} />
      <strong style={{ minWidth: '11rem', fontSize: '0.82rem' }}>{label}</strong>
      <span className={BADGE[state]}>{text}</span>
      {isDefault && <span className="badge badge-blue">default</span>}
      {detail && <span className="hint" style={{ marginTop: 0 }}>{detail}</span>}
    </div>
  )
}

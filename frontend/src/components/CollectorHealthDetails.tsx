import type { CollectorHealthDomain, CollectorHealthReport } from '../types'

const PROBLEM_STATUSES = new Set(['failed', 'timed_out', 'skipped_budget', 'partial', 'empty'])

function fmtAge(sec: number | null): string {
  if (sec == null) return 'never'
  if (sec < 60) return `${Math.round(sec)}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m ago`
}

function fmtCadence(sec: number): string {
  if (sec < 60) return `${sec}s`
  return `${sec / 60}m`
}

function fmtDomain(name: string): string {
  return name.replace(/_/g, ' ').toUpperCase()
}

function domainLight(d: CollectorHealthDomain): 'red' | 'yellow' | 'green' | 'muted' {
  if (d.display_severity === 'info') return 'yellow'
  if (d.degraded) return 'red'
  if (d.overdue || PROBLEM_STATUSES.has(d.last_status)) return 'yellow'
  if (d.display_severity === 'muted' || d.last_status === 'not_configured' || d.last_status === 'disabled') return 'muted'
  return d.last_status === 'success' ? 'green' : 'muted'
}

function isProblem(d: CollectorHealthDomain): boolean {
  return d.degraded || d.overdue || PROBLEM_STATUSES.has(d.last_status)
}

function FullDetails({ health }: { health: CollectorHealthReport }) {
  const sorted = [...health.domains].sort((a, b) => {
    const rank = (d: CollectorHealthDomain) => d.degraded ? 0 : PROBLEM_STATUSES.has(d.last_status) ? 1 : 2
    return rank(a) - rank(b)
  })
  const problems = sorted.filter(isProblem)
  const incident = health.infrastructure_incident
  const attributed = new Set(incident?.affected_domains ?? [])
  const independentProblems = problems.filter(d => !attributed.has(d.domain))
  const healthyCount = sorted.filter(d => d.last_status === 'success' && !isProblem(d)).length
  const emptyCount = sorted.filter(d => d.last_status === 'empty').length
  const disabledCount = sorted.filter(d => d.last_status === 'disabled').length
  const unconfiguredCount = sorted.filter(d => d.last_status === 'not_configured').length
  const neverRunCount = sorted.filter(d => d.last_status === 'never_run').length
  const otherStates = [
    emptyCount > 0 ? `${emptyCount} empty` : '',
    disabledCount > 0 ? `${disabledCount} disabled` : '',
    unconfiguredCount > 0 ? `${unconfiguredCount} unconfigured` : '',
    neverRunCount > 0 ? `${neverRunCount} unobserved` : '',
  ].filter(Boolean)
  const incidentTone = 'yellow'

  return (
    <div className="chd-panel">
      <div className="chd-title">Collector diagnostics</div>
      {incident && incident.affected_domains.length > 0 && (
        <div className={`chd-incident chd-incident-${incidentTone}`}>
          <div className="chd-incident-head">
            <span className={`status-light light-${incidentTone}`} />
            <span>Collector dependency</span>
            <span className={`chd-status chd-status-${incidentTone}`}>
              {incident.status === 'active' ? 'unavailable' : 'recovering'}
            </span>
          </div>
          <div className="chd-incident-message">
            {incident.status === 'active'
              ? 'These collectors are blocked by the connectivity incident above.'
              : 'Connectivity is restored; these collectors are waiting for their next retry.'}
          </div>
          <div className="chd-incident-meta">
            <span>Affected: {incident.affected_domains.map(fmtDomain).join(', ')}</span>
            <span>{incident.action}</span>
          </div>
        </div>
      )}
      {independentProblems.map(d => {
        const light = domainLight(d)
        const cons = d.consecutive_problems ?? 0
        return (
          <div key={d.domain} className="chd-domain">
            <div className="chd-domain-head">
              <span className={`status-light light-${light}`} />
              <span className="chd-domain-name">{fmtDomain(d.domain)}</span>
              <span className={`chd-status chd-status-${light}`}>{d.last_status || '—'}</span>
              {d.overdue && <span className="chd-badge-overdue">overdue</span>}
              {d.starved && <span className="chd-badge-overdue">starved</span>}
            </div>
            <div className="chd-domain-meta">
              {cons > 1 && <span>{cons}× consecutive</span>}
              {d.last_error_code && d.last_error_code !== 'asyncio_timeout' && (
                <span className="chd-err-code">{d.last_error_code === 'ExchangeNotAvailable' ? 'Exchange unavailable' : d.last_error_code}</span>
              )}
              {d.age_sec != null && (
                <span>last {fmtAge(d.age_sec)} · cadence {fmtCadence(d.cadence_sec)}</span>
              )}
            </div>
          </div>
        )
      })}
      {healthyCount > 0 && (
        <div className="chd-healthy-line">
          {healthyCount} healthy collector{healthyCount === 1 ? '' : 's'}
        </div>
      )}
      {otherStates.length > 0 && (
        <div className="chd-healthy-line">{otherStates.join(' · ')}</div>
      )}
    </div>
  )
}

function CompactDetails({ health }: { health: CollectorHealthReport }) {
  if (health.status === 'initializing') {
    return (
      <div className="sp-row">
        <span className="sp-label">Collectors</span>
        <span className="sp-value">initializing</span>
      </div>
    )
  }
  if (health.status === 'healthy') {
    const healthyCount = health.domains.filter(d => d.last_status === 'success' && !isProblem(d)).length
    return (
      <div className="sp-row">
        <span className="sp-label">Collectors</span>
        <span className="sp-value sp-value-wrap">{healthyCount} success · {health.domains.length - healthyCount} inactive or unobserved</span>
      </div>
    )
  }

  const incident = health.infrastructure_incident
  const attributed = new Set(incident?.affected_domains ?? [])
  const problems = health.domains.filter(d => isProblem(d) && !attributed.has(d.domain))
  return (
    <>
      {incident && incident.affected_domains.length > 0 && (
        <div className="sp-row">
          <span className="dot dot-yellow" />
          <span className="sp-label">Collector impact</span>
          <span className="sp-value sp-value-wrap">
            {incident.affected_domains.length} dependent collector{incident.affected_domains.length === 1 ? '' : 's'}
            {' · '}{incident.status === 'active' ? 'blocked' : 'retrying'}
          </span>
        </div>
      )}
      {problems.map(d => {
        const light = domainLight(d)
        const cons = d.consecutive_problems ?? 0
        const rawStatus = d.last_status === 'timed_out' ? 'timeout' : (d.last_status || '')
        const cleanErr = d.last_error_code === 'asyncio_timeout' || d.last_error_code === d.last_status ? null : d.last_error_code
        const parts: string[] = [rawStatus]
        if (d.overdue) parts.push('overdue')
        if (d.starved) parts.push('starved')
        if (cons > 1) parts.push(`${cons}×`)
        if (cleanErr) parts.push(cleanErr === 'ExchangeNotAvailable' ? 'Exchange unavailable' : cleanErr)
        return (
          <div key={d.domain} className="sp-row">
            <span className={`dot dot-${light}`} />
            <span className="sp-label">{d.domain}</span>
            <span className="sp-value sp-value-wrap">{parts.join(' · ')}</span>
          </div>
        )
      })}
    </>
  )
}

interface Props {
  health: CollectorHealthReport
  mode: 'full' | 'compact'
}

export default function CollectorHealthDetails({ health, mode }: Props) {
  if (mode === 'compact') return <CompactDetails health={health} />
  return <FullDetails health={health} />
}

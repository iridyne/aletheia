import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { CollectorHealthDomain, CollectorHealthReport } from '../types'
import CollectorHealthDetails from './CollectorHealthDetails'

function domain(name: string, status: string): CollectorHealthDomain {
  return {
    domain: name,
    cadence_sec: 600,
    last_status: status,
    display_status: status,
    attributed_cause: null,
    display_severity: status === 'success' ? 'ok' : 'muted',
    age_sec: status === 'never_run' ? null : 1,
    overdue: false,
    degraded: false,
  }
}

function report(status: CollectorHealthReport['status']): CollectorHealthReport {
  const domains = [
    domain('successful', 'success'),
    domain('source_partial', 'partial'),
    domain('no_rows', 'empty'),
    domain('disabled_source', 'disabled'),
    domain('missing_config', 'not_configured'),
    domain('not_started', 'never_run'),
  ]
  return {
    status,
    domain_count: domains.length,
    recorded_count: 5,
    completed_count: 3,
    degraded_count: 0,
    transient_problem_count: 0,
    infrastructure_incident: null,
    domains,
  }
}

describe('CollectorHealthDetails', () => {
  it('shows empty reasons without hiding independent failures behind connectivity', () => {
    const health = report('attention')
    health.domains[2].last_error_code = 'no_derivatives_history'
    health.domains[0].overdue = true
    health.infrastructure_incident = {
      code: 'external_connectivity_unavailable', status: 'active', severity: 'warning',
      priority: 1, message: 'Network unavailable', action: 'Check network',
      detected_at: null, observed_at: null, affected_domains: ['source_partial'],
    }
    const markup = renderToStaticMarkup(<CollectorHealthDetails health={health} mode="compact" />)
    expect(markup).toContain('no_rows')
    expect(markup).toContain('empty · no_derivatives_history')
    expect(markup).toContain('1 dependent collector')
    expect(markup).toContain('success · overdue')
    expect(markup).not.toContain('source_partial')
  })

  it('counts only exact success as healthy and preserves initialization', () => {
    const full = renderToStaticMarkup(
      <CollectorHealthDetails health={report('attention')} mode="full" />,
    )
    const compact = renderToStaticMarkup(
      <CollectorHealthDetails health={report('initializing')} mode="compact" />,
    )

    expect(full).toContain('SOURCE PARTIAL')
    expect(full).toContain('1 healthy collector')
    expect(full).toContain('1 empty')
    expect(full).toContain('1 disabled')
    expect(full).toContain('1 unconfigured')
    expect(full).toContain('1 unobserved')
    expect(full).not.toContain('6 healthy collectors')
    expect(compact).toContain('initializing')
    expect(compact).not.toContain('5/6 healthy')
  })
})

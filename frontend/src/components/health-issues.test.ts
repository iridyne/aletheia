import { describe, expect, it } from 'vitest'
import { parseHealthIssue, sortedIssues } from './health-issues'

describe('health issue projection', () => {
  it('turns known machine codes into readable labels, values, and actions', () => {
    expect(parseHealthIssue('market_data_stale:BTC/USDT')).toMatchObject({
      severity: 'yellow',
      label: 'Market data stale',
      value: 'BTC/USDT',
    })
    expect(parseHealthIssue('code_updated_pending_restart:abc123..def456')).toMatchObject({
      severity: 'yellow',
      label: 'Code updated',
      value: 'abc123..def456',
    })
    expect(parseHealthIssue('kill_switch_active')).toMatchObject({
      severity: 'red',
      label: 'Kill switch',
      value: 'active',
    })
    expect(parseHealthIssue('observer_run_failed')).toMatchObject({
      severity: 'red',
      label: 'Observer run failed',
      value: 'failed',
    })
  })

  it('keeps unknown issues actionable without promoting the raw code to primary copy', () => {
    const parsed = parseHealthIssue('new_backend_issue:secret-detail')
    expect(parsed).toMatchObject({ label: 'Health issue', value: 'attention' })
    expect(parsed.detail).toBe('new_backend_issue:secret-detail')
    expect(parsed.hint).toBeTruthy()
  })

  it('prioritizes red issues before yellow issues', () => {
    const issues = sortedIssues([
      parseHealthIssue('market_data_stale:BTC/USDT'),
      parseHealthIssue('health_check_error:timeout'),
    ])
    expect(issues.map(issue => issue.severity)).toEqual(['red', 'yellow'])
  })
})

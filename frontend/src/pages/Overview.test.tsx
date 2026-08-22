import { describe, expect, it } from 'vitest'
import { isGoLiveScorecard, operatorMutationError } from './Overview'
import SRC from './Overview.tsx?raw'
import CSS from '../index.css?raw'
import STATUS_SRC from '../components/StatusCluster.tsx?raw'

describe('Overview operator mutation truthfulness', () => {
  it('keeps the action identity and a usable rejection reason', () => {
    expect(operatorMutationError('Pause gateway failed', new Error('backend 503')))
      .toBe('Pause gateway failed: backend 503')
    expect(operatorMutationError('Resume gateway failed', 'startup gate blocked'))
      .toBe('Resume gateway failed: startup gate blocked')
    expect(operatorMutationError('Pause gateway failed', null))
      .toBe('Pause gateway failed: request failed')
  })

  it('renders sticky failure and prevents overlapping pause/resume commands', () => {
    expect(SRC).toContain("operatorMutationError('Pause gateway failed', error)")
    expect(SRC).toContain("operatorMutationError('Resume gateway failed', error)")
    expect(SRC).toContain('const gatewayCommandPending = pauseMut.isPending || resumeMut.isPending')
    expect(SRC).toContain('disabled={gatewayCommandPending || paused || !running}')
    expect(SRC).toContain('disabled={gatewayCommandPending || !paused}')
    expect(SRC).toContain('role="alert"')
    expect(SRC.match(/onMutate: \(\) => setGatewayCommandError\(''\)/g)).toHaveLength(2)
    expect(SRC).not.toContain("qc.setQueryData(['status']")
  })
})

describe('Overview semantic honesty', () => {
  it('loads persisted data coverage independently from the lightweight bootstrap', () => {
    expect(SRC).toContain("queryKey: ['market-data-coverage']")
    expect(SRC).toContain('queryFn: api.getDataCoverage')
    expect(SRC).toContain('coverage={coverage.data} coverageError={coverage.isError}')
    expect(SRC).toContain("coverage?.status === 'healthy'")
    expect(SRC).not.toContain('deferred_until_diagnostics')
  })

  it('RiskCard shows muted tone when shell data has not loaded', () => {
    // Unknown state (no data) must not show yellow (degraded) — it is unknown.
    // The tone expression must check !shell before assigning yellow.
    expect(SRC).toContain('const unavailable = shellError || !shell || !risk')
    expect(SRC).toContain('const tone = unavailable ? \'muted\'')
  })

  it('GoLiveScoreCard derives tone from backend traffic_light, not hardcoded yellow', () => {
    // Hardcoded tone="yellow" on the Go-Live badge violated semantic honesty:
    // the deferred state (data not yet fetched) is unknown, not degraded.
    expect(SRC).not.toContain('tone="yellow"')
    expect(SRC).toContain('data ? data.traffic_light')
  })

  it('BlotterSummaryStrip shows em-dash instead of 0 when summary is not loaded', () => {
    // Missing data is not zero. Counts must not coerce undefined to 0.
    expect(SRC).not.toContain('?? 0')
    expect(SRC).toContain("?? '—'")
  })

  it('canonical breakpoint scale documented in index.css matches design system', () => {
    // ui_design_system.md mandates 640/768/1024/1280; the old 480/700/900/1100
    // token comment conflicted with it. The documentation must list the target scale.
    expect(CSS).toContain('--bp-sm: 640px')
    expect(CSS).toContain('--bp-md: 768px')
    expect(CSS).toContain('--bp-lg: 1024px')
    expect(CSS).toContain('--bp-xl: 1280px')
  })
})

describe('Overview issue repairs', () => {
  it('validates the scorecard response before treating it as qualification evidence', () => {
    const valid = {
      composite_score: 82,
      traffic_light: 'green' as const,
      criteria: [{ key: 'risk', label: 'Risk', score: 100, weight: 1, weighted_contribution: 100, detail: 'ok', status: 'pass' as const }],
      blockers: [],
      computed_at: '2026-08-22T10:00:00Z',
    }
    expect(isGoLiveScorecard(valid)).toBe(true)
    expect(isGoLiveScorecard({ ...valid, traffic_light: 'ready' })).toBe(false)
    expect(isGoLiveScorecard({ ...valid, criteria: [{ ...valid.criteria[0], score: Number.NaN }] })).toBe(false)
  })

  it('keeps collector, coverage, mode, and navigation paths mounted on System', () => {
    expect(SRC).toContain("queryKey: ['collector-health']")
    expect(SRC).toContain('CollectorHealthDetails health={collectorHealth} mode="compact"')
    expect(SRC).toContain('CollectorHealthDetails health={collectorHealth} mode="full"')
    expect(SRC).toContain('DataCoverageDetail')
    expect(SRC).toContain('ModeDetail shell={shell}')
    expect(SRC).toContain("navigate('/settings?tab=execution')")
    expect(SRC).toContain("navigate('/settings?tab=agent')")
    expect(SRC).toContain("navigate('/settings?tab=runtime')")
    expect(STATUS_SRC).toContain('parseHealthIssue(issue)')
    expect(STATUS_SRC).not.toContain('function parseIssue')
  })

  it('feeds shell health into the persistent master status derivation', () => {
    expect(STATUS_SRC).toContain("const healthState = String(healthQuery.data?.status || data?.health?.status || '')")
    expect(STATUS_SRC).toContain('riskState,')
    expect(STATUS_SRC).toContain('healthState,')
  })

  it('separates pending, unavailable, and stale scorecard states', () => {
    expect(SRC).toContain('scorecardQuery.isPending && !data')
    expect(SRC).toContain('scorecardQuery.isError && !data')
    expect(SRC).toContain('scorecardQuery.isRefetchError')
    expect(SRC).toContain('Qualification details are unavailable.')
    expect(SRC).toContain('Refresh failed; showing the last computed scorecard.')
    expect(SRC).toContain('Last computed {fmtTimestamp(data.computed_at)}')
  })

  it('does not clip the restored status popover at the mobile topbar', () => {
    expect(CSS).toContain('gap: var(--space-2); overflow: visible;')
    expect(CSS).toContain('.status-popover {')
    expect(CSS).toContain('position: absolute; top: 100%; right: 0;')
  })
})

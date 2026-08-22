import { describe, expect, it } from 'vitest'
import { operatorMutationError } from './Overview'
import SRC from './Overview.tsx?raw'
import CSS from '../index.css?raw'

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

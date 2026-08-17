import { describe, expect, it } from 'vitest'
import { operatorMutationError } from './Overview'
import SRC from './Overview.tsx?raw'
import { overviewTodayPnl } from '../components/PnLZone'
import { connectivityTone } from '../lib/systemState'

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

  it('loads persisted data coverage after the bootstrap diagnostics delay', () => {
    expect(SRC).toContain("const [diagnosticsReady, setDiagnosticsReady] = useState(false)")
    expect(SRC).toContain('setDiagnosticsReady(true), 600')
    expect(SRC).toContain("queryKey: ['market-data-coverage']")
    expect(SRC).toContain('enabled: diagnosticsReady')
    expect(SRC).toContain('coverage={dataCoverage.data}')
    expect(SRC).toContain("? `${coverage.fresh_count}/${coverage.domain_count} current`")
  })

  it('keeps Overview status and partial data failures honest', () => {
    expect(connectivityTone('reachable')).toBe('green')
    expect(connectivityTone('unavailable')).toBe('red')
    expect(connectivityTone('recovering')).toBe('yellow')
    expect(connectivityTone('unknown')).toBe('muted')
    expect(SRC).toContain('connectivityTone(connectivityState)')
    expect(SRC).toContain("overview.data.errors?.summary")
    expect(SRC).toContain('unavailable={Boolean(overview.error)}')
    expect(SRC).toContain('Some Overview data is unavailable')
  })

  it('uses pure today-realized PnL and fails closed when incomplete', () => {
    expect(overviewTodayPnl({ today_pnl: 99, today_realized_pnl: 3 })).toBe(3)
    expect(overviewTodayPnl({ today_pnl: 99, today_realized_pnl: 0, today_realized_pnl_complete: false })).toBeNull()
    expect(overviewTodayPnl({ today_pnl: 99 })).toBeNull()
  })
})

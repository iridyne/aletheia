import { describe, expect, it } from 'vitest'
import SRC from './RiskControls.tsx?raw'

describe('RiskControls trust-path mutations', () => {
  it('fails closed when authoritative reads are unavailable', () => {
    expect(SRC).toContain('const killSwitchAvailable = !ksLoading && !ksError && killSwitch != null')
    expect(SRC).toContain('Kill switch state unavailable; controls disabled.')
    expect(SRC).toContain('Scheduler state unavailable; controls disabled.')
    expect(SRC).toContain("Authoritative runtime safety state is unavailable.")
    expect(SRC).toContain('disabled={!statusAvailable || paused || !gatewayRunning || schedulerPending}')
  })

  it('clears stale errors, checks backend-confirmed state, and refetches authority', () => {
    expect(SRC.match(/onMutate: \(\) => setKillErr\(''\)/g)).toHaveLength(2)
    expect(SRC.match(/onMutate: \(\) => setSchedErr\(''\)/g)).toHaveLength(2)
    expect(SRC).toContain("if (confirmed.active !== true) throw new Error('Kill switch engage was not confirmed by backend')")
    expect(SRC).toContain("if (confirmed.active !== false) throw new Error('Kill switch release was not confirmed by backend')")
    expect(SRC.match(/qc\.refetchQueries\(\{ queryKey: \['kill-switch'\] \}, \{ throwOnError: true \}\)/g)).toHaveLength(2)
    expect(SRC.match(/qc\.refetchQueries\(\{ queryKey: \['risk-status'\] \}, \{ throwOnError: true \}\)/g)).toHaveLength(2)
    expect(SRC.match(/qc\.refetchQueries\(\{ queryKey: \['status'\] \}, \{ throwOnError: true \}\)/g)).toHaveLength(2)
    expect(SRC).not.toContain("qc.setQueryData(['kill-switch'], confirmed)")
  })
})

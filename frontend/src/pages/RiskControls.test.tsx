import { describe, expect, it } from 'vitest'
import SRC from './RiskControls.tsx?raw'
import { fmtSymbol } from '../lib/format'
import { classifyRiskPolicyMode, marketTypesFromOpenPositions, openPositionRows, preferPerpMarketType } from './RiskControls'

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

  it('feeds health truth into the shared master state', () => {
    expect(SRC).toContain("queryKey: ['health']")
    expect(SRC).toContain("const healthState = healthError ? 'unavailable' : String(health?.status || 'unknown')")
    expect(SRC).toContain('riskState, healthState, paused')
  })

  it('keeps historical risk decisions visibly separate from current health', () => {
    expect(SRC).toContain("const riskState = riskStatusError ? 'unavailable' : String(riskStatus?.status || 'unknown')")
    expect(SRC).toContain("latest.decision_role === 'authoritative' ? 'Authoritative' : `${latest.decision_role || 'legacy'} history`")
    expect(SRC).toContain('latest.proposal_execution_mode')
    expect(SRC).toContain('latest.policy_id')
  })

  it('never chooses a policy without a current, recognized exchange mode', () => {
    expect(classifyRiskPolicyMode('live', true)).toBe('live')
    expect(classifyRiskPolicyMode('demo', true)).toBe('demo')
    expect(classifyRiskPolicyMode('paper', true)).toBe('demo')
    expect(classifyRiskPolicyMode('LIVE', true)).toBe('live')
    for (const mode of [null, undefined, '', 'bitget_live_limited', 'sandbox', 42]) {
      expect(classifyRiskPolicyMode(mode, true)).toBeNull()
    }
    expect(classifyRiskPolicyMode('demo', false)).toBeNull()
    expect(SRC).toContain('const policyMode = classifyRiskPolicyMode(s.exchange_mode, statusAvailable)')
    expect(SRC).toContain('Risk headroom unavailable. Current exchange mode is unavailable.')
    expect(SRC).not.toContain("String(s.exchange_mode || 'demo')")
  })

  it('derives live exposure market labels from open positions only', () => {
    const spotOpenPerpClosed = [
      { symbol: 'BTC/USDT', market_type: 'spot', status: 'open' },
      { symbol: 'BTC/USDT', market_type: 'perp', status: 'closed' },
    ]
    const perpOpenSpotClosed = [
      { symbol: 'ETH/USDT', market_type: 'spot', status: 'closed' },
      { symbol: 'ETH/USDT', market_type: 'perp', status: 'open' },
    ]
    const mixedOpen = [
      { symbol: 'SOL/USDT', market_type: 'perp', status: 'open' },
      { symbol: 'SOL/USDT', market_type: 'spot', status: 'open' },
    ]

    const spotTypes = marketTypesFromOpenPositions(spotOpenPerpClosed)
    expect(fmtSymbol('BTC/USDT', preferPerpMarketType(spotTypes['BTC/USDT']))).toBe('BTC/USDT')
    expect(spotTypes['BTC/USDT']).toEqual(new Set(['spot']))

    const perpTypes = marketTypesFromOpenPositions(perpOpenSpotClosed)
    expect(fmtSymbol('ETH/USDT', preferPerpMarketType(perpTypes['ETH/USDT']))).toBe('ETHUSDT')
    expect(perpTypes['ETH/USDT']).toEqual(new Set(['perp']))

    const mixedTypes = marketTypesFromOpenPositions(mixedOpen)
    expect(preferPerpMarketType(mixedTypes['SOL/USDT'])).toBe('perp')
    expect(mixedTypes['SOL/USDT'].size).toBe(2)

    expect(marketTypesFromOpenPositions([
      { symbol: 'DOGE/USDT', market_type: 'perp', status: 'closed' },
    ])).toEqual({})

    expect(openPositionRows([...spotOpenPerpClosed].reverse()).map(p => p.market_type)).toEqual(['spot'])
  })
})

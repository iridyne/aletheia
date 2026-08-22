import { describe, expect, it } from 'vitest'
import { deriveMasterState } from './systemState'

const healthyRuntime = {
  gatewayRunning: true,
  killed: false,
  paused: false,
  riskState: 'healthy',
  operatorStatus: 'online',
  operatorMode: 'Working',
}

describe('deriveMasterState health truthfulness', () => {
  it('does not report OPERATIONAL when health is degraded', () => {
    expect(deriveMasterState({ ...healthyRuntime, healthState: 'degraded' })).toEqual({
      word: 'CAUTION',
      tone: 'yellow',
      detail: 'System health is degraded — review the health details below.',
    })
  })

  it('does not report green when health is unavailable or unknown', () => {
    expect(deriveMasterState({ ...healthyRuntime, healthState: 'unavailable' }).tone).toBe('blue')
    expect(deriveMasterState({ ...healthyRuntime, healthState: 'unknown' }).word).toBe('STANDBY')
  })

  it('keeps a healthy runtime operational', () => {
    expect(deriveMasterState({ ...healthyRuntime, healthState: 'healthy' })).toEqual({
      word: 'OPERATIONAL',
      tone: 'green',
      detail: 'Agents are running within all risk limits.',
    })
  })

  it('preserves higher-priority safety states over health', () => {
    expect(deriveMasterState({ ...healthyRuntime, killed: true, healthState: 'degraded' }).word).toBe('HALTED')
    expect(deriveMasterState({ ...healthyRuntime, riskState: 'blocked', healthState: 'degraded' }).word).toBe('BLOCKED')
  })
})

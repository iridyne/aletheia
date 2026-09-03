import { describe, expect, it } from 'vitest'
import { deriveMasterState } from './systemState'

const healthyRuntime = {
  gatewayRunning: true,
  killed: false,
  paused: false,
  schedulerEnabled: true,
  schedulerRunning: true,
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

  it('never reports operational when the enabled scheduler is stopped', () => {
    expect(deriveMasterState({ ...healthyRuntime, schedulerRunning: false, healthState: 'healthy' })).toEqual({
      word: 'DEGRADED',
      tone: 'yellow',
      detail: 'Scheduler is stopped — automated jobs are not running.',
    })
  })

  it('keeps disabled and unobserved scheduler state out of green', () => {
    expect(deriveMasterState({ ...healthyRuntime, schedulerEnabled: false, schedulerRunning: false, healthState: 'healthy' }).word).toBe('STANDBY')
    expect(deriveMasterState({ ...healthyRuntime, schedulerRunning: null, healthState: 'healthy' }).word).toBe('STANDBY')
  })

  it('keeps known risk and health warnings above scheduler standby', () => {
    expect(deriveMasterState({
      ...healthyRuntime, schedulerEnabled: false, schedulerRunning: true, riskState: 'warning', healthState: 'healthy',
    }).word).toBe('CAUTION')
    expect(deriveMasterState({
      ...healthyRuntime, schedulerEnabled: null, schedulerRunning: null, healthState: 'degraded',
    }).word).toBe('CAUTION')
  })

  it('describes disabled-but-running layered scheduling without hiding infrastructure jobs', () => {
    expect(deriveMasterState({
      ...healthyRuntime, schedulerEnabled: false, schedulerRunning: true, healthState: 'healthy',
    })).toEqual({
      word: 'STANDBY',
      tone: 'blue',
      detail: 'Autonomous trading jobs are disabled; essential scheduler jobs remain active.',
    })
  })
})

// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import {
  calculateR20RiskReward,
  DEFAULT_ACTIVE_INDICATORS,
  MAIN_INDICATORS,
  registerR20Indicators,
  SUB_INDICATORS,
  updateR20Countdown,
} from './r20KLineChart'

describe('R20 KLineChart indicator model and calculus', () => {
  it('registers VWAP indicator idempotently', async () => {
    await expect(async () => {
      await registerR20Indicators()
      await registerR20Indicators()
    }).not.toThrow()
  })

  it('provides 6 main and 6 sub indicator definitions with correct default golden pair', () => {
    expect(MAIN_INDICATORS).toHaveLength(6)
    expect(SUB_INDICATORS).toHaveLength(6)
    expect(MAIN_INDICATORS.map(ind => ind.key)).toEqual(['VWAP', 'MA', 'EMA', 'BOLL', 'SAR', 'BBI'])
    expect(SUB_INDICATORS.map(ind => ind.key)).toEqual(['VOL', 'MACD', 'RSI', 'KDJ', 'OBV', 'WR'])
    expect(DEFAULT_ACTIVE_INDICATORS.VWAP).toBe(true)
    expect(DEFAULT_ACTIVE_INDICATORS.VOL).toBe(true)
    expect(DEFAULT_ACTIVE_INDICATORS.MA).toBe(false)
  })

  it('computes candle countdowns for minute and hour timeframes', () => {
    // 12:14:30 -> 15m remaining is 30s (00:30)
    const t1 = new Date(2026, 8, 7, 12, 14, 30)
    expect(updateR20Countdown('15m', t1)).toBe('00:30')

    // 12:45:00 -> 1h remaining is 15m (15:00)
    const t2 = new Date(2026, 8, 7, 12, 45, 0)
    expect(updateR20Countdown('1h', t2)).toBe('15:00')

    // 1m remaining 12s when at second 48
    const t3 = new Date(2026, 8, 7, 12, 0, 48)
    expect(updateR20Countdown('1m', t3)).toBe('00:12')
  })

  it('calculates risk-reward metrics accurately for long and short setups', () => {
    const longMetrics = calculateR20RiskReward({
      entry: 100,
      sl: 95,
      tp: 110,
      side: 'long',
      atr: 2.5,
      margin: 100,
      leverage: 3,
    })
    expect(longMetrics.riskDist).toBe(5)
    expect(longMetrics.rewardDist).toBe(10)
    expect(longMetrics.rrRatio).toBe(2)
    expect(longMetrics.isRrCompliant).toBe(true)
    expect(longMetrics.atrMultiple).toBe(2) // 5 / 2.5 = 2
    expect(longMetrics.isAtrOptimal).toBe(true)
    expect(longMetrics.estProfitUsd).toBeCloseTo(30, 2)
    expect(longMetrics.estRiskUsd).toBeCloseTo(15, 2)

    const shortMetrics = calculateR20RiskReward({
      entry: 100,
      sl: 105,
      tp: 90,
      side: 'short',
      atr: 2.5,
      margin: 100,
      leverage: 3,
    })
    expect(shortMetrics.riskDist).toBe(5)
    expect(shortMetrics.rewardDist).toBe(10)
    expect(shortMetrics.rrRatio).toBe(2)
    expect(shortMetrics.isRrCompliant).toBe(true)
  })
})

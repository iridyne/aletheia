import { expect, it } from 'vitest'
import {
  bucketChartMarkers, calculateAtr, calculateBollingerBands, calculateEma, calculateMacd,
  calculateRsi, calculateStochastic, estimateVisibleVolumeProfile, marketDataMetaState,
  type Bar, type ChartMarker,
} from './PriceChart'

it('advances retained metadata age and marks a failed poll stale', () => {
  expect(marketDataMetaState({ ageMs: 5_000, stale: false, receivedAt: 1_000 }, 22_000)).toEqual({
    ageMs: 26_000,
    stale: true,
  })
})

const bar = (time: number, low: number, high: number, volume: number, close = high): Bar => ({
  time, low, high, volume, close, open: low,
})

it('estimates a deterministic visible OHLCV value area from POC outward', () => {
  expect(estimateVisibleVolumeProfile([
    bar(1, 0, 2, 100),
    bar(2, 0, 1, 100),
  ], 0, 1, 2)).toEqual({ poc: 0.5, val: 0, vah: 1 })
})

it('assigns flat candles to the close bin and ignores bars outside the visible range', () => {
  const result = estimateVisibleVolumeProfile([
    bar(1, 0, 2, 1),
    bar(2, 1.5, 1.5, 100, 1.5),
    bar(3, 100, 200, 1_000),
  ], 0, 1, 2)
  expect(result).toEqual({ poc: 1.5, val: 1, vah: 2 })
})

it('reports an unavailable profile without positive volume', () => {
  expect(estimateVisibleVolumeProfile([bar(1, 1, 2, 0), bar(2, 1, 2, 0)], 0, 1)).toBeNull()
})

it('aligns events to the next candle and preserves every event in same-position buckets', () => {
  const bars = [bar(100, 1, 2, 1), bar(200, 1, 2, 1)]
  const markers: ChartMarker[] = [
    { id: 'one', entity: 'opportunity', time: 150, kind: 'long', label: 'Long' },
    { id: 'two', entity: 'trade', time: 175, kind: 'long', label: 'Long' },
    { id: 'three', entity: 'opportunity', time: 180, kind: 'short', label: 'Short' },
  ]
  const buckets = bucketChartMarkers(bars, markers)
  expect(buckets).toHaveLength(2)
  expect(buckets.find(bucket => bucket.position === 'belowBar')?.events.map(event => event.id)).toEqual(['one', 'two'])
  expect(buckets.find(bucket => bucket.position === 'aboveBar')?.events.map(event => event.id)).toEqual(['three'])
  expect(buckets.every(bucket => bucket.time === 200)).toBe(true)
})

const closeBar = (time: number, close: number): Bar => ({ time, open: close, high: close, low: close, close, volume: 1 })

it('returns no RSI when input length is under period + 1', () => {
  expect(calculateRsi(Array.from({ length: 10 }, (_, index) => closeBar(index, index + 1)), 14)).toEqual([])
})

it('reports RSI 100 when every close is rising', () => {
  const bars = Array.from({ length: 20 }, (_, index) => closeBar(index, 100 + index))
  const rsi = calculateRsi(bars, 14)
  expect(rsi).toHaveLength(bars.length - 14)
  for (const point of rsi) expect(point.value).toBe(100)
})

it('reports RSI 0 when every close is falling', () => {
  const bars = Array.from({ length: 20 }, (_, index) => closeBar(index, 100 - index))
  const rsi = calculateRsi(bars, 14)
  expect(rsi).toHaveLength(bars.length - 14)
  for (const point of rsi) expect(point.value).toBe(0)
})

it('reports RSI 100 for a flat series where Wilder loss is zero', () => {
  const bars = Array.from({ length: 20 }, (_, index) => closeBar(index, 100))
  const rsi = calculateRsi(bars, 14)
  expect(rsi).toHaveLength(bars.length - 14)
  expect(rsi[0]).toEqual({ time: 14, value: 100 })
  expect(rsi[rsi.length - 1].time).toBe(19)
})

it('rejects non-finite close prices without partial output', () => {
  const bars: Bar[] = Array.from({ length: 20 }, (_, index) => closeBar(index, 100 + index))
  bars[5] = closeBar(5, Number.NaN)
  expect(calculateRsi(bars, 14)).toEqual([])
})

const constCloseBars = (length: number, close: number): Bar[] =>
  Array.from({ length }, (_, index) => closeBar(index, close))

it('returns EMA equal to constant close and NaN-free from the first complete point', () => {
  const bars = constCloseBars(30, 42)
  const ema = calculateEma(bars, 20)
  expect(ema).toHaveLength(bars.length - 19)
  for (const point of ema) expect(point.value).toBeCloseTo(42, 10)
  expect(ema[0].time).toBe(19)
})

it('rejects EMA when samples are insufficient or a close is non-finite', () => {
  expect(calculateEma(constCloseBars(5, 10), 20)).toEqual([])
  const bars = constCloseBars(30, 42)
  bars[5] = closeBar(5, Number.NaN)
  expect(calculateEma(bars, 20)).toEqual([])
  expect(calculateEma(bars, 1)).toEqual([])
})

it('collapses Bollinger bands onto the mean for a constant series', () => {
  const bars = constCloseBars(30, 42)
  const bb = calculateBollingerBands(bars, 20, 2)
  expect(bb).toHaveLength(bars.length - 19)
  for (const point of bb) {
    expect(point.middle).toBeCloseTo(42, 10)
    expect(point.upper).toBeCloseTo(42, 10)
    expect(point.lower).toBeCloseTo(42, 10)
  }
  expect(bb[0].time).toBe(19)
})

it('rejects Bollinger bands with too-few samples or non-finite closes', () => {
  expect(calculateBollingerBands(constCloseBars(10, 5), 20)).toEqual([])
  const bars = constCloseBars(30, 42)
  bars[7] = closeBar(7, Number.POSITIVE_INFINITY)
  expect(calculateBollingerBands(bars, 20)).toEqual([])
})

it('produces zero MACD / signal / histogram on a flat series', () => {
  const bars = constCloseBars(60, 100)
  const macd = calculateMacd(bars, 12, 26, 9)
  expect(macd.length).toBeGreaterThan(0)
  for (const point of macd) {
    expect(point.macd).toBeCloseTo(0, 10)
    expect(point.signal).toBeCloseTo(0, 10)
    expect(point.histogram).toBeCloseTo(0, 10)
  }
  expect(macd[0].time).toBe(25 + 8)
})

it('rejects MACD when samples are insufficient or closes are non-finite', () => {
  expect(calculateMacd(constCloseBars(20, 1), 12, 26, 9)).toEqual([])
  const bars = constCloseBars(60, 100)
  bars[3] = closeBar(3, Number.NaN)
  expect(calculateMacd(bars, 12, 26, 9)).toEqual([])
})

const ohlcBar = (time: number, open: number, high: number, low: number, close: number): Bar => ({
  time, open, high, low, close, volume: 1,
})

it('reports Stochastic 100 for a strictly rising close series and bounds at 0/100', () => {
  const bars: Bar[] = Array.from({ length: 30 }, (_, index) => ohlcBar(index, index, index + 1, index, index + 1))
  const stoch = calculateStochastic(bars, 14, 3, 3)
  expect(stoch.length).toBeGreaterThan(0)
  for (const point of stoch) {
    expect(point.k).toBeGreaterThanOrEqual(0)
    expect(point.k).toBeLessThanOrEqual(100)
    expect(point.d).toBeGreaterThanOrEqual(0)
    expect(point.d).toBeLessThanOrEqual(100)
  }
  const last = stoch[stoch.length - 1]
  expect(last.k).toBeCloseTo(100, 6)
  expect(last.d).toBeCloseTo(100, 6)
})

it('rejects Stochastic when samples are insufficient or ohlc contain non-finite values', () => {
  expect(calculateStochastic(Array.from({ length: 5 }, (_, index) => ohlcBar(index, 0, 1, 0, 0.5)), 14)).toEqual([])
  const bars: Bar[] = Array.from({ length: 30 }, (_, index) => ohlcBar(index, 0, 1, 0, 0.5))
  bars[10] = ohlcBar(10, 0, Number.NaN, 0, 0.5)
  expect(calculateStochastic(bars, 14)).toEqual([])
})

it('smooths ATR by Wilder against a known 5-TR payload extended for period 3', () => {
  // TRs = [2, 3, 4, 5, 5] (period 3):
  //   seed ATR = (2+3+4)/3 = 3 at bars[3].time
  //   next    = (3*2 + 5)/3 = 11/3 at bars[4].time
  //   next    = (11/3*2 + 5)/3 = 37/9 at bars[5].time
  const bars: Bar[] = [
    ohlcBar(1, 10, 12, 10, 11),
    ohlcBar(2, 11, 13, 11, 12),
    ohlcBar(3, 12, 14, 11, 13),
    ohlcBar(4, 13, 15, 11, 14),
    ohlcBar(5, 14, 16, 11, 15),
    ohlcBar(6, 15, 17, 12, 16),
  ]
  const atr = calculateAtr(bars, 3)
  expect(atr).toHaveLength(3)
  expect(atr[0]).toEqual({ time: 4, value: 3 })
  expect(atr[1].value).toBeCloseTo(11 / 3, 10)
  expect(atr[1].time).toBe(5)
  expect(atr[2].value).toBeCloseTo(37 / 9, 10)
  expect(atr[2].time).toBe(6)
})

it('rejects ATR when samples are insufficient or high/low are non-finite', () => {
  expect(calculateAtr(Array.from({ length: 5 }, (_, index) => ohlcBar(index, 0, 1, 0, 0.5)), 14)).toEqual([])
  const bars: Bar[] = Array.from({ length: 30 }, (_, index) => ohlcBar(index, 0, 1, 0, 0.5))
  bars[5] = ohlcBar(5, 0, Number.NaN, 0, 0.5)
  expect(calculateAtr(bars, 14)).toEqual([])
})

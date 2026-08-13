import { expect, it } from 'vitest'
import {
  bucketChartMarkers, estimateVisibleVolumeProfile, marketDataMetaState, type Bar, type ChartMarker,
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

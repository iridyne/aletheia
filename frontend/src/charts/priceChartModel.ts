
export type MarkerKind = 'long' | 'short' | 'stop_long' | 'stop_short'
export interface ChartMarker {
  id: string
  entity: 'trade' | 'opportunity'
  time: number
  kind: MarkerKind
  label: string
}
export interface ChartMarkerBucket {
  time: number
  position: 'belowBar' | 'aboveBar'
  events: ChartMarker[]
}
export interface PositionLevel {
  id: string
  kind: 'entry' | 'stop' | 'take_profit' | 'liquidation'
  price: number
  title: string
}
export interface VisibleVolumeProfile { poc: number; vah: number; val: number }
export interface Bar { time: number; open: number; high: number; low: number; close: number; volume: number }


const MARKER_POSITIONS: Record<MarkerKind, 'belowBar' | 'aboveBar'> = {
  long: 'belowBar',
  short: 'aboveBar',
  stop_long: 'aboveBar',
  stop_short: 'belowBar',
}

export function bucketChartMarkers(bars: Bar[], markers: ChartMarker[]): ChartMarkerBucket[] {
  const buckets = new Map<string, ChartMarkerBucket>()
  for (const marker of markers) {
    let low = 0
    let high = bars.length
    while (low < high) {
      const middle = Math.floor((low + high) / 2)
      if (bars[middle].time < marker.time) low = middle + 1
      else high = middle
    }
    if (low >= bars.length) continue
    const time = bars[low].time
    const position = MARKER_POSITIONS[marker.kind]
    const key = `${time}|${position}`
    const bucket = buckets.get(key) ?? { time, position, events: [] }
    bucket.events.push(marker)
    buckets.set(key, bucket)
  }
  return [...buckets.values()].sort((a, b) => a.time - b.time)
}

export function estimateVisibleVolumeProfile(
  bars: Bar[], visibleFrom: number, visibleTo: number, binCount = 48, valueAreaRatio = 0.70,
): VisibleVolumeProfile | null {
  if (!Number.isFinite(visibleFrom) || !Number.isFinite(visibleTo) || binCount < 1) return null
  const visible = bars.slice(Math.max(0, Math.floor(visibleFrom)), Math.min(bars.length, Math.ceil(visibleTo) + 1))
  if (visible.length < 2) return null
  const usable = visible.filter(bar => Number.isFinite(bar.low) && Number.isFinite(bar.high) && Number.isFinite(bar.close)
    && Number.isFinite(bar.volume) && bar.volume > 0 && bar.high >= bar.low)
  if (!usable.length) return null
  const low = Math.min(...usable.map(bar => bar.low))
  const high = Math.max(...usable.map(bar => bar.high))
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) return null

  const width = (high - low) / binCount
  const volumes = Array.from({ length: binCount }, () => 0)
  for (const bar of usable) {
    if (bar.high === bar.low) {
      volumes[Math.min(binCount - 1, Math.max(0, Math.floor((bar.close - low) / width)))] += bar.volume
      continue
    }
    const first = Math.max(0, Math.floor((bar.low - low) / width))
    const last = Math.min(binCount - 1, Math.floor((bar.high - low) / width))
    for (let index = first; index <= last; index += 1) {
      const binLow = low + index * width
      const overlap = Math.max(0, Math.min(bar.high, binLow + width) - Math.max(bar.low, binLow))
      volumes[index] += bar.volume * overlap / (bar.high - bar.low)
    }
  }
  const total = volumes.reduce((sum, volume) => sum + volume, 0)
  if (!(total > 0)) return null
  let pocIndex = 0
  for (let index = 1; index < volumes.length; index += 1) if (volumes[index] > volumes[pocIndex]) pocIndex = index
  let left = pocIndex
  let right = pocIndex
  let accumulated = volumes[pocIndex]
  const target = total * Math.min(1, Math.max(0, valueAreaRatio))
  while (accumulated < target && (left > 0 || right < volumes.length - 1)) {
    const leftVolume = left > 0 ? volumes[left - 1] : -1
    const rightVolume = right < volumes.length - 1 ? volumes[right + 1] : -1
    if (leftVolume >= rightVolume) accumulated += volumes[--left]
    else accumulated += volumes[++right]
  }
  return { poc: low + (pocIndex + 0.5) * width, val: low + left * width, vah: low + (right + 1) * width }
}

export interface IndicatorPoint { time: number; value: number }
export interface BollingerPoint { time: number; upper: number; middle: number; lower: number }
export interface MacdPoint { time: number; macd: number; signal: number; histogram: number }
export interface StochasticPoint { time: number; k: number; d: number }

function closesAllFinite(bars: Bar[]): boolean {
  for (const bar of bars) if (!Number.isFinite(bar.close)) return false
  return true
}
function ohlcAllFinite(bars: Bar[]): boolean {
  for (const bar of bars) if (!Number.isFinite(bar.high) || !Number.isFinite(bar.low) || !Number.isFinite(bar.close)) return false
  return true
}

export function calculateRsi(bars: Bar[], period = 14): IndicatorPoint[] {
  if (!Number.isInteger(period) || period < 2) return []
  if (bars.length < period + 1) return []
  if (!closesAllFinite(bars)) return []
  let avgGain = 0
  let avgLoss = 0
  for (let index = 1; index <= period; index += 1) {
    const change = bars[index].close - bars[index - 1].close
    if (change > 0) avgGain += change
    else avgLoss -= change
  }
  avgGain /= period
  avgLoss /= period
  const firstRsi = avgLoss === 0 ? 100 : avgGain === 0 ? 0 : 100 - 100 / (1 + avgGain / avgLoss)
  const output: IndicatorPoint[] = [{ time: bars[period].time, value: firstRsi }]
  for (let index = period + 1; index < bars.length; index += 1) {
    const change = bars[index].close - bars[index - 1].close
    const gain = change > 0 ? change : 0
    const loss = change < 0 ? -change : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    output.push({ time: bars[index].time, value: avgLoss === 0 ? 100 : avgGain === 0 ? 0 : 100 - 100 / (1 + avgGain / avgLoss) })
  }
  return output
}

function emaSeries(closes: number[], period: number): number[] {
  // Returns per-input EMA aligned to closes; positions before the seed are NaN.
  const output = new Array<number>(closes.length).fill(Number.NaN)
  if (closes.length < period) return output
  let seed = 0
  for (let index = 0; index < period; index += 1) seed += closes[index]
  seed /= period
  output[period - 1] = seed
  const alpha = 2 / (period + 1)
  for (let index = period; index < closes.length; index += 1) {
    output[index] = output[index - 1] + alpha * (closes[index] - output[index - 1])
  }
  return output
}

export function calculateEma(bars: Bar[], period: number): IndicatorPoint[] {
  if (!Number.isInteger(period) || period < 2) return []
  if (bars.length < period) return []
  if (!closesAllFinite(bars)) return []
  const closes = bars.map(bar => bar.close)
  const values = emaSeries(closes, period)
  const output: IndicatorPoint[] = []
  for (let index = period - 1; index < bars.length; index += 1) {
    output.push({ time: bars[index].time, value: values[index] })
  }
  return output
}

export function calculateBollingerBands(
  bars: Bar[], period = 20, multiplier = 2,
): BollingerPoint[] {
  if (!Number.isInteger(period) || period < 2) return []
  if (!Number.isFinite(multiplier)) return []
  if (bars.length < period) return []
  if (!closesAllFinite(bars)) return []
  const output: BollingerPoint[] = []
  let sum = 0
  let sumSq = 0
  for (let index = 0; index < bars.length; index += 1) {
    sum += bars[index].close
    sumSq += bars[index].close * bars[index].close
    if (index >= period) {
      sum -= bars[index - period].close
      sumSq -= bars[index - period].close * bars[index - period].close
    }
    if (index >= period - 1) {
      const mean = sum / period
      const variance = Math.max(0, sumSq / period - mean * mean)
      const stddev = Math.sqrt(variance)
      output.push({
        time: bars[index].time,
        middle: mean,
        upper: mean + multiplier * stddev,
        lower: mean - multiplier * stddev,
      })
    }
  }
  return output
}

export function calculateMacd(
  bars: Bar[], fast = 12, slow = 26, signal = 9,
): MacdPoint[] {
  if (!Number.isInteger(fast) || fast < 2) return []
  if (!Number.isInteger(slow) || slow < 2) return []
  if (!Number.isInteger(signal) || signal < 2) return []
  if (fast >= slow) return []
  if (bars.length < slow + signal - 1) return []
  if (!closesAllFinite(bars)) return []
  const closes = bars.map(bar => bar.close)
  const emaFast = emaSeries(closes, fast)
  const emaSlow = emaSeries(closes, slow)
  // MACD defined from index slow-1; signal EMA seeded from slow-1.
  const macdFromSlow: number[] = []
  for (let index = slow - 1; index < bars.length; index += 1) macdFromSlow.push(emaFast[index] - emaSlow[index])
  const signalFromSlow = emaSeries(macdFromSlow, signal)
  const output: MacdPoint[] = []
  for (let index = signal - 1; index < macdFromSlow.length; index += 1) {
    const macd = macdFromSlow[index]
    const sig = signalFromSlow[index]
    output.push({
      time: bars[index + slow - 1].time,
      macd,
      signal: sig,
      histogram: macd - sig,
    })
  }
  return output
}

export function calculateStochastic(
  bars: Bar[], period = 14, smoothK = 3, smoothD = 3,
): StochasticPoint[] {
  if (!Number.isInteger(period) || period < 2) return []
  if (!Number.isInteger(smoothK) || smoothK < 1) return []
  if (!Number.isInteger(smoothD) || smoothD < 1) return []
  if (bars.length < period + smoothK + smoothD - 2) return []
  if (!ohlcAllFinite(bars)) return []
  const rawK: number[] = []
  for (let index = period - 1; index < bars.length; index += 1) {
    let low = Infinity
    let high = -Infinity
    for (let offset = 0; offset < period; offset += 1) {
      const bar = bars[index - offset]
      if (bar.low < low) low = bar.low
      if (bar.high > high) high = bar.high
    }
    const denom = high - low
    rawK.push(denom === 0 ? 0 : ((bars[index].close - low) / denom) * 100)
  }
  const smaWindow = (values: number[], window: number): number[] => {
    if (window <= 1) return values.slice()
    if (values.length < window) return []
    const output: number[] = []
    let sum = 0
    for (let index = 0; index < window; index += 1) sum += values[index]
    output.push(sum / window)
    for (let index = window; index < values.length; index += 1) {
      sum += values[index] - values[index - window]
      output.push(sum / window)
    }
    return output
  }
  const smoothedK = smaWindow(rawK, smoothK) // length = rawK.length - smoothK + 1
  const smoothedD = smaWindow(smoothedK, smoothD) // length = smoothedK.length - smoothD + 1
  const output: StochasticPoint[] = []
  // Time alignment: rawK[i] -> bars[i + period - 1]; smoothedK[j] -> rawK[j + smoothK - 1];
  //   smoothedD[k] -> smoothedK[k + smoothD - 1] -> rawK[k + smoothK + smoothD - 2].
  for (let index = 0; index < smoothedD.length; index += 1) {
    const rawIndex = index + smoothK + smoothD - 2
    output.push({
      time: bars[rawIndex + period - 1].time,
      k: smoothedK[index + smoothD - 1],
      d: smoothedD[index],
    })
  }
  return output
}

export function calculateAtr(bars: Bar[], period = 14): IndicatorPoint[] {
  if (!Number.isInteger(period) || period < 2) return []
  if (bars.length < period + 1) return []
  if (!ohlcAllFinite(bars)) return []
  const trs: number[] = []
  for (let index = 1; index < bars.length; index += 1) {
    const prevClose = bars[index - 1].close
    const high = bars[index].high
    const low = bars[index].low
    trs.push(Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose)))
  }
  // Wilder smoothing: seed = SMA of first `period` TRs, then ATR_t = (ATR_{t-1}*(period-1) + TR_t) / period.
  let atr = 0
  for (let index = 0; index < period; index += 1) atr += trs[index]
  atr /= period
  const output: IndicatorPoint[] = [{ time: bars[period].time, value: atr }]
  for (let index = period; index < trs.length; index += 1) {
    atr = (atr * (period - 1) + trs[index]) / period
    output.push({ time: bars[index + 1].time, value: atr })
  }
  return output
}

export type ChartScaleMode = 'normal' | 'logarithmic' | 'percent' | 'indexed100'
export type CrosshairPreference = 'magnet' | 'normal' | 'hidden'
export type ChartTimezone = 'UTC' | 'local'

export type MainIndicatorId = 'ema20' | 'ema50' | 'bollinger'
export type SubchartIndicatorId = 'rsi' | 'macd' | 'stochastic' | 'atr'
export const MAX_INDICATORS_PER_ZONE = 3
export const MAIN_INDICATOR_IDS: readonly MainIndicatorId[] = ['ema20', 'ema50', 'bollinger']
export const SUBCHART_INDICATOR_IDS: readonly SubchartIndicatorId[] = ['rsi', 'macd', 'stochastic', 'atr']

export interface ChartSettings {
  view: {
    chartType: 'candlestick'
    crosshair: CrosshairPreference
    grid: { horizontal: boolean; vertical: boolean }
    timezone: ChartTimezone
  }
  scale: { mode: ChartScaleMode; autoScale: boolean; priceLabels: boolean }
  overlays: { volume: boolean; profile: boolean; levels: boolean; signals: boolean }
  indicators: { main: MainIndicatorId[]; subcharts: SubchartIndicatorId[] }
}

export const DEFAULT_CHART_SETTINGS: ChartSettings = {
  view: { chartType: 'candlestick', crosshair: 'magnet', grid: { horizontal: true, vertical: true }, timezone: 'UTC' },
  scale: { mode: 'normal', autoScale: true, priceLabels: true },
  overlays: { volume: true, profile: true, levels: true, signals: true },
  indicators: { main: [], subcharts: ['rsi'] },
}

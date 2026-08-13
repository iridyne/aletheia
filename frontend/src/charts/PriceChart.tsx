import { useEffect, useRef, useState } from 'react'
import {
  createChart, LineStyle, type IChartApi, type IPriceLine, type ISeriesApi,
  type LogicalRange, type Time,
} from 'lightweight-charts'
import { api, type OhlcvResult } from '../api/client'
import { chartTheme } from './theme'

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

const INITIAL_LIMIT = 300
const PAGE_LIMIT = 300
const SCROLL_TRIGGER_BARS = 10
const LIVE_POLL_MS = 10_000

const MARKER_CFG: Record<MarkerKind, {
  position: 'belowBar' | 'aboveBar'; shape: 'arrowUp' | 'arrowDown'; color: string; label: string
}> = {
  long: { position: 'belowBar', shape: 'arrowUp', color: chartTheme.markerBuy, label: 'Long' },
  short: { position: 'aboveBar', shape: 'arrowDown', color: chartTheme.markerSell, label: 'Short' },
  stop_long: { position: 'aboveBar', shape: 'arrowDown', color: chartTheme.markerStopLong, label: 'Stop Long' },
  stop_short: { position: 'belowBar', shape: 'arrowUp', color: chartTheme.markerStopShort, label: 'Stop Short' },
}

export function marketDataMetaState(
  meta: { ageMs: number | null; stale: boolean | null; receivedAt: number }, now: number,
) {
  const elapsed = Math.max(0, now - meta.receivedAt)
  return { ageMs: meta.ageMs === null ? null : meta.ageMs + elapsed, stale: meta.stale === true || elapsed > LIVE_POLL_MS * 2 }
}

const toBar = (candle: Bar): Bar => ({ ...candle })

function firstBarAtOrAfter(bars: Bar[], timestamp: number) {
  let low = 0
  let high = bars.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (bars[middle].time < timestamp) low = middle + 1
    else high = middle
  }
  return low < bars.length ? low : null
}

export function bucketChartMarkers(bars: Bar[], markers: ChartMarker[]): ChartMarkerBucket[] {
  const buckets = new Map<string, ChartMarkerBucket>()
  for (const marker of markers) {
    const index = firstBarAtOrAfter(bars, marker.time)
    if (index === null) continue
    const time = bars[index].time
    const position = MARKER_CFG[marker.kind].position
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

function markerSeriesData(buckets: ChartMarkerBucket[]) {
  return buckets.map(bucket => {
    const first = bucket.events[0]
    const cfg = MARKER_CFG[first.kind]
    const label = cfg.label.startsWith('Stop') ? cfg.label : first.label || cfg.label
    return {
      time: bucket.time as Time, position: bucket.position, color: cfg.color, shape: cfg.shape,
      text: `${label}${bucket.events.length > 1 ? ` ×${bucket.events.length}` : ''}`.slice(0, 12),
    }
  })
}

function levelStyle(kind: PositionLevel['kind']) {
  if (kind === 'entry') return { color: chartTheme.markerBuy, lineStyle: LineStyle.Solid }
  if (kind === 'take_profit') return { color: chartTheme.upColor, lineStyle: LineStyle.Dashed }
  return { color: chartTheme.downColor, lineStyle: LineStyle.Dashed }
}

export default function PriceChart({
  symbol, timeframe = '1h', marketType = 'spot', exchange = '', markers = [], positionLevels = [], overlays,
  onMarkerClick, onMetaChange, onProfileChange,
}: {
  symbol: string; timeframe?: string; marketType?: string; exchange?: string
  markers?: ChartMarker[]; positionLevels?: PositionLevel[]
  overlays: { volume: boolean; profile: boolean; levels: boolean; signals: boolean }
  onMarkerClick?: (events: ChartMarker[]) => void
  onMetaChange?: (meta: OhlcvResult['meta'] | null) => void
  onProfileChange?: (profile: VisibleVolumeProfile | null) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const barsRef = useRef<Bar[]>([])
  const timesRef = useRef<Set<number>>(new Set())
  const bucketsRef = useRef<ChartMarkerBucket[]>([])
  const profileLinesRef = useRef<IPriceLine[]>([])
  const levelLinesRef = useRef<IPriceLine[]>([])
  const loadingOlderRef = useRef(false)
  const exhaustedRef = useRef(false)
  const initializedRef = useRef(false)
  const markersRef = useRef(markers)
  const overlaysRef = useRef(overlays)
  const levelsRef = useRef(positionLevels)
  const markerClickRef = useRef(onMarkerClick)
  const metaChangeRef = useRef(onMetaChange)
  const profileChangeRef = useRef(onProfileChange)
  markerClickRef.current = onMarkerClick
  metaChangeRef.current = onMetaChange
  profileChangeRef.current = onProfileChange
  const [status, setStatus] = useState<'loading' | 'ok' | 'empty' | 'error'>('loading')

  const clearLines = (series: ISeriesApi<'Candlestick'>, lines: IPriceLine[]) => {
    for (const line of lines) series.removePriceLine(line)
    lines.length = 0
  }
  const applyLevels = () => {
    const series = candleRef.current
    if (!series) return
    clearLines(series, levelLinesRef.current)
    if (!overlaysRef.current.levels) return
    for (const level of levelsRef.current) {
      if (!Number.isFinite(level.price) || level.price <= 0) continue
      const style = levelStyle(level.kind)
      levelLinesRef.current.push(series.createPriceLine({
        price: level.price, title: level.title, color: style.color, lineStyle: style.lineStyle,
        lineWidth: 1, axisLabelVisible: true,
      }))
    }
  }
  const applyProfile = (range?: LogicalRange | null) => {
    const chart = chartRef.current
    const series = candleRef.current
    if (!chart || !series) return
    clearLines(series, profileLinesRef.current)
    const visible = range ?? chart.timeScale().getVisibleLogicalRange()
    const profile = overlaysRef.current.profile && visible
      ? estimateVisibleVolumeProfile(barsRef.current, visible.from, visible.to) : null
    profileChangeRef.current?.(profile)
    if (!profile) return
    profileLinesRef.current.push(
      series.createPriceLine({ price: profile.poc, title: 'Est. POC', color: chartTheme.lineColor, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true }),
      series.createPriceLine({ price: profile.vah, title: 'Est. VAH', color: chartTheme.borderColor, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }),
      series.createPriceLine({ price: profile.val, title: 'Est. VAL', color: chartTheme.borderColor, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }),
    )
  }
  const applyMarkers = () => {
    const series = candleRef.current
    if (!series) return
    bucketsRef.current = bucketChartMarkers(barsRef.current, markersRef.current)
    series.setMarkers(overlaysRef.current.signals ? markerSeriesData(bucketsRef.current) : [])
  }

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let cancelled = false
    setStatus('loading')
    metaChangeRef.current?.(null)
    profileChangeRef.current?.(null)
    const chart = createChart(container, {
      layout: chartTheme.layout, grid: chartTheme.grid, width: container.clientWidth, height: container.clientHeight,
      timeScale: { timeVisible: true, rightOffset: 4 },
    })
    chartRef.current = chart
    const candles = chart.addCandlestickSeries({
      upColor: chartTheme.upColor, downColor: chartTheme.downColor, borderVisible: false,
      wickUpColor: chartTheme.upColor, wickDownColor: chartTheme.downColor,
    })
    candleRef.current = candles
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '', visible: overlaysRef.current.volume })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.76, bottom: 0 } })
    volumeRef.current = volume
    barsRef.current = []
    timesRef.current = new Set()
    loadingOlderRef.current = false
    exhaustedRef.current = false
    initializedRef.current = false

    const setAllData = () => {
      candles.setData(barsRef.current.map(bar => ({ ...bar, time: bar.time as Time })))
      volume.setData(barsRef.current.map(bar => ({
        time: bar.time as Time, value: bar.volume,
        color: bar.close >= bar.open ? `${chartTheme.upColor}80` : `${chartTheme.downColor}80`,
      })))
      applyMarkers()
      applyLevels()
      applyProfile()
    }
    const loadOlder = async () => {
      if (loadingOlderRef.current || exhaustedRef.current || !initializedRef.current || !barsRef.current.length) return
      loadingOlderRef.current = true
      try {
        const rows = await api.getOhlcv(symbol, timeframe, PAGE_LIMIT, barsRef.current[0].time, exchange, marketType)
        if (cancelled) return
        const fresh = (Array.isArray(rows) ? rows : []).map(toBar).filter(bar => !timesRef.current.has(bar.time))
        if (!fresh.length) { exhaustedRef.current = true; return }
        for (const bar of fresh) timesRef.current.add(bar.time)
        const range = chart.timeScale().getVisibleLogicalRange()
        barsRef.current = [...fresh, ...barsRef.current].sort((a, b) => a.time - b.time)
        setAllData()
        if (range) chart.timeScale().setVisibleLogicalRange({ from: range.from + fresh.length, to: range.to + fresh.length })
      } catch {
        // A later scroll retries a transient history failure.
      } finally {
        loadingOlderRef.current = false
      }
    }
    const pollLatest = async () => {
      if (cancelled || !initializedRef.current || !barsRef.current.length) return
      try {
        const result = await api.getOhlcvWithMeta(symbol, timeframe, 2, marketType, exchange)
        if (cancelled) return
        metaChangeRef.current?.(result.meta)
        for (const bar of result.candles.map(toBar).sort((a, b) => a.time - b.time)) {
          const last = barsRef.current[barsRef.current.length - 1]
          if (!last || bar.time < last.time) continue
          if (bar.time === last.time) barsRef.current[barsRef.current.length - 1] = bar
          else { barsRef.current.push(bar); timesRef.current.add(bar.time) }
        }
        setAllData()
      } catch {
        // The next live tick retries without replacing visible data.
      }
    }
    void (async () => {
      try {
        const result = await api.getOhlcvWithMeta(symbol, timeframe, INITIAL_LIMIT, marketType, exchange)
        if (cancelled) return
        metaChangeRef.current?.(result.meta)
        if (!result.candles.length) { setStatus('empty'); return }
        barsRef.current = result.candles.map(toBar).sort((a, b) => a.time - b.time)
        timesRef.current = new Set(barsRef.current.map(bar => bar.time))
        setAllData()
        chart.timeScale().fitContent()
        initializedRef.current = true
        setStatus('ok')
      } catch {
        if (!cancelled) setStatus('error')
      }
    })()
    const onRangeChange = (range: LogicalRange | null) => {
      if (!range) return
      applyProfile(range)
      if (range.from < SCROLL_TRIGGER_BARS) void loadOlder()
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange)
    const onClick = (param: Parameters<Parameters<typeof chart.subscribeClick>[0]>[0]) => {
      if (!param.time || !markerClickRef.current) return
      const events = bucketsRef.current.filter(bucket => bucket.time === Number(param.time)).flatMap(bucket => bucket.events)
        .sort((a, b) => a.time - b.time)
      if (events.length) markerClickRef.current(events)
    }
    chart.subscribeClick(onClick)
    const poll = window.setInterval(() => void pollLatest(), LIVE_POLL_MS)
    const resizeObserver = new ResizeObserver(entries => {
      const box = entries[0]?.contentRect
      if (box?.width && box.height) chart.applyOptions({ width: box.width, height: box.height })
    })
    resizeObserver.observe(container)
    return () => {
      cancelled = true
      window.clearInterval(poll)
      resizeObserver.disconnect()
      chart.unsubscribeClick(onClick)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRangeChange)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
    }
  }, [exchange, marketType, symbol, timeframe])

  useEffect(() => { markersRef.current = markers; applyMarkers() }, [markers])
  useEffect(() => { levelsRef.current = positionLevels; applyLevels() }, [positionLevels])
  useEffect(() => {
    overlaysRef.current = overlays
    volumeRef.current?.applyOptions({ visible: overlays.volume })
    applyMarkers()
    applyLevels()
    applyProfile()
  }, [overlays])

  return (
    <div className="market-chart-stage" aria-busy={status === 'loading'}>
      <div id="market-price-chart" ref={containerRef} className="market-price-chart" aria-label={`${symbol} candlestick chart`} />
      {status !== 'ok' && <div className="market-chart-state" role="status">
        {status === 'loading' && 'Loading market data…'}
        {status === 'empty' && 'No candle data for this instrument'}
        {status === 'error' && 'Market data unavailable · verify exchange, symbol, and market type'}
      </div>}
    </div>
  )
}

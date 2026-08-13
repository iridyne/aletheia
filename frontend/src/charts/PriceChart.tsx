import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  createChart, CrosshairMode, LineStyle, PriceScaleMode,
  type IChartApi, type IPriceLine, type ISeriesApi, type LogicalRange, type Time,
} from 'lightweight-charts'
import { api, type OhlcvResult } from '../api/client'
import { chartTheme } from './theme'
import {
  bucketChartMarkers, calculateAtr, calculateBollingerBands, calculateEma, calculateMacd,
  calculateRsi, calculateStochastic, DEFAULT_CHART_SETTINGS, estimateVisibleVolumeProfile,
  MAIN_INDICATOR_IDS, MAX_INDICATORS_PER_ZONE, SUBCHART_INDICATOR_IDS,
  type Bar, type ChartMarker, type ChartMarkerBucket, type ChartSettings, type MainIndicatorId,
  type MarkerKind, type PositionLevel, type SubchartIndicatorId, type VisibleVolumeProfile,
} from './priceChartModel'

export type {
  Bar, ChartMarker, ChartMarkerBucket, ChartSettings, MainIndicatorId, MarkerKind,
  PositionLevel, SubchartIndicatorId, VisibleVolumeProfile,
}
export {
  bucketChartMarkers, calculateAtr, calculateBollingerBands, calculateEma, calculateMacd,
  calculateRsi, calculateStochastic, DEFAULT_CHART_SETTINGS, estimateVisibleVolumeProfile,
  MAIN_INDICATOR_IDS, MAX_INDICATORS_PER_ZONE, SUBCHART_INDICATOR_IDS,
}

const INITIAL_LIMIT = 300
const PAGE_LIMIT = 300
const SCROLL_TRIGGER_BARS = 10
const LIVE_POLL_MS = 10_000

const MARKER_CFG: Record<MarkerKind, {
  position: 'belowBar' | 'aboveBar'; shape: 'arrowUp' | 'arrowDown'; color: string; label: string
}> = {
  long: { position: 'belowBar', shape: 'arrowUp', color: chartTheme.markerBuy, label: 'Long' },
  short: { position: 'aboveBar', shape: 'arrowDown', color: chartTheme.markerSell, label: 'Short' },
  stop_long: { position: 'aboveBar', shape: 'arrowDown', color: chartTheme.markerStopLong, label: 'Stop' },
  stop_short: { position: 'belowBar', shape: 'arrowUp', color: chartTheme.markerStopShort, label: 'Stop' },
}

const PRICE_SCALE_MODE: Record<ChartSettings['scale']['mode'], PriceScaleMode> = {
  normal: PriceScaleMode.Normal, logarithmic: PriceScaleMode.Logarithmic,
  percent: PriceScaleMode.Percentage, indexed100: PriceScaleMode.IndexedTo100,
}
const CROSSHAIR_MODE: Record<ChartSettings['view']['crosshair'], CrosshairMode> = {
  magnet: CrosshairMode.Magnet, normal: CrosshairMode.Normal, hidden: CrosshairMode.Hidden,
}

const MAIN_INDICATOR_LEGEND: Record<MainIndicatorId, string> = {
  ema20: 'EMA 20', ema50: 'EMA 50', bollinger: 'BB 20/2',
}
const SUBCHART_PANE_LABEL: Record<SubchartIndicatorId, string> = {
  rsi: 'RSI 14', macd: 'MACD 12/26/9', stochastic: 'Stochastic 14/3/3', atr: 'ATR 14',
}

export function marketDataMetaState(
  meta: { ageMs: number | null; stale: boolean | null; receivedAt: number }, now: number,
) {
  if (meta.ageMs == null) return { ageMs: null, stale: meta.stale }
  const elapsed = Math.max(0, now - meta.receivedAt)
  const ageMs = meta.ageMs + elapsed
  return { ageMs, stale: meta.stale || ageMs > 15_000 }
}

function markerSeriesData(buckets: ChartMarkerBucket[]) {
  return buckets.map(bucket => {
    const label = bucket.events.length > 1 ? `${bucket.events.length}` : bucket.events[0].label
    const cfg = MARKER_CFG[bucket.events[0].kind]
    return { time: bucket.time as Time, position: bucket.position, shape: cfg.shape, color: cfg.color, text: label }
  })
}

function levelStyle(kind: PositionLevel['kind']) {
  if (kind === 'entry') return { color: chartTheme.lineColor, lineStyle: LineStyle.Solid }
  if (kind === 'stop') return { color: chartTheme.markerStopLong, lineStyle: LineStyle.Dashed }
  if (kind === 'take_profit') return { color: chartTheme.markerBuy, lineStyle: LineStyle.Dashed }
  return { color: chartTheme.markerSell, lineStyle: LineStyle.Dotted }
}

function timeFormatter(timezone: ChartSettings['view']['timezone']) {
  const isUtc = timezone === 'UTC'
  const options: Intl.DateTimeFormatOptions = isUtc
    ? { hour12: false, timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
  const formatter = new Intl.DateTimeFormat(undefined, options)
  return {
    label: (time: Time) => formatter.format(new Date(Number(time) * 1000)),
    tick: (time: Time) => formatter.format(new Date(Number(time) * 1000)),
  }
}

export interface PriceChartProps {
  symbol: string
  timeframe?: string
  marketType?: string
  exchange?: string
  markers?: ChartMarker[]
  positionLevels?: PositionLevel[]
  settings: ChartSettings
  onMarkerClick?: (events: ChartMarker[]) => void
  onMetaChange?: (meta: OhlcvResult['meta'] | null) => void
}

interface IndicatorPaneProps {
  id: SubchartIndicatorId
  barsRef: React.MutableRefObject<Bar[]>
  barRevision: number
  settings: ChartSettings
  isBottom: boolean
  onRegister: (id: SubchartIndicatorId, chart: IChartApi) => void
  onUnregister: (id: SubchartIndicatorId) => void
  onRangeChange: (source: SubchartIndicatorId, range: LogicalRange) => void
  getInitialRange: () => LogicalRange | null
}

function IndicatorPane({
  id, barsRef, barRevision, settings, isBottom, onRegister, onUnregister, onRangeChange, getInitialRange,
}: IndicatorPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  // Line/histogram series slots keyed by role.
  const seriesRef = useRef<Record<string, ISeriesApi<'Line'> | ISeriesApi<'Histogram'>>>({})

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const startingSettings = settings
    const formatters = timeFormatter(startingSettings.view.timezone)
    const chart = createChart(container, {
      layout: chartTheme.layout,
      grid: {
        vertLines: { color: chartTheme.grid.vertLines.color, visible: startingSettings.view.grid.vertical },
        horzLines: { color: chartTheme.grid.horzLines.color, visible: false },
      },
      width: container.clientWidth, height: container.clientHeight,
      crosshair: { mode: CROSSHAIR_MODE[startingSettings.view.crosshair] },
      localization: { timeFormatter: formatters.label },
      timeScale: { visible: isBottom, timeVisible: true, rightOffset: 4, tickMarkFormatter: formatters.tick },
      rightPriceScale: { mode: PriceScaleMode.Normal, autoScale: true, scaleMargins: { top: 0.15, bottom: 0.15 } },
      handleScale: { axisPressedMouseMove: false },
    })
    chartRef.current = chart
    const bandedAutoscale = () => ({ priceRange: { minValue: 0, maxValue: 100 } })
    if (id === 'rsi') {
      seriesRef.current.line = chart.addLineSeries({
        color: chartTheme.accountEquityRaw, lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
        title: 'RSI 14', autoscaleInfoProvider: bandedAutoscale,
      })
      seriesRef.current.overbought = chart.addLineSeries({
        color: chartTheme.markerSell, lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: bandedAutoscale,
      })
      seriesRef.current.oversold = chart.addLineSeries({
        color: chartTheme.markerBuy, lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: bandedAutoscale,
      })
    } else if (id === 'macd') {
      seriesRef.current.histogram = chart.addHistogramSeries({
        priceFormat: { type: 'price', precision: 4, minMove: 0.0001 }, priceLineVisible: false, lastValueVisible: false,
      })
      seriesRef.current.macd = chart.addLineSeries({
        color: chartTheme.accountEquityRaw, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: 'MACD',
      })
      seriesRef.current.signal = chart.addLineSeries({
        color: chartTheme.accountEquityAdjusted, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: 'Signal',
      })
    } else if (id === 'stochastic') {
      seriesRef.current.k = chart.addLineSeries({
        color: chartTheme.accountEquityRaw, lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
        title: '%K', autoscaleInfoProvider: bandedAutoscale,
      })
      seriesRef.current.d = chart.addLineSeries({
        color: chartTheme.accountEquityAdjusted, lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
        title: '%D', autoscaleInfoProvider: bandedAutoscale,
      })
      seriesRef.current.overbought = chart.addLineSeries({
        color: chartTheme.markerSell, lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: bandedAutoscale,
      })
      seriesRef.current.oversold = chart.addLineSeries({
        color: chartTheme.markerBuy, lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: bandedAutoscale,
      })
    } else if (id === 'atr') {
      seriesRef.current.line = chart.addLineSeries({
        color: chartTheme.indicatorBlue, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: 'ATR 14',
      })
    }

    const onRange = (range: LogicalRange | null) => {
      if (!range) return
      onRangeChange(id, range)
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange)
    onRegister(id, chart)
    // Adopt current main range if available.
    const initial = getInitialRange()
    if (initial) chart.timeScale().setVisibleLogicalRange(initial)

    const resizeObserver = new ResizeObserver(() => {
      const rect = container.getBoundingClientRect()
      if (rect.width && rect.height) chart.applyOptions({ width: rect.width, height: rect.height })
    })
    resizeObserver.observe(container)
    return () => {
      resizeObserver.disconnect()
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange)
      onUnregister(id)
      chart.remove()
      chartRef.current = null
      seriesRef.current = {}
    }
    // Chart is recreated only when the pane id changes; range/data/settings updates handled by other effects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Update bottom-axis visibility whenever the pane's position changes.
  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { visible: isBottom } })
  }, [isBottom])

  // Apply crosshair, vertical grid, timezone whenever settings change.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const formatters = timeFormatter(settings.view.timezone)
    chart.applyOptions({
      crosshair: { mode: CROSSHAIR_MODE[settings.view.crosshair] },
      grid: {
        vertLines: { color: chartTheme.grid.vertLines.color, visible: settings.view.grid.vertical },
        horzLines: { color: chartTheme.grid.horzLines.color, visible: false },
      },
      localization: { timeFormatter: formatters.label },
      timeScale: { tickMarkFormatter: formatters.tick, visible: isBottom },
    })
  }, [settings, isBottom])

  // Recompute indicator data whenever bar revision or settings change.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const bars = barsRef.current
    const guide = bars.map(bar => ({ time: bar.time as Time }))
    if (id === 'rsi') {
      const points = calculateRsi(bars, 14)
      ;(seriesRef.current.line as ISeriesApi<'Line'> | undefined)?.setData(points.map(point => ({ time: point.time as Time, value: point.value })))
      ;(seriesRef.current.overbought as ISeriesApi<'Line'> | undefined)?.setData(guide.map(row => ({ time: row.time, value: 70 })))
      ;(seriesRef.current.oversold as ISeriesApi<'Line'> | undefined)?.setData(guide.map(row => ({ time: row.time, value: 30 })))
    } else if (id === 'macd') {
      const points = calculateMacd(bars, 12, 26, 9)
      ;(seriesRef.current.macd as ISeriesApi<'Line'> | undefined)?.setData(points.map(point => ({ time: point.time as Time, value: point.macd })))
      ;(seriesRef.current.signal as ISeriesApi<'Line'> | undefined)?.setData(points.map(point => ({ time: point.time as Time, value: point.signal })))
      ;(seriesRef.current.histogram as ISeriesApi<'Histogram'> | undefined)?.setData(points.map(point => ({
        time: point.time as Time, value: point.histogram,
        color: point.histogram >= 0 ? `${chartTheme.upColor}b0` : `${chartTheme.downColor}b0`,
      })))
    } else if (id === 'stochastic') {
      const points = calculateStochastic(bars, 14, 3, 3)
      ;(seriesRef.current.k as ISeriesApi<'Line'> | undefined)?.setData(points.map(point => ({ time: point.time as Time, value: point.k })))
      ;(seriesRef.current.d as ISeriesApi<'Line'> | undefined)?.setData(points.map(point => ({ time: point.time as Time, value: point.d })))
      ;(seriesRef.current.overbought as ISeriesApi<'Line'> | undefined)?.setData(guide.map(row => ({ time: row.time, value: 80 })))
      ;(seriesRef.current.oversold as ISeriesApi<'Line'> | undefined)?.setData(guide.map(row => ({ time: row.time, value: 20 })))
    } else if (id === 'atr') {
      const points = calculateAtr(bars, 14)
      ;(seriesRef.current.line as ISeriesApi<'Line'> | undefined)?.setData(points.map(point => ({ time: point.time as Time, value: point.value })))
    }
  }, [id, barRevision, barsRef])

  return (
    <div className="market-indicator-pane" data-indicator-id={id}>
      <span className="market-indicator-pane-label">{SUBCHART_PANE_LABEL[id]}</span>
      <div
        ref={containerRef}
        className="market-indicator-pane-canvas"
        aria-label={`${SUBCHART_PANE_LABEL[id]} sub-chart`}
        data-chart-library="lightweight-charts"
      />
    </div>
  )
}

export default function PriceChart({
  symbol, timeframe = '1h', marketType = 'spot', exchange = '', markers = [], positionLevels = [],
  settings, onMarkerClick, onMetaChange,
}: PriceChartProps) {
  const stageRef = useRef<HTMLDivElement>(null)
  const priceContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const ema20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ema50Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const bbUpperRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bbMiddleRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bbLowerRef = useRef<ISeriesApi<'Line'> | null>(null)
  const barsRef = useRef<Bar[]>([])
  const timesRef = useRef<Set<number>>(new Set())
  const bucketsRef = useRef<ChartMarkerBucket[]>([])
  const profileLinesRef = useRef<IPriceLine[]>([])
  const levelLinesRef = useRef<IPriceLine[]>([])
  const loadingOlderRef = useRef(false)
  const exhaustedRef = useRef(false)
  const initializedRef = useRef(false)
  const isSyncingRangeRef = useRef(false)
  const markersRef = useRef(markers)
  const settingsRef = useRef(settings)
  const levelsRef = useRef(positionLevels)
  const markerClickRef = useRef(onMarkerClick)
  const metaChangeRef = useRef(onMetaChange)
  const paneChartsRef = useRef<Map<SubchartIndicatorId, IChartApi>>(new Map())
  markerClickRef.current = onMarkerClick
  metaChangeRef.current = onMetaChange
  const [status, setStatus] = useState<'loading' | 'ok' | 'empty' | 'error'>('loading')
  const [barRevision, setBarRevision] = useState(0)

  // Snapshot subchart ids to keep prop stable when parent passes a fresh array each render.
  const subcharts = useMemo(
    () => settings.indicators.subcharts.filter(id => SUBCHART_INDICATOR_IDS.includes(id)),
    [settings.indicators.subcharts],
  )

  const clearLines = (series: ISeriesApi<'Candlestick'>, lines: IPriceLine[]) => {
    for (const line of lines) series.removePriceLine(line)
    lines.length = 0
  }
  const applyLevels = () => {
    const series = candleRef.current
    if (!series) return
    clearLines(series, levelLinesRef.current)
    if (!settingsRef.current.overlays.levels) return
    const axisLabelVisible = settingsRef.current.scale.priceLabels
    for (const level of levelsRef.current) {
      if (!Number.isFinite(level.price) || level.price <= 0) continue
      const style = levelStyle(level.kind)
      levelLinesRef.current.push(series.createPriceLine({
        price: level.price, title: level.title, color: style.color, lineStyle: style.lineStyle,
        lineWidth: 1, axisLabelVisible,
      }))
    }
  }
  const applyProfile = (range?: LogicalRange | null) => {
    const chart = chartRef.current
    const series = candleRef.current
    if (!chart || !series) return
    clearLines(series, profileLinesRef.current)
    const visible = range ?? chart.timeScale().getVisibleLogicalRange()
    const profile = settingsRef.current.overlays.profile && visible
      ? estimateVisibleVolumeProfile(barsRef.current, visible.from, visible.to) : null
    if (!profile) return
    const axisLabelVisible = settingsRef.current.scale.priceLabels
    profileLinesRef.current.push(
      series.createPriceLine({ price: profile.poc, title: 'Est. POC', color: chartTheme.lineColor, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible }),
      series.createPriceLine({ price: profile.vah, title: 'Est. VAH', color: chartTheme.borderColor, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible }),
      series.createPriceLine({ price: profile.val, title: 'Est. VAL', color: chartTheme.borderColor, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible }),
    )
  }
  const applyMarkers = () => {
    const series = candleRef.current
    if (!series) return
    bucketsRef.current = bucketChartMarkers(barsRef.current, markersRef.current)
    series.setMarkers(settingsRef.current.overlays.signals ? markerSeriesData(bucketsRef.current) : [])
  }
  const applyMainIndicators = () => {
    const bars = barsRef.current
    const active = new Set(settingsRef.current.indicators.main)
    const ema20Points = active.has('ema20') ? calculateEma(bars, 20) : []
    const ema50Points = active.has('ema50') ? calculateEma(bars, 50) : []
    const bbPoints = active.has('bollinger') ? calculateBollingerBands(bars, 20, 2) : []
    ema20Ref.current?.setData(ema20Points.map(point => ({ time: point.time as Time, value: point.value })))
    ema50Ref.current?.setData(ema50Points.map(point => ({ time: point.time as Time, value: point.value })))
    bbUpperRef.current?.setData(bbPoints.map(point => ({ time: point.time as Time, value: point.upper })))
    bbMiddleRef.current?.setData(bbPoints.map(point => ({ time: point.time as Time, value: point.middle })))
    bbLowerRef.current?.setData(bbPoints.map(point => ({ time: point.time as Time, value: point.lower })))
  }

  useEffect(() => {
    const priceContainer = priceContainerRef.current
    if (!priceContainer) return
    let cancelled = false
    setStatus('loading')
    metaChangeRef.current?.(null)
    const startingSettings = settingsRef.current
    const formatters = timeFormatter(startingSettings.view.timezone)
    const showMainTimeAxis = settingsRef.current.indicators.subcharts.length === 0
    const chart = createChart(priceContainer, {
      layout: chartTheme.layout,
      grid: {
        vertLines: { color: chartTheme.grid.vertLines.color, visible: startingSettings.view.grid.vertical },
        horzLines: { color: chartTheme.grid.horzLines.color, visible: startingSettings.view.grid.horizontal },
      },
      width: priceContainer.clientWidth, height: priceContainer.clientHeight,
      crosshair: { mode: CROSSHAIR_MODE[startingSettings.view.crosshair] },
      localization: { timeFormatter: formatters.label },
      timeScale: { visible: showMainTimeAxis, timeVisible: true, rightOffset: 4, tickMarkFormatter: formatters.tick },
      rightPriceScale: { mode: PRICE_SCALE_MODE[startingSettings.scale.mode], autoScale: startingSettings.scale.autoScale },
    })
    chartRef.current = chart
    const candles = chart.addCandlestickSeries({
      upColor: chartTheme.upColor, downColor: chartTheme.downColor, borderVisible: false,
      wickUpColor: chartTheme.upColor, wickDownColor: chartTheme.downColor,
      priceLineVisible: startingSettings.scale.priceLabels, lastValueVisible: startingSettings.scale.priceLabels,
    })
    candleRef.current = candles
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '', visible: startingSettings.overlays.volume })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.76, bottom: 0 } })
    volumeRef.current = volume
    // Main-chart indicator series overlay on the price scale.
    ema20Ref.current = chart.addLineSeries({
      color: chartTheme.lineColor, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: MAIN_INDICATOR_LEGEND.ema20,
    })
    ema50Ref.current = chart.addLineSeries({
      color: chartTheme.accountEquityAdjusted, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: MAIN_INDICATOR_LEGEND.ema50,
    })
    bbUpperRef.current = chart.addLineSeries({
      color: chartTheme.indicatorBlue, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'BB upper',
    })
    bbMiddleRef.current = chart.addLineSeries({
      color: chartTheme.indicatorBlue, lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, title: MAIN_INDICATOR_LEGEND.bollinger,
    })
    bbLowerRef.current = chart.addLineSeries({
      color: chartTheme.indicatorBlue, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'BB lower',
    })

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
      applyMainIndicators()
      setBarRevision(revision => revision + 1)
    }
    const loadOlder = async () => {
      if (loadingOlderRef.current || exhaustedRef.current || !initializedRef.current || !barsRef.current.length) return
      loadingOlderRef.current = true
      try {
        const rows = await api.getOhlcv(symbol, timeframe, PAGE_LIMIT, barsRef.current[0].time, exchange, marketType)
        if (cancelled) return
        const fresh = (Array.isArray(rows) ? rows : []).filter(bar => !timesRef.current.has(bar.time))
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
        for (const bar of result.candles.sort((a, b) => a.time - b.time)) {
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
        barsRef.current = result.candles.sort((a, b) => a.time - b.time)
        timesRef.current = new Set(barsRef.current.map(bar => bar.time))
        setAllData()
        chart.timeScale().fitContent()
        const initial = chart.timeScale().getVisibleLogicalRange()
        if (initial) {
          for (const paneChart of paneChartsRef.current.values()) paneChart.timeScale().setVisibleLogicalRange(initial)
        }
        initializedRef.current = true
        setStatus('ok')
      } catch {
        if (!cancelled) setStatus('error')
      }
    })()
    const onMainRange = (range: LogicalRange | null) => {
      if (!range) return
      applyProfile(range)
      if (range.from < SCROLL_TRIGGER_BARS) void loadOlder()
      if (isSyncingRangeRef.current) return
      isSyncingRangeRef.current = true
      for (const paneChart of paneChartsRef.current.values()) paneChart.timeScale().setVisibleLogicalRange(range)
      queueMicrotask(() => { isSyncingRangeRef.current = false })
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onMainRange)
    const onClick = (param: Parameters<Parameters<typeof chart.subscribeClick>[0]>[0]) => {
      if (!param.time || !markerClickRef.current) return
      const events = bucketsRef.current.filter(bucket => bucket.time === Number(param.time)).flatMap(bucket => bucket.events)
        .sort((a, b) => a.time - b.time)
      if (events.length) markerClickRef.current(events)
    }
    chart.subscribeClick(onClick)
    const poll = window.setInterval(() => void pollLatest(), LIVE_POLL_MS)
    const resizeObserver = new ResizeObserver(() => {
      const priceRect = priceContainer.getBoundingClientRect()
      if (priceRect.width && priceRect.height) chart.applyOptions({ width: priceRect.width, height: priceRect.height })
    })
    resizeObserver.observe(priceContainer)
    return () => {
      cancelled = true
      window.clearInterval(poll)
      resizeObserver.disconnect()
      chart.unsubscribeClick(onClick)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onMainRange)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
      ema20Ref.current = null
      ema50Ref.current = null
      bbUpperRef.current = null
      bbMiddleRef.current = null
      bbLowerRef.current = null
    }
  }, [exchange, marketType, symbol, timeframe])

  useEffect(() => { markersRef.current = markers; applyMarkers() }, [markers])
  useEffect(() => { levelsRef.current = positionLevels; applyLevels() }, [positionLevels])

  useEffect(() => {
    settingsRef.current = settings
    const chart = chartRef.current
    const candles = candleRef.current
    if (chart) {
      const formatters = timeFormatter(settings.view.timezone)
      chart.applyOptions({
        crosshair: { mode: CROSSHAIR_MODE[settings.view.crosshair] },
        grid: {
          vertLines: { color: chartTheme.grid.vertLines.color, visible: settings.view.grid.vertical },
          horzLines: { color: chartTheme.grid.horzLines.color, visible: settings.view.grid.horizontal },
        },
        localization: { timeFormatter: formatters.label },
        timeScale: {
          tickMarkFormatter: formatters.tick,
          visible: settings.indicators.subcharts.length === 0,
        },
      })
      chart.priceScale('right').applyOptions({
        mode: PRICE_SCALE_MODE[settings.scale.mode], autoScale: settings.scale.autoScale,
      })
    }
    candles?.applyOptions({
      priceLineVisible: settings.scale.priceLabels, lastValueVisible: settings.scale.priceLabels,
    })
    volumeRef.current?.applyOptions({ visible: settings.overlays.volume })
    applyMarkers()
    applyLevels()
    applyProfile()
    applyMainIndicators()
    // Bump revision so mounted panes recompute against latest settings-independent data if needed.
  }, [settings])

  const registerPane = (id: SubchartIndicatorId, chart: IChartApi) => {
    paneChartsRef.current.set(id, chart)
  }
  const unregisterPane = (id: SubchartIndicatorId) => {
    paneChartsRef.current.delete(id)
  }
  const onPaneRange = (source: SubchartIndicatorId, range: LogicalRange) => {
    if (isSyncingRangeRef.current) return
    const mainChart = chartRef.current
    if (!mainChart) return
    isSyncingRangeRef.current = true
    mainChart.timeScale().setVisibleLogicalRange(range)
    for (const [otherId, paneChart] of paneChartsRef.current) {
      if (otherId === source) continue
      paneChart.timeScale().setVisibleLogicalRange(range)
    }
    queueMicrotask(() => { isSyncingRangeRef.current = false })
  }
  const getInitialRange = () => chartRef.current?.timeScale().getVisibleLogicalRange() ?? null

  // Subchart region height in px; null means "auto" — resolved to ~28% of stage height on mount / when count changes.
  const [subchartRegionPx, setSubchartRegionPx] = useState<number | null>(null)
  const draggingRef = useRef(false)
  // Reset auto sizing when the number of subcharts changes.
  useEffect(() => {
    setSubchartRegionPx(null)
  }, [subcharts.length])

  useLayoutEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const count = subcharts.length
    if (count === 0) {
      stage.style.setProperty('--market-chart-grid-rows', 'minmax(0, 1fr)')
      return
    }
    const stageHeight = stage.getBoundingClientRect().height
    // Auto default: 28% of stage, clamped so both regions stay visible.
    const minSub = Math.max(80, 60 * count)
    const maxSub = Math.max(minSub, stageHeight - 120)
    const auto = Math.min(maxSub, Math.max(minSub, Math.round(stageHeight * 0.28 + (count - 1) * 60)))
    const chosen = subchartRegionPx == null ? auto : Math.min(maxSub, Math.max(minSub, subchartRegionPx))
    stage.style.setProperty('--market-chart-grid-rows', `minmax(80px, 1fr) 6px ${chosen}px`)
  }, [subcharts, subchartRegionPx])

  const onDividerPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const stage = stageRef.current
    if (!stage) return
    ;(event.currentTarget as HTMLDivElement).setPointerCapture(event.pointerId)
    draggingRef.current = true
    document.body.style.cursor = 'row-resize'
    const stageRect = stage.getBoundingClientRect()
    const count = subcharts.length
    const minSub = Math.max(80, 60 * count)
    const maxSub = Math.max(minSub, stageRect.height - 120)
    const onMove = (moveEvent: PointerEvent) => {
      if (!draggingRef.current) return
      // Distance from stage bottom to pointer = desired subchart region height.
      const next = Math.min(maxSub, Math.max(minSub, stageRect.bottom - moveEvent.clientY))
      setSubchartRegionPx(next)
    }
    const onUp = (upEvent: PointerEvent) => {
      draggingRef.current = false
      document.body.style.cursor = ''
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      ;(event.currentTarget as HTMLDivElement | null)?.releasePointerCapture?.(upEvent.pointerId)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  return (
    <div
      ref={stageRef}
      className="market-chart-stage"
      aria-busy={status === 'loading'}
      data-chart-engine="tradingview-lite"
      data-subchart-count={subcharts.length}
    >
      <div className="market-price-chart-wrap">
        <div
          ref={priceContainerRef}
          id="market-price-chart"
          className="market-price-chart"
          aria-label={`${symbol} candlestick chart`}
          data-chart-library="lightweight-charts"
        />
        {settings.indicators.main.length > 0 && (
          <div className="market-chart-indicator-legend" aria-label="Main chart indicators">
            {settings.indicators.main.map(id => (
              <span key={id} className="market-chart-indicator-legend-item" data-indicator-id={id}>
                {MAIN_INDICATOR_LEGEND[id]}
              </span>
            ))}
          </div>
        )}
      </div>
      {subcharts.length > 0 && (
        <div
          className="market-chart-divider"
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize sub-chart area"
          onPointerDown={onDividerPointerDown}
        />
      )}
      {subcharts.length > 0 && (
        <div
          className="market-subchart-region"
          style={{ gridTemplateRows: `repeat(${subcharts.length}, minmax(0, 1fr))` }}
        >
          {subcharts.map((id, index) => (
            <IndicatorPane
              key={id}
              id={id}
              barsRef={barsRef}
              barRevision={barRevision}
              settings={settings}
              isBottom={index === subcharts.length - 1}
              onRegister={registerPane}
              onUnregister={unregisterPane}
              onRangeChange={onPaneRange}
              getInitialRange={getInitialRange}
            />
          ))}
        </div>
      )}
      {status !== 'ok' && <div className="market-chart-state" role="status">
        {status === 'loading' && 'Loading market data…'}
        {status === 'empty' && 'No candle data for this instrument'}
        {status === 'error' && 'Market data unavailable · verify exchange, symbol, and market type'}
      </div>}
    </div>
  )
}

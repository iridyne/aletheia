import { useEffect, useRef, useState } from 'react'
import {
  createChart, type IChartApi, type ISeriesApi, type Time, type LogicalRange,
} from 'lightweight-charts'
import { api } from '../api/client'
import { chartTheme } from './theme'

export type MarkerKind = 'long' | 'short' | 'stop_long' | 'stop_short'
export interface TradeMarker { time: number; kind: MarkerKind; price: number; id?: string }

interface Bar { time: number; open: number; high: number; low: number; close: number }

// How many candles to load up-front and per scroll-back page.
const INITIAL_LIMIT = 300
const PAGE_LIMIT = 300
// Trigger a history page when the left-most visible bar is within this many bars of the start.
const SCROLL_TRIGGER_BARS = 10
// Live tail: re-poll the newest candles on this cadence (ms).
const LIVE_POLL_MS = 10_000

export function marketDataMetaState(
  meta: { ageMs: number | null; stale: boolean | null; receivedAt: number },
  now: number,
) {
  const elapsed = Math.max(0, now - meta.receivedAt)
  return {
    ageMs: meta.ageMs === null ? null : meta.ageMs + elapsed,
    stale: meta.stale === true || elapsed > LIVE_POLL_MS * 2,
  }
}

function toBar(c: any): Bar {
  return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }
}

const MARKER_CFG: Record<MarkerKind, { position: 'belowBar' | 'aboveBar'; shape: 'arrowUp' | 'arrowDown'; color: string; label: string }> = {
  long:       { position: 'belowBar', shape: 'arrowUp',   color: chartTheme.markerBuy,       label: 'Long' },
  short:      { position: 'aboveBar', shape: 'arrowDown', color: chartTheme.markerSell,      label: 'Short' },
  stop_long:  { position: 'aboveBar', shape: 'arrowDown', color: chartTheme.markerStopLong,  label: 'Stop Long' },
  stop_short: { position: 'belowBar', shape: 'arrowUp',   color: chartTheme.markerStopShort, label: 'Stop Short' },
}

function seriesMarkers(markers: TradeMarker[]) {
  return [...markers]
    .sort((a, b) => a.time - b.time)
    .map(m => {
      const cfg = MARKER_CFG[m.kind]
      return { time: m.time as Time, position: cfg.position, color: cfg.color, shape: cfg.shape, text: cfg.label }
    })
}

/**
 * Candlestick chart with exchange-style dynamic loading:
 *  - loads the most recent `INITIAL_LIMIT` candles on mount,
 *  - lazily pages OLDER history as the user scrolls/zooms toward the left edge
 *    (fetched via `before=<earliest candle>` and prepended), and
 *  - keeps the right edge LIVE by polling the newest candles every LIVE_POLL_MS
 *    and calling series.update() on the forming/closed bar.
 *
 * State is held imperatively in refs (the chart is long-lived, not re-rendered
 * per data change) so paging never resets the user's scroll position.
 */
export default function PriceChart({ symbol, timeframe = '1h', marketType = 'spot', markers = [], onMarkerClick }: {
  // #115 fix: PriceChart used to default every getOhlcv call to spot (client
  // signature marketType='spot'), so a perp instrument's chart quietly pulled
  // spot bars — the price would look off vs the true perp mark. Take
  // marketType as a prop; callers pass it based on the symbol's board /
  // context. Defaults to 'spot' for backward compat with any caller not yet
  // updated.
  symbol: string; timeframe?: string; marketType?: string; markers?: TradeMarker[]; onMarkerClick?: (id: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  // Ascending, deduped candle store + index of known times.
  const barsRef = useRef<Bar[]>([])
  const timesRef = useRef<Set<number>>(new Set())
  // Guards: one history fetch in flight at a time; stop paging once exhausted.
  const loadingOlderRef = useRef(false)
  const exhaustedRef = useRef(false)
  const initializedRef = useRef(false)
  // Latest markers, read by the click handler + re-applied without rebuilding the chart.
  const markersRef = useRef<TradeMarker[]>(markers)
  const markerClickRef = useRef(onMarkerClick)
  markerClickRef.current = onMarkerClick

  const [status, setStatus] = useState<'loading' | 'ok' | 'empty' | 'error'>('loading')
  const [dataMeta, setDataMeta] = useState<{
    source: string; asOf: string | null; ageMs: number | null; stale: boolean | null; receivedAt: number
  } | null>(null)
  const [now, setNow] = useState(() => Date.now())

  // Main lifecycle: rebuild whenever the requested market-data identity changes.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let cancelled = false
    setStatus('loading')
    setDataMeta(null)

    const chart = createChart(container, {
      layout: chartTheme.layout,
      grid: chartTheme.grid,
      width: container.clientWidth,
      height: 320,
      timeScale: { timeVisible: true, rightOffset: 4 },
    })
    chartRef.current = chart
    const series = chart.addCandlestickSeries({
      upColor: chartTheme.upColor, downColor: chartTheme.downColor, borderVisible: false,
      wickUpColor: chartTheme.upColor, wickDownColor: chartTheme.downColor,
    })
    seriesRef.current = series

    // Reset the imperative store for the new symbol/timeframe.
    barsRef.current = []
    timesRef.current = new Set()
    loadingOlderRef.current = false
    exhaustedRef.current = false
    initializedRef.current = false

    const applyMarkers = () => series.setMarkers(seriesMarkers(markersRef.current))

    // Prepend a page of older candles when the user scrolls toward the start.
    const loadOlder = async () => {
      if (loadingOlderRef.current || exhaustedRef.current) return
      if (!initializedRef.current || !barsRef.current.length) return
      loadingOlderRef.current = true
      const earliest = barsRef.current[0].time
      try {
        const res = await api.getOhlcv(symbol, timeframe, PAGE_LIMIT, earliest, '', marketType)
        if (cancelled) return
        const rows = Array.isArray(res) ? res : []
        const fresh = rows.map(toBar).filter(b => !timesRef.current.has(b.time))
        if (!fresh.length) { exhaustedRef.current = true; return }
        for (const b of fresh) timesRef.current.add(b.time)
        barsRef.current = [...fresh, ...barsRef.current].sort((a, b) => a.time - b.time)
        // Preserve the visible window: setData re-indexes bars, so shift the logical
        // range right by the number we prepended — the view stays put instead of jumping.
        const range = chart.timeScale().getVisibleLogicalRange()
        series.setData(barsRef.current.map(b => ({ ...b, time: b.time as Time })))
        applyMarkers()
        if (range) {
          const added = fresh.length
          chart.timeScale().setVisibleLogicalRange({ from: range.from + added, to: range.to + added })
        }
      } catch {
        // transient network error — leave guards so a later scroll retries
      } finally {
        loadingOlderRef.current = false
      }
    }

    // Keep the right edge live: merge the newest candles onto the tail.
    const pollLatest = async () => {
      if (cancelled || !initializedRef.current || !barsRef.current.length) return
      try {
        const res = await api.getOhlcvWithMeta(symbol, timeframe, 2, marketType)
        if (cancelled) return
        setDataMeta({ ...res.meta, receivedAt: Date.now() })
        const rows = res.candles.map(toBar).sort((a, b) => a.time - b.time)
        let appended = false
        for (const b of rows) {
          const last = barsRef.current[barsRef.current.length - 1]
          if (!last || b.time < last.time) continue   // update() requires time >= last
          series.update({ ...b, time: b.time as Time })
          if (b.time === last.time) {
            barsRef.current[barsRef.current.length - 1] = b   // replace forming/closed bar
          } else {
            barsRef.current.push(b)
            timesRef.current.add(b.time)
            appended = true
          }
        }
        if (appended) applyMarkers()
      } catch {
        // transient — next tick retries
      }
    }

    // Initial load: most recent INITIAL_LIMIT candles.
    ;(async () => {
      try {
        const res = await api.getOhlcvWithMeta(symbol, timeframe, INITIAL_LIMIT, marketType)
        if (cancelled) return
        const rows = res.candles
        setDataMeta({ ...res.meta, receivedAt: Date.now() })
        if (!rows.length) { setStatus('empty'); return }
        barsRef.current = rows.map(toBar)
        timesRef.current = new Set(barsRef.current.map(b => b.time))
        series.setData(barsRef.current.map(b => ({ ...b, time: b.time as Time })))
        applyMarkers()
        chart.timeScale().fitContent()
        initializedRef.current = true
        setStatus('ok')
      } catch {
        if (!cancelled) setStatus('error')
      }
    })()

    const onRangeChange = (range: LogicalRange | null) => {
      if (!range) return
      if (range.from < SCROLL_TRIGGER_BARS) void loadOlder()
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange)

    const handleClick = (param: Parameters<Parameters<typeof chart.subscribeClick>[0]>[0]) => {
      if (!param.time || !markerClickRef.current) return
      const t = param.time as number
      const matched = markersRef.current.find(m => m.time === t && m.id)
      if (matched?.id) markerClickRef.current(matched.id)
    }
    chart.subscribeClick(handleClick)

    const poll = window.setInterval(() => void pollLatest(), LIVE_POLL_MS)
    const clock = window.setInterval(() => setNow(Date.now()), LIVE_POLL_MS)
    const resizeObserver = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width
      if (width) chart.applyOptions({ width })
    })
    resizeObserver.observe(container)

    return () => {
      cancelled = true
      window.clearInterval(poll)
      window.clearInterval(clock)
      resizeObserver.disconnect()
      chart.unsubscribeClick(handleClick)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRangeChange)
      chart.remove()
      seriesRef.current = null
      chartRef.current = null
    }
  }, [symbol, timeframe, marketType])

  // Re-apply markers without rebuilding the chart when the prop changes.
  useEffect(() => {
    markersRef.current = markers
    if (seriesRef.current) seriesRef.current.setMarkers(seriesMarkers(markers))
  }, [markers])

  const metaState = dataMeta ? marketDataMetaState(dataMeta, now) : null

  return (
    <div>
      {status === 'error' && <p className="hint">Could not load market data (configure exchange / network).</p>}
      {status === 'empty' && <p className="hint">No candle data yet.</p>}
      {dataMeta && (
        <p className="hint" title={dataMeta.asOf ?? undefined}>
          Source: {dataMeta.source} · As of: {metaState?.ageMs === null ? 'not available' : `${Math.floor((metaState?.ageMs ?? 0) / 60_000)}m ago`}
          {metaState?.stale ? ' · stale' : ''}
        </p>
      )}
      <div ref={containerRef} style={{ width: '100%' }} />
      {markers.length > 0 && (
        <div className="chart-legend">
          <span style={{ color: chartTheme.markerBuy }}>▲ Long</span>
          <span style={{ color: chartTheme.markerSell }}>▼ Short</span>
          <span style={{ color: chartTheme.markerStopLong }}>▼ Stop Long</span>
          <span style={{ color: chartTheme.markerStopShort }}>▲ Stop Short</span>
        </div>
      )}
    </div>
  )
}

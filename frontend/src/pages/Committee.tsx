import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, type CommitteeEvaluation, type CommitteeOverviewResponse } from '../api/client'
import PageHeader from '../components/PageHeader'
import MetricTile from '../components/MetricTile'
import { Badge, Button, Card, CardHeader, TableWrap } from '../components/ui'
import { fmtPct, fmtSymbol, fmtTimestamp, humanize, normalizeIso } from '../lib/format'

/**
 * AI Jury — the standalone Investment Committee workbench.
 *
 * Dedicated home for every committee visualization (historical evaluations,
 * direction mix, explainability trend, per-agent reasoning/evidence/risks)
 * so new upstream committee features land here without touching other pages.
 * The single-opportunity view stays in DecisionTraceDrawer; this page is the
 * cross-opportunity ledger over the same append-only committee_reports rows.
 *
 * Read-only and advisory: committee opinions never change trade or risk
 * semantics, and absence/failure renders as honest "not available".
 */

const WINDOW_OPTIONS = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
  { days: 0, label: 'All' },
]

// Chart palette mirrors the CSS tokens in index.css (same source as Usage).
const C = {
  green: '#2ee6a8',
  red: '#ff5d6c',
  accent: '#22d3ee',
  yellow: '#f5b942',
  purple: '#a78bfa',
  blue: '#4aa3ff',
  orange: '#fb923c',
  pink: '#f472b6',
  muted: '#7a8a99',
  border: '#232c36',
  text: '#e6edf3',
}
const DIRECTION_COLORS: Record<string, string> = {
  long: C.green,
  short: C.red,
  neutral: C.muted,
}
const directionColor = (d: string) => DIRECTION_COLORS[d] ?? C.yellow

const axisStyle = { fill: C.muted, fontSize: 10 }
const gridProps = { stroke: C.border, strokeDasharray: '3 3' }
const tooltipProps = {
  contentStyle: {
    background: '#1d252f',
    border: `1px solid ${C.border}`,
    borderRadius: 6,
    fontSize: 11,
  },
  labelStyle: { color: C.text },
  itemStyle: { color: C.text },
}

const directionTone = (d: string): 'green' | 'red' | 'muted' =>
  d === 'long' ? 'green' : d === 'short' ? 'red' : 'muted'

// Confidence is stored 0-1; always display ×100 (fmtPct auto-detects ratios).
const fmtConfidence = (v: number | null | undefined) => fmtPct(v ?? null)
// ES is shown as a raw 0-1 score (same convention as DecisionTraceDrawer).
const fmtEs = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))

// ── Indicator keys the committee commonly receives (for scatter + preview) ──
// Ordered by display preference; the scatter dropdown lets the user switch.
const INDICATOR_LABELS: Record<string, string> = {
  rsi_14: 'RSI (14)',
  atr_pct: 'ATR %',
  funding_rate: 'Funding',
  return_24h: 'Return 24h',
  vol_ratio: 'Vol ratio',
  return_1h: 'Return 1h',
  return_4h: 'Return 4h',
  relative_strength_24h: 'Rel strength',
  rsi_4h: 'RSI (4h)',
  bb_squeeze: 'BB squeeze',
  open_interest: 'Open interest',
  oi_delta_1h: 'OI delta',
  taker_buy_ratio: 'Taker buy%',
  fear_greed: 'Fear & Greed',
  etf_flow_24h: 'ETF flow',
  dxy_change: 'DXY',
  onchain_mvrv: 'MVRV',
  stablecoin_supply_change: 'Stablecoin Δ',
}

/** Pick the most frequently-present indicator keys across the evaluations. */
function topIndicatorKeys(evals: CommitteeEvaluation[], n: number): string[] {
  const freq: Record<string, number> = {}
  for (const ev of evals) {
    for (const k of Object.keys(ev.indicators ?? {})) {
      freq[k] = (freq[k] ?? 0) + 1
    }
  }
  return Object.entries(freq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([k]) => k)
}

/** Bucket evaluations by calendar date for time-series aggregation. */
function bucketByDate(evals: CommitteeEvaluation[]): {
  dates: string[]
  byDate: Record<string, {
    long: number
    short: number
    neutral: number
    total: number
    blocked: number
    fallback: number
    confSum: number
    models: Record<string, number>
  }>
} {
  const byDate: Record<string, {
    long: number
    short: number
    neutral: number
    total: number
    blocked: number
    fallback: number
    confSum: number
    models: Record<string, number>
  }> = {}
  for (const ev of evals) {
    if (!ev.created_at) continue
    const day = normalizeIso(ev.created_at).slice(0, 10)
    if (!byDate[day]) {
      byDate[day] = { long: 0, short: 0, neutral: 0, total: 0, blocked: 0, fallback: 0, confSum: 0, models: {} }
    }
    const b = byDate[day]
    const d = ev.direction || 'neutral'
    if (d === 'long') b.long++
    else if (d === 'short') b.short++
    else b.neutral++
    b.total++
    if (ev.blocked) b.blocked++
    if (ev.fallback_used) b.fallback++
    b.confSum += ev.confidence || 0
    if (ev.model_used) b.models[ev.model_used] = (b.models[ev.model_used] ?? 0) + 1
  }
  const dates = Object.keys(byDate).sort()
  return { dates, byDate }
}

/** Confidence histogram: 10 buckets, each split by direction. */
function confidenceHistogram(evals: CommitteeEvaluation[]): {
  bucket: string
  long: number
  short: number
  neutral: number
}[] {
  const buckets: { long: number; short: number; neutral: number }[] =
    Array.from({ length: 10 }, () => ({ long: 0, short: 0, neutral: 0 }))
  for (const ev of evals) {
    const c = ev.confidence || 0
    const idx = Math.min(Math.floor(c * 10), 9)
    const d = ev.direction || 'neutral'
    if (d === 'long') buckets[idx].long++
    else if (d === 'short') buckets[idx].short++
    else buckets[idx].neutral++
  }
  return buckets.map((b, i) => ({
    bucket: `${i * 10}-${i * 10 + 10}%`,
    ...b,
  }))
}

/** Scatter points: one per evaluation, x = indicator value, y = confidence. */
function indicatorScatter(
  evals: CommitteeEvaluation[],
  key: string,
): { x: number; y: number; direction: string; symbol: string }[] {
  const pts: { x: number; y: number; direction: string; symbol: string }[] = []
  for (const ev of evals) {
    const raw = ev.indicators?.[key]
    if (raw == null) continue
    const x = Number(raw)
    if (Number.isNaN(x)) continue
    pts.push({ x, y: ev.confidence || 0, direction: ev.direction || 'neutral', symbol: ev.symbol })
  }
  return pts
}

type WorkbenchProps = {
  isLoading: boolean
  isError: boolean
  data: CommitteeOverviewResponse | undefined
}

/** Pure presentation body — exported so honesty states are unit-testable. */
export function CommitteeWorkbench({ isLoading, isError, data }: WorkbenchProps) {
  const [days, setDays] = useState(30)
  const [symbolFilter, setSymbolFilter] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [scatterKey, setScatterKey] = useState('rsi_14')

  const allEvals = useMemo(() => data?.evaluations ?? [], [data])

  const symbols = useMemo(() => {
    const s = new Set<string>()
    for (const ev of allEvals) if (ev.symbol) s.add(ev.symbol)
    return Array.from(s).sort()
  }, [allEvals])

  // The 3 most common indicator keys across the current dataset — used for
  // both the table preview columns and the scatter dropdown default.
  const previewKeys = useMemo(() => topIndicatorKeys(allEvals, 3), [allEvals])

  // Window + symbol filters are applied client-side: the endpoint caps at 200
  // rows, which comfortably covers the 90-day committee_reports retention.
  const evals = useMemo(() => {
    let rows = allEvals
    if (symbolFilter) rows = rows.filter(ev => ev.symbol === symbolFilter)
    if (days > 0) {
      const cutoff = Date.now() - days * 86_400_000
      rows = rows.filter(ev => {
        if (!ev.created_at) return false
        const t = new Date(normalizeIso(ev.created_at)).getTime()
        return !Number.isNaN(t) && t >= cutoff
      })
    }
    return rows
  }, [allEvals, days, symbolFilter])

  // ── KPI stats ──
  const stats = useMemo(() => {
    const mix: Record<string, number> = {}
    let confSum = 0
    let esSum = 0
    let blocked = 0
    let fallback = 0
    const models: Record<string, number> = {}
    for (const ev of evals) {
      const d = ev.direction || 'neutral'
      mix[d] = (mix[d] ?? 0) + 1
      confSum += ev.confidence || 0
      esSum += ev.explainability_score || 0
      if (ev.blocked) blocked++
      if (ev.fallback_used) fallback++
      if (ev.model_used) models[ev.model_used] = (models[ev.model_used] ?? 0) + 1
    }
    const n = evals.length
    const topModel = Object.entries(models).sort((a, b) => b[1] - a[1])[0]
    return {
      n,
      mix,
      avgConf: n ? confSum / n : null,
      avgEs: n ? esSum / n : null,
      blocked,
      fallback,
      blockedRate: n ? blocked / n : null,
      fallbackRate: n ? fallback / n : null,
      modelCount: Object.keys(models).length,
      topModel: topModel?.[0] ?? null,
    }
  }, [evals])

  // ── Time-series aggregations ──
  const { dates, byDate } = useMemo(() => bucketByDate(evals), [evals])

  const directionTrend = useMemo(() =>
    dates.map(d => ({
      date: d,
      long: byDate[d].long,
      short: byDate[d].short,
      neutral: byDate[d].neutral,
    })),
  [dates, byDate])

  const blockedRateTrend = useMemo(() =>
    dates.map(d => ({
      date: d,
      rate: byDate[d].total ? (byDate[d].blocked / byDate[d].total) * 100 : 0,
      count: byDate[d].blocked,
      total: byDate[d].total,
    })),
  [dates, byDate])

  // Model usage over time: one series per model (top 4 + "other").
  const modelTrend = useMemo(() => {
    const allModels = new Set<string>()
    for (const d of dates) {
      for (const m of Object.keys(byDate[d].models)) allModels.add(m)
    }
    const ranked = Array.from(allModels).sort((a, b) => {
      const totalA = dates.reduce((s, d) => s + (byDate[d].models[a] ?? 0), 0)
      const totalB = dates.reduce((s, d) => s + (byDate[d].models[b] ?? 0), 0)
      return totalB - totalA
    })
    const topModels = ranked.slice(0, 4)
    return dates.map(d => {
      const row: Record<string, string | number> = { date: d }
      let other = 0
      for (const m of ranked) {
        const v = byDate[d].models[m] ?? 0
        if (topModels.includes(m)) row[m] = v
        else other += v
      }
      row['other'] = other
      return row
    })
  }, [dates, byDate])

  const MODEL_COLORS = [C.accent, C.green, C.purple, C.orange, C.muted]

  const pieData = useMemo(
    () =>
      Object.entries(stats.mix)
        .map(([name, value]) => ({ name, value }))
        .sort((a, b) => b.value - a.value),
    [stats],
  )

  // ES + confidence trend (chronological; rows arrive newest-first).
  const trendData = useMemo(() => {
    return [...evals]
      .filter(ev => ev.created_at)
      .map(ev => ({
        t: new Date(normalizeIso(ev.created_at!)).getTime(),
        // ES is 0-1; plotted ×100 so it shares the % axis with conviction.
        // The tooltip formatter restores the true 0-1 value.
        es: (ev.explainability_score || 0) * 100,
        confidence: (ev.confidence || 0) * 100,
        symbol: ev.symbol,
      }))
      .filter(p => !Number.isNaN(p.t))
      .sort((a, b) => a.t - b.t)
  }, [evals])

  const confHist = useMemo(() => confidenceHistogram(evals), [evals])
  const scatterPts = useMemo(() => indicatorScatter(evals, scatterKey), [evals, scatterKey])

  const windowLabel = days > 0 ? `${days}d` : 'all (≤200)'

  // Time span of the filtered data (for the Coverage tile).
  const timeSpan = useMemo(() => {
    const ts = evals.map(e => e.created_at).filter(Boolean).sort()
    if (ts.length < 2) return null
    return { first: ts[0]!, last: ts[ts.length - 1]! }
  }, [evals])

  // ---- Honest states: never invent data, never fake a benign empty page ----
  if (isError) {
    return (
      <p className="text-muted text-sm">
        Not available — committee data could not be read.
      </p>
    )
  }
  if (data && data.status !== 'ok') {
    return (
      <p className="text-muted text-sm">
        Not available{data.reason ? ` — ${data.reason}` : ''}
      </p>
    )
  }
  if (isLoading || !data) {
    return <p className="text-muted text-sm">Loading committee evaluations…</p>
  }
  if (allEvals.length === 0) {
    return (
      <p className="hint">
        No committee evaluations recorded yet. The committee persists an
        opinion whenever an LLM evaluation of a candidate succeeds
        (append-only <code>committee_reports</code>, 90-day retention).
      </p>
    )
  }

  return (
    <>
      {/* ── Control bar: time window + symbol filter ── */}
      <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', marginBottom: 'var(--space-3)', flexWrap: 'wrap' }}>
        <div className="seg-toggle">
          {WINDOW_OPTIONS.map(opt => (
            <Button
              key={opt.days}
              variant="ghost"
              className={`seg-btn${days === opt.days ? ' active' : ''}`}
              onClick={() => setDays(opt.days)}
            >
              {opt.label}
            </Button>
          ))}
        </div>
        <select
          className="mono"
          value={symbolFilter}
          onChange={e => setSymbolFilter(e.target.value)}
          aria-label="Filter by symbol"
          style={{
            background: 'var(--surface-2, #141a21)',
            color: 'var(--text, #e6edf3)',
            border: `1px solid ${C.border}`,
            borderRadius: 6,
            padding: '4px 8px',
            fontSize: 12,
          }}
        >
          <option value="">All symbols</option>
          {symbols.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      {/* ── KPI tiles: 8 metrics ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <MetricTile
          label={`Evaluations (${windowLabel})`}
          value={String(stats.n)}
          context={symbolFilter ? `filtered: ${symbolFilter}` : `${symbols.length} symbol(s)`}
        />
        <MetricTile
          label="Direction mix"
          value={`${stats.mix.long ?? 0}L · ${stats.mix.short ?? 0}S · ${stats.mix.neutral ?? 0}N`}
          context="long · short · neutral"
        />
        <MetricTile
          label="Avg confidence"
          value={fmtConfidence(stats.avgConf)}
          context="mean committee confidence"
        />
        <MetricTile
          label="Avg explainability"
          value={fmtEs(stats.avgEs)}
          context="evidence + agreement + rules"
        />
        <MetricTile
          label="Blocked"
          value={`${stats.blocked}`}
          context={stats.blockedRate != null ? `${(stats.blockedRate * 100).toFixed(0)}% blocked rate` : 'by rule engine'}
          tone={stats.blockedRate != null && stats.blockedRate > 0.5 ? 'warning' : 'neutral'}
        />
        <MetricTile
          label="Fallback"
          value={`${stats.fallback}`}
          context={stats.fallbackRate != null ? `${(stats.fallbackRate * 100).toFixed(0)}% fallback rate` : 'model fallback'}
          tone={stats.fallbackRate != null && stats.fallbackRate > 0.3 ? 'warning' : 'neutral'}
        />
        <MetricTile
          label="Models active"
          value={String(stats.modelCount)}
          context={stats.topModel ? `lead: ${stats.topModel.split('/').pop()}` : 'LLM models'}
        />
        <MetricTile
          label="Coverage"
          value={`${symbols.length} symbols`}
          context={timeSpan
            ? `${fmtTimestamp(timeSpan.first, { dateOnly: true })} → ${fmtTimestamp(timeSpan.last, { dateOnly: true })}`
            : 'time span'}
        />
      </div>

      {/* ── Row A: Direction mix over time + Confidence distribution ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <Card>
          <CardHeader title={`Direction mix over time (${windowLabel})`} />
          {directionTrend.length === 0 ? (
            <p className="hint">No evaluations in this window.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={directionTrend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis
                  dataKey="date"
                  tick={axisStyle}
                  tickFormatter={v => v.slice(5)}
                  minTickGap={30}
                />
                <YAxis tick={axisStyle} width={32} />
                <Tooltip
                  {...tooltipProps}
                  labelFormatter={v => v}
                  formatter={(v: any, name: any) => [v, humanize(String(name))]}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area type="monotone" dataKey="long" stackId="1" stroke={C.green} fill={C.green} fillOpacity={0.18} />
                <Area type="monotone" dataKey="short" stackId="1" stroke={C.red} fill={C.red} fillOpacity={0.18} />
                <Area type="monotone" dataKey="neutral" stackId="1" stroke={C.muted} fill={C.muted} fillOpacity={0.12} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
        <Card>
          <CardHeader title="Confidence distribution" />
          {stats.n === 0 ? (
            <p className="hint">No evaluations in this window.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={confHist} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="bucket" tick={{ ...axisStyle, fontSize: 9 }} interval={0} angle={-30} textAnchor="end" height={40} />
                <YAxis tick={axisStyle} width={32} />
                <Tooltip
                  {...tooltipProps}
                  formatter={(v: any, name: any) => [v, humanize(String(name))]}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="long" stackId="c" fill={C.green} />
                <Bar dataKey="short" stackId="c" fill={C.red} />
                <Bar dataKey="neutral" stackId="c" fill={C.muted} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* ── Row B: Model usage + Blocked rate trend ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <Card>
          <CardHeader title={`Model usage over time (${windowLabel})`} />
          {modelTrend.length === 0 ? (
            <p className="hint">No evaluations in this window.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={modelTrend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis
                  dataKey="date"
                  tick={axisStyle}
                  tickFormatter={v => v.slice(5)}
                  minTickGap={30}
                />
                <YAxis tick={axisStyle} width={32} />
                <Tooltip
                  {...tooltipProps}
                  labelFormatter={v => v}
                  formatter={(v: any, name: any) => [v, String(name) === 'other' ? 'Other' : String(name).split('/').pop()]}
                />
                <Legend wrapperStyle={{ fontSize: 10 }} formatter={(v: string) => v === 'other' ? 'Other' : v.split('/').pop()!} />
                {Object.keys(modelTrend[0] ?? {}).filter(k => k !== 'date').map((m, i) => (
                  <Area key={m} type="monotone" dataKey={m} stackId="m" stroke={MODEL_COLORS[i % MODEL_COLORS.length]} fill={MODEL_COLORS[i % MODEL_COLORS.length]} fillOpacity={0.18} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
        <Card>
          <CardHeader title={`Blocked rate over time (${windowLabel})`} />
          {blockedRateTrend.length === 0 ? (
            <p className="hint">No evaluations in this window.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={blockedRateTrend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis
                  dataKey="date"
                  tick={axisStyle}
                  tickFormatter={v => v.slice(5)}
                  minTickGap={30}
                />
                <YAxis tick={axisStyle} width={36} domain={[0, 100]} unit="%" />
                <Tooltip
                  {...tooltipProps}
                  labelFormatter={v => v}
                  formatter={(v: any) => [`${Number(v).toFixed(0)}%`, 'blocked rate']}
                />
                <Line
                  type="monotone"
                  dataKey="rate"
                  stroke={C.red}
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 3 }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* ── Row C: Indicator vs Confidence scatter (full-width) ── */}
      <div style={{ marginBottom: 'var(--space-3)' }}>
        <Card>
          <CardHeader
            title="Indicator vs confidence"
            actions={
              <select
                className="mono"
                value={scatterKey}
                onChange={e => setScatterKey(e.target.value)}
                aria-label="Select indicator"
                style={{
                  background: 'var(--surface-2, #141a21)',
                  color: 'var(--text, #e6edf3)',
                  border: `1px solid ${C.border}`,
                  borderRadius: 6,
                  padding: '3px 6px',
                  fontSize: 11,
                }}
              >
                {Object.keys(INDICATOR_LABELS)
                  .filter(k => evals.some(e => e.indicators?.[k] != null))
                  .map(k => (
                    <option key={k} value={k}>{INDICATOR_LABELS[k] ?? k}</option>
                  ))}
              </select>
            }
          />
          {scatterPts.length === 0 ? (
            <p className="hint">No evaluations with this indicator in the current window.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <ScatterChart margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis
                  dataKey="x"
                  type="number"
                  name={INDICATOR_LABELS[scatterKey] ?? scatterKey}
                  tick={axisStyle}
                  domain={['dataMin', 'dataMax']}
                  label={{ value: INDICATOR_LABELS[scatterKey] ?? scatterKey, position: 'bottom', offset: -2, style: { fill: C.muted, fontSize: 10 } }}
                />
                <YAxis
                  dataKey="y"
                  type="number"
                  name="confidence"
                  tick={axisStyle}
                  domain={[0, 1]}
                  tickFormatter={v => `${(Number(v) * 100).toFixed(0)}%`}
                  width={40}
                />
                <Tooltip
                  {...tooltipProps}
                  cursor={{ strokeDasharray: '3 3' }}
                  formatter={(v: any, name: any) => [
                    name === 'y' ? `${(Number(v) * 100).toFixed(0)}%` : Number(v).toFixed(3),
                    name === 'y' ? 'confidence' : INDICATOR_LABELS[scatterKey] ?? scatterKey,
                  ]}
                  labelFormatter={() => ''}
                  content={({ payload }) => {
                    if (!payload || payload.length === 0) return null
                    const p = payload[0]?.payload as { x: number; y: number; direction: string; symbol: string } | undefined
                    if (!p) return null
                    return (
                      <div style={tooltipProps.contentStyle as React.CSSProperties}>
                        <div style={{ color: C.text, fontSize: 11, fontWeight: 600 }}>{p.symbol}</div>
                        <div style={{ color: C.muted, fontSize: 10, marginTop: 2 }}>
                          {humanize(p.direction)} · {(p.y * 100).toFixed(0)}% conf · {INDICATOR_LABELS[scatterKey]} {p.x.toFixed(2)}
                        </div>
                      </div>
                    )
                  }}
                />
                <Scatter data={scatterPts.filter(p => p.direction === 'long')} fill={C.green} fillOpacity={0.7} />
                <Scatter data={scatterPts.filter(p => p.direction === 'short')} fill={C.red} fillOpacity={0.7} />
                <Scatter data={scatterPts.filter(p => p.direction !== 'long' && p.direction !== 'short')} fill={C.muted} fillOpacity={0.5} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
          <p className="hint" style={{ marginTop: 6 }}>
            Each point is one committee evaluation. Color = direction. Reveals whether extreme indicator values drive higher confidence.
          </p>
        </Card>
      </div>

      {/* ── Row D: Explainability & conviction trend (existing, kept) ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <Card>
          <CardHeader title={`Direction mix (${windowLabel})`} />
          {pieData.length === 0 ? (
            <p className="hint">No evaluations in this window.</p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={45}
                  outerRadius={80}
                  paddingAngle={3}
                  stroke={C.border}
                >
                  {pieData.map((d, i) => (
                    <Cell key={i} fill={directionColor(d.name)} />
                  ))}
                </Pie>
                <Tooltip {...tooltipProps} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Card>
        <Card>
          <CardHeader title="Explainability & conviction trend" />
          {trendData.length === 0 ? (
            <p className="hint">No evaluations in this window.</p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={trendData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  tick={axisStyle}
                  tickFormatter={v => fmtTimestamp(new Date(Number(v)).toISOString())}
                  minTickGap={40}
                />
                <YAxis tick={axisStyle} width={36} domain={[0, 100]} />
                <Tooltip
                  {...tooltipProps}
                  labelFormatter={v => fmtTimestamp(new Date(Number(v)).toISOString())}
                  formatter={(v: any, name: any) =>
                    name === 'es'
                      ? [(Number(v) / 100).toFixed(2), 'ES (0–1)']
                      : [`${Number(v).toFixed(0)}%`, 'conviction']
                  }
                />
                <Line
                  type="monotone"
                  dataKey="es"
                  name="es"
                  stroke={C.accent}
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 3 }}
                />
                <Line
                  type="monotone"
                  dataKey="confidence"
                  name="confidence"
                  stroke={C.green}
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 3 }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* ── Evaluations table (with indicator preview columns) ── */}
      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Recent evaluations ({windowLabel})</h3>
        {evals.length === 0 ? (
          <p className="hint">No evaluations match this window/filter.</p>
        ) : (
          <Card flush>
            <TableWrap>
              <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>Time</th>
                    <th style={{ textAlign: 'left' }}>Symbol</th>
                    <th style={{ textAlign: 'left' }}>Direction</th>
                    <th style={{ textAlign: 'right' }}>Conviction</th>
                    <th style={{ textAlign: 'right' }}>ES</th>
                    {previewKeys.map(k => (
                      <th key={k} style={{ textAlign: 'right' }} title={k}>
                        {INDICATOR_LABELS[k] ?? k}
                      </th>
                    ))}
                    <th style={{ textAlign: 'left' }}>Flags</th>
                    <th style={{ textAlign: 'left' }}>Model</th>
                  </tr>
                </thead>
                <tbody>
                  {evals.map(ev => {
                    const isOpen = !!expanded[ev.id]
                    return (
                      <CommitteeRow
                        key={ev.id}
                        ev={ev}
                        isOpen={isOpen}
                        previewKeys={previewKeys}
                        onToggle={() => setExpanded(x => ({ ...x, [ev.id]: !x[ev.id] }))}
                      />
                    )
                  })}
                </tbody>
              </table>
            </TableWrap>
          </Card>
        )}
        <p className="hint" style={{ marginTop: '0.5rem' }}>
          Click a row for the full opinion (reasoning, evidence, risk warnings,
          indicators). Per-opportunity trace stays in the Blotter decision
          drawer.
        </p>
      </section>

      <p className="hint">
        Source: append-only <code>committee_reports</code> ledger (90-day
        retention, newest {data.window ? data.window.limit : 200} rows). Advisory
        data only — committee opinions never change trade or risk semantics.
      </p>
    </>
  )
}

function CommitteeRow({ ev, isOpen, previewKeys, onToggle }: {
  ev: CommitteeEvaluation
  isOpen: boolean
  previewKeys: string[]
  onToggle: () => void
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        style={{ cursor: 'pointer' }}
        title={isOpen ? 'Collapse opinion detail' : 'Expand opinion detail'}
      >
        <td>{ev.created_at ? fmtTimestamp(ev.created_at) : '—'}</td>
        <td>{fmtSymbol(ev.symbol)}</td>
        <td><Badge tone={directionTone(ev.direction)}>{humanize(ev.direction)}</Badge></td>
        <td style={{ textAlign: 'right' }}>{fmtConfidence(ev.confidence)}</td>
        <td style={{ textAlign: 'right' }}>{fmtEs(ev.explainability_score)}</td>
        {previewKeys.map(k => {
          const v = ev.indicators?.[k]
          if (v == null) return <td key={k} style={{ textAlign: 'right' }} className="text-muted">—</td>
          const num = Number(v)
          return (
            <td key={k} style={{ textAlign: 'right' }}>
              {Number.isNaN(num) ? String(v) : num >= 100 ? num.toFixed(0) : num >= 1 ? num.toFixed(2) : num.toFixed(4)}
            </td>
          )
        })}
        <td>
          {ev.blocked && <Badge tone="red">blocked</Badge>}{' '}
          {ev.fallback_used && <Badge tone="yellow">fallback</Badge>}
        </td>
        <td className="text-muted">{ev.model_used ? ev.model_used.split('/').pop() : '—'}</td>
      </tr>
      {isOpen && (
        <tr>
          <td colSpan={7 + previewKeys.length} style={{ background: 'rgba(34,211,238,0.04)', padding: '8px 12px' }}>
            <CommitteeOpinionDetail ev={ev} />
          </td>
        </tr>
      )}
    </>
  )
}

/**
 * Full opinion detail — same semantics as the DecisionTraceDrawer "Committee
 * opinion" section (reasoning + evidence + risk warnings), plus the indicator
 * snapshot the LLM saw. Absence renders as honest dashes/notes.
 */
export function CommitteeOpinionDetail({ ev }: { ev: CommitteeEvaluation }) {
  const indicators = Object.entries(ev.indicators ?? {})
  return (
    <div style={{ display: 'block' }}>
      <div className="text-xs text-muted">
        {ev.decision_id ? `decision ${ev.decision_id}` : 'decision —'}
        {ev.opportunity_id ? ` · opportunity ${ev.opportunity_id}` : ''}
      </div>
      {ev.agent_opinions.length === 0 && (
        <p className="text-sm text-muted" style={{ marginTop: 4 }}>
          No agent opinions were persisted for this evaluation.
        </p>
      )}
      {ev.agent_opinions.map((op, j) => (
        <div key={j} style={{ marginTop: 6 }}>
          <div className="text-xs text-muted">
            {[
              op.agent,
              op.direction ? humanize(op.direction) : null,
              typeof op.confidence === 'number' ? `conviction ${fmtConfidence(op.confidence)}` : null,
            ].filter(Boolean).join(' · ') || '—'}
          </div>
          {op.reasoning && (
            <div className="text-sm" style={{ whiteSpace: 'pre-wrap' }}>{op.reasoning}</div>
          )}
          {op.evidence && op.evidence.length > 0 && (
            <ul className="text-xs text-muted" style={{ marginTop: 4 }}>
              {op.evidence.map((e, k) => (
                <li key={k}>{e.value || e.indicator || '—'}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {ev.risks && ev.risks.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div className="text-xs text-muted">Risk warnings</div>
          <ul className="text-xs" style={{ marginTop: 2 }}>
            {ev.risks.map((r, k) => (
              <li key={k}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {indicators.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div className="text-xs text-muted">Indicators fed to the committee</div>
          <div className="text-xs mono" style={{ marginTop: 2, display: 'flex', flexWrap: 'wrap', gap: '4px 12px' }}>
            {indicators.map(([k, v]) => (
              <span key={k}>{INDICATOR_LABELS[k] ?? k}: {String(v)}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Committee() {
  // 60s poll matches the Usage page: committee rows land once per LLM
  // evaluation, so high-frequency polling buys nothing.
  const { data, isError, isLoading } = useQuery({
    queryKey: ['committee-reports'],
    queryFn: () => api.getCommitteeReports({ limit: 200 }),
    refetchInterval: 60_000,
  })
  return (
    <div>
      <PageHeader
        title="AI Jury"
        role="Read-only workbench for the Investment Committee's persisted evaluations — direction mix, conviction, explainability, per-agent reasoning, model usage, and indicator correlation. Advisory data; never trade or risk semantics."
      />
      <CommitteeWorkbench isLoading={isLoading} isError={isError} data={data} />
    </div>
  )
}

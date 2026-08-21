import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, type UsageInsightsRow, type UsageInsightsSection } from '../api/client'
import { DataTable } from '../components/DataTable'
import MetricTile from '../components/MetricTile'
import PageHeader from '../components/PageHeader'
import { fmtCompact, fmtNum, fmtPct } from '../lib/format'
import { Badge, Button, Card, CardHeader, TableWrap } from '../components/ui'
import { useToast } from '../components/ui/Toast'

const DAY_OPTIONS = [{ days: 1, label: '1d' }, { days: 7, label: '7d' }, { days: 30, label: '30d' }, { days: 90, label: '90d' }] as const
const C = { accent: '#22d3ee', green: '#2ee6a8', yellow: '#f5b942', border: '#232c36', muted: '#7d8997', text: '#e6edf3' }
const tooltipProps = { contentStyle: { background: '#1a212a', border: `1px solid ${C.border}`, borderRadius: 5, fontSize: 11 }, labelStyle: { color: C.text }, itemStyle: { color: C.text } }

const usd = (value: number | null | undefined) => value == null ? '—' : `$${fmtNum(value)}`
const count = (value: number | null | undefined) => value == null ? '—' : fmtCompact(value)
const windowLabel = (days: number | undefined) => days === 1 ? 'last 24 hours' : `last ${days ?? 1} days`
const humanize = (key: string) => key.replace(/_/g, ' ')

function valueText(value: unknown): string {
  if (value == null) return 'N/A'
  if (typeof value === 'number') return Number.isFinite(value) ? fmtNum(value) : 'N/A'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'string') return value.trim() || 'unknown'
  if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' && item != null && 'error_type' in item && 'event_count' in item ? `${String(item.error_type)}: ${String(item.event_count)}` : typeof item === 'string' ? item : 'item').join(', ') : 'none'
  return 'unknown'
}

function Section({ title, section, children }: { title: string; section: UsageInsightsSection; children: ReactNode }) {
  return (
    <section className="usage-section">
      <div className="usage-section-head">
        <h2>{title}</h2>
        {section.status !== 'available' && <Badge tone="yellow">{section.status === 'partial' ? 'Partial' : 'N/A'}</Badge>}
      </div>
      {section.status !== 'available' && <p className="hint">{section.reason ?? 'This section is not fully measured.'}</p>}
      {children}
    </section>
  )
}

function Skeleton({ label }: { label: string }) {
  return <section className="usage-section skeleton" role="status" aria-label={`Loading ${label}`}><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></section>
}

function LoadingIndicator() {
  return <span className="usage-loading-indicator"><span className="hint">Loading</span><progress aria-label="Loading usage sections" /></span>
}

function InsightTable({ title, rows, empty }: { title: string; rows: UsageInsightsRow[]; empty: string }) {
  const columns = useMemo<ColumnDef<UsageInsightsRow>[]>(() => Array.from(new Set(rows.flatMap(Object.keys))).map(key => ({ accessorKey: key, header: humanize(key), cell: ({ row }) => valueText(row.original[key]) })), [rows])
  if (!rows.length || !columns.length) return <p className="hint">{empty}</p>
  return <Card flush className="usage-table-card"><CardHeader title={`${title} (${rows.length.toLocaleString()})`} /><DataTable data={rows} columns={columns} pageSize={25} emptyMessage={empty} /></Card>
}

function Facts({ title, facts }: { title: string; facts: UsageInsightsRow | null }) {
  const rows = Object.entries(facts ?? {})
  if (!rows.length) return <p className="hint">N/A — no facts reported.</p>
  return <Card flush className="usage-facts"><TableWrap><table className="mono"><caption>{title}</caption><tbody>{rows.map(([key, value]) => <tr key={key}><th scope="row">{humanize(key)}</th><td>{valueText(value)}</td></tr>)}</tbody></table></TableWrap></Card>
}

function TokenComposition({ input, output, cached, reasoning }: { input: number | null; output: number | null; cached: number | null; reasoning: number | null }) {
  const safeCached = Math.min(cached ?? 0, input ?? 0)
  const safeReasoning = Math.min(reasoning ?? 0, output ?? 0)
  const bars = [
    { label: 'Input', ordinary: Math.max((input ?? 0) - safeCached, 0), subtype: safeCached, subtypeName: 'Cached' },
    { label: 'Output', ordinary: Math.max((output ?? 0) - safeReasoning, 0), subtype: safeReasoning, subtypeName: 'Reasoning' },
  ]
  const max = Math.max(input ?? 0, output ?? 0, 1)
  return <Card className="usage-composition"><CardHeader title="Token composition" meta="Subtypes are contained within input/output; totals are not double-counted." />{bars.map(bar => <div className="usage-composition-row" key={bar.label}><div className="usage-composition-label"><span>{bar.label}</span><strong>{count(bar.ordinary + bar.subtype)}</strong></div><div className="usage-composition-track" aria-label={`${bar.label}: ${bar.ordinary} ordinary, ${bar.subtype} ${bar.subtypeName.toLowerCase()}`}><span className="usage-composition-ordinary" style={{ width: `${bar.ordinary * 100 / max}%` }} /><span className="usage-composition-subtype" style={{ width: `${bar.subtype * 100 / max}%` }} /></div><div className="usage-composition-legend"><span>Ordinary {count(bar.ordinary)}</span><span>{bar.subtypeName} {count(bar.subtype)}</span></div></div>)}</Card>
}

function CoverageList({ axes }: { axes: Array<{ id: string; label: string; status: string; coverage_pct: number | null; reason: string | null }> }) {
  return <Card className="usage-coverage-list">{axes.map(axis => <div className="usage-coverage-row" key={axis.id}><div><strong>{axis.label}</strong><span>{axis.reason ?? 'Measured for the selected window.'}</span></div><div className="usage-coverage-value"><span>{fmtPct(axis.coverage_pct, { decimals: 1, dash: 'N/A', scale: 'percent' })}</span><Badge tone={axis.status === 'available' ? 'green' : 'yellow'}>{axis.status === 'not_available' ? 'N/A' : axis.status}</Badge></div></div>)}</Card>
}

export default function Usage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [days, setDays] = useState<1 | 7 | 30 | 90>(1)
  const summaryQuery = useQuery({ queryKey: ['usage-insights', days, 'summary'], queryFn: () => api.getUsageInsights(days, 'summary'), placeholderData: keepPreviousData })
  const summary = summaryQuery.data
  const summaryReady = summary?.window.days === days && !summaryQuery.isPlaceholderData
  const operationsQuery = useQuery({ queryKey: ['usage-insights', days, 'operations'], queryFn: () => api.getUsageInsights(days, 'operations'), placeholderData: keepPreviousData, enabled: summaryReady })
  const analysisQuery = useQuery({ queryKey: ['usage-insights', days, 'analysis'], queryFn: () => api.getUsageInsights(days, 'analysis'), placeholderData: keepPreviousData, enabled: summaryReady })
  const operations = operationsQuery.data
  const analysis = analysisQuery.data
  const queries = [summaryQuery, operationsQuery, analysisQuery]
  const fetching = queries.some(query => query.isFetching)
  const error = queries.find(query => query.error)?.error
  const { data: pricingRefresh } = useQuery({ queryKey: ['pricing-refresh-status'], queryFn: api.getPricingRefreshStatus, refetchInterval: query => query.state.data?.status === 'running' ? 2_000 : false })
  const refreshPricing = useMutation({ mutationFn: api.refreshPricing, onSuccess: status => queryClient.setQueryData(['pricing-refresh-status'], status), onError: error => toast('error', `Pricing refresh failed: ${error.message}`) })
  const handledRefresh = useRef<string | null>(null)

  useEffect(() => {
    if (pricingRefresh?.status !== 'succeeded' || !pricingRefresh.completed_at || handledRefresh.current === pricingRefresh.completed_at) return
    handledRefresh.current = pricingRefresh.completed_at
    queryClient.invalidateQueries({ queryKey: ['usage-insights'] })
  }, [pricingRefresh, queryClient])

  const selectedLabel = windowLabel(days)
  const summaryLabel = windowLabel(summary?.window.days)
  const operationsLabel = windowLabel(operations?.window.days)
  const analysisLabel = windowLabel(analysis?.window.days)
  const freshAxes = [...(!summaryQuery.isPlaceholderData ? summary?.coverage.axes ?? [] : []), ...(!operationsQuery.isPlaceholderData ? operations?.coverage.axes ?? [] : []), ...(!analysisQuery.isPlaceholderData ? analysis?.coverage.axes ?? [] : [])]
  const pricingCoverage = summary?.pricing?.pricing_coverage_pct
  const qualityTone = freshAxes.length === 8 && freshAxes.every(axis => axis.status === 'available') ? 'green' : 'yellow'
  const coverageStatus = freshAxes.length < 8 ? 'partial' : freshAxes.every(axis => axis.status === 'available') ? 'available' : freshAxes.every(axis => axis.status === 'not_available') ? 'not_available' : 'partial'
  const coverageReason = freshAxes.length < 8 ? 'Some coverage dimensions are still loading.' : coverageStatus === 'available' ? null : coverageStatus === 'not_available' ? 'No coverage dimension is measurable in this window.' : 'Some coverage dimensions are incomplete or unavailable.'
  const physical = summary?.reliability?.physical
  const trendRows = operations?.trends?.daily ?? []

  return (
    <div className="usage-page" aria-busy={fetching}>
      <PageHeader title="Usage insights" role={`Consumption, spend, reliability, attribution, and research yield for ${selectedLabel}. Estimates and unavailable facts remain explicitly labelled.`} actions={<div className="usage-header-actions">{fetching && <LoadingIndicator />}<div className="seg-toggle" aria-label="Usage time range">{DAY_OPTIONS.map(option => <Button key={option.days} variant="ghost" className={`seg-btn${days === option.days ? ' active' : ''}`} aria-pressed={days === option.days} onClick={() => setDays(option.days)}>{option.label}</Button>)}</div></div>} />
      <div className="usage-context-bar"><span>{summary ? `${summary.window.started_at.slice(0, 16)} → ${summary.window.ended_at.slice(0, 16)} UTC` : selectedLabel}</span><span className="usage-quality"><Badge tone={qualityTone}>Data quality</Badge><span>Pricing {fmtPct(pricingCoverage, { decimals: 1, dash: 'pending', scale: 'percent' })}</span></span></div>
      {fetching && <p className="usage-fetch-note" role="status" aria-live="polite">Refreshing in stages. Available sections remain interactive.</p>}
      {error && <div className="lever-err" role="alert">Usage insights could not be loaded: {error.message}</div>}

      {!summary ? <Skeleton label="consumption and spend" /> : <>
        <Section title={`Consumption & spend · ${summaryLabel}`} section={summary.token_mix!}>
          <div className="usage-primary-metrics">
            <MetricTile label="Total tokens" value={count(summary.token_mix!.total_tokens)} context={summaryLabel} />
            <MetricTile label="Input tokens" value={count(summary.token_mix!.input_tokens)} context={summaryLabel} />
            <MetricTile label="Output tokens" value={count(summary.token_mix!.output_tokens)} context={summaryLabel} />
            <MetricTile label="Estimated cost" value={usd(summary.pricing!.estimated_cost_usd)} context={summaryLabel} />
            <MetricTile label="LLM calls" value={count(summary.token_mix!.event_count)} context={summaryLabel} />
            <MetricTile label="Pricing coverage" value={fmtPct(summary.pricing!.pricing_coverage_pct, { decimals: 1, scale: 'percent' })} context={`${count(summary.pricing!.unknown_event_count)} unknown`} />
          </div>
          <div className="usage-token-band">
            <TokenComposition input={summary.token_mix!.input_tokens} output={summary.token_mix!.output_tokens} cached={summary.token_mix!.cached_tokens} reasoning={summary.token_mix!.reasoning_tokens} />
            <Card className="usage-cost-provenance"><CardHeader title="Cost provenance" actions={<Button size="sm" variant="secondary" disabled={pricingRefresh?.status === 'running' || refreshPricing.isPending} onClick={() => refreshPricing.mutate()}>{pricingRefresh?.status === 'running' || refreshPricing.isPending ? 'Refreshing…' : 'Refresh pricing'}</Button>} /><div className="usage-provenance-grid"><div><span>Known</span><strong>{usd(summary.pricing!.known_cost_usd)}</strong></div><div><span>Estimated</span><strong>{usd(summary.pricing!.estimated_cost_usd)}</strong></div><div><span>Unknown events</span><strong>{count(summary.pricing!.unknown_event_count)}</strong></div></div>{pricingRefresh?.status === 'failed' && <div className="lever-err" role="alert">Pricing refresh failed: {pricingRefresh.error ?? 'unknown error'}</div>}{pricingRefresh?.last_success_at && <p className="hint">LiteLLM estimate refreshed {pricingRefresh.last_success_at}; {pricingRefresh.snapshots_created} snapshot(s) added.</p>}</Card>
          </div>
        </Section>
      </>}

      {!operations ? <Skeleton label="usage trends and breakdowns" /> : <>
        <Section title={`Usage & cost trend · ${operationsLabel}`} section={operations.trends!}>
          <div className="usage-trend-grid"><Card><CardHeader title="Daily tokens" meta="Input and output are mutually exclusive totals." /><ResponsiveContainer width="100%" height={240}><BarChart data={trendRows}><CartesianGrid stroke={C.border} vertical={false} /><XAxis dataKey="date" tick={{ fill: C.muted, fontSize: 10 }} /><YAxis tick={{ fill: C.muted, fontSize: 10 }} tickFormatter={value => fmtCompact(Number(value))} /><Tooltip {...tooltipProps} formatter={(value: unknown, name: unknown) => [fmtNum(Number(value)), humanize(String(name))]} /><Legend /><Bar dataKey="input_tokens" stackId="tokens" fill={C.accent} /><Bar dataKey="output_tokens" stackId="tokens" fill={C.green} /></BarChart></ResponsiveContainer></Card><Card><CardHeader title="Daily cost" meta="Known and estimated provenance remain separate." /><ResponsiveContainer width="100%" height={240}><BarChart data={trendRows}><CartesianGrid stroke={C.border} vertical={false} /><XAxis dataKey="date" tick={{ fill: C.muted, fontSize: 10 }} /><YAxis tick={{ fill: C.muted, fontSize: 10 }} tickFormatter={value => `$${fmtCompact(Number(value))}`} /><Tooltip {...tooltipProps} formatter={(value: unknown, name: unknown) => [usd(Number(value)), humanize(String(name))]} /><Legend /><Bar dataKey="known_cost_usd" stackId="cost" fill={C.green} /><Bar dataKey="estimated_cost_usd" stackId="cost" fill={C.yellow} /></BarChart></ResponsiveContainer></Card></div>
        </Section>
        <Section title={`Breakdown · ${operationsLabel}`} section={operations.attribution!}><InsightTable title="Attribution" rows={operations.attribution!.rows} empty="No attributable usage reported." /><div className="usage-section-gap"><InsightTable title="Tools" rows={operations.tools!.rows} empty="No tool usage reported." /></div></Section>
      </>}

      {summary && <Section title={`Reliability · ${summaryLabel}`} section={summary.reliability!}><div className="usage-reliability-grid"><Facts title="Logical reliability" facts={summary.reliability!.logical} /><Facts title="Physical reliability" facts={summary.reliability!.physical} /></div><div className="usage-health-strip"><span>Physical attempts <strong>{count(Number(physical?.attempt_count ?? 0))}</strong></span><span>Failures <strong>{count(Number(physical?.failure_count ?? 0))}</strong></span><span>P95 latency <strong>{physical?.p95_duration_ms == null ? 'N/A' : `${fmtNum(Number(physical.p95_duration_ms))} ms`}</strong></span></div><div className="usage-section-gap"><InsightTable title="Current model cooldown" rows={summary.reliability!.current_model_cooldown.rows} empty="No model is currently cooling down." /></div></Section>}
      {operations && <Section title={`Budgets · ${operationsLabel}`} section={operations.budgets!}><InsightTable title="Budget observations" rows={operations.budgets!.rows} empty="No budget observations reported." /></Section>}

      <Section title="Data coverage & gaps" section={{ status: coverageStatus, reason: coverageReason }}><CoverageList axes={freshAxes} />{summary?.pricing && <div className="usage-section-gap"><InsightTable title="Pricing gaps" rows={summary.pricing.gaps} empty="No pricing gaps reported." /></div>}</Section>

      {!analysis ? <Skeleton label="research yield" /> : <Section title={`Research yield · ${analysisLabel}`} section={analysis.research_yield!}><div className="usage-yield-summary"><MetricTile label="Produced rows" value={count(analysis.research_yield!.artifacts.length)} context="aggregated run/type rows" /><MetricTile label="Lineage stages" value={count(analysis.research_yield!.lineage.length)} context="factual coverage checks" /></div><div className="usage-yield-grid"><InsightTable title="Produced artifacts" rows={analysis.research_yield!.artifacts} empty="No typed research artifacts reported." /><InsightTable title="Artifact lineage" rows={analysis.research_yield!.lineage} empty="No factual artifact lineage reported." /></div></Section>}
    </div>
  )
}

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  api,
  type UsageAgentBreakdown,
  type UsageBudget,
  type UsageModelBreakdown,
  type UsageProviderBreakdown,
  type UsageToolBreakdown,
} from '../api/client'
import { DataTable } from '../components/DataTable'
import MetricTile from '../components/MetricTile'
import PageHeader from '../components/PageHeader'
import { Button, Card, CardHeader } from '../components/ui'
import { fmtCompact } from '../lib/format'

const DAY_OPTIONS = [{ days: 1, label: '1d' }, { days: 7, label: '7d' }, { days: 30, label: '30d' }, { days: 90, label: '90d' }] as const
const TREND_OPTIONS = ['Cost', 'Tokens', 'Calls'] as const
const GROUP_OPTIONS = ['Agent', 'Model', 'Provider', 'Tool'] as const
const C = { accent: '#22d3ee', green: '#2ee6a8', border: '#232c36', muted: '#7d8997', text: '#e6edf3' }
const tooltipProps = { contentStyle: { background: '#1a212a', border: `1px solid ${C.border}`, borderRadius: 5, fontSize: 11 }, labelStyle: { color: C.text }, itemStyle: { color: C.text } }

const integer = (value: number | null | undefined) => value == null ? 'N/A' : Math.round(value).toLocaleString()
const compact = (value: number | null | undefined) => value == null ? 'N/A' : Math.abs(value) < 1000 ? integer(value) : fmtCompact(value)
const percent = (value: number | null | undefined) => value == null ? 'N/A' : `${value.toFixed(1)}%`
const duration = (value: number | null | undefined) => value == null ? 'N/A' : `${Math.round(value).toLocaleString()} ms`
const usd = (value: number | null | undefined) => value == null ? 'N/A' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: value === 0 ? 0 : 2, maximumFractionDigits: 6 }).format(value)
const delta = (complete: boolean, value: number | null | undefined, points = false) => !complete || value == null ? 'Selected window' : `${value > 0 ? '+' : ''}${value.toFixed(1)}${points ? ' pp' : '%'} vs previous`

const agentColumns: ColumnDef<UsageAgentBreakdown>[] = [
  { accessorKey: 'agent', header: 'Agent' }, { accessorKey: 'runs', header: 'Runs', cell: info => integer(info.getValue<number>()) },
  { accessorKey: 'calls', header: 'LLM calls', cell: info => integer(info.getValue<number>()) }, { accessorKey: 'success_rate_pct', header: 'Success', cell: info => percent(info.getValue<number | null>()) },
  { accessorKey: 'tokens', header: 'Tokens', cell: info => integer(info.getValue<number | null>()) }, { accessorKey: 'cost_usd', header: 'Cost', cell: info => usd(info.getValue<number | null>()) },
  { accessorKey: 'p95_latency_ms', header: 'P95 latency', cell: info => duration(info.getValue<number | null>()) },
]
const modelColumns: ColumnDef<UsageModelBreakdown>[] = [
  { accessorKey: 'model', header: 'Model' }, { accessorKey: 'provider', header: 'Provider' }, { accessorKey: 'calls', header: 'Calls', cell: info => integer(info.getValue<number>()) },
  { accessorKey: 'success_rate_pct', header: 'Success', cell: info => percent(info.getValue<number | null>()) }, { accessorKey: 'input_tokens', header: 'Input', cell: info => integer(info.getValue<number | null>()) },
  { accessorKey: 'output_tokens', header: 'Output', cell: info => integer(info.getValue<number | null>()) }, { accessorKey: 'cost_usd', header: 'Cost', cell: info => usd(info.getValue<number | null>()) },
  { accessorKey: 'p95_latency_ms', header: 'P95', cell: info => duration(info.getValue<number | null>()) },
]
const providerColumns: ColumnDef<UsageProviderBreakdown>[] = [
  { accessorKey: 'provider', header: 'Provider' }, { accessorKey: 'models', header: 'Models', cell: info => integer(info.getValue<number>()) },
  { accessorKey: 'calls', header: 'Calls', cell: info => integer(info.getValue<number>()) }, { accessorKey: 'success_rate_pct', header: 'Success', cell: info => percent(info.getValue<number | null>()) },
  { accessorKey: 'tokens', header: 'Tokens', cell: info => integer(info.getValue<number | null>()) }, { accessorKey: 'cost_usd', header: 'Cost', cell: info => usd(info.getValue<number | null>()) },
  { accessorKey: 'p95_latency_ms', header: 'P95', cell: info => duration(info.getValue<number | null>()) },
]
const toolColumns: ColumnDef<UsageToolBreakdown>[] = [
  { accessorKey: 'tool', header: 'Tool' }, { accessorKey: 'class', header: 'Class' }, { accessorKey: 'permitted', header: 'Permitted', cell: info => integer(info.getValue<number>()) },
  { accessorKey: 'denied', header: 'Denied', cell: info => integer(info.getValue<number>()) }, { accessorKey: 'calls', header: 'Calls', cell: info => integer(info.getValue<number>()) },
  { accessorKey: 'p50_latency_ms', header: 'P50', cell: info => duration(info.getValue<number | null>()) }, { accessorKey: 'p95_latency_ms', header: 'P95', cell: info => duration(info.getValue<number | null>()) },
]
const budgetColumns: ColumnDef<UsageBudget>[] = [
  { accessorKey: 'limit_usd', header: 'Limit', cell: info => usd(info.getValue<number>()) }, { accessorKey: 'spend_usd', header: 'Spend', cell: info => usd(info.getValue<number | null>()) },
  { accessorKey: 'remaining_usd', header: 'Remaining', cell: info => usd(info.getValue<number | null>()) }, { accessorKey: 'utilization_pct', header: 'Utilization', cell: info => percent(info.getValue<number | null>()) },
  { accessorKey: 'period', header: 'Period' },
]

function Skeleton({ label }: { label: string }) {
  return <section className="usage-section skeleton" role="status" aria-label={`Loading ${label}`}><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></section>
}

export default function Usage() {
  const [days, setDays] = useState<1 | 7 | 30 | 90>(1)
  const [trendMetric, setTrendMetric] = useState<typeof TREND_OPTIONS[number]>('Cost')
  const [groupBy, setGroupBy] = useState<typeof GROUP_OPTIONS[number]>('Agent')
  const summaryQuery = useQuery({ queryKey: ['usage-insights', days, 'summary'], queryFn: () => api.getUsageInsights(days, 'summary') })
  const summary = summaryQuery.data
  const operationsQuery = useQuery({ queryKey: ['usage-insights', days, 'operations'], queryFn: () => api.getUsageInsights(days, 'operations'), enabled: summary?.window.days === days })
  const operations = operationsQuery.data
  const error = summaryQuery.error ?? operationsQuery.error

  return (
    <div className="usage-page" aria-busy={summaryQuery.isPending || operationsQuery.isPending}>
      <PageHeader title="Usage" role="LLM consumption, cost, and service health." actions={<div className="usage-header-actions"><span className="usage-updated">{summary ? `Updated ${summary.window.updated_at.slice(0, 16)} UTC` : 'Updated — UTC'}</span><div className="seg-toggle" aria-label="Usage time range">{DAY_OPTIONS.map(option => <Button key={option.days} variant="ghost" className={`seg-btn${days === option.days ? ' active' : ''}`} aria-pressed={days === option.days} onClick={() => setDays(option.days)}>{option.label}</Button>)}</div></div>} />
      {error && <div className="lever-err" role="alert">Usage could not be loaded: {error.message}</div>}

      {!summary ? <Skeleton label="usage overview" /> : <>
        <section className="usage-section">
          <div className="usage-primary-metrics">
            <MetricTile label="Cost" value={usd(summary.overview.cost_usd)} context={delta(summary.comparison.complete, summary.comparison.cost_usd_change_pct)} />
            <MetricTile label="Total tokens" value={<span title={integer(summary.overview.total_tokens)}>{compact(summary.overview.total_tokens)}</span>} context={delta(summary.comparison.complete, summary.comparison.total_tokens_change_pct)} />
            <MetricTile label="LLM calls" value={integer(summary.overview.llm_calls)} context={delta(summary.comparison.complete, summary.comparison.llm_calls_change_pct)} />
            <MetricTile label="Success rate" value={percent(summary.overview.success_rate_pct)} context={delta(summary.comparison.complete, summary.comparison.success_rate_change_pct_points, true)} />
            <MetricTile label="Avg cost / call" value={usd(summary.overview.avg_cost_per_call_usd)} context={delta(summary.comparison.complete, summary.comparison.avg_cost_per_call_usd_change_pct)} />
          </div>
        </section>

        <section className="usage-section"><div className="usage-section-head"><h2>Trend</h2><div className="seg-toggle" aria-label="Trend metric">{TREND_OPTIONS.map(option => <Button key={option} variant="ghost" className={`seg-btn${trendMetric === option ? ' active' : ''}`} aria-pressed={trendMetric === option} onClick={() => setTrendMetric(option)}>{option}</Button>)}</div></div>
          <Card><ResponsiveContainer width="100%" height={280}><BarChart data={summary.trend}><CartesianGrid stroke={C.border} vertical={false} /><XAxis dataKey="started_at" tick={{ fill: C.muted, fontSize: 10 }} tickFormatter={value => String(value).slice(5, 16)} /><YAxis tick={{ fill: C.muted, fontSize: 10 }} tickFormatter={value => fmtCompact(Number(value))} /><Tooltip {...tooltipProps} formatter={(value: unknown, name: unknown) => [trendMetric === 'Cost' ? usd(Number(value)) : integer(Number(value)), String(name).replace(/_/g, ' ')]} />{trendMetric === 'Tokens' && <Legend />}{trendMetric === 'Cost' && <Bar dataKey="cost_usd" fill={C.accent} />}{trendMetric === 'Calls' && <Bar dataKey="llm_calls" fill={C.accent} />}{trendMetric === 'Tokens' && <><Bar dataKey="input_tokens" stackId="tokens" fill={C.accent} /><Bar dataKey="output_tokens" stackId="tokens" fill={C.green} /></>}</BarChart></ResponsiveContainer></Card>
        </section>

        <section className="usage-section"><div className="usage-section-head"><h2>Token composition</h2></div><Card><CardHeader title="Input and output" meta="Cache and reasoning are included in input/output; do not add them again." /><div className="usage-composition-grid"><div><span>Input</span><strong>{integer(summary.token_composition.input_tokens)}</strong></div><div><span>Output</span><strong>{integer(summary.token_composition.output_tokens)}</strong></div>{summary.token_composition.cache_read_tokens !== undefined && <div><span>Cache read</span><strong>{integer(summary.token_composition.cache_read_tokens)}</strong></div>}{summary.token_composition.cache_write_tokens !== undefined && <div><span>Cache write</span><strong>{integer(summary.token_composition.cache_write_tokens)}</strong></div>}{summary.token_composition.reasoning_tokens !== undefined && <div><span>Reasoning</span><strong>{integer(summary.token_composition.reasoning_tokens)}</strong></div>}</div></Card></section>
      </>}

      {!operations ? <Skeleton label="breakdown and reliability" /> : <>
        <section className="usage-section"><div className="usage-section-head"><h2>Breakdown</h2><div className="seg-toggle" aria-label="Group by">{GROUP_OPTIONS.map(option => <Button key={option} variant="ghost" className={`seg-btn${groupBy === option ? ' active' : ''}`} aria-pressed={groupBy === option} onClick={() => setGroupBy(option)}>{option}</Button>)}</div></div><Card flush className="usage-table-card">{groupBy === 'Agent' && <DataTable data={operations.breakdowns.agents} columns={agentColumns} emptyMessage="No agent usage in this window." />}{groupBy === 'Model' && <DataTable data={operations.breakdowns.models} columns={modelColumns} emptyMessage="No model usage in this window." />}{groupBy === 'Provider' && <DataTable data={operations.breakdowns.providers} columns={providerColumns} emptyMessage="No provider usage in this window." />}{groupBy === 'Tool' && <DataTable data={operations.breakdowns.tools} columns={toolColumns} emptyMessage="No tool usage in this window." />}</Card></section>

        <section className="usage-section"><div className="usage-section-head"><h2>Reliability</h2></div><div className="usage-reliability-metrics"><MetricTile label="Attempts" value={integer(operations.reliability.attempts)} /><MetricTile label="Errors" value={integer(operations.reliability.errors)} /><MetricTile label="Retries" value={integer(operations.reliability.retries)} /><MetricTile label="Fallbacks" value={integer(operations.reliability.fallbacks)} /><MetricTile label="P50" value={duration(operations.reliability.p50_latency_ms)} /><MetricTile label="P95" value={duration(operations.reliability.p95_latency_ms)} /></div>{operations.reliability.error_categories?.length ? <Card className="usage-errors"><CardHeader title="Errors by category" />{operations.reliability.error_categories.map(row => <div key={row.category}><span>{row.category}</span><strong>{integer(row.count)}</strong></div>)}</Card> : null}{operations.active_cooldowns.length > 0 && <Card className="usage-cooldowns"><CardHeader title="Active cooldowns" />{operations.active_cooldowns.map(row => <p key={`${row.provider}/${row.model}`}>{row.provider} / {row.model} · {integer(row.remaining_seconds)}s remaining</p>)}</Card>}</section>

        {operations.budgets.length > 0 && <section className="usage-section"><div className="usage-section-head"><h2>Budget</h2></div><Card flush className="usage-table-card"><DataTable data={operations.budgets} columns={budgetColumns} emptyMessage="" /></Card></section>}
      </>}
    </div>
  )
}

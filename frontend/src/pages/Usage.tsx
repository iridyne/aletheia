import { useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import {
  api,
  type UsageInsightsRow,
  type UsageInsightsSection,
} from '../api/client'
import PageHeader from '../components/PageHeader'
import MetricTile from '../components/MetricTile'
import { Badge, Button, Card, CardHeader, TableWrap } from '../components/ui'

const DAY_OPTIONS = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
] as const

const C = {
  green: '#2ee6a8',
  accent: '#22d3ee',
  yellow: '#f5b942',
  purple: '#a78bfa',
  border: '#232c36',
  text: '#e6edf3',
  muted: '#7a8a99',
}

const tooltipProps = {
  contentStyle: {
    background: '#1d2530',
    border: `1px solid ${C.border}`,
    borderRadius: 6,
    fontSize: 11,
  },
  labelStyle: { color: C.text },
  itemStyle: { color: C.text },
}

const fmtUsd = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(6)}` : 'N/A'

const fmtPct = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)}%` : 'N/A'

const fmtCount = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : 'N/A'

function fmtTokens(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'N/A'
  const bytes = value * 4
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

function formatKey(key: string) {
  return key.replace(/_/g, ' ')
}

function formatValue(value: unknown): string {
  if (value == null) return 'N/A'
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString() : 'N/A'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'string') return value.trim() || 'unknown'
  if (Array.isArray(value)) return value.length ? `${value.length} item(s)` : 'none'
  return 'unknown'
}

function SectionStatus({ section }: { section: UsageInsightsSection }) {
  if (section.status === 'available') return null
  const label = section.status === 'partial' ? 'Partial' : 'N/A'
  return (
    <p className="hint" style={{ marginBottom: 'var(--space-2)' }}>
      <Badge tone="yellow">{label}</Badge>{' '}
      {section.reason ?? 'Coverage is unknown for this window.'}
    </p>
  )
}

function UsageSection({ title, section, children }: {
  title: string
  section: UsageInsightsSection
  children: ReactNode
}) {
  return (
    <section style={{ marginBottom: 'var(--space-4)' }}>
      <h3 style={{ marginBottom: '0.5rem' }}>{title}</h3>
      <SectionStatus section={section} />
      {children}
    </section>
  )
}

function InsightTable({ title, rows, empty }: {
  title: string
  rows: UsageInsightsRow[]
  empty: string
}) {
  const columns = useMemo(
    () => Array.from(new Set(rows.flatMap(row => Object.keys(row)))),
    [rows],
  )
  if (!rows.length || !columns.length) return <p className="hint">{empty}</p>

  return (
    <Card flush>
      <TableWrap>
        <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
          <caption style={{ textAlign: 'left', padding: 'var(--space-2)' }}>{title}</caption>
          <thead>
            <tr>
              {columns.map(column => <th key={column}>{formatKey(column)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={String(row.id ?? row.name ?? index)}>
                {columns.map(column => <td key={column}>{formatValue(row[column])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  )
}

function InsightFacts({ title, facts }: { title: string; facts: UsageInsightsRow | null }) {
  const rows = Object.entries(facts ?? {})
  if (!rows.length) return <p className="hint">N/A — no facts reported for this window.</p>

  return (
    <Card flush>
      <TableWrap>
        <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
          <caption style={{ textAlign: 'left', padding: 'var(--space-2)' }}>{title}</caption>
          <tbody>
            {rows.map(([key, value]) => (
              <tr key={key}>
                <th scope="row" style={{ textAlign: 'left' }}>{formatKey(key)}</th>
                <td>{formatValue(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  )
}

export default function Usage() {
  const [days, setDays] = useState<7 | 30 | 90>(7)
  const { data: insights, isLoading, error } = useQuery({
    queryKey: ['usage-insights', days],
    queryFn: () => api.getUsageInsights(days),
    refetchInterval: 30_000,
  })

  const windowLabel = `last ${days} days`
  const range = insights
    ? `${insights.window.started_at.slice(0, 10)} → ${insights.window.ended_at.slice(0, 10)} UTC`
    : `${windowLabel} (UTC)`
  const coverageAxes = insights?.coverage.axes ?? []
  const measurableAxes = coverageAxes.filter(axis => axis.coverage_pct != null)
  const tokenMix = insights?.token_mix
  const tokenPie = [
    { name: 'input', value: tokenMix?.input_tokens ?? null, color: C.accent },
    { name: 'output', value: tokenMix?.output_tokens ?? null, color: C.green },
    { name: 'reasoning', value: tokenMix?.reasoning_tokens ?? null, color: C.purple },
    { name: 'cached', value: tokenMix?.cached_tokens ?? null, color: C.yellow },
  ].filter((item): item is { name: string; value: number; color: string } => item.value != null && item.value > 0)

  return (
    <div>
      <PageHeader
        title="Usage insights"
        role={`Read-only usage, coverage, and research-yield evidence for ${windowLabel} (${range}). Unknown and unavailable values are labelled explicitly.`}
        actions={
          <div className="seg-toggle" aria-label="Usage time range">
            {DAY_OPTIONS.map(option => (
              <Button
                key={option.days}
                variant="ghost"
                className={`seg-btn${days === option.days ? ' active' : ''}`}
                aria-pressed={days === option.days}
                onClick={() => setDays(option.days)}
              >
                {option.label}
              </Button>
            ))}
          </div>
        }
      />

      {isLoading && <p className="hint">Loading usage insights for {windowLabel}…</p>}
      {error && <p className="hint">N/A — usage insights could not be loaded: {error.message}</p>}
      {!insights || (
        <>
          <p className="hint" style={{ marginBottom: 'var(--space-3)' }}>
            Window: {range}. As of {insights.window.as_of} ({insights.window.timezone}).
          </p>

          <UsageSection title={`Coverage (${windowLabel})`} section={insights.coverage}>
            <Card>
              <CardHeader title="Coverage radar" />
              {measurableAxes.length ? (
                <ResponsiveContainer width="100%" height={280}>
                  <RadarChart data={measurableAxes} margin={{ top: 18, right: 32, bottom: 18, left: 32 }}>
                    <PolarGrid stroke={C.border} />
                    <PolarAngleAxis dataKey="label" tick={{ fill: C.muted, fontSize: 11 }} />
                    <Radar
                      name="Measured coverage"
                      dataKey="coverage_pct"
                      stroke={C.accent}
                      fill={C.accent}
                      fillOpacity={0.2}
                      strokeWidth={1.5}
                    />
                    <Tooltip {...tooltipProps} formatter={(value: any) => [fmtPct(Number(value)), 'Coverage']} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </RadarChart>
                </ResponsiveContainer>
              ) : (
                <p className="hint">N/A — no coverage percentages are measured in this window.</p>
              )}
            </Card>
            <Card flush>
              <TableWrap>
                <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
                  <caption style={{ textAlign: 'left', padding: 'var(--space-2)' }}>Coverage details (text equivalent)</caption>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left' }}>Measure</th>
                      <th style={{ textAlign: 'left' }}>Status</th>
                      <th style={{ textAlign: 'right' }}>Coverage</th>
                      <th style={{ textAlign: 'left' }}>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {coverageAxes.map(axis => (
                      <tr key={axis.id}>
                        <td>{axis.label}</td>
                        <td>{axis.status === 'not_available' ? 'N/A' : axis.status}</td>
                        <td style={{ textAlign: 'right' }}>{fmtPct(axis.coverage_pct)}</td>
                        <td>{axis.reason ?? (axis.coverage_pct == null ? 'unknown' : '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
          </UsageSection>

          <UsageSection title={`Token mix (${windowLabel})`} section={insights.token_mix}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
              <MetricTile label="Input" value={fmtTokens(tokenMix?.input_tokens)} context={windowLabel} />
              <MetricTile label="Output" value={fmtTokens(tokenMix?.output_tokens)} context={windowLabel} />
              <MetricTile label="Reasoning" value={fmtTokens(tokenMix?.reasoning_tokens)} context={windowLabel} />
              <MetricTile label="Cached" value={fmtTokens(tokenMix?.cached_tokens)} context={windowLabel} />
              <MetricTile label="LLM events" value={fmtCount(tokenMix?.event_count)} context={windowLabel} />
            </div>
            <Card>
              {tokenPie.length ? (
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie data={tokenPie} dataKey="value" nameKey="name" innerRadius={45} outerRadius={80} paddingAngle={3} stroke={C.border}>
                      {tokenPie.map(item => <Cell key={item.name} fill={item.color} />)}
                    </Pie>
                    <Tooltip {...tooltipProps} formatter={(value: any, name: any) => [fmtTokens(Number(value)), String(name)]} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              ) : <p className="hint">N/A — no measured token mix in this window.</p>}
            </Card>
          </UsageSection>

          <UsageSection title={`Pricing (${windowLabel})`} section={insights.pricing}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
              <MetricTile label="Known cost" value={fmtUsd(insights.pricing.known_cost_usd)} context={windowLabel} />
              <MetricTile label="Estimated cost" value={fmtUsd(insights.pricing.estimated_cost_usd)} context={windowLabel} />
              <MetricTile label="Pricing coverage" value={fmtPct(insights.pricing.pricing_coverage_pct)} context={windowLabel} />
              <MetricTile label="Unknown-price events" value={fmtCount(insights.pricing.unknown_event_count)} context={windowLabel} />
            </div>
            <InsightTable title="Pricing gaps" rows={insights.pricing.gaps} empty="No pricing gaps reported in this window." />
          </UsageSection>

          <UsageSection title={`Attribution (${windowLabel})`} section={insights.attribution}>
            <InsightTable title="Attribution rows" rows={insights.attribution.rows} empty="N/A — no attributable usage reported in this window." />
          </UsageSection>

          <UsageSection title={`Tools (${windowLabel})`} section={insights.tools}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
              <MetricTile label="Timing coverage" value={fmtPct(insights.tools.timing_coverage_pct)} context={windowLabel} />
              <MetricTile label="Pricing coverage" value={fmtPct(insights.tools.pricing_coverage_pct)} context={windowLabel} />
            </div>
            <InsightTable title="Tool rows" rows={insights.tools.rows} empty="N/A — no tool usage reported in this window." />
          </UsageSection>

          <UsageSection title={`Reliability (${windowLabel})`} section={insights.reliability}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 'var(--space-3)' }}>
              <InsightFacts title="Logical reliability" facts={insights.reliability.logical} />
              <InsightFacts title="Physical reliability" facts={insights.reliability.physical} />
            </div>
            <div style={{ marginTop: 'var(--space-3)' }}>
              <h4 style={{ marginBottom: '0.5rem' }}>Current model health</h4>
              <SectionStatus section={insights.reliability.current_model_cooldown} />
              <p className="hint">
                Current in-process state as of {insights.reliability.current_model_cooldown.as_of}; this is not {windowLabel} data.
              </p>
              <InsightTable
                title="Current model cooldown"
                rows={insights.reliability.current_model_cooldown.rows}
                empty="N/A — no current model-health facts are available."
              />
            </div>
          </UsageSection>

          <UsageSection title={`Budgets (${windowLabel})`} section={insights.budgets}>
            <InsightTable title="Budget observations" rows={insights.budgets.rows} empty="N/A — no budget observations reported in this window." />
          </UsageSection>

          <UsageSection title={`Trends (${windowLabel})`} section={insights.trends}>
            <InsightFacts title="Trend trace coverage" facts={insights.trends.trace_coverage} />
            <InsightFacts title="Trend baseline" facts={insights.trends.baseline} />
            <div style={{ marginTop: 'var(--space-3)' }}>
              <InsightTable title="Daily trend observations" rows={insights.trends.daily} empty="N/A — no daily trend observations reported in this window." />
            </div>
          </UsageSection>

          <UsageSection title={`Research yield (${windowLabel})`} section={insights.research_yield}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 'var(--space-3)' }}>
              <InsightTable title="Produced artifacts" rows={insights.research_yield.artifacts} empty="N/A — no typed research artifacts reported in this window." />
              <InsightTable title="Artifact lineage" rows={insights.research_yield.lineage} empty="N/A — no factual artifact lineage reported in this window." />
            </div>
          </UsageSection>
        </>
      )}
    </div>
  )
}

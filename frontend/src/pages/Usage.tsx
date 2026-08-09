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
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, type EconomicsProviderBucket, type EconomicsToolBucket } from '../api/client'
import PageHeader from '../components/PageHeader'
import MetricTile from '../components/MetricTile'
import { Badge, Button, Card, CardHeader, TableWrap } from '../components/ui'

/**
 * API Usage — the read-only cost / token surface for the tokenomics harness.
 *
 * This is the top-level economics dashboard: money and tokens first (7d/30d/90d
 * window selector), then per-provider token cards, daily trend charts, and the
 * budget / cooldown ledger surfaces that used to live inside Settings.
 *
 * Everything here is read-only; policies and pricing snapshots stay CLI-managed
 * (`uv run aletheia audit …`).
 */

const DAY_OPTIONS = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
]

// Chart palette mirrors the CSS tokens in index.css (:root). Providers get
// colors round-robin from this fixed list so the stacked series stay stable.
const C = {
  green: '#2ee6a8',
  red: '#ff5d6c',
  accent: '#22d3ee',
  yellow: '#f5b942',
  purple: '#a78bfa',
  blue: '#4aa3ff',
  orange: '#fb923c',
  pink: '#f472b6',
  surface: '#141a21',
  border: '#232c36',
  text: '#e6edf3',
  muted: '#7a8a99',
}
const PROVIDER_COLORS = [C.accent, C.green, C.purple, C.yellow, C.blue, C.orange, C.pink, C.red]

const axisStyle = { fill: C.muted, fontSize: 10 }
const gridProps = { stroke: C.border, strokeDasharray: '3 3' }
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

const fmtUsd = (v: number) => `$${v.toFixed(6)}`

// token → bytes is an estimate, not a measurement: the industry-standard
// approximation is 1 token ≈ 4 bytes (≈3-4 chars of English text). We show the
// rounded MB/KB for readability and keep the exact token count in the hover
// title — never present the conversion as an exact byte count.
const BYTES_PER_TOKEN = 4
const KB = 1024
const MB = 1024 * 1024
function fmtTokens(tokens: number): string {
  const bytes = tokens * BYTES_PER_TOKEN
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)} MB`
  if (bytes >= KB) return `${Math.round(bytes / KB)} KB`
  return `${bytes} B`
}

export default function Usage() {
  const [days, setDays] = useState(7)

  // Polling intervals are relaxed from 30s→60s: the cost endpoints now run
  // SQL GROUP BY aggregation with index-backed range scans (~50ms) instead of
  // full row scans in Python (~1.4s), so less frequent polling keeps the same
  // freshness with lower background load.
  const { data: status } = useQuery({
    queryKey: ['economics-status'],
    queryFn: api.getEconomicsStatus,
    refetchInterval: 60_000,
  })
  const { data: costs } = useQuery({
    queryKey: ['economics-costs', days],
    queryFn: () => api.getEconomicsCosts({ days }),
    refetchInterval: 60_000,
  })
  const { data: byProvider } = useQuery({
    queryKey: ['economics-costs-by-provider', days],
    // The provider aggregation is now a SQL GROUP BY (no row-scan LIMIT);
    // event_limit is kept for backward compatibility but no longer caps the
    // aggregation result.
    queryFn: () => api.getEconomicsCostsByProvider({ days, event_limit: days >= 30 ? 100_000 : 50_000 }),
    refetchInterval: 60_000,
  })
  const { data: byTool } = useQuery({
    queryKey: ['economics-costs-by-tool', days],
    // Same as by-provider: SQL GROUP BY aggregation, event_limit kept for API
    // compatibility but no longer bounds the output.
    queryFn: () => api.getEconomicsCostsByTool({ days, event_limit: days >= 30 ? 100_000 : 50_000 }),
    refetchInterval: 60_000,
  })
  // All-time totals: the cost ledger's full history (starts 2026-07-11), via
  // the widest window the endpoint allows (365d) — no backend change needed.
  const { data: allTime } = useQuery({
    queryKey: ['economics-costs-by-provider', 'all'],
    queryFn: () => api.getEconomicsCostsByProvider({ days: 365, event_limit: 100_000 }),
    refetchInterval: 60_000,
  })
  const { data: allTimeTools } = useQuery({
    queryKey: ['economics-costs-by-tool', 'all'],
    queryFn: () => api.getEconomicsCostsByTool({ days: 365, event_limit: 100_000 }),
    refetchInterval: 60_000,
  })
  // Full run history — same queryKey as AgentActivity so the fetch is shared.
  const { data: runs = [] } = useQuery({
    queryKey: ['agent-runs'],
    queryFn: api.getAgentRuns,
    staleTime: 60_000,
  })
  const { data: budgets } = useQuery({
    queryKey: ['economics-budgets'],
    queryFn: () => api.getEconomicsBudgets({ enabled_only: false }),
    refetchInterval: 60_000,
  })
  const { data: wouldBlock } = useQuery({
    queryKey: ['economics-would-block'],
    queryFn: () => api.getEconomicsBudgetsWouldBlock({ days: 7, limit: 50 }),
    refetchInterval: 60_000,
  })
  const { data: modelHealth } = useQuery({
    queryKey: ['economics-model-health'],
    queryFn: api.getEconomicsModelHealth,
    refetchInterval: 10_000,
  })

  const tracingEnabled = status?.tracing_enabled === true
  const windowLabel = `${days}d`

  const providerEntries = useMemo(
    () => (byProvider?.providers ? Object.entries(byProvider.providers) : []),
    [byProvider],
  )

  // daily cost stacked by provider: { date, providerA: $, providerB: $ }
  const costSeries = useMemo(() => {
    if (!byProvider?.daily_series?.length) return []
    const byDate = new Map<string, Record<string, number>>()
    const providerOrder = Object.keys(byProvider.providers)
    for (const p of byProvider.daily_series) {
      const row = byDate.get(p.date) ?? {}
      row[p.provider] = p.known_cost_usd
      byDate.set(p.date, row)
    }
    return Array.from(byDate.entries())
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([date, row]) => {
        for (const name of providerOrder) if (row[name] === undefined) row[name] = 0
        return { date, ...row }
      })
  }, [byProvider])

  // daily token volume stacked by type: { date, input/output/reasoning/cached }
  type TokenRow = {
    date: string
    input: number
    output: number
    reasoning: number
    cached: number
  }
  const tokenSeries = useMemo(() => {
    if (!byProvider?.daily_series?.length) return []
    const byDate = new Map<string, TokenRow>()
    for (const p of byProvider.daily_series) {
      const row = byDate.get(p.date) ?? { date: p.date, input: 0, output: 0, reasoning: 0, cached: 0 }
      row.input += p.input_tokens
      row.output += p.output_tokens
      row.reasoning += p.reasoning_tokens
      row.cached += p.cached_input_tokens
      byDate.set(p.date, row)
    }
    return Array.from(byDate.values()).sort((a, b) => (a.date < b.date ? -1 : 1))
  }, [byProvider])

  // flattened (provider, model) rows, biggest first — mirrors the per-model
  // usage table (calls / share / success / latency / tokens).
  const modelRows = useMemo(() => {
    const out: Array<{ provider: string; model: string; b: EconomicsProviderBucket }> = []
    for (const [provider, mods] of Object.entries(byProvider?.models ?? {})) {
      for (const [model, b] of Object.entries(mods)) out.push({ provider, model, b })
    }
    out.sort((a, z) => z.b.event_count - a.b.event_count)
    return out
  }, [byProvider])
  const totalCalls = modelRows.reduce((sum, r) => sum + r.b.event_count, 0)

  // per-tool rows (mirrored agent_tool_calls), biggest first
  const toolRows = useMemo(() => {
    const out: Array<{ tool: string; b: EconomicsToolBucket }> = []
    for (const [tool, b] of Object.entries(byTool?.tools ?? {})) out.push({ tool, b })
    out.sort((a, z) => z.b.event_count - a.b.event_count)
    return out
  }, [byTool])
  const totalToolCalls = toolRows.reduce((sum, r) => sum + r.b.event_count, 0)

  // all-time token totals across every provider (full ledger history)
  const allTimeTokens = useMemo(() => {
    const t = { input: 0, output: 0, reasoning: 0, cached: 0 }
    for (const b of Object.values(allTime?.providers ?? {})) {
      t.input += b.input_tokens
      t.output += b.output_tokens
      t.reasoning += b.reasoning_tokens
      t.cached += b.cached_input_tokens
    }
    return t
  }, [allTime])
  const allTimeToolCalls = Object.values(allTimeTools?.tools ?? {}).reduce(
    (sum, b) => sum + b.event_count, 0,
  )
  const allTimeLlmCalls = allTime?.event_count ?? 0

  // role-play = agent runs: each agent_name is a role, each run is one act
  const agentRoles = useMemo(() => {
    const counts = new Map<string, number>()
    for (const r of runs as Array<{ agent_name?: string }>) {
      const name = r?.agent_name ?? 'unknown'
      counts.set(name, (counts.get(name) ?? 0) + 1)
    }
    return Array.from(counts.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
  }, [runs])
  const allTimeRolePlays = agentRoles.reduce((sum, r) => sum + r.value, 0)

  const tokenPie = [
    { name: 'input', value: allTimeTokens.input },
    { name: 'output', value: allTimeTokens.output },
    { name: 'reasoning', value: allTimeTokens.reasoning },
    { name: 'cached', value: allTimeTokens.cached },
  ].filter(d => d.value > 0)
  const PIE_COLORS = [C.accent, C.green, C.purple, C.yellow]

  const allTimeRange = allTime
    ? `${allTime.window_start.slice(0, 10)} → ${allTime.window_end.slice(0, 10)}`
    : 'ledger history'

  // daily rollup across providers (from daily_series), newest first.
  type DailyRow = TokenRow & { events: number; cost: number }
  const dailyRows = useMemo(() => {
    if (!byProvider?.daily_series?.length) return []
    const byDate = new Map<string, DailyRow>()
    for (const p of byProvider.daily_series) {
      const row = byDate.get(p.date) ?? { date: p.date, input: 0, output: 0, reasoning: 0, cached: 0, events: 0, cost: 0 }
      row.input += p.input_tokens
      row.output += p.output_tokens
      row.reasoning += p.reasoning_tokens
      row.cached += p.cached_input_tokens
      row.events += p.event_count
      row.cost += p.known_cost_usd
      byDate.set(p.date, row)
    }
    return Array.from(byDate.values()).sort((a, b) => (a.date < b.date ? 1 : -1))
  }, [byProvider])
  const todayUtc = new Date().toISOString().slice(0, 10)

  const budgetList = budgets?.budgets ?? []
  const enabledBudgetCount = budgetList.filter(b => b.enabled === 1).length
  const wouldBlockCount = wouldBlock?.events?.length ?? 0
  const cooling = (modelHealth?.snapshot ?? []).filter(m => m.cooling_down)
  const knownUsd = costs?.known_cost_subtotal_usd ?? 0
  const unknownCount = costs?.unknown_event_count ?? 0
  const eventCount = costs?.event_count ?? 0
  const parseErrors = byProvider?.parse_errors ?? 0

  // Costs by type aggregate (costs?.by_type) — show the known $ / event split.
  const byTypeRows = Object.entries(costs?.by_type ?? {})

  return (
    <div>
      <PageHeader
        title="API Usage"
        role="Read-only cost and token ledger for LLM calls, tools, and data. Budget policies and pricing snapshots are CLI-managed."
        actions={
          <div className="seg-toggle">
            {DAY_OPTIONS.map(opt => (
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
        }
      />

      {tracingEnabled ? (
        <Badge tone="green">Cost tracing enabled</Badge>
      ) : (
        <div style={{ marginBottom: 'var(--space-3)' }}>
          <Badge tone="yellow">Cost tracing not enabled</Badge>{' '}
          <span className="hint">
            token ledger is off (ALETHEIA_COST_TRACING=0) — the numbers below show no cost events.
          </span>
        </div>
      )}

      <section style={{ marginBottom: 'var(--space-4)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>All-time usage ({allTimeRange})</h3>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 'var(--space-3)',
            marginBottom: 'var(--space-3)',
          }}
        >
          <MetricTile
            label="Total input"
            value={fmtTokens(allTimeTokens.input)}
            context="all LLM calls since ledger start"
          />
          <MetricTile
            label="Total output"
            value={fmtTokens(allTimeTokens.output)}
            context="all LLM calls since ledger start"
          />
          <MetricTile label="LLM calls" value={allTimeLlmCalls.toLocaleString()} />
          <MetricTile label="Tool calls" value={allTimeToolCalls.toLocaleString()} />
          <MetricTile
            label="Role plays (agent runs)"
            value={allTimeRolePlays.toLocaleString()}
            context={`${agentRoles.length} role(s)`}
          />
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: 'var(--space-3)',
          }}
        >
          <Card>
            <CardHeader title="Token mix (input/output/reasoning/cached)" />
            {tokenPie.length === 0 ? (
              <p className="hint">No token usage recorded.</p>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie
                    data={tokenPie}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={45}
                    outerRadius={80}
                    paddingAngle={3}
                    stroke={C.border}
                  >
                    {tokenPie.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    {...tooltipProps}
                    formatter={(v: any, name: any) => [fmtTokens(Number(v)), String(name)]}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </Card>
          <Card>
            <CardHeader title="Role plays by agent" />
            {agentRoles.length === 0 ? (
              <p className="hint">No agent runs recorded.</p>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={agentRoles} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid {...gridProps} />
                  <XAxis
                    dataKey="name"
                    tick={{ fill: C.muted, fontSize: 9 }}
                    interval={0}
                    angle={-20}
                    textAnchor="end"
                    height={60}
                  />
                  <YAxis tick={axisStyle} width={44} />
                  <Tooltip {...tooltipProps} cursor={{ fill: 'rgba(34,211,238,0.06)' }} />
                  <Bar dataKey="value" name="runs" fill={C.accent} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </Card>
        </div>
      </section>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <MetricTile
          label={`Spent (known, ${windowLabel})`}
          value={fmtUsd(knownUsd)}
          tone={unknownCount > 0 ? 'warning' : 'neutral'}
          caveat={unknownCount > 0 ? `${unknownCount} events with unknown pricing` : undefined}
        />
        <MetricTile
          label={`Events (${windowLabel})`}
          value={String(eventCount)}
          context={`by type: ${byTypeRows.map(([t, b]) => `${t} ${b.event_count}`).join(' · ')}`}
        />
        <MetricTile label="Providers" value={String(providerEntries.length)} />
        <MetricTile
          label="Declared budgets"
          value={String(budgetList.length)}
          context={`${enabledBudgetCount} enabled`}
        />
        <MetricTile
          label={`Would-block events (7d)`}
          value={String(wouldBlockCount)}
          tone={wouldBlockCount > 0 ? 'warning' : 'neutral'}
        />
        <MetricTile
          label="Models cooling down"
          value={String(cooling.length)}
          tone={cooling.length > 0 ? 'warning' : 'neutral'}
        />
      </div>

      {parseErrors > 0 && (
        <p className="hint" style={{ marginBottom: 'var(--space-3)' }}>
          {parseErrors} cost event(s) had usage payloads that could not be parsed;
          they still count toward totals but have no token breakdown.
        </p>
      )}

      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Model usage ({windowLabel})</h3>
        {modelRows.length === 0 ? (
          <p className="hint">No cost events in this window.</p>
        ) : (
          <div>
            <Card flush>
              <TableWrap>
                <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left' }}>Provider</th>
                      <th style={{ textAlign: 'left' }}>Model</th>
                      <th style={{ textAlign: 'right' }}>Calls</th>
                      <th style={{ textAlign: 'right' }}>Share</th>
                      <th style={{ textAlign: 'right' }}>Success</th>
                      <th style={{ textAlign: 'right' }}>Avg latency</th>
                      <th style={{ textAlign: 'right' }}>Input (MB)</th>
                      <th style={{ textAlign: 'right' }}>Output (MB)</th>
                      <th style={{ textAlign: 'right' }}>Reasoning (MB)</th>
                      <th style={{ textAlign: 'right' }}>Cached (MB)</th>                    </tr>
                  </thead>
                  <tbody>
                    {modelRows.map(({ provider, model, b }) => {
                      const okPct = b.event_count > 0
                        ? `${((b.success_count / b.event_count) * 100).toFixed(1)}%`
                        : '—'
                      return (
                        <tr key={`${provider}/${model}`}>
                          <td>{provider}</td>
                          <td>{model}</td>
                          <td style={{ textAlign: 'right' }}>{b.event_count.toLocaleString()}</td>
                          <td style={{ textAlign: 'right' }}>
                            {totalCalls > 0
                              ? `${((b.event_count / totalCalls) * 100).toFixed(1)}%`
                              : '—'}
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            {okPct} ({b.success_count}/{b.error_count})
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            {b.avg_latency_ms == null ? '—' : `${Math.round(b.avg_latency_ms).toLocaleString()} ms`}
                          </td>
                          <td style={{ textAlign: 'right' }} title={`${b.input_tokens.toLocaleString()} tokens`}>{fmtTokens(b.input_tokens)}</td>
                          <td style={{ textAlign: 'right' }} title={`${b.output_tokens.toLocaleString()} tokens`}>{fmtTokens(b.output_tokens)}</td>
                          <td style={{ textAlign: 'right' }} title={`${b.reasoning_tokens.toLocaleString()} tokens`}>{fmtTokens(b.reasoning_tokens)}</td>
                          <td style={{ textAlign: 'right' }} title={`${b.cached_input_tokens.toLocaleString()} tokens`}>{fmtTokens(b.cached_input_tokens)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
            {unknownCount > 0 && (
              <p className="hint" style={{ marginTop: '0.5rem' }}>
                All {unknownCount} event(s) are missing pricing snapshots, so
                known cost is $0.00 — set one with{' '}
                <code>uv run aletheia audit pricing-set …</code> for future calls.
              </p>
            )}
            <p className="hint" style={{ marginTop: '0.5rem' }}>
              Token → MB is an approximation: 1 token ≈ 4 bytes (≈3–4 English
              characters). Hover a cell for the exact token count.
            </p>
          </div>
        )}
      </section>

      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Tool usage ({windowLabel})</h3>
        {toolRows.length === 0 ? (
          <p className="hint">No tool call events in this window.</p>
        ) : (
          <div>
            <Card flush>
              <TableWrap>
                <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left' }}>Tool</th>
                      <th style={{ textAlign: 'right' }}>Calls</th>
                      <th style={{ textAlign: 'right' }}>Share</th>
                      <th style={{ textAlign: 'right' }}>Recorded</th>
                      <th style={{ textAlign: 'right' }}>Denied</th>
                      <th style={{ textAlign: 'right' }}>Cost (known)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {toolRows.map(({ tool, b }) => (
                      <tr key={tool}>
                        <td>{tool}</td>
                        <td style={{ textAlign: 'right' }}>{b.event_count.toLocaleString()}</td>
                        <td style={{ textAlign: 'right' }}>
                          {totalToolCalls > 0
                            ? `${((b.event_count / totalToolCalls) * 100).toFixed(1)}%`
                            : '—'}
                        </td>
                        <td style={{ textAlign: 'right' }}>{b.recorded_count.toLocaleString()}</td>
                        <td style={{ textAlign: 'right' }}>
                          {b.denied_count > 0 ? b.denied_count.toLocaleString() : '0'}
                        </td>
                        <td style={{ textAlign: 'right' }}>{fmtUsd(b.known_cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
            <p className="hint" style={{ marginTop: '0.5rem' }}>
              Tool rows mirror every <code>agent_tool_calls</code> entry; no
              latency or tokens are recorded for them. Costs need{' '}
              <code>uv run aletheia audit tool-pricing-set …</code>.
            </p>
          </div>
        )}
      </section>

      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Cost by provider ({windowLabel})</h3>
        {costSeries.length === 0 ? (
          <p className="hint">No cost events in this window.</p>
        ) : (
          <div>
            {knownUsd === 0 && (
              <div style={{ marginBottom: '0.5rem' }}>
                <Badge tone="yellow">No priced cost in this window</Badge>{' '}
                <span className="hint">
                  All {unknownCount} event(s) are missing pricing snapshots, so
                  known cost is $0.00 — set one with{' '}
                  <code>uv run aletheia audit pricing-set …</code> for future
                  calls. Token usage below is unaffected.
                </span>
              </div>
            )}
            <Card>
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={costSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid {...gridProps} />
                  <XAxis
                    dataKey="date"
                    tick={axisStyle}
                    interval="preserveStartEnd"
                    minTickGap={32}
                  />
                  <YAxis tick={axisStyle} width={56} domain={[0, 'auto']} tickFormatter={v => `$${Number(v).toFixed(4)}`} />
                  <Tooltip
                    {...tooltipProps}
                    formatter={(v: any) => `$${Number(v).toFixed(6)}`}
                  />
                  {providerEntries.map(([name], i) => (
                    <Area
                      key={name}
                      type="monotone"
                      dataKey={name}
                      stackId="cost"
                      stroke={PROVIDER_COLORS[i % PROVIDER_COLORS.length]}
                      fill={PROVIDER_COLORS[i % PROVIDER_COLORS.length]}
                      fillOpacity={0.18}
                      strokeWidth={1.5}
                      dot={false}
                      activeDot={{ r: 3 }}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </Card>
          </div>
        )}
      </section>

      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Token volume by type ({windowLabel})</h3>
        {tokenSeries.length === 0 ? (
          <p className="hint">No token usage recorded in this window.</p>
        ) : (
          <Card>
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={tokenSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...gridProps} />
                <XAxis
                  dataKey="date"
                  tick={axisStyle}
                  interval="preserveStartEnd"
                  minTickGap={32}
                />
                <YAxis tick={axisStyle} width={56} tickFormatter={v => fmtTokens(Number(v))} />
                <Tooltip {...tooltipProps} formatter={(v: any, name: any) => [fmtTokens(Number(v)), String(name)]} />
                <Area
                  type="monotone"
                  dataKey="input"
                  stackId="tokens"
                  name="input"
                  stroke={C.accent}
                  fill={C.accent}
                  fillOpacity={0.2}
                  strokeWidth={1.5}
                  dot={false}
                />
                <Area
                  type="monotone"
                  dataKey="output"
                  stackId="tokens"
                  name="output"
                  stroke={C.green}
                  fill={C.green}
                  fillOpacity={0.2}
                  strokeWidth={1.5}
                  dot={false}
                />
                <Area
                  type="monotone"
                  dataKey="reasoning"
                  stackId="tokens"
                  name="reasoning"
                  stroke={C.purple}
                  fill={C.purple}
                  fillOpacity={0.2}
                  strokeWidth={1.5}
                  dot={false}
                />
                <Area
                  type="monotone"
                  dataKey="cached"
                  stackId="tokens"
                  name="cached"
                  stroke={C.yellow}
                  fill={C.yellow}
                  fillOpacity={0.2}
                  strokeWidth={1.5}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        )}
      </section>

      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Daily usage ({windowLabel})</h3>
        {dailyRows.length === 0 ? (
          <p className="hint">No token usage recorded in this window.</p>
        ) : (
          <Card flush>
            <TableWrap>
              <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>Date (UTC)</th>
                    <th style={{ textAlign: 'right' }}>Calls</th>
                    <th style={{ textAlign: 'right' }}>Input (MB)</th>
                    <th style={{ textAlign: 'right' }}>Output (MB)</th>
                    <th style={{ textAlign: 'right' }}>Reasoning (MB)</th>
                    <th style={{ textAlign: 'right' }}>Cached (MB)</th>
                    <th style={{ textAlign: 'right' }}>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {dailyRows.map(row => (
                    <tr key={row.date}>
                      <td>
                        {row.date}
                        {row.date === todayUtc && (
                          <span style={{ marginLeft: '0.4rem' }}>
                            <Badge tone="blue">today</Badge>
                          </span>
                        )}
                      </td>
                      <td style={{ textAlign: 'right' }}>{row.events.toLocaleString()}</td>
                      <td style={{ textAlign: 'right' }} title={`${row.input.toLocaleString()} tokens`}>{fmtTokens(row.input)}</td>
                      <td style={{ textAlign: 'right' }} title={`${row.output.toLocaleString()} tokens`}>{fmtTokens(row.output)}</td>
                      <td style={{ textAlign: 'right' }} title={`${row.reasoning.toLocaleString()} tokens`}>{fmtTokens(row.reasoning)}</td>
                      <td style={{ textAlign: 'right' }} title={`${row.cached.toLocaleString()} tokens`}>{fmtTokens(row.cached)}</td>
                      <td style={{ textAlign: 'right' }}>{fmtUsd(row.cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
        )}
      </section>

      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Budget policies</h3>
        {budgetList.length === 0 ? (
          <p className="hint">
            No declared budget policies. Declare one with{' '}
            <code>uv run aletheia audit budget-set …</code>
          </p>
        ) : (
          <Card flush>
            <TableWrap>
              <table className="mono" style={{ width: '100%', fontSize: '0.85em' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>Scope</th>
                    <th style={{ textAlign: 'left' }}>Subject</th>
                    <th style={{ textAlign: 'left' }}>Window</th>
                    <th style={{ textAlign: 'right' }}>USD cap</th>
                    <th style={{ textAlign: 'right' }}>Enabled</th>
                  </tr>
                </thead>
                <tbody>
                  {budgetList.map(b => (
                    <tr key={b.id}>
                      <td>{b.scope}</td>
                      <td>{b.subject ?? '—'}</td>
                      <td>{b.window}</td>
                      <td style={{ textAlign: 'right' }}>${b.usd_cap.toFixed(4)}</td>
                      <td style={{ textAlign: 'right' }}>{b.enabled === 1 ? 'yes' : 'no'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
        )}
        {wouldBlockCount > 0 && (
          <p className="hint" style={{ marginTop: '0.5rem' }}>
            {wouldBlockCount} call(s) in the last 7 days would have breached a budget.
            Shadow mode is on but no calls were blocked — see the would-block
            observations in <code>runtime_cost_events.metadata_json</code> for the
            exact policies triggered.
          </p>
        )}
      </section>

      <section style={{ marginBottom: 'var(--space-3)' }}>
        <h3 style={{ marginBottom: '0.5rem' }}>Models in cooldown</h3>
        {cooling.length === 0 ? (
          <p className="hint">No models currently cooling down.</p>
        ) : (
          <ul className="usage-cooldown">
            {cooling.map(m => (
              <li key={`${m.provider}/${m.model}`} className="mono">
                {m.provider}/{m.model} — {Math.round(m.cooldown_remaining_sec)}s remaining
                {m.last_error && ` — ${m.last_error.slice(0, 80)}`}
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="hint">
        Source: append-only <code>runtime_cost_events</code> ledger. Full history via{' '}
        <code>uv run aletheia audit costs --json</code>. Read-only surface; budget
        policies and pricing snapshots are CLI-managed.
      </p>
    </div>
  )
}

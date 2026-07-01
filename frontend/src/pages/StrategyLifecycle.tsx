import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { StrategyRecord } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, TableWrap } from '../components/ui'
import { humanize, fmtTimestamp, fmtDelta } from '../lib/format'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

const STATES = ['draft', 'research_note', 'strategy_hypothesis', 'paper_candidate', 'paper_running', 'paper_passed', 'live_candidate', 'live_limited', 'retired']

// State sort order for the lifecycle table: paper_running first (the live-paper
// strategies that need watching), then the rest in lifecycle order. Unknown
// states sort last.
function stateRank(s: string): number {
  if (s === 'paper_running') return -1
  const i = STATES.indexOf(s)
  return i === -1 ? STATES.length : i
}

const NEXT_STATE: Record<string, string> = {
  draft: 'research_note',
  research_note: 'strategy_hypothesis',
  strategy_hypothesis: 'paper_candidate',
  paper_candidate: 'paper_running',
  paper_running: 'paper_passed',
  paper_passed: 'live_candidate',
  live_candidate: 'live_limited',
}

const col = createColumnHelper<StrategyRecord & { _kpi?: { trade_count: number; win_rate: number; total_pnl: number } }>()

const columns = [
  col.accessor('name', { header: 'Name' }),
  col.accessor('state', {
    header: 'State',
    cell: info => <StatusBadge status={info.getValue()} />,
    sortingFn: (a, b) => stateRank(a.original.state) - stateRank(b.original.state),
  }),
  col.display({
    id: 'trades',
    header: 'Trades',
    cell: info => {
      const kpi = (info.row.original as any)._kpi
      return <span className="mono">{kpi ? kpi.trade_count : '—'}</span>
    },
  }),
  col.display({
    id: 'win_rate',
    header: 'Win%',
    cell: info => {
      const kpi = (info.row.original as any)._kpi
      if (!kpi || kpi.trade_count === 0) return <span className="text-muted">—</span>
      return <span className="mono">{kpi.win_rate.toFixed(1)}%</span>
    },
  }),
  col.display({
    id: 'pnl',
    header: 'PnL',
    cell: info => {
      const kpi = (info.row.original as any)._kpi
      if (!kpi || kpi.trade_count === 0) return <span className="text-muted">—</span>
      const tone = kpi.total_pnl > 0 ? 'pnl-positive' : kpi.total_pnl < 0 ? 'pnl-negative' : ''
      return <span className={`mono ${tone}`}>{fmtDelta(kpi.total_pnl)}</span>
    },
  }),
  col.display({
    id: 'action',
    header: 'Diagnosis',
    cell: info => {
      const ev = (info.row.original as any)._ev
      if (!ev) return <span className="text-muted">—</span>
      const tone = ACTION_TONE[ev.recommended_action] || 'muted'
      return <Badge tone={tone as any}>{humanize(ev.recommended_action)}</Badge>
    },
  }),
  col.accessor('created_at', {
    header: 'Created',
    cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span>,
  }),
]

function PipelineStepper({ currentState }: { currentState: string }) {
  const idx = STATES.indexOf(currentState)
  return (
    <div className="pipeline-stepper">
      {STATES.filter(s => s !== 'retired').map((s, i) => (
        <div key={s} className={`pipeline-step ${i < idx ? 'step-done' : i === idx ? 'step-active' : 'step-future'}`}>
          <div className="step-dot" />
          <span className="step-label">{humanize(s)}</span>
        </div>
      ))}
    </div>
  )
}

const ACTION_TONE: Record<string, string> = {
  promote_candidate: 'green',
  needs_more_paper: 'yellow',
  retire_candidate: 'red',
  needs_executable_ruleset: 'yellow',
  monitor: 'blue',
}

const DRIFT_TONE: Record<string, string> = {
  ok: 'green',
  degraded: 'yellow',
  severe_degradation: 'red',
  no_paper_evidence: 'muted',
  no_backtest: 'muted',
}

export default function StrategyLifecycle() {
  const qc = useQueryClient()
  const { data = [], isLoading: strategiesLoading } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })
  const { data: kpis = {} } = useQuery({ queryKey: ['strategy-kpis'], queryFn: api.getStrategyKpis, staleTime: 60000 })
  const { data: evidenceList = [] } = useQuery({ queryKey: ['strategy-evidence'], queryFn: api.getStrategyEvidence, staleTime: 120000 })
  const [selected, setSelected] = useState<StrategyRecord | null>(null)
  const [promoteError, setPromoteError] = useState<string | null>(null)

  const promote = useMutation({
    mutationFn: ({ id, target }: { id: string; target: string }) => api.promoteStrategy(id, target),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); setPromoteError(null) },
    onError: (e: any) => setPromoteError(e.message || 'Promotion failed'),
  })
  const retire = useMutation({
    mutationFn: (id: string) => api.retireStrategy(id),
    onSuccess: () => { setPromoteError(null); qc.invalidateQueries({ queryKey: ['strategies'] }) },
    onError: (e: any) => setPromoteError(e.message || 'Retirement failed'),
  })
  const setVenue = useMutation({
    mutationFn: ({ id, venue }: { id: string; venue: string | null }) => api.setStrategyVenue(id, venue),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
    onError: (e: any) => setPromoteError(e.message || 'Venue update failed'),
  })

  const kpiMap = kpis as Record<string, { trade_count: number; win_rate: number; total_pnl: number }>
  const evidenceMap = Object.fromEntries((evidenceList as any[]).map(e => [e.strategy_id, e]))
  const rows = (data as StrategyRecord[])
    .map(s => ({ ...s, _kpi: kpiMap[s.id], _ev: evidenceMap[s.id] }))
    .sort((a, b) => stateRank(a.state) - stateRank(b.state))

  function handlePromote() {
    if (!selected) return
    const next = NEXT_STATE[selected.state]
    if (!next) return
    const isLivePath = next === 'live_candidate' || next === 'live_limited'
    const msg = isLivePath
      ? `Promote "${selected.name}" to ${humanize(next)}? This strategy may route to live execution.`
      : `Promote "${selected.name}" to ${humanize(next)}?`
    if (!confirm(msg)) return
    setPromoteError(null)
    promote.mutate({ id: selected.id, target: next })
  }

  return (
    <div>
      <PageHeader
        title="Strategy Lifecycle"
        role="Evolve · the promotion pipeline (draft → research → hypothesis → paper → live-limited). Click a strategy to see its promotion gates and advance or retire it."
      />
      <Card>
        {strategiesLoading
          ? <div className="skeleton"><div className="skeleton-bar"/><div className="skeleton-bar"/><div className="skeleton-bar"/></div>
          : <DataTable
              data={rows}
              columns={columns}
              onRowClick={setSelected}
              emptyMessage="No strategies yet."
            />}
      </Card>

      <DetailDrawer open={!!selected} onClose={() => { setSelected(null); setPromoteError(null) }} title={selected?.name || 'Strategy'}>
        {selected && (
          <>
            <DrawerSection label="Pipeline">
              <PipelineStepper currentState={selected.state} />
            </DrawerSection>
            <DrawerSection label="Details">
              <DrawerField label="Name" value={selected.name} />
              <DrawerField label="State" value={<StatusBadge status={selected.state} />} />
              <DrawerField label="Description" value={selected.description} />
              <DrawerField label="Execution Venue" value={
                <select
                  className="input"
                  value={selected.execution_venue || ''}
                  onChange={e => {
                    const v = e.target.value || null
                    setVenue.mutate({ id: selected.id, venue: v })
                    setSelected({ ...selected, execution_venue: v })
                  }}
                >
                  <option value="">Global default</option>
                  <option value="bitget_demo">bitget_demo</option>
                  <option value="okx_demo">okx_demo</option>
                  <option value="binance_testnet">binance_testnet</option>
                  <option value="paper_internal">paper_internal</option>
                </select>
              } />
              <DrawerField label="Created" value={fmtTimestamp(selected.created_at)} mono />
              <DrawerField label="Updated" value={fmtTimestamp(selected.updated_at)} mono />
            </DrawerSection>
            <PromotionGates strategyId={selected.id} />
            <StrategyDiagnosis evidence={evidenceMap[selected.id]} />
            <BacktestPaperDriftPanel strategyId={selected.id} />
            <BacktestHistory strategyId={selected.id} />
            {promoteError && (
              <DrawerSection label="Action Error">
                <div className="promote-error">
                  {parsePromotionError(promoteError)}
                </div>
              </DrawerSection>
            )}
            <div className="flex gap-2 mt-3">
              {NEXT_STATE[selected.state] && (
                <Button onClick={handlePromote} disabled={promote.isPending}>
                  Promote → {humanize(NEXT_STATE[selected.state])}
                </Button>
              )}
              {selected.state !== 'retired' && selected.state !== 'deprecated' && (
                <Button variant="danger" onClick={() => {
                  if (!confirm(`Retire "${selected.name}"? This cannot be undone.`)) return
                  retire.mutate(selected.id)
                }} disabled={retire.isPending}>
                  Retire
                </Button>
              )}
            </div>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}

function PromotionGates({ strategyId }: { strategyId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['strategy-gates', strategyId],
    queryFn: () => api.getStrategyGates(strategyId),
    enabled: !!strategyId,
  })
  if (isLoading) return <DrawerSection label="Promotion Gates"><p className="text-muted text-sm">Loading gates...</p></DrawerSection>
  if (!data) return null
  const metrics = data.metrics as Record<string, any> | undefined
  const failures = (data.failures || []) as string[]
  const passed = data.passed as boolean
  if (!metrics) return null
  return (
    <DrawerSection label="Promotion Gates">
      <p className="mb-2">
        <Badge tone={passed ? 'green' : 'red'}>{passed ? 'All gates pass' : `${failures.length} gate(s) failed`}</Badge>
      </p>
      <TableWrap>
        <table className="drawer-table">
          <thead>
            <tr><th>Metric</th><th>Value</th></tr>
          </thead>
          <tbody>
            {Object.entries(metrics).map(([k, v]) => (
              <tr key={k}>
                <td>{humanize(k)}</td>
                <td className="mono">{v != null ? String(v) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      {failures.length > 0 && (
        <ul className="drawer-list drawer-list-error mt-2">
          {failures.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
    </DrawerSection>
  )
}

function parsePromotionError(raw: string): React.ReactNode {
  const match = raw.match(/\{.*\}/)
  if (match) {
    try {
      const obj = JSON.parse(match[0])
      if (obj.detail) {
        const failures = typeof obj.detail === 'string' ? obj.detail.split(', ') : [obj.detail]
        return (
          <ul className="drawer-list drawer-list-error">
            {failures.map((f: string, i: number) => <li key={i}>{f}</li>)}
          </ul>
        )
      }
    } catch { /* fall through */ }
  }
  return <p className="text-sm pnl-negative">{raw}</p>
}


function BacktestHistory({ strategyId }: { strategyId: string }) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const { data: runs, isLoading } = useQuery({
    queryKey: ['backtest-runs', strategyId],
    queryFn: () => api.getBacktestRuns(strategyId),
    enabled: !!strategyId,
  })
  const { data: detail } = useQuery({
    queryKey: ['backtest-detail', expandedId],
    queryFn: () => api.getBacktestRunDetail(expandedId!),
    enabled: !!expandedId,
  })

  if (isLoading) return <DrawerSection label="Backtest History"><p className="text-muted text-sm">Loading...</p></DrawerSection>
  if (!runs || runs.length === 0) return null

  return (
    <DrawerSection label="Backtest History">
      <TableWrap>
        <table className="table-sm">
          <thead><tr>
            <th>Date</th><th>Trades</th><th>Return</th><th>Win%</th><th>MDD</th>
          </tr></thead>
          <tbody>
            {runs.slice(0, 5).map((r: any) => (
              <tr key={r.id} onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}
                  className={`clickable ${expandedId === r.id ? 'row-selected' : ''}`}>
                <td className="mono text-xs">{r.created_at?.slice(0, 10)}</td>
                <td>{r.trade_count}</td>
                <td className={r.total_return_pct >= 0 ? 'pnl-positive' : 'pnl-negative'}>
                  {r.total_return_pct?.toFixed(1)}%
                </td>
                <td>{(r.win_rate * 100).toFixed(0)}%</td>
                <td className="pnl-negative">{r.max_drawdown_pct?.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      {expandedId && detail?.equity_curve?.length > 0 && (
        <BacktestEquityChart curve={detail.equity_curve} />
      )}
    </DrawerSection>
  )
}


function BacktestEquityChart({ curve }: { curve: number[] }) {
  const data = curve.map((v: number, i: number) => ({ bar: i, equity: v }))
  const minY = Math.min(...curve) * 0.98
  const maxY = Math.max(...curve) * 1.02

  return (
    <div className="mt-2 chart-h-sm">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--c-border)" />
          <XAxis dataKey="bar" tick={false} />
          <YAxis domain={[minY, maxY]} tick={{ fontSize: 10 }} width={50} />
          <Tooltip formatter={(v: any) => `$${Number(v).toFixed(2)}`} />
          <Area type="monotone" dataKey="equity" stroke="#2ee6a8" fill="#2ee6a8" fillOpacity={0.15} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Strategy Diagnosis (strategy-evidence) ────────────────────────────────────
function StrategyDiagnosis({ evidence }: { evidence: any }) {
  if (!evidence) return null
  const { recommended_action, recommended_action_reasons, evidence_status, paper_summary, factor_summary } = evidence
  const tone = ACTION_TONE[recommended_action] || 'muted'
  return (
    <DrawerSection label="Diagnosis">
      <div className="flex gap-2 mb-2 flex-wrap">
        <Badge tone={tone as any}>{humanize(recommended_action)}</Badge>
        <Badge tone="muted">{humanize(evidence_status)}</Badge>
      </div>
      {recommended_action_reasons?.length > 0 && (
        <ul className="drawer-list mb-2">
          {recommended_action_reasons.map((r: string, i: number) => (
            <li key={i} className="text-sm text-muted">{humanize(r)}</li>
          ))}
        </ul>
      )}
      <div className="strategy-diag-grid">
        {paper_summary && paper_summary.trade_count > 0 && (
          <div className="strategy-diag-block">
            <div className="strategy-diag-label">Paper trades</div>
            <div className="mono">{paper_summary.trade_count} · WR {(paper_summary.win_rate * 100).toFixed(0)}% · PF {paper_summary.profit_factor?.toFixed(2) ?? '—'}</div>
            <div className={`mono text-sm ${paper_summary.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
              {fmtDelta(paper_summary.total_pnl)} USDT
            </div>
          </div>
        )}
        {factor_summary && (
          <div className="strategy-diag-block">
            <div className="strategy-diag-label">Factors</div>
            <div className="mono">{factor_summary.total_factors} total</div>
            {factor_summary.blocked_count > 0 && <div className="mono text-sm pnl-negative">{factor_summary.blocked_count} blocked</div>}
            {factor_summary.preferred_count > 0 && <div className="mono text-sm pnl-positive">{factor_summary.preferred_count} preferred</div>}
          </div>
        )}
      </div>
    </DrawerSection>
  )
}

// ── Backtest vs Paper Drift (backtest-paper-drift) ────────────────────────────
function BacktestPaperDriftPanel({ strategyId }: { strategyId: string }) {
  const { data: driftList, isLoading } = useQuery({
    queryKey: ['backtest-paper-drift'],
    queryFn: api.getBacktestPaperDrift,
    staleTime: 120000,
  })

  if (isLoading) return null
  const row = (driftList as any[] | undefined)?.find(r => r.strategy_id === strategyId)
  if (!row || row.paper_trade_count === 0) return null

  const driftTone = DRIFT_TONE[row.drift_status] || 'muted'
  const ratio = row.paper_to_backtest_ratio
  const ratioText = ratio != null ? `${(ratio * 100).toFixed(0)}% of backtest` : '—'

  return (
    <DrawerSection label="Backtest vs Paper Drift">
      <div className="flex gap-2 mb-2">
        <Badge tone={driftTone as any}>{humanize(row.drift_status)}</Badge>
        <span className="text-muted text-sm">{ratioText}</span>
      </div>
      <TableWrap>
        <table className="drawer-table">
          <thead><tr><th></th><th>Backtest</th><th>Paper</th></tr></thead>
          <tbody>
            <tr>
              <td>Trades</td>
              <td className="mono">{row.backtest_trade_count}</td>
              <td className="mono">{row.paper_trade_count}</td>
            </tr>
            <tr>
              <td>Win%</td>
              <td className="mono">{(row.backtest_win_rate * 100).toFixed(0)}%</td>
              <td className="mono">{(row.paper_win_rate * 100).toFixed(0)}%</td>
            </tr>
            <tr>
              <td>PF</td>
              <td className="mono">{row.backtest_profit_factor?.toFixed(2) ?? '—'}</td>
              <td className="mono">{row.paper_profit_factor?.toFixed(2) ?? '—'}</td>
            </tr>
            <tr>
              <td>Expectancy</td>
              <td className={`mono ${row.backtest_expectancy >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>{row.backtest_expectancy?.toFixed(2)}</td>
              <td className={`mono ${row.paper_expectancy >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>{row.paper_expectancy?.toFixed(2)}</td>
            </tr>
          </tbody>
        </table>
      </TableWrap>
      {row.drift_reasons?.length > 0 && (
        <ul className="drawer-list mt-1">
          {row.drift_reasons.map((r: string, i: number) => (
            <li key={i} className="text-sm text-muted">{humanize(r)}</li>
          ))}
        </ul>
      )}
    </DrawerSection>
  )
}

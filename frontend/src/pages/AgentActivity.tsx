import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { AgentRun, Opportunity } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import Tabs from '../components/Tabs'
import PageHeader from '../components/PageHeader'
import ThesisText from '../components/ThesisText'
import { Badge, Button, Card, useToast } from '../components/ui'
import { humanize, fmtTimestamp, fmtSymbol } from '../lib/format'

/**
 * Agent Activity (WEBUI_PAGE_MAP.md — Agent Runs + Opportunities MERGED).
 *
 * Both surfaces are §23.4/§23.7 agent transparency: an Opportunity IS an agent output
 * (a market-observer finding with a thesis), so it belongs with run transparency, not as
 * an orphan list. Two tabs over one workbench:
 *   RUNS          — every agent invocation, with tool-call drill-down (the reasoning log).
 *   OPPORTUNITIES — the findings those runs produced, with thesis + strategy link.
 *
 * The run drawer's "Reasoning Steps" block renders BOTH the tool-calls ledger AND the
 * per-step reasoning trace (#72 fix, 2026-07-07 — GET /api/agent-runs/{id}/steps is live at
 * server/routes/data.py:236; the earlier "backend gap" comment was stale).
 */

const runCol = createColumnHelper<AgentRun>()
const oppCol = createColumnHelper<Opportunity>()

const AGENTS = [
  'market_observer', 'research_agent', 'strategy_designer',
  'risk_critic', 'portfolio_agent', 'post_trade_reviewer', 'dreaming_agent',
  'prediction_observer', 'stock_observer',
]

function parseJson(raw: string | null): any[] {
  if (!raw) return []
  try { return JSON.parse(raw) } catch { return [] }
}

const runColumns = [
  runCol.accessor('agent_name', { header: 'Agent', cell: info => humanize(info.getValue()) }),
  runCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  runCol.accessor('trigger_type', { header: 'Trigger', cell: info => humanize(info.getValue()) }),
  runCol.accessor('model_provider', { header: 'Provider', cell: info => info.getValue() || '—' }),
  runCol.accessor('outputs_created', {
    header: 'Outputs',
    cell: info => { try { return <span className="mono">{JSON.parse(info.getValue() || '[]').length}</span> } catch { return <span className="mono">0</span> } },
  }),
  runCol.accessor('started_at', { header: 'Started', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

const oppColumns = [
  oppCol.accessor('symbol', { header: 'Symbol', cell: info => <span className="mono">{fmtSymbol(info.getValue(), info.row.original.market)}</span> }),
  oppCol.accessor('market', { header: 'Market', cell: info => humanize(info.getValue()) }),
  oppCol.accessor('direction_bias', { header: 'Direction', cell: info => humanize(info.getValue()) }),
  oppCol.accessor('confidence_score', { header: 'Confidence', cell: info => <span className="mono">{info.getValue() != null ? `${info.getValue()}%` : '—'}</span> }),
  oppCol.accessor('source_agent', { header: 'Source', cell: info => info.getValue() ? humanize(info.getValue()) : '—' }),
  oppCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  oppCol.accessor('created_at', { header: 'Created', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

export default function AgentActivity() {
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const { data: opps = [] } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities })

  const runRows = runs as AgentRun[]
  const oppRows = opps as Opportunity[]
  const openOpps = oppRows.filter(o => o.status === 'open' || o.status === 'pending').length

  return (
    <div>
      <PageHeader
        title="Agent Activity"
        role="Decide · agent transparency. Every agent run with its tool-call trace, and the opportunities those runs produced (each with its thesis). The reasoning behind what the system proposes."
      />
      <Tabs
        tabs={[
          { id: 'runs', label: 'Runs', badge: runRows.length || undefined, render: () => <RunsTab runs={runRows} /> },
          { id: 'opps', label: 'Opportunities', badge: openOpps || undefined, render: () => <OpportunitiesTab opps={oppRows} /> },
        ]}
      />
    </div>
  )
}

function RunsTab({ runs }: { runs: AgentRun[] }) {
  const qc = useQueryClient()
  const { toast } = useToast()
  const [selected, setSelected] = useState<AgentRun | null>(null)
  const run = useMutation({
    mutationFn: (name: string) => api.startAgentRun(name).then(r => ({ name, r })),
    onSuccess: ({ name }) => { toast('success', `${name} started`); qc.invalidateQueries({ queryKey: ['agent-runs'] }) },
    onError: (e: any, name: string) => toast('error', `${name}: ${e?.message || 'failed to start'}`),
  })

  return (
    <>
      <Card>
        <h3>Run an agent now</h3>
        <div className="flex flex-wrap gap-1">
          {AGENTS.map(a => (
            <Button key={a} variant="secondary" onClick={() => run.mutate(a)} disabled={run.isPending}>{a}</Button>
          ))}
        </div>
      </Card>
      <Card>
        <DataTable data={runs} columns={runColumns} onRowClick={setSelected} emptyMessage="No agent runs yet." />
      </Card>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Agent Run Detail">
        {selected && (
          <>
            <DrawerSection label="Run Info">
              <DrawerField label="Agent" value={humanize(selected.agent_name)} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
              <DrawerField label="Trigger" value={humanize(selected.trigger_type)} />
              <DrawerField label="Provider" value={selected.model_provider} />
              <DrawerField label="Model" value={selected.model_name} mono />
              <DrawerField label="Started" value={fmtTimestamp(selected.started_at)} mono />
              <DrawerField label="Ended" value={fmtTimestamp(selected.ended_at)} mono />
              <DrawerField label="Cost" value={selected.cost_estimate != null ? `$${selected.cost_estimate.toFixed(4)}` : null} mono />
            </DrawerSection>
            <ReasoningStepsSection runId={selected.id} fallback={selected.tools_called} />
            <DrawerSection label="Outputs Created">
              {(() => {
                const outputs = parseJson(selected.outputs_created)
                return outputs.length > 0
                  ? <ul className="drawer-list">{outputs.map((o, i) => <li key={i}>{typeof o === 'string' ? o : JSON.stringify(o)}</li>)}</ul>
                  : <span className="text-muted text-sm">No outputs recorded</span>
              })()}
            </DrawerSection>
            <DrawerSection label="Errors">
              {(() => {
                const errors = parseJson(selected.errors)
                return errors.length > 0
                  ? <ul className="drawer-list drawer-list-error">{errors.map((e, i) => <li key={i}>{typeof e === 'string' ? e : JSON.stringify(e)}</li>)}</ul>
                  : <span className="text-muted text-sm">No errors</span>
              })()}
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </>
  )
}

function OpportunitiesTab({ opps }: { opps: Opportunity[] }) {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<Opportunity | null>(null)
  const [filter, setFilter] = useState<'all' | 'crypto' | 'equity' | 'prediction'>('all')

  const filtered = filter === 'all' ? opps : opps.filter(o => (o.asset_class || 'crypto') === filter)
  const counts = {
    crypto: opps.filter(o => (o.asset_class || 'crypto') === 'crypto').length,
    equity: opps.filter(o => o.asset_class === 'equity').length,
    prediction: opps.filter(o => o.asset_class === 'prediction').length,
  }

  return (
    <>
      <Card>
        <div className="flex flex-wrap gap-1 mb-2">
          <Button variant="secondary" className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>
            All ({opps.length})
          </Button>
          <Button variant="secondary" className={filter === 'crypto' ? 'active' : ''} onClick={() => setFilter('crypto')}>
            Crypto ({counts.crypto})
          </Button>
          {counts.equity > 0 && (
            <Button variant="secondary" className={filter === 'equity' ? 'active' : ''} onClick={() => setFilter('equity')}>
              Equity ({counts.equity})
            </Button>
          )}
          {counts.prediction > 0 && (
            <Button variant="secondary" className={filter === 'prediction' ? 'active' : ''} onClick={() => setFilter('prediction')}>
              Prediction ({counts.prediction})
            </Button>
          )}
        </div>
        <DataTable data={filtered} columns={oppColumns} onRowClick={setSelected} emptyMessage="No opportunities yet." />
      </Card>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Opportunity Detail">
        {selected && (
          <>
            <DrawerSection label="Market Signal">
              <DrawerField label="Symbol" value={fmtSymbol(selected.symbol, selected.market)} mono />
              <DrawerField label="Market" value={humanize(selected.market)} />
              <DrawerField label="Asset Class" value={
                <Badge tone={selected.asset_class === 'prediction' ? 'blue' : selected.asset_class === 'equity' ? 'yellow' : 'green'}>
                  {humanize(selected.asset_class || 'crypto')}
                </Badge>
              } />
              <DrawerField label="Direction Bias" value={humanize(selected.direction_bias)} />
              <DrawerField label="Confidence" value={selected.confidence_score != null ? `${selected.confidence_score}%` : selected.confidence ?? null} mono />
              <DrawerField label="Source Agent" value={selected.source_agent ? humanize(selected.source_agent) : null} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
            </DrawerSection>
            {(selected.asset_class === 'prediction' || selected.asset_class === 'equity') && (
              <DrawerSection label="Observation Only">
                <p className="text-muted text-sm">
                  This {humanize(selected.asset_class)} opportunity is observation-only and will not be routed to Paper Trader.
                </p>
              </DrawerSection>
            )}
            <DrawerSection label="Thesis">
              {selected.thesis
                ? <ThesisText text={selected.thesis} />
                : <p className="opp-thesis">No thesis provided.</p>}
            </DrawerSection>
            <DrawerSection label="Links">
              <DrawerField label="Linked Strategy" value={selected.linked_strategy_id ? (
                <span className="mono">{selected.linked_strategy_id}</span>
              ) : null} />
              <DrawerField label="Created" value={fmtTimestamp(selected.created_at)} mono />
            </DrawerSection>
            <div className="mt-2 flex gap-1">
              {selected.asset_class === 'prediction' ? (
                <Button variant="secondary" onClick={() => navigate('/prediction-markets')}>View in Prediction Lens</Button>
              ) : (
                <Button variant="secondary" onClick={() => navigate('/charts')}>View on Chart</Button>
              )}
            </div>
          </>
        )}
      </DetailDrawer>
    </>
  )
}

/**
 * Reasoning drill-down. Renders the tool-call ledger (/agent-runs/{id}/tool-calls)
 * PLUS the per-step reasoning trace (/agent-runs/{id}/steps) — the latter was
 * previously labelled "not yet available" but the endpoint has been live at
 * server/routes/data.py:236 for a while (#72 fix).
 */
function ReasoningStepsSection({ runId, fallback }: { runId: string; fallback: string | null }) {
  const { data: toolCalls = [] } = useQuery({
    queryKey: ['tool-calls', runId],
    queryFn: () => api.getAgentRunToolCalls(runId),
    enabled: !!runId,
  })
  const { data: steps = [] } = useQuery({
    queryKey: ['agent-run-steps', runId],
    queryFn: () => api.getAgentRunSteps(runId),
    enabled: !!runId,
  })

  const tools = toolCalls.length > 0
    ? toolCalls as any[]
    : (fallback ? (() => { try { return JSON.parse(fallback) } catch { return [] } })() : [])

  return (
    <DrawerSection label="Reasoning Steps">
      {(toolCalls as any[]).length > 0 ? (
        <table className="drawer-table">
          <thead>
            <tr><th>Tool</th><th>Class</th><th>Permitted</th><th>Time</th></tr>
          </thead>
          <tbody>
            {(toolCalls as any[]).map((tc: any, i: number) => (
              <tr key={tc.id || i}>
                <td className="mono">{tc.tool_name}</td>
                <td>{humanize(tc.tool_class)}</td>
                <td><span className={`badge ${tc.permitted ? 'badge-green' : 'badge-red'}`}>{tc.permitted ? 'Yes' : 'No'}</span></td>
                <td className="mono">{tc.created_at?.slice(11, 19) || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : tools.length > 0 ? (
        <ul className="drawer-list">{tools.map((t: any, i: number) => <li key={i}>{typeof t === 'string' ? t : JSON.stringify(t)}</li>)}</ul>
      ) : (
        <span className="text-muted text-sm">No tool calls recorded</span>
      )}
      {(steps as any[]).length > 0 && (
        <table className="drawer-table mt-2">
          <thead>
            <tr><th>#</th><th>Action</th><th>Input</th><th>Output</th></tr>
          </thead>
          <tbody>
            {(steps as any[]).map((s: any, i: number) => (
              <tr key={s.id || i}>
                <td className="mono">{s.step_number ?? i + 1}</td>
                <td className="mono">{s.action || '—'}</td>
                <td className="mono text-muted">{s.input_data ? String(s.input_data).slice(0, 80) : '—'}</td>
                <td className="mono text-muted">{s.output_data ? String(s.output_data).slice(0, 80) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </DrawerSection>
  )
}

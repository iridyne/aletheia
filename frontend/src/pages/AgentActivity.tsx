import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { ApiError, api } from '../api/client'
import type { AgentRun, Opportunity } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DataStateMessage, type DataState } from '../components/DataState'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import Tabs from '../components/Tabs'
import PageHeader from '../components/PageHeader'
import ThesisText from '../components/ThesisText'
import { Badge, Button, Card, SymbolLink, useToast } from '../components/ui'
import {
  fmtOpportunityScore,
  fmtScore100,
  fmtSymbol,
  fmtTimestamp,
  humanize,
  opportunityScoreLabel,
} from '../lib/format'

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
const AGENT_ACTIVITY_TABS = ['runs', 'opps'] as const
type AgentActivityTab = typeof AGENT_ACTIVITY_TABS[number]
function isAgentActivityTab(tab: string | null): tab is AgentActivityTab {
  return !!tab && AGENT_ACTIVITY_TABS.includes(tab as AgentActivityTab)
}

function parseJson(raw: string | null): any[] {
  if (!raw) return []
  try { return JSON.parse(raw) } catch { return [] }
}

function isMissingAgentRun(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'agent_run_not_found'
}

type ArrayReadState = Extract<DataState, 'loading' | 'available' | 'empty' | 'stale' | 'unavailable'>

function resolveArrayReadState(
  data: unknown[] | undefined,
  isPending: boolean,
  isError: boolean,
  isFetching: boolean,
): ArrayReadState {
  if (data && (isError || isFetching)) return 'stale'
  if (isError) return 'unavailable'
  if (isPending || !data) return 'loading'
  return data.length > 0 ? 'available' : 'empty'
}

const runColumns = [
  runCol.accessor('agent_name', { header: 'Agent', cell: info => humanize(info.getValue()) }),
  runCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  runCol.display({
    id: 'trigger_provider',
    header: 'Trigger · Provider',
    cell: ({ row }) => {
      const r = row.original
      return (
        <span className="mono">
          {humanize(r.trigger_type)}
          {r.model_provider ? <span className="text-muted"> · {r.model_provider}</span> : null}
        </span>
      )
    },
  }),
  runCol.accessor('outputs_created', {
    header: 'Outputs',
    cell: info => {
      try {
        const count = JSON.parse(info.getValue() || '[]').length
        return count === 0
          ? <span className="text-muted">none</span>
          : <span className="mono">{count} artifact{count === 1 ? '' : 's'}</span>
      } catch {
        return <span className="text-muted">none</span>
      }
    },
  }),
  runCol.accessor('started_at', { header: 'Started', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

const oppColumns = [
  oppCol.accessor('symbol', {
    header: 'Instrument',
    cell: info => (
      <span>
        <SymbolLink symbol={info.getValue()} marketType={info.row.original.market} bold />
        <br />
        <span className="text-muted text-sm mono">{humanize(info.row.original.market || 'spot')}</span>
      </span>
    ),
  }),
  oppCol.accessor('direction_bias', { header: 'Direction', cell: info => humanize(info.getValue()) }),
  oppCol.accessor('confidence_score', {
    header: 'Score',
    cell: info => (
      <span className="mono">
        {fmtOpportunityScore(
          info.row.original.source_agent,
          info.getValue(),
          info.row.original.confidence,
        )}
      </span>
    ),
  }),
  oppCol.accessor('source_agent', { header: 'Source', cell: info => info.getValue() ? humanize(info.getValue()) : '—' }),
  oppCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  oppCol.accessor('created_at', { header: 'Created', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

export default function AgentActivity() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const activeTab: AgentActivityTab = isAgentActivityTab(requestedTab) ? requestedTab : 'runs'
  const { data: runs, isPending: runsPending, isError: runsError } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const { data: opps, isPending: oppsPending, isError: oppsError } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities })

  const runRows = (runs ?? []) as AgentRun[]
  const oppRows = (opps ?? []) as Opportunity[]
  const openOpps = oppRows.filter(o => o.status === 'open' || o.status === 'pending').length

  const runsState = runsPending && runRows.length === 0
    ? 'loading'
    : runsError && runRows.length === 0
      ? 'unavailable'
      : runsError && runRows.length > 0
        ? 'stale'
        : undefined
  const oppsState = oppsPending && oppRows.length === 0
    ? 'loading'
    : oppsError && oppRows.length === 0
      ? 'unavailable'
      : oppsError && oppRows.length > 0
        ? 'stale'
        : undefined

  useEffect(() => {
    if (requestedTab === activeTab) return
    const next = new URLSearchParams(searchParams)
    next.set('tab', activeTab)
    setSearchParams(next, { replace: true })
  }, [activeTab, requestedTab, searchParams, setSearchParams])

  const setActiveTab = (tab: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('tab', isAgentActivityTab(tab) ? tab : 'runs')
    setSearchParams(next)
  }

  return (
    <div>
      <PageHeader
        title="Agent Activity"
        role="Decide · agent transparency. Every agent run with its tool-call trace, and the opportunities those runs produced (each with its thesis). The reasoning behind what the system proposes."
      />
      <Tabs
        value={activeTab}
        onChange={setActiveTab}
        tabs={[
          { id: 'runs', label: 'Runs', badge: runRows.length || undefined, render: () => <RunsTab runs={runRows} state={runsState} /> },
          { id: 'opps', label: 'Opportunities', badge: openOpps > 0 ? openOpps : undefined, render: () => <OpportunitiesTab opps={oppRows} state={oppsState} /> },
        ]}
      />
    </div>
  )
}

function RunsTab({ runs, state }: { runs: AgentRun[], state?: DataState }) {
  const qc = useQueryClient()
  const { toast } = useToast()
  const [selected, setSelected] = useState<AgentRun | null>(null)
  const catalogQuery = useQuery({ queryKey: ['agent-catalog'], queryFn: api.getAgentCatalog })
  const agents = catalogQuery.data?.agents ?? []
  const catalogState = resolveArrayReadState(
    catalogQuery.data?.agents,
    catalogQuery.isPending,
    catalogQuery.isError,
    catalogQuery.isFetching,
  )
  const run = useMutation({
    mutationFn: (name: string) => api.startAgentRun(name).then(r => ({ name, r })),
    onSuccess: ({ name }) => { toast('success', `${name} started`); qc.invalidateQueries({ queryKey: ['agent-runs'] }) },
    onError: (e: any, name: string) => toast('error', `${name}: ${e?.message || 'failed to start'}`),
  })

  return (
    <>
      <Card>
        <h3>Run an agent now</h3>
        {catalogState !== 'available' && (
          <DataStateMessage
            state={catalogState}
            message={catalogState === 'loading'
              ? 'Agent catalog is loading…'
              : catalogState === 'unavailable'
                ? 'Agent catalog unavailable. The read failed; no manual-run allowlist is implied.'
                : catalogState === 'stale'
                  ? 'Agent catalog is stale. Retained agents are shown but manual runs are disabled.'
                  : 'No agents are available for manual runs.'}
          />
        )}
        <div className="flex flex-wrap gap-1">
          {agents.map(a => (
            <span key={a.name} className="flex items-center gap-1">
              <Button
                variant="secondary"
                onClick={() => { if (catalogState === 'available') run.mutate(a.name) }}
                disabled={catalogState !== 'available' || run.isPending || a.runnable === false}
                aria-label={a.blocked_reason ? `${a.name}: ${a.blocked_reason}` : a.name}
              >
                {a.name}
              </Button>
              {a.runnable === false && <span className="text-muted text-xs">{a.blocked_reason || 'unavailable'}</span>}
            </span>
          ))}
        </div>
      </Card>
      <Card>
        <DataTable data={runs} columns={runColumns} onRowClick={setSelected} state={state} emptyMessage="No agent runs yet." />
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

function OpportunitiesTab({ opps, state }: { opps: Opportunity[], state?: DataState }) {
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
        <DataTable data={filtered} columns={oppColumns} onRowClick={setSelected} state={state} emptyMessage="No opportunities yet." />
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
              <DrawerField
                label={opportunityScoreLabel(selected.source_agent)}
                value={selected.confidence_score != null
                  ? fmtScore100(selected.confidence_score)
                  : selected.confidence ?? null}
                mono
              />
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
                <Button variant="secondary" onClick={() => navigate(`/charts?symbol=${encodeURIComponent(selected.symbol || '')}&market_type=${encodeURIComponent(selected.market || 'spot')}`)}>View on Chart</Button>
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
  const toolCallsQuery = useQuery({
    queryKey: ['tool-calls', runId],
    queryFn: () => api.getAgentRunToolCalls(runId),
    enabled: !!runId,
  })
  const stepsQuery = useQuery({
    queryKey: ['agent-run-steps', runId],
    queryFn: () => api.getAgentRunSteps(runId),
    enabled: !!runId,
  })

  const toolCalls = (toolCallsQuery.data ?? []) as any[]
  const steps = (stepsQuery.data ?? []) as any[]
  const toolCallsState = resolveArrayReadState(
    toolCallsQuery.data,
    toolCallsQuery.isPending,
    toolCallsQuery.isError,
    toolCallsQuery.isFetching,
  )
  const stepsState = resolveArrayReadState(
    stepsQuery.data,
    stepsQuery.isPending,
    stepsQuery.isError,
    stepsQuery.isFetching,
  )
  const legacyTools = parseJson(fallback)
  const runNotFound = isMissingAgentRun(toolCallsQuery.error) || isMissingAgentRun(stepsQuery.error)

  return (
    <DrawerSection label="Reasoning Steps">
      {runNotFound ? (
        <span className="text-muted text-sm">Run identity not found; trace unavailable.</span>
      ) : (
        <>
          <h4>Tool calls</h4>
          {toolCallsState !== 'available' && toolCallsState !== 'empty' && (
            <DataStateMessage
              state={toolCallsState}
              message={toolCallsState === 'loading'
                ? 'Tool-call ledger is loading…'
                : toolCallsState === 'stale'
                  ? 'Tool-call ledger is stale. Retained rows may not reflect the latest read.'
                  : 'Tool-call ledger unavailable. The read failed; no empty ledger is implied.'}
            />
          )}
          {toolCalls.length > 0 ? (
            <table className="drawer-table">
              <thead>
                <tr><th>Tool</th><th>Class</th><th>Permitted</th><th>Time</th></tr>
              </thead>
              <tbody>
                {toolCalls.map((tc: any, i: number) => (
                  <tr key={tc.id || i}>
                    <td className="mono">{tc.tool_name}</td>
                    <td>{humanize(tc.tool_class)}</td>
                    <td><span className={`badge ${tc.permitted ? 'badge-green' : 'badge-red'}`}>{tc.permitted ? 'Yes' : 'No'}</span></td>
                    <td className="mono">{tc.created_at?.slice(11, 19) || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : toolCallsState === 'empty' && legacyTools.length > 0 ? (
            <>
              <p className="text-muted text-sm">Legacy run snapshot (canonical tool-call ledger is empty)</p>
              <ul className="drawer-list">{legacyTools.map((tool: any, i: number) => <li key={i}>{typeof tool === 'string' ? tool : JSON.stringify(tool)}</li>)}</ul>
            </>
          ) : toolCallsState === 'empty' ? (
            <span className="text-muted text-sm">No tool calls recorded</span>
          ) : null}

          <h4 className="mt-2">Reasoning trace</h4>
          {stepsState !== 'available' && stepsState !== 'empty' && (
            <DataStateMessage
              state={stepsState}
              message={stepsState === 'loading'
                ? 'Reasoning-step trace is loading…'
                : stepsState === 'stale'
                  ? 'Reasoning-step trace is stale. Retained rows may not reflect the latest read.'
                  : 'Reasoning-step trace unavailable. The read failed; no empty trace is implied.'}
            />
          )}
          {steps.length > 0 ? (
            <table className="drawer-table mt-2">
              <thead>
                <tr><th>Step / Action</th><th>Input</th><th>Output</th></tr>
              </thead>
              <tbody>
                {steps.map((s: any, i: number) => (
                  <tr key={s.id || i}>
                    <td className="mono">{s.step_number ?? i + 1}. {s.action || '—'}</td>
                    <td className="mono text-muted">{s.input_data ? String(s.input_data).slice(0, 80) : '—'}</td>
                    <td className="mono text-muted">{s.output_data ? String(s.output_data).slice(0, 80) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : stepsState === 'empty' ? (
            <span className="text-muted text-sm">No reasoning steps recorded</span>
          ) : null}
        </>
      )}
    </DrawerSection>
  )
}

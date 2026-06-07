import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { AgentRun } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'

const AGENTS = [
  'market_observer', 'research_agent', 'strategy_designer',
  'risk_critic', 'portfolio_agent', 'post_trade_reviewer', 'dreaming_agent',
]

const col = createColumnHelper<AgentRun>()

const columns = [
  col.accessor('agent_name', { header: 'Agent' }),
  col.accessor('status', {
    header: 'Status',
    cell: info => <StatusBadge status={info.getValue()} />,
  }),
  col.accessor('trigger_type', { header: 'Trigger' }),
  col.accessor('model_provider', {
    header: 'Provider',
    cell: info => info.getValue() || '—',
  }),
  col.accessor('outputs_created', {
    header: 'Outputs',
    cell: info => {
      try { return JSON.parse(info.getValue() || '[]').length }
      catch { return 0 }
    },
  }),
  col.accessor('started_at', {
    header: 'Started',
    cell: info => info.getValue()?.slice(0, 16) || '—',
  }),
]

export default function AgentRuns() {
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const [selected, setSelected] = useState<AgentRun | null>(null)

  const run = useMutation({
    mutationFn: (name: string) => api.startAgentRun(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-runs'] }),
  })

  const rows = data as AgentRun[]

  function parseJson(raw: string | null): any[] {
    if (!raw) return []
    try { return JSON.parse(raw) } catch { return [] }
  }

  return (
    <div>
      <h2 className="page-title">Agent Runs</h2>
      <div className="card">
        <h3>Run an agent now</h3>
        <div className="flex flex-wrap gap-1">
          {AGENTS.map(a => (
            <button key={a} className="secondary" onClick={() => run.mutate(a)} disabled={run.isPending}>{a}</button>
          ))}
        </div>
      </div>
      <div className="card">
        <DataTable
          data={rows}
          columns={columns}
          onRowClick={setSelected}
          emptyMessage="No agent runs yet."
        />
      </div>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Agent Run Detail">
        {selected && (
          <>
            <DrawerSection label="Run Info">
              <DrawerField label="Agent" value={selected.agent_name} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
              <DrawerField label="Trigger" value={selected.trigger_type} />
              <DrawerField label="Provider" value={selected.model_provider} />
              <DrawerField label="Model" value={selected.model_name} />
              <DrawerField label="Started" value={selected.started_at?.slice(0, 16)} />
              <DrawerField label="Ended" value={selected.ended_at?.slice(0, 16)} />
              <DrawerField label="Cost" value={selected.cost_estimate != null ? `$${selected.cost_estimate.toFixed(4)}` : null} mono />
            </DrawerSection>
            <ToolCallsSection runId={selected.id} fallback={selected.tools_called} />
            <DrawerSection label="Outputs Created">
              {(() => {
                const outputs = parseJson(selected.outputs_created)
                return outputs.length > 0
                  ? <ul style={{ paddingLeft: '1rem', fontSize: '0.8rem' }}>{outputs.map((o, i) => <li key={i}>{typeof o === 'string' ? o : JSON.stringify(o)}</li>)}</ul>
                  : <span className="text-muted text-sm">No outputs recorded</span>
              })()}
            </DrawerSection>
            <DrawerSection label="Errors">
              {(() => {
                const errors = parseJson(selected.errors)
                return errors.length > 0
                  ? <ul style={{ paddingLeft: '1rem', fontSize: '0.8rem', color: 'var(--red)' }}>{errors.map((e, i) => <li key={i}>{typeof e === 'string' ? e : JSON.stringify(e)}</li>)}</ul>
                  : <span className="text-muted text-sm">No errors</span>
              })()}
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}

function ToolCallsSection({ runId, fallback }: { runId: string; fallback: string | null }) {
  const { data: toolCalls = [] } = useQuery({
    queryKey: ['tool-calls', runId],
    queryFn: () => api.getAgentRunToolCalls(runId),
    enabled: !!runId,
  })

  if (toolCalls.length > 0) {
    return (
      <DrawerSection label="Tools Called">
        <table style={{ fontSize: '0.78rem' }}>
          <thead>
            <tr><th>Tool</th><th>Class</th><th>Permitted</th><th>Time</th></tr>
          </thead>
          <tbody>
            {(toolCalls as any[]).map((tc: any, i: number) => (
              <tr key={tc.id || i}>
                <td className="mono">{tc.tool_name}</td>
                <td>{tc.tool_class}</td>
                <td><span className={`badge ${tc.permitted ? 'badge-green' : 'badge-red'}`}>{tc.permitted ? 'yes' : 'no'}</span></td>
                <td>{tc.created_at?.slice(11, 19) || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DrawerSection>
    )
  }

  const tools = fallback ? (() => { try { return JSON.parse(fallback) } catch { return [] } })() : []
  return (
    <DrawerSection label="Tools Called">
      {tools.length > 0
        ? <ul style={{ paddingLeft: '1rem', fontSize: '0.8rem' }}>{tools.map((t: any, i: number) => <li key={i}>{typeof t === 'string' ? t : JSON.stringify(t)}</li>)}</ul>
        : <span className="text-muted text-sm">No tools recorded</span>}
    </DrawerSection>
  )
}

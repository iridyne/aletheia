import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { TradeProposal } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, StatusText } from '../components/ui'
import { formatViolation, formatViolationText } from '../lib/riskViolations'
import { humanize, fmtTimestamp } from '../lib/format'

/**
 * Approval Desk (WEBUI_PAGE_MAP.md — Proposals ELEVATED).
 *
 * The one human-in-loop control point (§15.3, trust §3.4). Designed as a desk, not a list:
 *  - PENDING QUEUE: rich decision cards. Everything needed to decide is visible WITHOUT a
 *    click — trade params + agent rationale + the risk engine's verdict (fetched inline) +
 *    Approve/Reject with confirmation.
 *  - HISTORY: already-decided proposals as a sortable table; click for the full drawer.
 *
 * Honesty: risk verdict comes from /proposals/{id}/risk-decision; "no risk decision recorded"
 * when absent (never a reassuring fake). Approve requires explicit confirm (trust §3.4).
 */

const col = createColumnHelper<TradeProposal>()

const historyColumns = [
  col.accessor('symbol', { header: 'Symbol', cell: info => <span className="mono">{info.getValue()}</span> }),
  col.accessor('market_type', { header: 'Market', cell: info => humanize(info.getValue()) }),
  col.accessor('direction', { header: 'Direction', cell: info => humanize(info.getValue()) }),
  col.accessor('execution_mode', { header: 'Mode', cell: info => humanize(info.getValue()) }),
  col.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  col.accessor('created_at', { header: 'Created', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

export default function Proposals() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const [selected, setSelected] = useState<TradeProposal | null>(null)

  const approve = useMutation({
    mutationFn: (id: string) => api.approveProposal(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['proposals'] }); qc.invalidateQueries({ queryKey: ['positions'] }) },
  })
  const reject = useMutation({
    mutationFn: (id: string) => api.rejectProposal(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  })

  const rows = data as TradeProposal[]
  const pending = rows.filter(p => p.status === 'pending')
  const decided = rows.filter(p => p.status !== 'pending')

  function doApprove(p: TradeProposal) {
    if (!confirm(`Approve ${p.direction.toUpperCase()} ${p.symbol} via ${p.execution_mode}?\n\nThis routes the order through the execution gateway.`)) return
    approve.mutate(p.id)
  }
  function doReject(p: TradeProposal) {
    if (!confirm(`Reject ${p.direction.toUpperCase()} ${p.symbol}?`)) return
    reject.mutate(p.id)
  }

  return (
    <div>
      <PageHeader
        title="Approval Desk"
        role="Decide · the single human-in-loop control point. Each pending proposal shows its parameters, the agent's rationale, and the risk engine's verdict — approve or reject without leaving the queue."
      />

      {/* PENDING QUEUE — the focal point */}
      <div className="desk-queue-header">
        <h3>Pending Queue</h3>
        {pending.length > 0
          ? <Badge tone="yellow">{pending.length}</Badge>
          : <StatusText tone="green">clear</StatusText>}
      </div>

      {pending.length === 0 ? (
        <Card><p className="text-muted text-sm">No proposals awaiting decision.</p></Card>
      ) : (
        <div className="desk-queue">
          {pending.map(p => (
            <ProposalCard
              key={p.id}
              p={p}
              onApprove={() => doApprove(p)}
              onReject={() => doReject(p)}
              onOpenDetail={() => setSelected(p)}
              busy={approve.isPending || reject.isPending}
              navigate={navigate}
            />
          ))}
        </div>
      )}

      {/* HISTORY — decided proposals */}
      <h3 className="desk-history-title">Decision History</h3>
      <Card>
        <DataTable
          data={decided}
          columns={historyColumns}
          onRowClick={setSelected}
          emptyMessage="No decided proposals yet."
        />
      </Card>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Proposal Detail">
        {selected && (
          <>
            <DrawerSection label="Trade">
              <DrawerField label="Symbol" value={selected.symbol} mono />
              <DrawerField label="Market" value={humanize(selected.market_type)} />
              <DrawerField label="Direction" value={humanize(selected.direction)} />
              <DrawerField label="Mode" value={humanize(selected.execution_mode)} />
              <DrawerField label="Order Type" value={humanize(selected.order_type)} />
              <DrawerField label="Quantity" value={selected.quantity} mono />
              <DrawerField label="Price" value={selected.price} mono />
              <DrawerField label="Leverage" value={selected.leverage ? `${selected.leverage}x` : null} mono />
              <DrawerField label="Stop" value={selected.stop_price} mono />
            </DrawerSection>
            <DrawerSection label="Rationale">
              <p className="feed-thesis">{selected.agent_rationale || 'No rationale provided.'}</p>
            </DrawerSection>
            <RiskDecisionSection proposalId={selected.id} />
            <DrawerSection label="Links">
              <DrawerField label="Strategy ID" value={selected.strategy_id ? (
                <a href="#" onClick={e => { e.preventDefault(); navigate('/strategies') }} className="mono">{selected.strategy_id}</a>
              ) : null} />
              <DrawerField label="Opportunity ID" value={selected.opportunity_id ? (
                <a href="#" onClick={e => { e.preventDefault(); navigate('/agent-activity') }} className="mono">{selected.opportunity_id}</a>
              ) : null} />
            </DrawerSection>
            <DrawerSection label="Status">
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
              <DrawerField label="Created" value={fmtTimestamp(selected.created_at)} mono />
            </DrawerSection>
            <div className="flex gap-2 mt-3">
              {selected.status === 'pending' && (
                <>
                  <Button onClick={() => { doApprove(selected); setSelected(null) }} disabled={approve.isPending}>Approve</Button>
                  <Button variant="secondary" onClick={() => { doReject(selected); setSelected(null) }} disabled={reject.isPending}>Reject</Button>
                </>
              )}
              <Button variant="secondary" onClick={() => navigate('/charts')}>View on Chart</Button>
            </div>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}

/** A pending proposal as a full decision card — everything visible without a click. */
function ProposalCard({
  p, onApprove, onReject, onOpenDetail, busy, navigate,
}: {
  p: TradeProposal
  onApprove: () => void
  onReject: () => void
  onOpenDetail: () => void
  busy: boolean
  navigate: (path: string) => void
}) {
  const dirClass = p.direction === 'long' || p.direction === 'buy' ? 'dir-long' : 'dir-short'
  return (
    <Card className="proposal-card">
      <div className="proposal-card-head">
        <div className="flex items-center gap-2">
          <span className={`dir-tag ${dirClass}`}>{p.direction.toUpperCase()}</span>
          <span className="proposal-symbol mono">{p.symbol}</span>
          <Badge tone="blue">{humanize(p.market_type)}</Badge>
          <Badge tone="yellow">{humanize(p.execution_mode)}</Badge>
        </div>
        <span className="text-muted text-sm mono">{fmtTimestamp(p.created_at)}</span>
      </div>

      <div className="proposal-params mono">
        <span>qty <b>{p.quantity ?? '—'}</b></span>
        <span>px <b>{p.price ?? 'mkt'}</b></span>
        {p.leverage ? <span>lev <b>{p.leverage}x</b></span> : null}
        <span>stop <b>{p.stop_price ?? '—'}</b></span>
        <span>{humanize(p.order_type)}</span>
      </div>

      {p.agent_rationale && (
        <div className="proposal-rationale">
          <span className="proposal-rationale-label">WHY</span>
          <span>{p.agent_rationale.length > 220 ? p.agent_rationale.slice(0, 220) + '…' : p.agent_rationale}</span>
        </div>
      )}

      {/* Risk verdict — fetched inline so the decision is informed without a click */}
      <RiskVerdictInline proposalId={p.id} />

      <div className="proposal-card-actions">
        <Button onClick={onApprove} disabled={busy}>Approve</Button>
        <Button variant="secondary" onClick={onReject} disabled={busy}>Reject</Button>
        <Button variant="secondary" onClick={onOpenDetail}>Details</Button>
        {(p.strategy_id || p.opportunity_id) && (
          <Button variant="secondary" onClick={() => navigate(p.opportunity_id ? '/agent-activity' : '/strategies')}>
            {p.opportunity_id ? 'Opportunity →' : 'Strategy →'}
          </Button>
        )}
      </div>
    </Card>
  )
}

/** Compact inline risk verdict for the pending card. */
function RiskVerdictInline({ proposalId }: { proposalId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['risk-decision', proposalId],
    queryFn: () => api.getProposalRiskDecision(proposalId),
    enabled: !!proposalId,
  })
  if (isLoading) return <div className="proposal-risk loading">risk: checking…</div>
  const d = data?.decision
  if (!d) return <div className="proposal-risk none">risk: no decision recorded</div>

  const approved = d.approved === true || d.approved === 1 || d.decision === 'approved'
  const violations = parseViolations(d.violations)
  return (
    <div className={`proposal-risk ${approved ? 'ok' : 'block'}`}>
      <span className="proposal-risk-verdict">
        risk: {approved ? 'approved' : 'flagged'}
        {d.risk_score != null && <span className="mono"> · score {d.risk_score}</span>}
      </span>
      {violations.length > 0 && (
        <span className="proposal-risk-violations">{violations.slice(0, 2).map(formatViolationText).join(' · ')}{violations.length > 2 && ` +${violations.length - 2}`}</span>
      )}
      {d.message && violations.length === 0 && <span className="proposal-risk-msg">{d.message}</span>}
    </div>
  )
}

function parseViolations(v: unknown): string[] {
  if (!v) return []
  if (Array.isArray(v)) return v as string[]
  if (typeof v === 'string') { try { const a = JSON.parse(v); return Array.isArray(a) ? a : [] } catch { return [] } }
  return []
}

/** Full risk decision block for the detail drawer (decided proposals). */
function RiskDecisionSection({ proposalId }: { proposalId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['risk-decision', proposalId],
    queryFn: () => api.getProposalRiskDecision(proposalId),
    enabled: !!proposalId,
  })
  if (isLoading) return <DrawerSection label="Risk Decision"><p className="text-muted text-sm">Loading...</p></DrawerSection>
  const d = data?.decision
  if (!d) return <DrawerSection label="Risk Decision"><p className="text-muted text-sm">No risk decision recorded.</p></DrawerSection>
  const violations = parseViolations(d.violations)
  return (
    <DrawerSection label="Risk Decision">
      <DrawerField label="Decision" value={<StatusBadge status={d.decision || (d.approved ? 'approved' : 'rejected')} />} />
      <DrawerField label="Risk Score" value={d.risk_score} mono />
      {/* The backend message is `Rejected: <raw codes>` when there are violations — redundant
          with (and uglier than) the humanized Violations list below. Show it only when it
          adds something the list doesn't, i.e. no violations (e.g. "Approved"). */}
      {violations.length === 0 && <DrawerField label="Message" value={d.message} />}
      {violations.length > 0 && (
        <div className="mt-2">
          <span className="text-muted text-sm">Violations:</span>
          <ul className="proposal-violations-list">
            {violations.map((v, i) => {
              const f = formatViolation(v)
              return <li key={i}>{f.label}{f.detail && <span className="text-muted"> — {f.detail}</span>}</li>
            })}
          </ul>
        </div>
      )}
    </DrawerSection>
  )
}

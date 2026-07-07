import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { TradeProposal } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, StatusText } from '../components/ui'
import { ConvictionMeter } from '../components/ConvictionMeter'
import { formatViolation, formatViolationText } from '../lib/riskViolations'
import { humanize, fmtTimestamp, fmtSymbol } from '../lib/format'

interface DecisionMeta {
  conviction: number
  debate_ran: boolean
  debate_bull: number | null
  debate_bear: number | null
  supporting: string[]
  opposing: string[]
  guards: { guards_run: number; blocks: number; warnings: number; details: any[] }
}

function parseDecisionMeta(rationale: string | null): { text: string; meta: DecisionMeta | null } {
  if (!rationale) return { text: '', meta: null }
  const sep = rationale.indexOf('\n---\n')
  if (sep === -1) return { text: rationale, meta: null }
  const text = rationale.slice(0, sep)
  try {
    const meta = JSON.parse(rationale.slice(sep + 5)) as DecisionMeta
    return { text, meta }
  } catch {
    // Defense in depth: a malformed/truncated meta JSON must NEVER leak the raw
    // "---{...broken json...}" tail to the user. Show only the clean thesis
    // (the part before the separator), drop the unparseable remainder.
    return { text, meta: null }
  }
}

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
  col.accessor('symbol', { header: 'Symbol', cell: info => <span className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</span> }),
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
  const [pendingId, setPendingId] = useState<string | null>(null)
  // Inline per-proposal error — trust path, the operator MUST see an approve/reject failure
  // (silent failure means "approved button reset" while order never landed). Stays until
  // the next action on that proposal or until the proposals list refreshes.
  const [errorByProposal, setErrorByProposal] = useState<Record<string, string>>({})
  const setErr = (id: string, msg: string) => setErrorByProposal(prev => ({ ...prev, [id]: msg }))
  const clearErr = (id: string) => setErrorByProposal(prev => { const n = { ...prev }; delete n[id]; return n })

  const approve = useMutation({
    mutationFn: (id: string) => api.approveProposal(id).then(r => ({ id, r })),
    // #113 fix: backend approve-paper responds 200 for both success AND
    // execution-time failures — success body is `{execution: {status:...}}`,
    // failure body is `{execution: {status:'error', message:'...'}}`. The old
    // onSuccess unconditionally cleared any inline error, so a 200-with-error
    // silently reset the UI to "clean" while the order never landed. Inspect
    // execution.status and route real failures back to setErr.
    onSuccess: ({ id, r }: { id: string; r: any }) => {
      const execStatus = String(r?.execution?.status || '').toLowerCase()
      const isFailure = execStatus === 'error' || execStatus === 'rejected'
      if (isFailure) {
        setErr(id, String(r?.execution?.message || `execution ${execStatus}`))
        return
      }
      clearErr(id)
      qc.invalidateQueries({ queryKey: ['proposals'] })
      qc.invalidateQueries({ queryKey: ['positions'] })
    },
    onError: (e: any, id: string) => setErr(id, String(e?.message || 'approve failed')),
  })
  const reject = useMutation({
    mutationFn: (id: string) => api.rejectProposal(id).then(r => ({ id, r })),
    onSuccess: ({ id }) => { clearErr(id); qc.invalidateQueries({ queryKey: ['proposals'] }) },
    onError: (e: any, id: string) => setErr(id, String(e?.message || 'reject failed')),
  })

  const rows = data as TradeProposal[]
  const pending = rows.filter(p => p.status === 'pending')
  const decided = rows.filter(p => p.status !== 'pending')

  function doApprove(p: TradeProposal) {
    // #114 fix: the confirm dialog used to echo p.execution_mode — which may
    // say `bitget_live_limited` / `okx_demo` etc. — but the /approve-paper
    // route ALWAYS overrides to `paper_internal` on the backend
    // (data.py:642). The dialog lied to the operator about what would
    // actually happen. Show the real, forced mode instead, along with a
    // heads-up if the proposal's stored mode differs (rare but honest).
    const forcedMode = 'paper_internal'
    const modeLine = `Mode: ${forcedMode} (paper simulation — no real order will be placed)`
    const proposalMode = String(p.execution_mode || '')
    const modeMismatch = proposalMode && proposalMode !== forcedMode
      ? `\nNote: the proposal was tagged '${proposalMode}', but the /approve-paper route routes every approval through ${forcedMode}. Use the live-unlock flow to place a real order.`
      : ''
    if (!confirm(`Approve ${p.direction.toUpperCase()} ${fmtSymbol(p.symbol, p.market_type)}\n${modeLine}${modeMismatch}\n\nConfirm?`)) return
    setPendingId(p.id)
    approve.mutate(p.id, { onSettled: () => setPendingId(null) })
  }
  function doReject(p: TradeProposal) {
    if (!confirm(`Reject ${p.direction.toUpperCase()} ${fmtSymbol(p.symbol, p.market_type)}?`)) return
    setPendingId(p.id)
    reject.mutate(p.id, { onSettled: () => setPendingId(null) })
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
              busy={p.id === pendingId}
              navigate={navigate}
              errorMsg={errorByProposal[p.id]}
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
              <DrawerField label="Symbol" value={fmtSymbol(selected.symbol, selected.market_type)} mono />
              <DrawerField label="Market" value={humanize(selected.market_type)} />
              <DrawerField label="Direction" value={humanize(selected.direction)} />
              <DrawerField label="Mode" value={humanize(selected.execution_mode)} />
              <DrawerField label="Order Type" value={humanize(selected.order_type)} />
              <DrawerField label="Quantity" value={selected.quantity} mono />
              <DrawerField label="Price" value={selected.price} mono />
              <DrawerField label="Leverage" value={selected.leverage ? `${selected.leverage}x` : null} mono />
              <DrawerField label="Stop" value={selected.stop_price} mono />
              <DrawerField label="Take Profit" value={selected.take_profit} mono />
              <DrawerField label="Reward:Risk" value={selected.risk_reward != null ? `${selected.risk_reward}:1` : null} mono />
            </DrawerSection>
            <DrawerSection label="Rationale">
              <ProposalRationaleDetail rationale={selected.agent_rationale} />
            </DrawerSection>
            <RiskDecisionSection proposalId={selected.id} />
            <DrawerSection label="Links">
              <DrawerField label="Strategy ID" value={selected.strategy_id ? (
                <span className="mono">{selected.strategy_id}</span>
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
  p, onApprove, onReject, onOpenDetail, busy, navigate, errorMsg,
}: {
  p: TradeProposal
  onApprove: () => void
  onReject: () => void
  onOpenDetail: () => void
  busy: boolean
  navigate: (path: string) => void
  errorMsg?: string
}) {
  const dirClass = p.direction === 'long' || p.direction === 'buy' ? 'dir-long' : 'dir-short'
  return (
    <Card className="proposal-card">
      <div className="proposal-card-head">
        <div className="flex items-center gap-2">
          <span className={`dir-tag ${dirClass}`}>{p.direction.toUpperCase()}</span>
          <span className="proposal-symbol mono">{fmtSymbol(p.symbol, p.market_type)}</span>
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
        <span>tp <b>{p.take_profit ?? '—'}</b></span>
        <span>r:r <b>{p.risk_reward != null ? `${p.risk_reward}:1` : '—'}</b></span>
        <span>{humanize(p.order_type)}</span>
      </div>

      <ProposalRationale rationale={p.agent_rationale} />

      {/* Risk verdict — fetched inline so the decision is informed without a click */}
      <RiskVerdictInline proposalId={p.id} />

      <div className="proposal-card-actions">
        <Button onClick={onApprove} disabled={busy}>Approve</Button>
        <Button variant="secondary" onClick={onReject} disabled={busy}>Reject</Button>
        <Button variant="secondary" onClick={onOpenDetail}>Details</Button>
        {p.opportunity_id && (
          <Button variant="secondary" onClick={() => navigate('/agent-activity')}>
            Opportunity →
          </Button>
        )}
      </div>
      {errorMsg && <div className="lever-err" role="alert">✗ {errorMsg}</div>}
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

function ProposalRationale({ rationale }: { rationale: string | null }) {
  const { text, meta } = useMemo(() => parseDecisionMeta(rationale), [rationale])
  if (!rationale) return null
  return (
    <div className="proposal-rationale">
      {meta ? (
        <>
          <ConvictionMeter conviction={meta.conviction} debateRan={meta.debate_ran}
            bull={meta.debate_bull} bear={meta.debate_bear} />
          <span className="proposal-rationale-label">WHY</span>
          <span>{text.length > 180 ? text.slice(0, 180) + '…' : text}</span>
        </>
      ) : (
        <>
          <span className="proposal-rationale-label">WHY</span>
          <span>{text.length > 220 ? text.slice(0, 220) + '…' : text}</span>
        </>
      )}
    </div>
  )
}

export function ProposalRationaleDetail({ rationale }: { rationale: string | null }) {
  const { text, meta } = useMemo(() => parseDecisionMeta(rationale), [rationale])
  if (!rationale) return <p className="feed-thesis">No rationale provided.</p>
  if (!meta) return <p className="feed-thesis">{text || 'No rationale provided.'}</p>
  return (
    <div className="decision-detail">
      <ConvictionMeter conviction={meta.conviction} debateRan={meta.debate_ran}
        bull={meta.debate_bull} bear={meta.debate_bear} />

      <div className="decision-section mt-2">
        <div className="decision-section-label">Thesis</div>
        <p className="decision-thesis">{text}</p>
      </div>

      {meta.supporting.length > 0 && (
        <div className="decision-section mt-2">
          <div className="decision-section-label">Supporting Signals</div>
          <ul className="decision-signal-list">
            {meta.supporting.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {meta.opposing.length > 0 && (
        <div className="decision-section mt-2">
          <div className="decision-section-label">Opposing Signals</div>
          <ul className="decision-signal-list opposing">
            {meta.opposing.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {meta.guards && (
        <div className="decision-section mt-2">
          <div className="decision-section-label">Risk Guards</div>
          <span className="text-muted text-sm">
            {meta.guards.guards_run} checked · {meta.guards.blocks} blocked · {meta.guards.warnings} warned
          </span>
          {meta.guards.details.length > 0 && (
            <ul className="decision-signal-list opposing">
              {meta.guards.details.map((d: any, i: number) => <li key={i}>{d.guard}: {d.reason}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Order, Position, TradeProposal } from '../types'
import { fmtSymbol } from '../lib/format'

/**
 * Causal-chain inspector (WEBUI_PAGE_MAP.md — cross-cutting, lives in Trades & Positions).
 *
 * Renders the immutable trust §1.1 chain:
 *   opportunity → strategy → proposal → entry reasoning → risk_decision → order → fill → position → close reasoning
 *
 * HONESTY:
 *  - Entry reasoning is parsed only from the proposal's persisted immutable metadata.
 *    Config defaults, market snapshots and leverage caps carry their own source label;
 *    they are never presented as an AI-chosen value.
 *  - Close reasoning is read only from positions.exit_reasoning after a real close.
 *    A protective/routed/reconciled close is labelled by its real actor/source.
 *  - Older rows without either record render "not recorded" rather than a plausible story.
 */

type Anchor =
  | { kind: 'order'; order: Order }
  | { kind: 'position'; position: Position }

type NodeState = 'present' | 'empty' | 'missing'
type JsonRecord = Record<string, unknown>

const ENTRY_PARAMETER_LABELS: Array<[string, string]> = [
  ['entry_price', 'Entry price'],
  ['stop_loss', 'Stop loss'],
  ['take_profit', 'Take profit'],
  ['notional_usdt', 'Notional'],
  ['leverage', 'Leverage'],
]

function CausalNode({
  stage, state, facts, onOpen, note,
}: {
  stage: string
  state: NodeState
  facts?: React.ReactNode
  onOpen?: () => void
  note?: string
}) {
  return (
    <div className={`cc-node cc-${state}`}>
      <div className="cc-rail"><span className="cc-dot" /></div>
      <div className="cc-body">
        <div className="cc-head">
          <span className="cc-stage">{stage}</span>
          {onOpen && <button className="cc-open" onClick={onOpen}>open →</button>}
        </div>
        {state === 'present' && facts && <div className="cc-facts">{facts}</div>}
        {state === 'empty' && <div className="cc-note">none</div>}
        {state === 'missing' && <div className="cc-note cc-note-gap">not recorded{note ? ` · ${note}` : ''}</div>}
      </div>
    </div>
  )
}

function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : null
}

function parseEntryParameters(proposal: TradeProposal | null): JsonRecord | null {
  const rationale = proposal?.agent_rationale
  if (!rationale) return null
  const separator = rationale.indexOf('\n---\n')
  if (separator < 0) return null
  try {
    const meta = asRecord(JSON.parse(rationale.slice(separator + 5)))
    return asRecord(meta?.entry_parameters)
  } catch {
    return null
  }
}

function parseExitReasoning(position: Position | null): JsonRecord | null {
  if (!position?.exit_reasoning) return null
  try {
    return asRecord(JSON.parse(position.exit_reasoning))
  } catch {
    return null
  }
}

function printable(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}

function EntryReasoningFacts({ parameters }: { parameters: JsonRecord }) {
  return (
    <div>
      {ENTRY_PARAMETER_LABELS.map(([key, label]) => {
        const item = asRecord(parameters[key])
        if (!item) return null
        const value = key === 'notional_usdt'
          ? item.actual_value
          : item.final_value
        return (
          <div key={key} className="cc-entry-reason">
            <div><b>{label}</b> <span className="mono">{printable(value)}</span>{' '}
              <span className="text-muted text-sm">· {printable(item.value_source)}</span>
            </div>
            <div className="cc-why">{printable(item.reason) === '—' ? 'AI rationale not recorded.' : printable(item.reason)}</div>
            {key === 'notional_usdt' && (
              <div className="text-muted text-sm">estimated loss at stop: {printable(item.estimated_loss_at_stop_usdt)} USDT</div>
            )}
            {key === 'leverage' && item.estimated_margin_usdt != null && (
              <div className="text-muted text-sm">estimated margin: {printable(item.estimated_margin_usdt)} USDT</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function CloseReasoningFacts({ reasoning }: { reasoning: JsonRecord }) {
  const trigger = asRecord(reasoning.trigger)
  return (
    <div>
      <div>{printable(reasoning.reason_code)} · {printable(reasoning.actor)} / {printable(reasoning.source)}</div>
      <div className="cc-why">{printable(reasoning.reason)}</div>
      {trigger && <div className="text-muted text-sm mono">
        exit @ {printable(trigger.exit_price)}
        {trigger.configured_stop_price != null && ` · stop ${printable(trigger.configured_stop_price)}`}
        {trigger.configured_take_profit_price != null && ` · target ${printable(trigger.configured_take_profit_price)}`}
      </div>}
    </div>
  )
}

export default function CausalChain({ anchor }: { anchor: Anchor }) {
  const navigate = useNavigate()
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const { data: orders = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  // Needed for an order anchor to find the resulting position. React Query shares
  // this request with TradesPositions, so this is normally a cache read.
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })

  const proposalId = anchor.kind === 'order'
    ? anchor.order.proposal_id
    : anchor.position.proposal_id ?? null
  const proposalFromList = proposalId
    ? (proposals as TradeProposal[]).find(p => p.id === proposalId) || null
    : null

  const { data: proposalById } = useQuery({
    queryKey: ['proposal', proposalId],
    queryFn: () => api.getProposal(proposalId as string),
    enabled: !!proposalId && !proposalFromList,
  })
  const proposal = (proposalFromList ?? proposalById ?? null) as TradeProposal | null

  const { data: riskData } = useQuery({
    queryKey: ['risk-decision', proposalId],
    queryFn: () => api.getProposalRiskDecision(proposalId as string),
    enabled: !!proposalId,
  })
  const risk = riskData?.decision

  const resolvedOrder: Order | null = anchor.kind === 'position' && anchor.position.order_id
    ? (orders as Order[]).find(o => o.id === anchor.position.order_id) ?? null
    : null
  const resolvedPosition: Position | null = anchor.kind === 'position'
    ? anchor.position
    : (positions as Position[]).find(p => p.order_id === anchor.order.id) ?? null

  const strategyId = anchor.kind === 'order'
    ? proposal?.strategy_id ?? null
    : anchor.position.strategy_id ?? proposal?.strategy_id ?? null
  const opportunityId = proposal?.opportunity_id ?? null
  const entryParameters = parseEntryParameters(proposal)
  const exitReasoning = parseExitReasoning(resolvedPosition)

  const rationaleText = proposal?.agent_rationale
    ? (() => { const i = proposal.agent_rationale.indexOf('\n---\n'); const t = i > 0 ? proposal.agent_rationale.slice(0, i) : proposal.agent_rationale; return t.length > 350 ? t.slice(0, 350) + '…' : t })()
    : null

  return (
    <div className="causal-chain">
      {proposalId
        ? proposal
          ? <CausalNode stage="Opportunity" state={opportunityId ? 'present' : 'empty'} facts={<span className="mono">{opportunityId}</span>} onOpen={opportunityId ? () => navigate('/agent-activity') : undefined} />
          : <CausalNode stage="Opportunity" state="missing" note="proposal not in recent list" />
        : <CausalNode stage="Opportunity" state="empty" />}

      <CausalNode stage="Strategy" state={strategyId ? 'present' : 'empty'} facts={<span className="mono">{strategyId}</span>} />

      {proposalId
        ? <CausalNode stage="Proposal" state="present" facts={<><div className="mono cc-id">{proposalId}</div>{proposal ? <div>{proposal.direction?.toUpperCase()} {fmtSymbol(proposal.symbol, proposal.market_type)} · {proposal.execution_mode}{rationaleText && <div className="cc-why">{rationaleText}</div>}</div> : <div className="text-muted text-sm">(detail not in recent list)</div>}</>} onOpen={() => navigate('/blotter')} />
        : <CausalNode stage="Proposal" state="empty" />}

      {proposal
        ? entryParameters
          ? <CausalNode stage="Entry Reasoning" state="present" facts={<EntryReasoningFacts parameters={entryParameters} />} />
          : <CausalNode stage="Entry Reasoning" state="missing" note="proposal predates parameter rationale" />
        : <CausalNode stage="Entry Reasoning" state={proposalId ? 'missing' : 'empty'} note={proposalId ? 'proposal not available' : undefined} />}

      {risk
        ? <CausalNode stage="Risk Decision" state="present" facts={<>{anchor.kind === 'order' && anchor.order.risk_decision_id && <div className="mono cc-id">{anchor.order.risk_decision_id}</div>}<div>{(risk.approved === true || risk.approved === 1 || risk.decision === 'approved') ? 'approved' : 'flagged'}{risk.risk_score != null && <span className="mono"> · score {risk.risk_score}</span>}{risk.message && <div className="text-muted text-sm">{risk.message}</div>}</div></>} onOpen={() => navigate('/risk')} />
        : <CausalNode stage="Risk Decision" state="empty" />}

      {anchor.kind === 'order'
        ? <CausalNode stage="Order" state="present" facts={<><div className="mono cc-id">{anchor.order.id}</div><div>{anchor.order.direction?.toUpperCase()} {anchor.order.quantity} {fmtSymbol(anchor.order.symbol, anchor.order.market_type)} @ {anchor.order.price ?? 'mkt'} · {anchor.order.status}</div></>} />
        : anchor.position.order_id
          ? <CausalNode stage="Order" state="present" facts={resolvedOrder ? <><div className="mono cc-id">{anchor.position.order_id}</div><div>{resolvedOrder.direction?.toUpperCase()} {resolvedOrder.quantity} {fmtSymbol(resolvedOrder.symbol, resolvedOrder.market_type)} @ {resolvedOrder.price ?? 'mkt'} · {resolvedOrder.status}</div></> : <><div className="mono cc-id">{anchor.position.order_id}</div><div className="text-muted text-sm">(order detail not in recent list)</div></>} />
          : <CausalNode stage="Order" state="empty" />}

      {anchor.kind === 'order'
        ? anchor.order.filled_at
          ? <CausalNode stage="Fill" state="present" facts={<div>filled @ {anchor.order.price ?? '—'} · {anchor.order.filled_at.slice(0, 16)}{anchor.order.exchange_order_id && <div className="mono cc-id">ex: {anchor.order.exchange_order_id}</div>}</div>} />
          : <CausalNode stage="Fill" state="empty" />
        : resolvedOrder?.filled_at
          ? <CausalNode stage="Fill" state="present" facts={<div>filled @ {resolvedOrder.price ?? '—'} · {resolvedOrder.filled_at.slice(0, 16)}{resolvedOrder.exchange_order_id && <div className="mono cc-id">ex: {resolvedOrder.exchange_order_id}</div>}</div>} />
          : <CausalNode stage="Fill" state="empty" />}

      {resolvedPosition
        ? <CausalNode stage="Position" state="present" facts={<><div className="mono cc-id">{resolvedPosition.id}</div><div>{resolvedPosition.direction?.toUpperCase()} {resolvedPosition.quantity} {fmtSymbol(resolvedPosition.symbol, resolvedPosition.market_type)} · {resolvedPosition.status}</div></>} />
        : anchor.kind === 'order'
          ? <CausalNode stage="Position" state="missing" note="no position record found for this order" />
          : <CausalNode stage="Position" state="empty" />}

      {resolvedPosition
        ? resolvedPosition.status === 'open'
          ? <CausalNode stage="Close Reasoning" state="empty" />
          : exitReasoning
            ? <CausalNode stage="Close Reasoning" state="present" facts={<CloseReasoningFacts reasoning={exitReasoning} />} />
            : <CausalNode stage="Close Reasoning" state="missing" note="closed position predates exit explanation" />
        : <CausalNode stage="Close Reasoning" state="empty" />}
    </div>
  )
}

import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import { positionFreshness, positionKeys } from '../lib/queryKeys'
import type { EntryPlan, Order, Position, TradeProposal } from '../types'
import { fmtSymbol } from '../lib/format'
import { parseDecisionRationale } from '../lib/decisionReasoning'
import { orderIntent, orderIntentLabel } from '../lib/orderIntent'
import { DataStateMessage } from './DataState'

/**
 * Causal-chain inspector (WEBUI_PAGE_MAP.md — cross-cutting, lives in Trades & Positions).
 *
 * Renders Candidate → Entry Plan → Risk → Order → Fill → Position for the
 * current path, retaining the older Opportunity/Proposal chain only when no
 * exact Entry Plan identity was persisted.
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

type NodeState = 'present' | 'empty' | 'missing' | 'loading' | 'stale' | 'unavailable'
type ReadState = Extract<NodeState, 'present' | 'loading' | 'stale' | 'unavailable'>
type JsonRecord = Record<string, unknown>
type RiskDecisionData = {
  order_id?: string
  proposal_id: string | null
  risk_decision_id?: string | null
  status: string
  reason_code: string | null
  decision: any
  preflight_decision?: any
}

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
  const tone = state === 'stale'
    ? 'present'
    : state === 'loading' || state === 'unavailable'
      ? 'missing'
      : state
  const readState = state === 'loading' || state === 'stale' || state === 'unavailable'
    ? state
    : null
  return (
    <div className={`cc-node cc-${tone}`}>
      <div className="cc-rail"><span className="cc-dot" /></div>
      <div className="cc-body">
        <div className="cc-head">
          <span className="cc-stage">{stage}</span>
          {onOpen && <button className="cc-open" onClick={onOpen}>open →</button>}
        </div>
        {facts && <div className="cc-facts">{facts}</div>}
        {state === 'empty' && <div className="cc-note">none</div>}
        {state === 'missing' && <div className="cc-note cc-note-gap">not recorded{note ? ` · ${note}` : ''}</div>}
        {readState && <DataStateMessage state={readState} message={note} />}
      </div>
    </div>
  )
}

function resolveReadState(data: unknown, isPending: boolean, isError: boolean): ReadState {
  if (data !== undefined) return isError ? 'stale' : 'present'
  if (isError) return 'unavailable'
  return isPending ? 'loading' : 'unavailable'
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : null
}

function parseEntryParameters(proposal: TradeProposal | null): JsonRecord | null {
  const { meta } = parseDecisionRationale(proposal?.agent_rationale)
  return asRecord(meta?.entry_parameters)
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
  const proposalsQuery = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const ordersQuery = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  // Needed for an order anchor to find the resulting position. React Query shares
  // this request with TradesPositions, so this is normally a cache read.
  const positionsQuery = useQuery({ queryKey: positionKeys.all, queryFn: api.getPositions, ...positionFreshness })
  const proposals = proposalsQuery.data ?? []
  const orders = ordersQuery.data ?? []
  const positions = positionsQuery.data ?? []
  const ordersReadState = resolveReadState(ordersQuery.data, ordersQuery.isPending, ordersQuery.isError)
  const positionsReadState = resolveReadState(positionsQuery.data, positionsQuery.isPending, positionsQuery.isError)

  const proposalId = anchor.kind === 'order'
    ? anchor.order.proposal_id
    : anchor.position.proposal_id ?? null
  const proposalFromList = proposalId
    ? (proposals as TradeProposal[]).find(p => p.id === proposalId) || null
    : null

  const proposalQuery = useQuery({
    queryKey: ['proposal', proposalId],
    queryFn: () => api.getProposal(proposalId as string),
    enabled: !!proposalId && !proposalFromList,
  })
  const proposalById = proposalQuery.data
  const proposal = (proposalFromList ?? proposalById ?? null) as TradeProposal | null
  const proposalState: NodeState = !proposalId
    ? 'empty'
    : proposalFromList
      ? resolveReadState(proposalsQuery.data, proposalsQuery.isPending, proposalsQuery.isError)
      : proposalById
        ? resolveReadState(proposalQuery.data, proposalQuery.isPending, proposalQuery.isError)
        : isNotFound(proposalQuery.error)
          ? 'missing'
          : proposalQuery.isError
            ? 'unavailable'
            : proposalQuery.isPending
              ? 'loading'
              : 'missing'

  const anchorOrderIntent = anchor.kind === 'order' ? orderIntent(anchor.order) : null
  const resolvedOrder: Order | null = anchor.kind === 'position' && anchor.position.order_id
    ? (orders as Order[]).find(o => o.id === anchor.position.order_id) ?? null
    : null
  const resolvedPosition: Position | null = anchor.kind === 'position'
    ? anchor.position
    : anchorOrderIntent === 'close' && anchor.order.position_id
      ? (positions as Position[]).find(p => p.id === anchor.order.position_id) ?? null
      : (positions as Position[]).find(p => p.order_id === anchor.order.id) ?? null
  const exactOrder = anchor.kind === 'order' ? anchor.order : resolvedOrder
  const entryPlanId = exactOrder?.entry_plan_id ?? proposal?.entry_plan_id ?? null
  const entryPlanQuery = useQuery<EntryPlan>({
    queryKey: ['entry-plan', entryPlanId],
    queryFn: () => api.getEntryPlan(entryPlanId as string),
    enabled: Boolean(entryPlanId),
  })
  const entryPlan = entryPlanQuery.data
  const entryPlanState: NodeState = !entryPlanId
    ? 'empty'
    : entryPlan
      ? resolveReadState(entryPlan, entryPlanQuery.isPending, entryPlanQuery.isError)
      : isNotFound(entryPlanQuery.error)
        ? 'missing'
        : entryPlanQuery.isError
          ? 'unavailable'
          : entryPlanQuery.isPending
            ? 'loading'
            : 'missing'
  const riskDecisionId = exactOrder?.risk_decision_id ?? null
  const useOrderRiskDecision = !!riskDecisionId && !!exactOrder && anchorOrderIntent !== 'close'
  const riskProjectionBlocked = anchor.kind === 'position'
    && !!anchor.position.order_id
    && !useOrderRiskDecision
    && ordersReadState !== 'present'

  const riskQuery = useQuery<RiskDecisionData>({
    queryKey: useOrderRiskDecision
      ? ['order-risk-decision', exactOrder?.id, riskDecisionId]
      : ['risk-decision', proposalId],
    queryFn: async () => useOrderRiskDecision
      ? api.getOrderRiskDecision(exactOrder.id)
      : api.getProposalRiskDecision(proposalId as string),
    enabled: !!(useOrderRiskDecision || proposalId),
  })
  const riskData = riskQuery.data
  const risk = riskData?.decision
  const riskGap = riskData?.reason_code ?? (
    riskData?.status === 'advisory_only' ? 'advisory preflight only' : undefined
  )

  const strategyId = anchor.kind === 'order'
    ? proposal?.strategy_id ?? null
    : anchor.position.strategy_id ?? proposal?.strategy_id ?? null
  const opportunityId = proposal?.opportunity_id ?? null
  const entryParameters = parseEntryParameters(proposal)
  const exitReasoning = parseExitReasoning(resolvedPosition)

  const opportunityState: NodeState = proposalState === 'present'
    ? opportunityId ? 'present' : 'empty'
    : proposalState
  const strategyState: NodeState = strategyId
    ? anchor.kind === 'position' && anchor.position.strategy_id
      ? 'present'
      : proposalState === 'stale' ? 'stale' : 'present'
    : proposalId
      ? proposalState === 'present' ? 'empty' : proposalState
      : 'empty'
  const entryReasoningState: NodeState = proposalState === 'present'
    ? entryParameters ? 'present' : 'missing'
    : proposalState
  const riskEnabled = !!(useOrderRiskDecision || proposalId)
  const riskState: NodeState = riskProjectionBlocked
    ? ordersReadState
    : !riskEnabled
    ? 'empty'
    : riskData
      ? riskQuery.isError
        ? 'stale'
        : risk ? 'present' : 'missing'
      : riskQuery.isError
        ? 'unavailable'
        : riskQuery.isPending
          ? 'loading'
          : 'missing'

  const positionOrderState: NodeState = anchor.kind === 'order' || !anchor.position.order_id
    ? 'empty'
    : resolvedOrder
      ? ordersReadState === 'stale' ? 'stale' : 'present'
      : ordersReadState === 'present' ? 'present' : ordersReadState
  const positionFillState: NodeState = anchor.kind === 'order'
    ? 'empty'
    : resolvedOrder
      ? ordersReadState === 'stale'
        ? 'stale'
        : resolvedOrder.filled_at ? 'present' : 'empty'
      : anchor.position.order_id
        ? ordersReadState === 'present' ? 'missing' : ordersReadState
        : 'empty'
  const positionState: NodeState = anchor.kind === 'position'
    ? 'present'
    : resolvedPosition
      ? positionsReadState === 'stale' ? 'stale' : 'present'
      : positionsReadState === 'present' ? 'missing' : positionsReadState
  const closeReasoningState: NodeState = resolvedPosition
    ? anchor.kind === 'order' && positionsReadState === 'stale'
      ? 'stale'
      : resolvedPosition.status === 'open'
        ? 'empty'
        : exitReasoning ? 'present' : 'missing'
    : anchor.kind === 'order' && positionsReadState !== 'present'
      ? positionsReadState
      : 'empty'

  const rationaleText = (() => {
    const { thesis } = parseDecisionRationale(proposal?.agent_rationale)
    if (!thesis) return null
    return thesis.length > 350 ? thesis.slice(0, 350) + '…' : thesis
  })()

  return (
    <div className="causal-chain">
      {entryPlanId ? <>
        <CausalNode stage="Candidate" state={entryPlanState} facts={entryPlan ? <span className="mono">{entryPlan.candidate_id}</span> : undefined} note={entryPlanState === 'missing' ? 'exact Entry Plan detail not found' : undefined} onOpen={entryPlan ? () => navigate(`/universe?tab=candidates&candidate_id=${encodeURIComponent(entryPlan.candidate_id)}`) : undefined} />
        <CausalNode stage="Entry Plan" state={entryPlanState} facts={<span className="mono">{entryPlanId}</span>} note={entryPlanState === 'missing' ? 'exact Entry Plan detail not found' : undefined} onOpen={() => navigate(`/universe?tab=plans&plan_id=${encodeURIComponent(entryPlanId)}`)} />
      </> : <>
        {proposalId
          ? <CausalNode stage="Opportunity" state={opportunityState} facts={opportunityId ? <span className="mono">{opportunityId}</span> : undefined} note={proposalState === 'missing' ? 'exact Proposal detail not found' : undefined} onOpen={opportunityId ? () => navigate('/agent-activity') : undefined} />
          : <CausalNode stage="Opportunity" state="empty" />}
        <CausalNode stage="Strategy" state={strategyState} facts={strategyId ? <span className="mono">{strategyId}</span> : undefined} note={proposalState === 'missing' ? 'exact Proposal detail not found' : undefined} />
        {proposalId
          ? <CausalNode stage="Proposal" state={proposalState} facts={<><div className="mono cc-id">{proposalId}</div>{proposal && <div>{proposal.direction?.toUpperCase()} {fmtSymbol(proposal.symbol, proposal.market_type)} · {proposal.execution_mode}{rationaleText && <div className="cc-why">{rationaleText}</div>}</div>}</>} note={proposalState === 'missing' ? 'exact Proposal detail not found' : undefined} />
          : <CausalNode stage="Proposal" state="empty" />}
        <CausalNode
          stage="Entry Reasoning"
          state={entryReasoningState}
          facts={entryParameters ? <EntryReasoningFacts parameters={entryParameters} /> : undefined}
          note={entryReasoningState === 'missing' ? (proposal ? 'proposal predates parameter rationale' : 'exact Proposal detail not found') : undefined}
        />
      </>}

      {risk && !riskProjectionBlocked
        ? <CausalNode stage="Risk Decision" state={riskState} facts={<>{risk.id && <div className="mono cc-id">{risk.id}</div>}<div>{(risk.approved === true || risk.approved === 1 || risk.decision === 'approved') ? (anchorOrderIntent === 'close' ? 'entry approval (not close authorization)' : 'approved') : 'flagged'}{risk.risk_score != null && <span className="mono"> · score {risk.risk_score}</span>}{risk.message && <div className="text-muted text-sm">{risk.message}</div>}</div></>} onOpen={() => navigate('/risk')} />
        : <CausalNode stage="Risk Decision" state={riskState} note={riskState === 'missing' ? riskGap : undefined} />}

      {anchor.kind === 'order'
        ? <CausalNode stage="Order" state="present" facts={<><div className="mono cc-id">{anchor.order.id}</div><div>{orderIntentLabel(anchorOrderIntent ?? 'entry')} · {anchor.order.direction?.toUpperCase()} {anchor.order.quantity} {fmtSymbol(anchor.order.symbol, anchor.order.market_type)} @ {anchor.order.price ?? 'mkt'} · {anchor.order.status}</div></>} />
        : anchor.position.order_id
          ? <CausalNode stage="Order" state={positionOrderState} facts={resolvedOrder ? <><div className="mono cc-id">{anchor.position.order_id}</div><div>{resolvedOrder.direction?.toUpperCase()} {resolvedOrder.quantity} {fmtSymbol(resolvedOrder.symbol, resolvedOrder.market_type)} @ {resolvedOrder.price ?? 'mkt'} · {resolvedOrder.status}</div></> : <><div className="mono cc-id">{anchor.position.order_id}</div>{positionOrderState === 'present' && <div className="text-muted text-sm">(order detail not in recent list)</div>}</>} />
          : <CausalNode stage="Order" state="empty" />}

      {anchor.kind === 'order'
        ? anchor.order.filled_at
          ? <CausalNode stage="Fill" state="present" facts={<div>filled @ {anchor.order.price ?? '—'} · {anchor.order.filled_at.slice(0, 16)}{anchor.order.exchange_order_id && <div className="mono cc-id">ex: {anchor.order.exchange_order_id}</div>}</div>} />
          : <CausalNode stage="Fill" state="empty" />
        : <CausalNode stage="Fill" state={positionFillState} facts={resolvedOrder?.filled_at ? <div>filled @ {resolvedOrder.price ?? '—'} · {resolvedOrder.filled_at.slice(0, 16)}{resolvedOrder.exchange_order_id && <div className="mono cc-id">ex: {resolvedOrder.exchange_order_id}</div>}</div> : undefined} note={positionFillState === 'missing' ? 'order detail not in recent list' : undefined} />}

      {resolvedPosition
        ? <CausalNode stage="Position" state={positionState} facts={<><div className="mono cc-id">{resolvedPosition.id}</div><div>{resolvedPosition.direction?.toUpperCase()} {resolvedPosition.quantity} {fmtSymbol(resolvedPosition.symbol, resolvedPosition.market_type)} · {resolvedPosition.status}</div></>} />
        : anchor.kind === 'order'
          ? <CausalNode stage="Position" state={positionState} note={positionState === 'missing' ? 'no position record found for this order' : undefined} />
          : <CausalNode stage="Position" state="empty" />}

      <CausalNode
        stage="Close Reasoning"
        state={closeReasoningState}
        facts={exitReasoning ? <CloseReasoningFacts reasoning={exitReasoning} /> : undefined}
        note={closeReasoningState === 'missing' ? 'closed position predates exit explanation' : undefined}
      />
    </div>
  )
}

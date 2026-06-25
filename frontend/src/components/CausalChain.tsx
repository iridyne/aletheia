import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Order, Position, TradeProposal } from '../types'

/**
 * Causal-chain inspector (WEBUI_PAGE_MAP.md — cross-cutting, lives in Trades & Positions).
 *
 * Renders the immutable trust §1.1 chain:
 *   opportunity → strategy → proposal → risk_decision → order → fill → position
 *
 * HONESTY (trust §1.1 — do NOT fake the chain):
 *  - A Position stores proposal_id and order_id. Anchored on a position, the full chain
 *    resolves via proposal_id → proposal → opportunity_id/strategy_id, and order_id → order → fill.
 *  - For adopted positions (source="adopted"): proposal.opportunity_id is null and no risk_decision
 *    exists — these render as "none" (honest absence), not "not recorded" (data loss).
 *  - A null pointer on a record (e.g. proposal.opportunity_id == null) renders "none".
 *  - If a linked record isn't found in the fetched list, we say so with a note.
 */

type Anchor =
  | { kind: 'order'; order: Order }
  | { kind: 'position'; position: Position }

type NodeState = 'present' | 'empty' | 'missing'

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

export default function CausalChain({ anchor }: { anchor: Anchor }) {
  const navigate = useNavigate()
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const { data: orders = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })

  // Resolve the anchoring proposal id (both order and position carry it).
  const proposalId = anchor.kind === 'order'
    ? anchor.order.proposal_id
    : anchor.position.proposal_id ?? null
  const proposalFromList = proposalId
    ? (proposals as TradeProposal[]).find(p => p.id === proposalId) || null
    : null

  // If proposal not in the batch list (limit=50), fetch by ID directly.
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

  // Resolve the order for position anchors.
  const resolvedOrder: Order | null = anchor.kind === 'position' && anchor.position.order_id
    ? (orders as Order[]).find(o => o.id === anchor.position.order_id) ?? null
    : null

  // strategy_id: position has it directly; also available via proposal.
  const strategyId = anchor.kind === 'order'
    ? proposal?.strategy_id ?? null
    : anchor.position.strategy_id ?? proposal?.strategy_id ?? null
  const opportunityId = proposal?.opportunity_id ?? null

  return (
    <div className="causal-chain">
      {/* 1 — OPPORTUNITY */}
      {proposalId
        ? proposal
          ? <CausalNode
              stage="Opportunity"
              state={opportunityId ? 'present' : 'empty'}
              facts={<span className="mono">{opportunityId}</span>}
              onOpen={opportunityId ? () => navigate('/agent-activity') : undefined}
            />
          : <CausalNode stage="Opportunity" state="missing" note="proposal not in recent list" />
        : <CausalNode stage="Opportunity" state="empty" />}

      {/* 2 — STRATEGY */}
      <CausalNode
        stage="Strategy"
        state={strategyId ? 'present' : 'empty'}
        facts={<span className="mono">{strategyId}</span>}
        onOpen={strategyId ? () => navigate('/strategies') : undefined}
      />

      {/* 3 — PROPOSAL */}
      {proposalId
        ? <CausalNode
            stage="Proposal"
            state="present"
            facts={
              <>
                <div className="mono cc-id">{proposalId}</div>
                {proposal
                  ? <div>{proposal.direction?.toUpperCase()} {proposal.symbol} · {proposal.execution_mode}
                      {proposal.agent_rationale && <div className="cc-why">{proposal.agent_rationale.slice(0, 160)}{proposal.agent_rationale.length > 160 ? '…' : ''}</div>}
                    </div>
                  : <div className="text-muted text-sm">(detail not in recent list)</div>}
              </>
            }
            onOpen={() => navigate('/proposals')}
          />
        : <CausalNode stage="Proposal" state="empty" />}

      {/* 4 — RISK DECISION */}
      {risk
        ? <CausalNode
            stage="Risk Decision"
            state="present"
            facts={
              <>
                {anchor.kind === 'order' && anchor.order.risk_decision_id && <div className="mono cc-id">{anchor.order.risk_decision_id}</div>}
                <div>
                  {(risk.approved === true || risk.approved === 1 || risk.decision === 'approved') ? 'approved' : 'flagged'}
                  {risk.risk_score != null && <span className="mono"> · score {risk.risk_score}</span>}
                  {risk.message && <div className="text-muted text-sm">{risk.message}</div>}
                </div>
              </>
            }
            onOpen={() => navigate('/risk')}
          />
        : <CausalNode stage="Risk Decision" state="empty" />}

      {/* 5 — ORDER */}
      {anchor.kind === 'order'
        ? <CausalNode
            stage="Order"
            state="present"
            facts={
              <>
                <div className="mono cc-id">{anchor.order.id}</div>
                <div>{anchor.order.direction?.toUpperCase()} {anchor.order.quantity} {anchor.order.symbol} @ {anchor.order.price ?? 'mkt'} · {anchor.order.status}</div>
              </>
            }
          />
        : anchor.position.order_id
          ? <CausalNode
              stage="Order"
              state="present"
              facts={resolvedOrder ? (
                <>
                  <div className="mono cc-id">{anchor.position.order_id}</div>
                  <div>{resolvedOrder.direction?.toUpperCase()} {resolvedOrder.quantity} {resolvedOrder.symbol} @ {resolvedOrder.price ?? 'mkt'} · {resolvedOrder.status}</div>
                </>
              ) : (
                <>
                  <div className="mono cc-id">{anchor.position.order_id}</div>
                  <div className="text-muted text-sm">(order detail not in recent list)</div>
                </>
              )}
            />
          : <CausalNode stage="Order" state="empty" />}

      {/* 6 — FILL */}
      {anchor.kind === 'order'
        ? anchor.order.filled_at
          ? <CausalNode
              stage="Fill"
              state="present"
              facts={
                <div>
                  filled @ {anchor.order.price ?? '—'} · {anchor.order.filled_at.slice(0, 16)}
                  {anchor.order.exchange_order_id && <div className="mono cc-id">ex: {anchor.order.exchange_order_id}</div>}
                </div>
              }
            />
          : <CausalNode stage="Fill" state="empty" />
        : resolvedOrder?.filled_at
          ? <CausalNode
              stage="Fill"
              state="present"
              facts={
                <div>
                  filled @ {resolvedOrder.price ?? '—'} · {resolvedOrder.filled_at.slice(0, 16)}
                  {resolvedOrder.exchange_order_id && <div className="mono cc-id">ex: {resolvedOrder.exchange_order_id}</div>}
                </div>
              }
            />
          : <CausalNode stage="Fill" state="empty" />}

      {/* 7 — POSITION */}
      {anchor.kind === 'position'
        ? <CausalNode
            stage="Position"
            state="present"
            facts={
              <>
                <div className="mono cc-id">{anchor.position.id}</div>
                <div>{anchor.position.direction?.toUpperCase()} {anchor.position.quantity} {anchor.position.symbol} · {anchor.position.status}</div>
              </>
            }
          />
        : <CausalNode stage="Position" state="missing" note="orders store no resulting-position link" />}
    </div>
  )
}

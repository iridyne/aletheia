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
 *  - An Order stores proposal_id + risk_decision_id, so anchored on an order the chain is
 *    near-complete UPSTREAM. The order→position direction has no stored pointer, so the
 *    position node is shown as "not recorded" rather than guessed by symbol.
 *  - A Position stores only strategy_id (the known schema gap). Anchored on a position,
 *    everything upstream of the strategy is shown as "not recorded" — never inferred.
 *  - A null pointer on a record (e.g. proposal.opportunity_id == null) renders "none".
 *  - Proposal/risk detail is read from the proposals list + /proposals/{id}/risk-decision;
 *    if the proposal isn't in the returned list we say so instead of inventing it.
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

  // Resolve the anchoring proposal id (orders carry it; positions do not).
  const proposalId = anchor.kind === 'order' ? anchor.order.proposal_id : null
  const proposal = proposalId
    ? (proposals as TradeProposal[]).find(p => p.id === proposalId) || null
    : null

  const { data: riskData } = useQuery({
    queryKey: ['risk-decision', proposalId],
    queryFn: () => api.getProposalRiskDecision(proposalId as string),
    enabled: !!proposalId,
  })
  const risk = riskData?.decision

  // strategy_id is reachable from either anchor (order→proposal→strategy, or position directly).
  const strategyId = anchor.kind === 'order' ? proposal?.strategy_id ?? null : anchor.position.strategy_id
  const opportunityId = proposal?.opportunity_id ?? null

  return (
    <div className="causal-chain">
      {/* 1 — OPPORTUNITY */}
      {anchor.kind === 'order'
        ? proposal
          ? <CausalNode
              stage="Opportunity"
              state={opportunityId ? 'present' : 'empty'}
              facts={<span className="mono">{opportunityId}</span>}
              onOpen={opportunityId ? () => navigate('/agent-activity') : undefined}
            />
          : <CausalNode stage="Opportunity" state="missing" note="proposal not in recent list" />
        : <CausalNode stage="Opportunity" state="missing" note="position stores no proposal link" />}

      {/* 2 — STRATEGY */}
      <CausalNode
        stage="Strategy"
        state={strategyId ? 'present' : (anchor.kind === 'position' ? 'empty' : 'missing')}
        facts={<span className="mono">{strategyId}</span>}
        onOpen={strategyId ? () => navigate('/strategies') : undefined}
        note={anchor.kind === 'order' && !proposal ? 'proposal not in recent list' : undefined}
      />

      {/* 3 — PROPOSAL */}
      {anchor.kind === 'order'
        ? anchor.order.proposal_id
          ? <CausalNode
              stage="Proposal"
              state="present"
              facts={
                <>
                  <div className="mono cc-id">{anchor.order.proposal_id}</div>
                  {proposal
                    ? <div>{proposal.direction?.toUpperCase()} {proposal.symbol} · {proposal.execution_mode}
                        {proposal.agent_rationale && <div className="cc-why">{proposal.agent_rationale.slice(0, 160)}{proposal.agent_rationale.length > 160 ? '…' : ''}</div>}
                      </div>
                    : <div className="text-muted text-sm">(detail not in recent list)</div>}
                </>
              }
              onOpen={() => navigate('/proposals')}
            />
          : <CausalNode stage="Proposal" state="empty" />
        : <CausalNode stage="Proposal" state="missing" note="position stores no proposal link" />}

      {/* 4 — RISK DECISION */}
      {anchor.kind === 'order'
        ? anchor.order.risk_decision_id || risk
          ? <CausalNode
              stage="Risk Decision"
              state="present"
              facts={
                <>
                  {anchor.order.risk_decision_id && <div className="mono cc-id">{anchor.order.risk_decision_id}</div>}
                  {risk
                    ? <div>
                        {(risk.approved === true || risk.approved === 1 || risk.decision === 'approved') ? 'approved' : 'flagged'}
                        {risk.risk_score != null && <span className="mono"> · score {risk.risk_score}</span>}
                        {risk.message && <div className="text-muted text-sm">{risk.message}</div>}
                      </div>
                    : <div className="text-muted text-sm">(verdict not recorded)</div>}
                </>
              }
              onOpen={() => navigate('/risk')}
            />
          : <CausalNode stage="Risk Decision" state="empty" />
        : <CausalNode stage="Risk Decision" state="missing" note="position stores no proposal link" />}

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
        : <CausalNode stage="Order" state="missing" note="position stores no order link" />}

      {/* 6 — FILL (embedded in order; no separate fills record) */}
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
        : <CausalNode stage="Fill" state="missing" note="position stores no order link" />}

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

import { useMemo } from 'react'
import { parseDecisionRationale } from '../lib/decisionReasoning'
import { DecisionRationaleView } from './PositionDecisionSummary'

/**
 * Structured rationale renderer for a trade proposal.
 * Consolidated with PositionDecisionSummary per ponytail YAGNI audit.
 */
export function ProposalRationaleDetail({ rationale }: { rationale: string | null }) {
  const { thesis, meta } = useMemo(() => parseDecisionRationale(rationale), [rationale])
  if (!rationale) return <p className="feed-thesis">No rationale provided.</p>
  if (!meta) return <p className="feed-thesis">{thesis || 'No rationale provided.'}</p>
  return (
    <div className="decision-detail">
      <DecisionRationaleView thesis={thesis} meta={meta as Record<string, unknown> | null} raw={rationale} />
    </div>
  )
}

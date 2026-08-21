import { useMemo } from 'react'
import { ConvictionMeter } from './ConvictionMeter'
import { parseDecisionRationale, stringList } from '../lib/decisionReasoning'

/**
 * Structured rationale renderer for a trade proposal.
 *
 * The rationale string carries the human-readable thesis followed by an
 * optional JSON tail delimited by `\n---\n`. The tail encodes the debate
 * meta (conviction, bull/bear scores) plus supporting/opposing signals and
 * risk-guard trace. When present, we render the full decision-detail card;
 * when absent (older proposals or malformed JSON) we fall back to a plain
 * thesis paragraph.
 *
 * Previously lived inside pages/Proposals.tsx as an exported helper. That
 * page has been superseded by /blotter (2026-07-18 migration) but AgentFeed
 * still needs this component to render proposal detail inside its own
 * drawer — moved here so the source of truth outlives the retired page.
 *
 * Uses the shared parseDecisionRationale() parser from decisionReasoning.ts
 * to guarantee identical split behaviour across all callers.
 */

// Keep the local interface for the structured rendering layer.
interface DecisionMeta {
  conviction?: number
  debate_ran?: boolean
  debate_bull?: number | null
  debate_bear?: number | null
  supporting?: unknown
  opposing?: unknown
  guards?: { guards_run?: number; blocks?: number; warnings?: number; details?: unknown[] }
}

export function ProposalRationaleDetail({ rationale }: { rationale: string | null }) {
  const { thesis: text, meta: rawMeta } = useMemo(
    () => parseDecisionRationale(rationale),
    [rationale],
  )
  // Treat missing, non-object, or array JSON tails as absent.
  const meta: DecisionMeta | null =
    rawMeta !== null && typeof rawMeta === 'object' && !Array.isArray(rawMeta)
      ? (rawMeta as DecisionMeta)
      : null

  if (!rationale) return <p className="feed-thesis">No rationale provided.</p>
  if (!meta) return <p className="feed-thesis">{text || 'No rationale provided.'}</p>

  const supporting = stringList(meta.supporting)
  const opposing = stringList(meta.opposing)
  const guards = meta.guards && typeof meta.guards === 'object' && !Array.isArray(meta.guards)
    ? meta.guards
    : null
  const guardDetails: { guard?: unknown; reason?: unknown }[] =
    Array.isArray(guards?.details)
      ? (guards!.details as { guard?: unknown; reason?: unknown }[])
      : []

  return (
    <div className="decision-detail">
      <ConvictionMeter conviction={meta.conviction ?? 0} debateRan={meta.debate_ran ?? false}
        bull={meta.debate_bull ?? null} bear={meta.debate_bear ?? null} />

      <div className="decision-section mt-2">
        <div className="decision-section-label">Thesis</div>
        <p className="decision-thesis">{text}</p>
      </div>

      {supporting.length > 0 && (
        <div className="decision-section mt-2">
          <div className="decision-section-label">Supporting Signals</div>
          <ul className="decision-signal-list">
            {supporting.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {opposing.length > 0 && (
        <div className="decision-section mt-2">
          <div className="decision-section-label">Opposing Signals</div>
          <ul className="decision-signal-list opposing">
            {opposing.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {guards && (
        <div className="decision-section mt-2">
          <div className="decision-section-label">Risk Guards</div>
          <span className="text-muted text-sm">
            {guards.guards_run ?? 0} checked · {guards.blocks ?? 0} blocked · {guards.warnings ?? 0} warned
          </span>
          {guardDetails.length > 0 && (
            <ul className="decision-signal-list opposing">
              {guardDetails.map((d, i) => <li key={i}>{String(d?.guard ?? '')}: {String(d?.reason ?? '')}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

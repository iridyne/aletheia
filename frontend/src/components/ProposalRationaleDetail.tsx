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
 */

export function ProposalRationaleDetail({ rationale }: { rationale: string | null }) {
  const { thesis, meta } = useMemo(() => parseDecisionRationale(rationale), [rationale])
  if (!rationale) return <p className="feed-thesis">No rationale provided.</p>
  if (!meta) return <p className="feed-thesis">{thesis || 'No rationale provided.'}</p>

  const conviction = typeof meta.conviction === 'number' ? meta.conviction : 0
  const debateRan = Boolean(meta.debate_ran)
  const bull = typeof meta.debate_bull === 'number' ? meta.debate_bull : null
  const bear = typeof meta.debate_bear === 'number' ? meta.debate_bear : null
  const supporting = stringList(meta.supporting)
  const opposing = stringList(meta.opposing)

  // guards must have the expected shape; wrong-shaped → absent
  const rawGuards = meta.guards
  const guards = (
    rawGuards !== null &&
    typeof rawGuards === 'object' &&
    !Array.isArray(rawGuards) &&
    typeof (rawGuards as Record<string, unknown>).guards_run === 'number'
  ) ? rawGuards as { guards_run: number; blocks: number; warnings: number; details: unknown[] } : null

  return (
    <div className="decision-detail">
      <ConvictionMeter conviction={conviction} debateRan={debateRan}
        bull={bull} bear={bear} />

      <div className="decision-section mt-2">
        <div className="decision-section-label">Thesis</div>
        <p className="decision-thesis">{thesis}</p>
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
            {guards.guards_run} checked · {guards.blocks} blocked · {guards.warnings} warned
          </span>
          {stringList(guards.details, 50).length > 0 && (
            <ul className="decision-signal-list opposing">
              {(Array.isArray(guards.details) ? guards.details : []).map((d: unknown, i: number) => {
                const item = d !== null && typeof d === 'object' ? d as Record<string, unknown> : {}
                return <li key={i}>{String(item.guard ?? '')}: {String(item.reason ?? '')}</li>
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

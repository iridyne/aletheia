import { useMemo } from 'react'
import { ConvictionMeter } from './ConvictionMeter'

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

interface DecisionMeta {
  conviction: number
  debate_ran: boolean
  debate_bull: number | null
  debate_bear: number | null
  supporting: string[]
  opposing: string[]
  guards: { guards_run: number; blocks: number; warnings: number; details: any[] }
}

export function parseDecisionMeta(rationale: string | null): { text: string; meta: DecisionMeta | null } {
  if (!rationale) return { text: '', meta: null }
  const sep = rationale.indexOf('\n---\n')
  if (sep === -1) return { text: rationale, meta: null }
  const text = rationale.slice(0, sep)
  try {
    const meta = JSON.parse(rationale.slice(sep + 5)) as DecisionMeta
    return { text, meta }
  } catch {
    // Defense in depth: a malformed/truncated meta JSON must NEVER leak the
    // raw "---{...broken json...}" tail to the user. Show only the clean
    // thesis (the part before the separator), drop the unparseable
    // remainder.
    return { text, meta: null }
  }
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

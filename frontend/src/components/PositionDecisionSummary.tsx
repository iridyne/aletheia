import type { Position } from '../types'
import { parseDecisionRationale, stringList } from '../lib/decisionReasoning'
import { humanize } from '../lib/format'
import { Badge } from './ui'

// Compact operator-facing projection of position.decision_reasoning.
// The persisted format is "{thesis}\n---\n{json_meta}" — the raw dump was the
// old failure mode. This surfaces ONLY decision-level context (thesis, action,
// conviction, supporting/opposing/bear, debate); pricing, sizing, entry
// parameters and thesis-lineage evidence are owned by other drawer sections.

export function PositionDecisionSummary({ position }: { position: Position }) {
  const { thesis, meta } = parseDecisionRationale(position.decision_reasoning)
  if (!thesis && !meta) {
    // Neither half survived — decision_reasoning was empty. The parent already
    // guards on truthiness, but a whitespace-only record can still land here.
    return <p className="text-muted text-sm">No AI decision recorded.</p>
  }

  const conviction = typeof meta?.conviction === 'number' ? meta.conviction : null
  const supporting = stringList(meta?.supporting_signals ?? meta?.supporting)
  const opposing = stringList(meta?.opposing_signals ?? meta?.opposing)
  const bearCase = typeof meta?.bear_case === 'string' ? meta.bear_case.trim() : ''
  const debateRan = meta?.debate_ran === true
  const debateBull = typeof meta?.debate_bull === 'number' ? meta.debate_bull : null
  const debateBear = typeof meta?.debate_bear === 'number' ? meta.debate_bear : null
  const debateInitial = typeof meta?.debate_initial_conviction === 'number' ? meta.debate_initial_conviction : null

  return (
    <div className="decision-summary">
      <div className="decision-summary-head">
        <Badge tone={directionTone(position.direction)}>{humanize(position.direction || 'unknown')}</Badge>
        {conviction != null && (
          <span className="decision-conviction" title="AI conviction 0–100">
            conviction <strong className="mono">{conviction}</strong>
          </span>
        )}
      </div>

      {thesis && <p className="decision-thesis">{thesis}</p>}

      {supporting.length > 0 && (
        <SignalList label="Supporting" values={supporting} tone="good" />
      )}
      {(opposing.length > 0 || bearCase) && (
        <div className="decision-opposing">
          {opposing.length > 0 && <SignalList label="Opposing" values={opposing} tone="bad" />}
          {bearCase && (
            <div className="decision-bear-case">
              <span className="text-muted">Primary risk:</span> {bearCase}
            </div>
          )}
        </div>
      )}

      {debateRan && (debateBull != null || debateBear != null || debateInitial != null) && (
        <div className="decision-debate text-muted text-sm">
          debate ·
          {debateInitial != null && <> initial <span className="mono">{debateInitial}</span></>}
          {debateBull != null && <> · bull <span className="mono">{debateBull}</span></>}
          {debateBear != null && <> · bear <span className="mono">{debateBear}</span></>}
        </div>
      )}

      {thesis && !meta && position.decision_reasoning?.includes('\n---\n') && (
        <p className="text-muted text-xs">Structured decision metadata unavailable for this record.</p>
      )}
    </div>
  )
}

function SignalList({ label, values, tone }: { label: string; values: string[]; tone: 'good' | 'bad' }) {
  return (
    <div className={`decision-signals decision-signals-${tone}`}>
      <span className="text-muted">{label}:</span> {values.join('; ')}
    </div>
  )
}

function directionTone(direction: string | undefined): 'green' | 'red' | 'muted' {
  const d = (direction || '').toLowerCase()
  if (d === 'long' || d === 'buy') return 'green'
  if (d === 'short' || d === 'sell') return 'red'
  return 'muted'
}

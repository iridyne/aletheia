// Shared parser for the "{thesis}\n---\n{json}" audit format used in both
// TradeProposal.agent_rationale and Position.decision_reasoning. The persisted
// source stays whole (see trading_decision.py:1714); this helper is the ONLY
// place the frontend splits the two halves back out.

export type JsonRecord = Record<string, unknown>

export interface DecisionRationale {
  thesis: string
  meta: JsonRecord | null
}

const SEPARATOR = '\n---\n'

export function parseDecisionRationale(raw: string | null | undefined): DecisionRationale {
  if (!raw) return { thesis: '', meta: null }
  const at = raw.indexOf(SEPARATOR)
  if (at < 0) return { thesis: raw, meta: null }
  const thesis = raw.slice(0, at)
  try {
    const parsed = JSON.parse(raw.slice(at + SEPARATOR.length))
    const meta = parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as JsonRecord
      : null
    return { thesis, meta }
  } catch {
    return { thesis, meta: null }
  }
}

export function stringList(value: unknown, max = 5): string[] {
  if (!Array.isArray(value)) return []
  const out: string[] = []
  for (const item of value) {
    if (out.length >= max) break
    if (item == null || typeof item === 'object') continue
    const s = typeof item === 'string' ? item.trim() : String(item)
    if (s) out.push(s)
  }
  return out
}

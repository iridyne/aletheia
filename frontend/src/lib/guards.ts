export type GuardEffect = {
  block_candidate: boolean
  score_penalty: number
}

export type GuardRow = {
  guard_id: string
  title: string
  description?: string
  status?: string
  lifecycle_status?: string
  origin?: 'seed' | 'manual' | 'learned'
  scope?: string
  enforcement?: string
  severity?: string
  score_penalty?: number
  block_candidate?: boolean
  shadow_only?: boolean
  proposed_effect?: GuardEffect
  activation_effect?: GuardEffect
  experiment_id?: string | null
  experiment_stage?: string | null
  experiment_status?: string | null
  assignment?: string | null
  authority_granted?: boolean
  authority_scope?: string
  evaluation_enabled?: boolean
  actual_effect?: GuardEffect
  predicates?: Array<{ field?: string; op?: string; value?: unknown }>
  evidence_refs?: string[]
  expires_at?: string
  tags?: string[]
}

function effectText(effect?: GuardEffect): string {
  if (!effect) return 'none'
  const parts = []
  if (effect.block_candidate) parts.push('block candidate')
  if (effect.score_penalty !== 0) parts.push(`score penalty ${effect.score_penalty}`)
  return parts.join(', ') || 'none'
}

export function guardActivationConfirmation(guard: GuardRow): string {
  const observeOnly = guard.origin === 'learned' || guard.shadow_only
  const action = observeOnly
    ? `Promote this ${guard.origin === 'learned' ? 'learned ' : ''}${guard.shadow_only ? 'shadow ' : ''}guard to active observation? This action does not grant blocking or score-penalty authority.${guard.origin === 'learned' ? ' If this path is separately admitted in the future, an exact authorized treatment candidate may apply the proposed effect shown below.' : ''}`
    : 'Activate this owner-authored guard with its configured effect?'
  return [
    action,
    `Guard: ${guard.title} (${guard.guard_id})`,
    `Scope: ${guard.scope || 'not specified'}`,
    `Predicates: ${guard.predicates?.length ? JSON.stringify(guard.predicates) : 'none'}`,
    `Proposed effect: ${effectText(guard.proposed_effect)}`,
    `Activation effect: ${effectText(guard.activation_effect || guard.proposed_effect)}`,
    `Evidence: ${guard.evidence_refs?.length ? guard.evidence_refs.join(', ') : 'none'}`,
    `Expires: ${guard.expires_at || 'not specified'}`,
  ].join('\n')
}

export function guardEffectText(effect?: GuardEffect): string {
  return effectText(effect)
}

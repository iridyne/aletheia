import { describe, expect, it } from 'vitest'
import { guardActivationConfirmation, type GuardRow } from './guards'

describe('guardActivationConfirmation', () => {
  it('previews a shadow learned candidate without implying enforcement', () => {
    const guard: GuardRow = {
      guard_id: 'auto_momentum_btc',
      title: 'Momentum losses',
      origin: 'learned',
      shadow_only: true,
      scope: 'paper_entry',
      predicates: [{ field: 'scanner_family', op: 'eq', value: 'momentum' }],
      proposed_effect: { block_candidate: true, score_penalty: 12 },
      evidence_refs: ['trade-1'],
      expires_at: '2026-08-30T00:00:00Z',
    }

    const message = guardActivationConfirmation(guard)
    expect(message).toContain('active observation')
    expect(message).toContain('does not grant blocking or score-penalty authority')
    expect(message).toContain('exact authorized treatment candidate may apply the proposed effect')
    expect(message).toContain('block candidate, score penalty 12')
    expect(message).toContain('scanner_family')
    expect(message).toContain('trade-1')
    expect(message).toContain('2026-08-30')
  })

  it('shows the exact post-activation defaults for an owner candidate', () => {
    const message = guardActivationConfirmation({
      guard_id: 'manual-new-guard',
      title: 'Owner candidate',
      origin: 'manual',
      scope: 'paper_entry',
      proposed_effect: { block_candidate: false, score_penalty: 0 },
      activation_effect: { block_candidate: true, score_penalty: 10 },
    })

    expect(message).toContain('Activate this owner-authored guard')
    expect(message).toContain('Activation effect: block candidate, score penalty 10')
    expect(message).not.toContain('Activation effect: none')
  })

  it('names a manual shadow candidate as active observation', () => {
    const message = guardActivationConfirmation({
      guard_id: 'manual-shadow',
      title: 'Owner shadow candidate',
      origin: 'manual',
      shadow_only: true,
      activation_effect: { block_candidate: false, score_penalty: 0 },
    })

    expect(message).toContain('shadow guard to active observation')
    expect(message).toContain('Activation effect: none')
  })
})

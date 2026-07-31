import { describe, expect, it } from 'vitest'
import {
  PORTFOLIO_CONTEXT_LABEL,
  learningPulseValue,
  learningReadState,
  learningReadValue,
  optionalBooleanLabel,
} from './learningWorkbench'

describe('learning workbench read honesty', () => {
  it('keeps loading and failed reads distinct from genuine empty data', () => {
    expect(learningReadState(true, false)).toBe('loading')
    expect(learningReadState(false, true)).toBe('unavailable')
    expect(learningReadState(false, true, true)).toBe('stale')
    expect(learningReadState(false, false, true, true)).toBe('partial')
    expect(learningReadState(false, false)).toBe('available')
    expect(learningReadValue('loading', 0)).toBe('Loading...')
    expect(learningReadValue('unavailable', 0)).toBe('Unavailable')
    expect(learningReadValue('stale', 7)).toBe(7)
    expect(learningReadValue('partial', 7)).toBe(7)
    expect(learningPulseValue('stale', 7)).toBe('7 · Stale')
    expect(learningPulseValue('partial', 7)).toBe('7 · Partial')
    expect(learningReadValue('available', 0)).toBe(0)
    expect(optionalBooleanLabel(true)).toBe('Yes')
    expect(optionalBooleanLabel(false)).toBe('No')
    expect(optionalBooleanLabel(null)).toBe('Unavailable')
  })

  it('labels portfolio performance as context rather than Learning attribution', () => {
    expect(PORTFOLIO_CONTEXT_LABEL).toBe('Portfolio context · not Learning attribution')
  })
})

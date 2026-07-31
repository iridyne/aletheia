export type LearningReadState = 'loading' | 'available' | 'unavailable' | 'stale' | 'partial'

export const PORTFOLIO_CONTEXT_LABEL = 'Portfolio context · not Learning attribution'

export function learningReadState(
  isLoading: boolean,
  isError: boolean,
  hasData = false,
  isPartial = false,
): LearningReadState {
  if (isLoading) return 'loading'
  if (isError) return hasData ? 'stale' : 'unavailable'
  return isPartial ? 'partial' : 'available'
}

export function learningReadValue<T>(state: LearningReadState, value: T): T | string {
  if (state === 'loading') return 'Loading...'
  if (state === 'unavailable') return 'Unavailable'
  return value
}

export function learningPulseValue<T>(state: LearningReadState, value: T): T | string {
  const displayed = learningReadValue(state, value)
  if (state === 'stale') return `${String(displayed)} · Stale`
  if (state === 'partial') return `${String(displayed)} · Partial`
  return displayed
}

export function optionalBooleanLabel(value: boolean | null | undefined): 'Yes' | 'No' | 'Unavailable' {
  if (value == null) return 'Unavailable'
  return value ? 'Yes' : 'No'
}

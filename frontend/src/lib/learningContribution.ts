import type { LearningContributionProjection } from '../api/client'

export type ContributionViewState =
  | 'loading'
  | 'unavailable'
  | 'empty'
  | 'partial'
  | 'stale'
  | LearningContributionProjection['items'][number]['measurement']['state']

export function deriveContributionViewState(input: {
  loading: boolean
  failed: boolean
  data?: LearningContributionProjection
}): ContributionViewState {
  if (input.loading) return 'loading'
  if (input.failed || !input.data || input.data.availability === 'unavailable') {
    return 'unavailable'
  }
  if (input.data.items.length === 0) return 'empty'
  if (input.data.data_freshness.state === 'stale') return 'stale'
  if (input.data.availability === 'partial') return 'partial'
  return input.data.items[0].measurement.state
}

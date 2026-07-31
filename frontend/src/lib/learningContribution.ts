import type { LearningContributionProjection } from '../api/client'

export type ContributionViewState =
  | 'loading'
  | 'unavailable'
  | 'empty'
  | 'partial'
  | 'stale'
  | LearningContributionProjection['items'][number]['measurement']['state']

export function contributionPartialCopy(input: {
  fetchFailed: boolean
  itemStale: boolean
  selectedPathPartial: boolean
  operationsFailed: boolean
}): { title: string; detail: string } {
  if (input.fetchFailed || input.itemStale) {
    return {
      title: 'Measurement is stale',
      detail: 'The last successful evidence remains visible, but the latest refresh failed.',
    }
  }
  if (input.selectedPathPartial) {
    return {
      title: 'Measurement is partial',
      detail: 'The selected path evidence is preserved, but it must not be treated as a complete current effectiveness claim.',
    }
  }
  if (input.operationsFailed) {
    return {
      title: 'Projection is partial',
      detail: 'The selected path measurement remains available, but one or more operational health reads failed.',
    }
  }
  return {
    title: 'Projection is partial',
    detail: 'The selected path measurement remains available, but another projection section is unavailable.',
  }
}

export function authorityLabel(
  enabled: boolean,
  blockReason: string | null,
): 'Enabled' | 'Blocked' | 'Unavailable' {
  if (enabled) return 'Enabled'
  return blockReason === 'authority_projection_failed' ? 'Unavailable' : 'Blocked'
}

export function deriveContributionViewState(input: {
  loading: boolean
  failed: boolean
  data?: LearningContributionProjection
  pathId?: string
}): ContributionViewState {
  if (input.loading) return 'loading'
  if (!input.data || input.data.availability === 'unavailable') {
    return 'unavailable'
  }
  const pathState = input.pathId
    ? input.data.path_states.find(candidate => candidate.path_id === input.pathId)
    : undefined
  if (pathState?.availability === 'unavailable') return 'unavailable'
  if (input.failed) return 'stale'
  if (pathState?.state === 'empty') return 'empty'
  if (pathState?.data_freshness.state === 'stale') return 'stale'
  if (pathState?.availability === 'partial') return 'partial'
  const item = input.pathId
    ? input.data.items.find(candidate => candidate.path_id === input.pathId)
    : input.data.items[0]
  if (!item) return 'empty'
  if (item.availability === 'unavailable') return 'unavailable'
  if (item.data_freshness.state === 'stale') return 'stale'
  if (item.availability === 'partial') return 'partial'
  return item.measurement.state
}

export function measuredContributionPathIds(
  data: LearningContributionProjection,
): string[] {
  return data.paths
    .filter(path => path.measurement_supported)
    .map(path => path.path_id)
}

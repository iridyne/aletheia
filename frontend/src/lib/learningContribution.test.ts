import { describe, expect, it } from 'vitest'
import type { LearningContributionProjection } from '../api/client'
import { deriveContributionViewState } from './learningContribution'

function projection(
  overrides: Partial<LearningContributionProjection> = {},
): LearningContributionProjection {
  return {
    schema_version: 'lco-v1',
    generated_at: '2026-07-24T00:00:00Z',
    availability: 'available',
    unavailable_reason: null,
    data_freshness: {
      last_evaluated_at: null,
      age_sec: null,
      stale_after_sec: 7200,
      state: 'fresh',
    },
    paths: [],
    items: [{
      path_id: 'universe_family_calibration',
      hypothesis_id: 'hypothesis',
      experiment_id: 'experiment',
      stage: 'shadow',
      experiment_status: 'active',
      allocation_pct: 10,
      current_identity: {
        policy_epoch: 'epoch',
        target_horizon: '1h',
        outcome_contract_version: 'v1',
        metric_version: 'v2',
        behavior_contract_hash: 'behavior-contract',
        code_commit: 'commit',
        matches_running_cohort: true,
        mismatch_reason: null,
      },
      measurement: {
        state: 'insufficient_data',
        reason: 'no_actual_intervention',
        baseline: null,
        observed: null,
        delta: null,
        confidence_low: null,
        confidence_high: null,
        evaluated_at: null,
        window_start: null,
        window_end: null,
      },
      samples: {
        raw_exposures: 999,
        treatment_assignment_units: 999,
        control_assignment_units: 999,
        required_per_arm: 1,
        complete_assignment_units: 999,
        cluster_members: 999,
        selected_members: 999,
      },
      integrity: {
        source_code_commits: ['commit'],
        source_code_commit_unknown_n: 0,
        code_commit_provenance_complete: true,
        outcome_source_member_counts: {},
        allocation_selection_source_member_counts: {},
        matching_mirror_rows: 0,
        exclusion_reasons: {},
        invalid_reasons: {},
        conflict_count: 0,
        evidence_conflicts: [],
        evidence_conflict_provenance_truncated: false,
        harm_state: 'indeterminate',
        harm_indeterminate_reason: 'insufficient_samples',
        harm_breached: false,
        breach_reasons: [],
      },
      lifecycle: {
        recommended_action: 'hold',
        recommendation_reason: null,
        authority_enabled: false,
        authority_block_reason: 'read_only_projection',
        mutation_available: false,
        mutation_unavailable_reason: 'authenticated_owner_admission_unavailable',
      },
      latest_historical_measurement: null,
    }],
    operations: null,
    ...overrides,
  }
}

describe('deriveContributionViewState', () => {
  it('uses the backend measurement verdict rather than large activity counts', () => {
    expect(deriveContributionViewState({
      loading: false,
      failed: false,
      data: projection(),
    })).toBe('insufficient_data')
  })

  it('keeps loading, fetch failure, genuine empty, stale and partial distinct', () => {
    expect(deriveContributionViewState({ loading: true, failed: false })).toBe('loading')
    expect(deriveContributionViewState({ loading: false, failed: true })).toBe('unavailable')
    expect(deriveContributionViewState({
      loading: false,
      failed: false,
      data: projection({ items: [] }),
    })).toBe('empty')
    expect(deriveContributionViewState({
      loading: false,
      failed: false,
      data: projection({
        data_freshness: {
          last_evaluated_at: '2026-07-23T00:00:00Z',
          age_sec: 9000,
          stale_after_sec: 7200,
          state: 'stale',
        },
      }),
    })).toBe('stale')
    expect(deriveContributionViewState({
      loading: false,
      failed: false,
      data: projection({ availability: 'partial' }),
    })).toBe('partial')
  })
})

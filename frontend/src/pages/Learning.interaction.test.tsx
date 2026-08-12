// @vitest-environment happy-dom

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

const queryProbe = vi.hoisted(() => ({ keys: [] as Array<Array<string | number>>, catalogTotal: 2 }))

const knowledgeRows = vi.hoisted(() => ({
  all: [
    { id: 'all-case', title: 'Visible case study', item_type: 'trade_case_study', status: 'active', created_at: '2026-08-01T00:00:00Z' },
    { id: 'all-action', title: 'Visible system action', item_type: 'system_action', status: 'active', created_at: '2026-08-01T00:00:00Z' },
  ],
  research: [{ id: 'research', title: 'Visible research note', item_type: 'research_note', status: 'active', created_at: '2026-08-01T00:00:00Z' }],
  strategy: [{ id: 'strategy', title: 'Visible strategy memory', item_type: 'strategy_memory', status: 'active', created_at: '2026-08-01T00:00:00Z' }],
  reviews: [{ id: 'review', title: 'Visible daily review', item_type: 'daily_review', status: 'active', created_at: '2026-08-01T00:00:00Z' }],
  dreaming: [{ id: 'dreaming', title: 'Visible dreaming log', item_type: 'dreaming_log', status: 'active', created_at: '2026-08-01T00:00:00Z' }],
  lessons: [{ lesson_id: 'lesson', summary: 'Visible post-trade lesson', category: 'exit', severity: 'warning', status: 'proposed', created_at: '2026-08-01T00:00:00Z' }],
  guardCandidates: [{ guard_id: 'candidate', title: 'Visible guard candidate', severity: 'warning', lifecycle_status: 'candidate', scope: 'global', actual_effect: {} }],
  guards: [{ guard_id: 'guard', title: 'Visible active guard', severity: 'warning', lifecycle_status: 'active', scope: 'global', actual_effect: {} }],
}))

const contribution = vi.hoisted(() => ({
  schema_version: 'lco-v1', generated_at: '2026-08-12T00:00:00Z', availability: 'available', unavailable_reason: null,
  data_freshness: { last_evaluated_at: '2026-08-12T00:00:00Z', age_sec: 0, stale_after_sec: 7200, state: 'fresh' },
  paths: [{ path_id: 'universe_family_calibration', producer: 'universe', consumer: 'ranker', consumer_wired: true, settings_supported: true, measurement_supported: true, authority: 'paper', allowed_effects: [], fallback: 'static', outcome_source: 'filter_candidate_outcomes' }],
  path_states: [{ path_id: 'universe_family_calibration', availability: 'available', unavailable_reason: null, state: 'active', data_freshness: { last_evaluated_at: '2026-08-12T00:00:00Z', age_sec: 0, stale_after_sec: 7200, state: 'fresh' } }],
  items: [{
    path_id: 'universe_family_calibration', availability: 'available', data_freshness: { last_evaluated_at: '2026-08-12T00:00:00Z', age_sec: 0, stale_after_sec: 7200, state: 'fresh' },
    hypothesis_id: 'hypothesis', experiment_id: 'experiment', stage: 'paper_10', experiment_status: 'active', allocation_pct: 10,
    current_identity: { policy_epoch: 'epoch', target_horizon: '4h', outcome_contract_version: 'v1', metric_version: 'v10', behavior_contract_hash: 'hash', code_commit: 'commit', matches_running_cohort: true, mismatch_reason: null },
    measurement: { state: 'insufficient_data', reason: 'insufficient unique prospective treatment/control samples', baseline: -0.01, observed: -0.02, delta: -0.01, confidence_low: null, confidence_high: null, randomization_contract_version: 'v1', randomization_p_value: 0.7, randomization_mode: 'monte_carlo', iid_outcome_assumption: false, evaluated_at: '2026-08-12T00:00:00Z', window_start: '2026-08-09T00:00:00Z', window_end: '2026-08-12T00:00:00Z' },
    samples: { raw_exposures: 3352, treatment_assignment_units: 40, control_assignment_units: 332, required_per_arm: 50, complete_assignment_units: 372, cluster_members: 3145, selected_members: 158, treatment_members: 307, treatment_selected_members: 14, control_members: 2838, control_selected_members: 144 },
    integrity: { source_code_commits: ['commit'], source_code_commit_unknown_n: 0, code_commit_provenance_complete: true, outcome_source_member_counts: {}, allocation_selection_source_member_counts: {}, matching_mirror_rows: 172, exclusion_reasons: { unsettled: 199 }, invalid_reasons: {}, conflict_count: 0, evidence_conflicts: [], evidence_conflict_provenance_truncated: false, harm_state: 'indeterminate', harm_indeterminate_reason: 'insufficient_samples', harm_breached: false, breach_reasons: [] },
    lifecycle: { recommended_action: 'hold', recommendation_reason: 'insufficient samples', authority_enabled: true, authority_block_reason: null, mutation_available: false, mutation_unavailable_reason: 'admission_not_recommended' },
    latest_historical_measurement: null,
  }],
  operations: { availability: 'available', query_errors: {}, config: { contribution_evaluation_enabled: true, autonomous_learning_enabled: true, lifecycle_automation_enabled: true, paper_influence_enabled: true }, evaluator: { current_status: 'healthy', last_succeeded_at: '2026-08-12T00:00:00Z', next_due_at: '2026-08-12T01:00:00Z', backlog_count: 0, catchup_due: false, last_failure_code: null }, lifecycle_audit: { current_status: 'healthy' } },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: Array<string | number> }) => {
    queryProbe.keys.push(queryKey)
    const [key, subkey] = queryKey
    const data = key === 'knowledge' && subkey === 'all'
      ? { items: knowledgeRows.all, total: queryProbe.catalogTotal, limit: 100, offset: Number(queryKey[2] ?? 0), returned: 2, has_more: Number(queryKey[2] ?? 0) + 2 < queryProbe.catalogTotal }
      : key === 'knowledge' ? knowledgeRows[subkey as keyof typeof knowledgeRows]
      : key === 'learning-contribution' ? contribution
      : key === 'dreaming-summary' ? { recent_knowledge_total: 2, recent_knowledge_returned: 2, recent_knowledge_truncated: false, recent_knowledge_items: knowledgeRows.all, recent_research_notes: [], recent_strategy_hypotheses: [], recent_dreaming_logs: [], recent_daily_reviews: [], recent_dreaming_runs: [], deprecated_memories: [], dreaming_job: { status: 'healthy', last_started_at: '2026-08-12T00:00:00Z', last_succeeded_at: '2026-08-12T00:00:01Z', backlog_count: 0 } }
      : key === 'learning-funnel' ? { knowledge_items_by_type: [{ item_type: 'trade_case_study', count: 1 }], pi_actual_visibility: { layer3_lesson_hits: 1, layer3_guards_available: 1 }, ps_lessons: { availability: 'available', total: 1, by_status: [{ status: 'proposed', count: 1 }] }, active_guards_total: 1, serenity_opportunities_active: 0, contradictions: { checked: true, count: 0 }, query_errors: {} }
      : key === 'filter-controller' ? { persistent_recommendations: [], persistent_config: { min_consecutive_runs: 3, max_runs_lookback: 10 }, runs_available: 0, last_run_at: null, schedule: { enabled: false, observe_interval_hours: 6 }, empty_reason: 'controller_disabled' }
      : key === 'strategies' ? []
      : key === 'lessons-all' ? knowledgeRows.lessons
      : key === 'dreaming' ? knowledgeRows.dreaming
      : key === 'reviews' ? knowledgeRows.reviews
      : key === 'post-trade-reviews' ? []
      : key === 'knowledge-content' ? { content: 'Visible stored content' }
      : []
    return { data, isLoading: false, isError: false }
  },
  useMutation: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

vi.mock('../charts/PnLChart', () => ({ default: () => <div>Visible PnL chart</div> }))

import Learning, { KnowledgeTab } from './Learning'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: ReturnType<typeof createRoot> | undefined
afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  document.body.innerHTML = ''
  queryProbe.keys = []
  queryProbe.catalogTotal = 2
})

function render(ui: React.ReactNode) {
  const node = document.createElement('div')
  document.body.appendChild(node)
  root = createRoot(node)
  act(() => root!.render(ui))
  return node
}

function clickButton(node: HTMLElement, label: string) {
  const button = [...node.querySelectorAll('button')].find(item => item.textContent?.startsWith(label))
  expect(button, `button ${label}`).toBeTruthy()
  act(() => button!.click())
}

describe('Learning populated navigation', () => {
  it('renders the populated default contribution and every top-level tab', () => {
    const node = render(<MemoryRouter initialEntries={['/learning']}><Learning /></MemoryRouter>)

    expect(node.textContent).toContain('What changed')
    expect(node.textContent).toContain('Needs attention')
    clickButton(node, 'Impact')
    expect(node.textContent).toContain('3352')
    expect(node.textContent).toContain('40 / 50')
    expect(node.textContent).toContain('332 / 50')

    clickButton(node, 'Runs')
    expect(node.textContent).toContain('Visible case study')
    expect(node.textContent).toContain('Scheduled consolidation')

    clickButton(node, 'Library')
    expect(node.textContent).toContain('Visible case study')
    const caseRow = [...node.querySelectorAll('tr')].find(row => row.textContent?.includes('Visible case study'))
    expect(caseRow).toBeTruthy()
    act(() => caseRow!.click())
    expect(node.textContent).toContain('Visible stored content')

    clickButton(node, 'Operations')
    expect(node.textContent).toContain('Knowledge Funnel')
    expect(node.textContent).not.toContain('Visible PnL chart')
  })

  it.each([
    ['All Knowledge', 'Visible case study'],
    ['Research Notes', 'Visible research note'],
    ['Strategy Memory', 'Visible strategy memory'],
    ['Daily Reviews', 'Visible daily review'],
    ['Dreaming Logs', 'Visible dreaming log'],
    ['Post-Trade Lessons', 'Visible post-trade lesson'],
    ['Guard Candidates', 'Visible guard candidate'],
    ['Active Guards', 'Visible active guard'],
  ])('renders %s data after navigation', (label, expected) => {
    const node = render(<KnowledgeTab />)
    clickButton(node, label)
    expect(node.textContent).toContain(expected)
  })

  it('loads only the active knowledge collection and exposes complete catalog semantics', () => {
    queryProbe.catalogTotal = 202
    const node = render(<KnowledgeTab />)

    expect(queryProbe.keys.filter(key => key[0] === 'knowledge')).toEqual([['knowledge', 'all', 0, '', 'active']])
    expect(node.textContent).toContain('Showing 1–2 of 202 matching knowledge items')
    expect(node.textContent).toContain('Page 1 of 3')
    expect(node.querySelector('[role="tablist"]')).toBeTruthy()
    expect(node.querySelector('[role="tabpanel"]')?.getAttribute('aria-labelledby')).toBe('knowledge-tab-all')

    clickButton(node, 'Next')
    expect(queryProbe.keys.some(key => key.join('/') === 'knowledge/all/100//active')).toBe(true)
  })

  it('supports keyboard tab navigation and shows decision-first contribution semantics', () => {
    const node = render(<MemoryRouter initialEntries={['/learning']}><Learning /></MemoryRouter>)

    expect(node.textContent).toContain('Verdict')
    expect(node.textContent).toContain('Next action')
    clickButton(node, 'Impact')
    expect(node.textContent).toContain('Evidence gap')
    expect(node.textContent).toContain('Preliminary until evidence gates pass')
    expect(node.textContent).not.toContain('Strategies in paper')

    clickButton(node, 'Library')
    const firstTab = node.querySelector('#knowledge-tab-all') as HTMLButtonElement
    act(() => firstTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })))
    expect(node.querySelector('#knowledge-tab-research')?.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement?.id).toBe('knowledge-tab-research')
  })
})

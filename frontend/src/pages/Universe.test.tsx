// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { BrowserRouter, MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { EntryWorkbenchResponse } from '../types'
import Universe from './Universe'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | undefined

afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  document.body.replaceChildren()
  vi.restoreAllMocks()
})

const workbench: EntryWorkbenchResponse = {
  generated_at: '2026-08-28T00:00:00Z',
  progress: {
    generated_at: null, availability: { available: false, reason: 'not_sampled' },
    age_seconds: null, ttl_seconds: 1200, last_attempt_at: null, last_success_at: null,
    last_snapshot_id: null, selection_policy: null, policy_epoch: null, selected_count: null,
    planning_supported_count: null, observe_only_count: null, selection_reason_counts: {},
    ready_planning_count: null, oldest_ready_at: null, expired_claim_count: null,
    due_outcome_count: null, oldest_due_at: null, issues: [], blocking_reason_counts: {},
    shadow_capture_status: null, shadow_capture_reason: null,
  },
  availability: {
    summary: { available: true, stale: false },
    entry_plans: { available: true, stale: false },
    current_candidates: { available: true, stale: false },
    candidate_attention: { available: true, stale: false },
  },
  universe: {
    enabled: true,
    status: 'active',
    fallback_instruments: [],
    snapshot: {
      id: 'snapshot-1234', created_at: '2026-08-28T00:00:00Z', market_type: 'spot', policy_id: null,
      status: 'active', expires_at: '2026-08-28T01:00:00Z', summary: '{}',
    },
  },
  summary: { active_entry_plans: 1, needs_planning: 2, expiring_soon: 1 },
  entry_plans: [{
    id: 'plan-1', planning_generation_id: 'generation-1', candidate_id: 'candidate-1', planning_key: 'a'.repeat(64),
    version: 'entry-plan/v1', venue: 'bitget', symbol: 'AVAX/USDT', market_type: 'perp', direction: 'long',
    execution_mode: 'RESTING_MAKER', status: 'waiting', order_id: null, plan_hash: 'b'.repeat(64),
    created_at: '2026-08-28T00:00:00Z', updated_at: '2026-08-28T00:00:00Z', terminal_at: null,
    plan_json: '{}', runtime_session_id: null, producer_commit: null, behavior_fingerprint: null,
    terminal_reason: null, contract_valid: true, plan: {
      planning_generation_id: 'generation-1', candidate_id: 'candidate-1', candidate_family: 'momentum',
      venue: 'bitget', symbol: 'AVAX/USDT', market_type: 'perp', direction: 'long',
      execution_mode: 'RESTING_MAKER', approved_at: '2026-08-28T00:00:00Z',
      invalidation_conditions: [], maker_required: true, cancel_replace_policy: 'CANCEL_ONLY',
      max_requotes: 0, risk_policy_hash: 'test-policy-hash',
      thesis: 'bounded continuation', scenario: 'retrace', entry_zone: [100, 101], invalidation_price: 98,
      stop_price: 98, target_price: 105, expires_at: '2026-08-28T01:00:00Z', limit_price: 100,
      trigger_spec_id: null, trigger_spec_version: null, order_tactic: 'POST_ONLY_LIMIT',
      time_in_force: 'POST_ONLY', price_rule: 'EXACT_LIMIT', max_wait_sec: 60,
      partial_fill_policy: 'KEEP_PARTIAL_CANCEL_REMAINDER', evidence_refs: ['feature:snapshot-1'],
      expected_horizon_hours: 4, max_notional: 100, max_risk_pct: 0.25, max_leverage: 2,
      required_permission: 'Paper', execution_account_mode: 'bitget_demo', risk_policy_id: 'demo-v1', risk_policy_revision: 1,
    },
    monitoring_state: 'degraded',
    monitoring_evidence_json: JSON.stringify({ channels: { quote: { state: 'ready', reason: null }, trade: { state: 'degraded', reason: 'channel_event_stale' } } }),
    monitoring_evidence_hash: 'c'.repeat(64),
    lineage: {
      order: { id: 'order-1', exchange_order_id: 'exchange-1', status: 'submitted', quantity: 1, position_id: 'position-1' },
      fills: { count: 1, quantity: 0.5, latest_at: '2026-08-28T00:30:00Z' },
      position: { id: 'position-1', status: 'open', quantity: 0.5, entry_price: 100 },
    },
  }],
  current_candidates: [{
    id: 'candidate-1', snapshot_id: 'snapshot-1234', asset_id: 'asset-1', venue: 'binance',
    all_scores: '{}', atr_pct: null, candidate_opportunity_context_hash: null,
    candidate_opportunity_context_json: null, candidate_opportunity_context_version: null,
    crowding_risk: null, entry_price_usd: null, estimated_cost: null, late_entry_penalty: null,
    learning_candidate_key: null, liquidity_score: null, opportunity_score: null,
    quote_volume_24h: null, regime_tag: null, spread_bps: null, tradability_score: null, ttl_sec: 3600,
    registry_venue: 'binance', planning_key: null, cycle_number: null, outcome_class: null, outcome_reason: null,
    symbol: 'BTC/USDT', market_type: 'perp', rank: 1, total_score: 88,
    candidate_selection_policy: 'legacy_total_score_v1', candidate_selection_policy_epoch: null,
    candidate_selection_utility_pct: null,
    opportunity_mechanism: null, opportunity_mechanism_version: null,
    opportunity_source_component: null, opportunity_source_cutoff_at: null,
    opportunity_evidence_hash: null,
    scanner_family: 'momentum', scanner_direction: 'long', inclusion_reason: '["ranked"]',
    risk_flags: '[]', execution_eligible: 1, expires_at: '2026-08-28T01:00:00Z',
    planning_generation_id: null, planning_status: null, result_kind: null,
    planning_result_json: null, attempt_count: null, attention_status: null,
    soft_expires_at: null, hard_expires_at: null, attention_terminal_reason: null,
    entry_plan_id: null, entry_plan_status: null, entry_plan_terminal_reason: null,
    lifecycle_state: 'AWAITING_PLANNING', lifecycle_reason: 'candidate_selected',
    current_cycle_number: null, expired_plan_count: 0,
    current_generation_id: null, current_plan_id: null, last_stage: null,
    model_invoked: false, compiler_invoked: false, next_action: 'await_planner', cycles: [],
    state_changed_at: null,
  }],
  candidate_attention: [],
}

function renderWorkbench(data: EntryWorkbenchResponse = workbench, initialEntry = '/universe') {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
  client.setQueryData(['entry-workbench'], data)
  if (data.entry_plans?.[0]) client.setQueryData(['entry-plan', data.entry_plans[0].id], data.entry_plans[0])
  return renderToStaticMarkup(<MemoryRouter initialEntries={[initialEntry]}><QueryClientProvider client={client}><Universe /></QueryClientProvider></MemoryRouter>)
}

async function renderWorkbenchError(cached?: EntryWorkbenchResponse) {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: cached ? 0 : Infinity, retry: false } } })
  if (cached) client.setQueryData(['entry-workbench'], cached)
  vi.spyOn(api, 'getEntryWorkbench').mockRejectedValue(new Error('workbench unavailable'))
  vi.spyOn(api, 'getOperatorStatus').mockRejectedValue(new Error('operator unavailable'))
  const node = document.createElement('div')
  document.body.appendChild(node)
  root = createRoot(node)
  act(() => root!.render(<MemoryRouter><QueryClientProvider client={client}><Universe /></QueryClientProvider></MemoryRouter>))
  await act(async () => {
    await vi.waitFor(() => expect(node.textContent).not.toContain('Loading Current Candidates'))
  })
  return node.textContent ?? ''
}

describe('Entry Workbench', () => {
  it('defaults to Current Candidates independently of the Planning Queue', () => {
    const html = renderWorkbench()
    expect(html).toContain('Entry Workbench')
    expect(html).toContain('Active Entry Plans')
    expect(html).toContain('Planning Eligible')
    expect(html).toContain('Blocked')
    expect(html).toContain('BTCUSDT')
    expect(html).toContain('No Planning Generation yet')
    expect(html).toContain('Current Candidates')
    expect(html).toContain('Entry Plans')
    expect(html).toContain('Planning Queue')
    expect(html).toContain('Planning History')
    expect(html).toContain('Fast monitoring:')
    expect(html).toContain('trade: Channel Event Stale')
    expect(html).not.toContain('Funding readiness')
    expect(html).not.toContain('Manual Review')
  })

  it('opens the Universe settings deep link when scanning is disabled', async () => {
    window.history.replaceState(null, '', '/universe')
    const client = new QueryClient({ defaultOptions: { queries: { enabled: false } } })
    client.setQueryData(['entry-workbench'], { ...workbench, universe: { ...workbench.universe, enabled: false, status: 'disabled' } })
    const host = document.createElement('div')
    document.body.append(host)
    root = createRoot(host)
    await act(async () => {
      root?.render(<BrowserRouter><QueryClientProvider client={client}><Universe /></QueryClientProvider></BrowserRouter>)
    })
    expect(host.textContent).toContain('Universe disabled')
    const settings = Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Settings')!
    await act(async () => { settings.click() })
    expect(window.location.pathname + window.location.search).toBe('/settings?tab=risk&section=universe')
    client.clear()
  })

  it('projects repeated expiry and replanning cycles without collapsing history', () => {
    const base = workbench.current_candidates![0]
    const renderState = (lifecycle_state: string, cycle: number, expired: number) => renderWorkbench({
      ...workbench,
      current_candidates: [{
        ...base,
        planning_generation_id: `generation-${cycle}`,
        lifecycle_state,
        lifecycle_reason: null,
        current_cycle_number: cycle,
        expired_plan_count: expired,
      }],
    })

    expect(renderState('PLAN_EXPIRED', 1, 1)).toContain('Plan Expired · cycle 1 · expired 1×')
    expect(renderState('REPLANNING', 2, 1)).toContain('Replanning · cycle 2 · expired 1×')
    expect(renderState('PLAN_EXPIRED', 2, 2)).toContain('Plan Expired · cycle 2 · expired 2×')
  })

  it('shows passive market waiting as an active plan lifecycle', () => {
    const html = renderWorkbench({
      ...workbench,
      current_candidates: [{
        ...workbench.current_candidates![0],
        planning_generation_id: 'generation-1',
        lifecycle_state: 'WAITING_MARKET',
        lifecycle_reason: 'post_only_would_cross_bbo',
        current_cycle_number: 1,
        next_action: 'await_passive_market',
      }],
    })
    expect(html).toContain('Waiting Market · cycle 1 · Post Only Would Cross Bbo')
    expect(html).not.toContain('No Planning Generation yet')
  })

  it('labels target utility without reinterpreting the legacy score', () => {
    const html = renderWorkbench({
      ...workbench,
      current_candidates: workbench.current_candidates?.map(candidate => ({
        ...candidate,
        candidate_selection_policy: 'candidate_selection_utility_v1',
        candidate_selection_policy_epoch: 'candidate-selection-utility:test',
        candidate_selection_utility_pct: 1.25,
      })) ?? [],
    })
    expect(html).toContain('收益排序：预期净收益 1.25%')
    expect(html).not.toContain('Legacy score 88')
  })

  it('keeps startup signal strength distinct from unknown net utility', () => {
    const html = renderWorkbench({ ...workbench, current_candidates: [{
      ...workbench.current_candidates![0],
      candidate_selection_policy: 'candidate_selection_utility_v1', admission_basis: 'startup',
      pessimistic_strength: 72, startup_reason: 'parent_sample_insufficient',
      candidate_selection_utility_pct: null,
    }] })
    expect(html).toContain('冷启动：信号强度 72')
    expect(html).toContain('预期净收益未知')
    expect(html).toContain('父组样本不足')
    expect(html).not.toContain('72%')
  })
  it('exposes all six progressive Plan sections and exact downstream lineage', () => {
    const html = renderWorkbench(workbench, '/universe?tab=plans&plan_id=plan-1')
    for (const section of ['Plan Summary', 'Entry Geometry', 'Trigger &amp; Execution', 'Thesis &amp; Evidence', 'Risk Envelope', 'Lineage / exact execution']) {
      expect(html).toContain(section)
    }
    expect(html).toContain('order-1')
    expect(html).toContain('exchange-1')
    expect(html).toContain('position-1')
    expect(html).toContain('Open exact execution')
  })

  it('keeps a no-generation Candidate on the Current Candidates tab in its drawer', () => {
    const html = renderWorkbench(workbench, '/universe?tab=current&candidate_id=candidate-1')
    expect(html).toContain('Candidate is visible independently of planning')
    expect(html).toContain('Planning Generation')
    expect(html).toContain('Not created')
    expect(html).toContain('Momentum')
  })

  it('shows both original and renewed activation facts in the Candidate cycle history', () => {
    const html = renderWorkbench({
      ...workbench,
      current_candidates: [{
        ...workbench.current_candidates![0],
        cycles: [{
          generation_id: 'generation-1', cycle_number: 1, previous_generation_id: null,
          cycle_reason: null, requalification_candidate_id: null, last_stage: 'plan_created',
          model_invoked: true, compiler_invoked: true, outcome_class: 'entry_plan', outcome_reason: null,
          entry_plan_id: 'plan-1', entry_plan_status: 'triggered', execution_state: 'NOT_STARTED', execution: null,
          events: [
            { event: 'trigger_activation_created', at: '2026-08-28T00:00:00Z', reason: null },
            { event: 'trigger_activation_created', at: '2026-08-29T00:00:00Z', reason: null },
          ],
        }],
      }],
    }, '/universe?tab=current&candidate_id=candidate-1')
    expect(html).toContain('Planning cycles')
    expect(html).toContain('Cycle 1')
    expect(html.match(/Trigger Activation Created/g)).toHaveLength(2)
  })

  it('distinguishes fallback instruments from Candidate attention', () => {
    const html = renderWorkbench({
      ...workbench,
      universe: { enabled: false, status: 'disabled', snapshot: null, fallback_instruments: ['BTC/USDT', 'ETH/USDT'] },
      summary: { active_entry_plans: 1, needs_planning: 0, expiring_soon: 0 },
      candidate_attention: [],
    })
    expect(html).toContain('Fallback instruments available')
    expect(html).toContain('They are not durable Candidates')
  })

  it('renders the backend stale snapshot state truthfully', () => {
    const html = renderWorkbench({
      ...workbench,
      universe: { ...workbench.universe, status: 'stale_snapshot' },
    })
    expect(html).toContain('Stale snapshot')
  })

  it('renders an initial whole-workbench failure as unavailable, never empty', async () => {
    const html = await renderWorkbenchError()
    expect(html).toContain('Entry Workbench is unavailable')
    expect(html).toContain('Current Candidates is unavailable')
    expect(html).toContain('Candidate stage counts unavailable')
    expect(html).not.toContain('Planning: pending 0')
    expect(html).not.toContain('No active Entry Plans')
  })

  it('keeps cached facts visible after a refetch failure', async () => {
    const html = await renderWorkbenchError(workbench)
    expect(html).toContain('BTCUSDT')
    expect(html).not.toContain('Entry Workbench is unavailable')
  })

  it('renders peer Entry Plans tab with status filters and honest badge tones', () => {
    const html = renderWorkbench(workbench, '/universe?tab=plans')
    expect(html).toContain('Plan status:')
    expect(html).toContain('Active')
    expect(html).toContain('Expired')
    expect(html).toContain('All')
    expect(html).toContain('AVAXUSDT')
    expect(html).toContain('Waiting')
  })
})

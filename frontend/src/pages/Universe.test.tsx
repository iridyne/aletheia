// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
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
    terminal_reason: null, contract_valid: true, plan: {
      thesis: 'bounded continuation', scenario: 'retrace', entry_zone: [100, 101], invalidation_price: 98,
      stop_price: 98, target_price: 105, expires_at: '2026-08-28T01:00:00Z', limit_price: 100,
      trigger_spec_id: null, trigger_spec_version: null, order_tactic: 'POST_ONLY_LIMIT',
      time_in_force: 'POST_ONLY', price_rule: 'EXACT_LIMIT', max_wait_sec: 60,
      partial_fill_policy: 'KEEP_PARTIAL_CANCEL_REMAINDER', evidence_refs: ['feature:snapshot-1'],
      expected_horizon_hours: 4, max_notional: 100, max_risk_pct: 0.25, max_leverage: 2,
      required_permission: 'Paper', execution_account_mode: 'bitget_demo', risk_policy_id: 'demo-v1', risk_policy_revision: 1,
    },
    lineage: {
      order: { id: 'order-1', exchange_order_id: 'exchange-1', status: 'submitted', quantity: 1, position_id: 'position-1' },
      fills: { count: 1, quantity: 0.5, latest_at: '2026-08-28T00:30:00Z' },
      position: { id: 'position-1', status: 'open', quantity: 0.5, entry_price: 100 },
    },
  }],
  current_candidates: [{
    id: 'candidate-1', snapshot_id: 'snapshot-1234', asset_id: 'asset-1', venue: 'binance',
    symbol: 'BTC/USDT', market_type: 'perp', rank: 1, total_score: 88,
    scanner_family: 'momentum', scanner_direction: 'long', inclusion_reason: '["ranked"]',
    risk_flags: '[]', execution_eligible: 1, expires_at: '2026-08-28T01:00:00Z',
    planning_generation_id: null, planning_status: null, result_kind: null,
    planning_result_json: null, attempt_count: null, attention_status: null,
    planner_contract_version: null,
    soft_expires_at: null, hard_expires_at: null, attention_terminal_reason: null,
    entry_plan_id: null, entry_plan_status: null, entry_plan_terminal_reason: null,
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
    expect(html).not.toContain('Manual Review')
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
})

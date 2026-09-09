// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import type { Order, Position, TradeProposal } from '../types'
import CausalChain from './CausalChain'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | undefined

afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  document.body.replaceChildren()
  vi.restoreAllMocks()
})

async function flushQueries() {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 10))
  })
}

function proposalFor(order: Order): TradeProposal {
  return {
    id: order.proposal_id,
    strategy_id: null,
    opportunity_id: 'opportunity-1',
    symbol: order.symbol,
    market_type: order.market_type,
    direction: order.direction,
    order_type: order.order_type,
    quantity: order.quantity,
    price: order.price,
    leverage: null,
    stop_price: null,
    take_profit: null,
    risk_reward: null,
    execution_mode: order.execution_mode,
    agent_rationale: null,
    status: 'executed',
    created_at: '2026-08-26T00:00:00Z',
  }
}

function positionFor(order: Order): Position {
  return {
    id: 'position-1',
    symbol: order.symbol,
    market_type: order.market_type,
    direction: order.direction,
    quantity: order.quantity,
    entry_price: order.price ?? 0,
    current_price: order.price,
    unrealized_pnl: 0,
    realized_pnl: null,
    execution_mode: order.execution_mode,
    strategy_id: null,
    proposal_id: order.proposal_id,
    order_id: order.id,
    status: 'open',
    stop_price: null,
    take_profit_price: null,
    close_reason: null,
    opened_at: '2026-08-26T00:01:00Z',
    closed_at: null,
  }
}

function createClient(staleTime = Infinity) {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime } } })
}

type ChainAnchor =
  | { kind: 'order'; order: Order }
  | { kind: 'position'; position: Position }

function mountChain(anchor: ChainAnchor, client: QueryClient) {
  const node = document.createElement('div')
  document.body.appendChild(node)
  root = createRoot(node)
  act(() => root!.render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <CausalChain anchor={anchor} />
      </QueryClientProvider>
    </MemoryRouter>,
  ))
  return node
}

function stageNode(container: HTMLElement, stage: string): HTMLElement {
  const match = [...container.querySelectorAll<HTMLElement>('.cc-node')]
    .find(node => node.querySelector('.cc-stage')?.textContent === stage)
  if (!match) throw new Error(`Missing CausalChain stage: ${stage}`)
  return match
}

function renderChain(order: Order) {
  const proposal = proposalFor(order)
  const client = createClient()
  client.setQueryData(['proposal', proposal.id], proposal)
  client.setQueryData(['trades'], [order])
  client.setQueryData(['positions'], [] as Position[])
  vi.spyOn(api, 'getOrderRiskDecision').mockResolvedValue({
    order_id: order.id,
    proposal_id: order.proposal_id,
    risk_decision_id: order.risk_decision_id,
    status: 'available',
    reason_code: null,
    decision: {
      id: 'risk-authoritative-a',
      approved: true,
      risk_score: 12,
      message: 'authoritative A',
    },
  })
  vi.spyOn(api, 'getProposalRiskDecision').mockResolvedValue({
    proposal_id: order.proposal_id,
    status: 'available',
    reason_code: null,
    decision: {
      id: 'risk-preflight-b',
      approved: false,
      risk_score: 99,
      message: 'newer preflight B',
    },
  })
  return mountChain({ kind: 'order', order }, client)
}

const order: Order = {
  id: 'order-1',
  proposal_id: 'proposal-1',
  risk_decision_id: 'risk-authoritative-a',
  symbol: 'BTC/USDT',
  market_type: 'spot',
  direction: 'long',
  order_type: 'market',
  quantity: 0.001,
  price: 60_000,
  execution_mode: 'bitget_demo',
  exchange_order_id: null,
  status: 'filled',
  created_at: '2026-08-26T00:00:00Z',
  filled_at: '2026-08-26T00:01:00Z',
  error_reason: null,
}

describe('CausalChain risk authority', () => {
  it('renders the exact RiskDecision consumed by an Order', async () => {
    const node = renderChain(order)
    await flushQueries()

    expect(api.getOrderRiskDecision).toHaveBeenCalledWith('order-1')
    expect(api.getProposalRiskDecision).not.toHaveBeenCalled()
    expect(node.textContent).toContain('risk-authoritative-a')
    expect(node.textContent).toContain('authoritative A')
    expect(node.textContent).not.toContain('risk-preflight-b')
    expect(node.textContent).not.toContain('newer preflight B')
  })
})

describe('CausalChain read states', () => {
  it('renders pending linked reads as loading instead of absence', () => {
    const pending = new Promise<never>(() => undefined)
    vi.spyOn(api, 'getProposal').mockReturnValue(pending)
    vi.spyOn(api, 'getTrades').mockReturnValue(pending)
    vi.spyOn(api, 'getPositions').mockReturnValue(pending)
    vi.spyOn(api, 'getProposal').mockReturnValue(pending)
    vi.spyOn(api, 'getOrderRiskDecision').mockReturnValue(pending)

    const node = mountChain({ kind: 'order', order }, createClient())

    expect(stageNode(node, 'Proposal').textContent).toContain('Loading data…')
    expect(stageNode(node, 'Risk Decision').textContent).toContain('Loading data…')
    expect(stageNode(node, 'Position').textContent).toContain('Loading data…')
    expect(node.textContent).not.toContain('proposal not in recent list')
    expect(node.textContent).not.toContain('no position record found for this order')
  })

  it('renders failed linked reads as unavailable without inventing empty records', async () => {
    const failure = new Error('read failed')
    vi.spyOn(api, 'getProposal').mockRejectedValue(failure)
    vi.spyOn(api, 'getTrades').mockRejectedValue(failure)
    vi.spyOn(api, 'getPositions').mockRejectedValue(failure)
    vi.spyOn(api, 'getProposal').mockRejectedValue(failure)
    vi.spyOn(api, 'getOrderRiskDecision').mockRejectedValue(failure)

    const node = mountChain({ kind: 'order', order }, createClient())
    await flushQueries()

    expect(stageNode(node, 'Opportunity').textContent).toContain('Data unavailable')
    expect(stageNode(node, 'Proposal').textContent).toContain('Data unavailable')
    expect(stageNode(node, 'Proposal').textContent).toContain('proposal-1')
    expect(stageNode(node, 'Risk Decision').textContent).toContain('Data unavailable')
    expect(stageNode(node, 'Position').textContent).toContain('Data unavailable')
    expect(stageNode(node, 'Order').textContent).toContain('order-1')
    expect(node.textContent).not.toContain('proposal not in recent list')
    expect(node.textContent).not.toContain('no position record found for this order')
  })

  it('keeps exact 404 and successful null reads as missing records', async () => {
    const client = createClient()
    client.setQueryData(['proposals'], [])
    client.setQueryData(['trades'], [order])
    client.setQueryData(['positions'], [] as Position[])
    vi.spyOn(api, 'getProposal').mockRejectedValue(new ApiError(404, 'not found'))
    vi.spyOn(api, 'getOrderRiskDecision').mockResolvedValue({
      order_id: order.id,
      proposal_id: order.proposal_id,
      risk_decision_id: order.risk_decision_id,
      status: 'missing',
      reason_code: 'not_found',
      decision: null,
    })

    const node = mountChain({ kind: 'order', order }, client)
    await flushQueries()

    expect(stageNode(node, 'Proposal').textContent).toContain('not recorded')
    expect(stageNode(node, 'Proposal').textContent).toContain('exact Proposal detail not found')
    expect(stageNode(node, 'Risk Decision').textContent).toContain('not recorded')
    expect(stageNode(node, 'Risk Decision').textContent).toContain('not_found')
    expect(stageNode(node, 'Proposal').textContent).not.toContain('Data unavailable')
    expect(stageNode(node, 'Risk Decision').textContent).not.toContain('Data unavailable')
  })

  it('retains cached facts and marks their failed refresh as stale', async () => {
    const proposal = proposalFor(order)
    const position = positionFor(order)
    const client = createClient(0)
    client.setQueryData(['proposal', proposal.id], proposal, { updatedAt: 1 })
    client.setQueryData(['trades'], [order], { updatedAt: 1 })
    client.setQueryData(['positions'], [position], { updatedAt: 1 })
    vi.spyOn(api, 'getProposal').mockRejectedValue(new Error('refresh failed'))
    vi.spyOn(api, 'getTrades').mockRejectedValue(new Error('refresh failed'))
    vi.spyOn(api, 'getPositions').mockRejectedValue(new Error('refresh failed'))
    vi.spyOn(api, 'getOrderRiskDecision').mockResolvedValue({
      order_id: order.id,
      proposal_id: order.proposal_id,
      risk_decision_id: order.risk_decision_id,
      status: 'available',
      reason_code: null,
      decision: { id: 'risk-authoritative-a', approved: true },
    })

    const node = mountChain({ kind: 'order', order }, client)
    await flushQueries()

    expect(stageNode(node, 'Proposal').textContent).toContain('BTC/USDT')
    expect(stageNode(node, 'Proposal').textContent).toContain('Data is stale')
    expect(stageNode(node, 'Position').textContent).toContain('position-1')
    expect(stageNode(node, 'Position').textContent).toContain('Data is stale')
    expect(stageNode(node, 'Position').textContent).not.toContain('no position record found')
  })

  it('treats an exact Entry Plan 404 as missing while retaining its identity', async () => {
    const planOrder = { ...order, entry_plan_id: 'plan-404' }
    const client = createClient()
    client.setQueryData(['proposal', planOrder.proposal_id], proposalFor(planOrder))
    client.setQueryData(['trades'], [planOrder])
    client.setQueryData(['positions'], [] as Position[])
    vi.spyOn(api, 'getEntryPlan').mockRejectedValue(new ApiError(404, 'not found'))
    vi.spyOn(api, 'getOrderRiskDecision').mockResolvedValue({
      order_id: planOrder.id,
      proposal_id: planOrder.proposal_id,
      risk_decision_id: planOrder.risk_decision_id,
      status: 'missing',
      reason_code: 'not_found',
      decision: null,
    })

    const node = mountChain({ kind: 'order', order: planOrder }, client)
    await flushQueries()

    expect(stageNode(node, 'Candidate').textContent).toContain('not recorded')
    expect(stageNode(node, 'Candidate').textContent).toContain('exact Entry Plan detail not found')
    expect(stageNode(node, 'Candidate').textContent).not.toContain('Data unavailable')
    expect(stageNode(node, 'Entry Plan').textContent).toContain('plan-404')
    expect(stageNode(node, 'Entry Plan').textContent).toContain('not recorded')
  })

  it('keeps a Position anchor while its linked Order read is unavailable', async () => {
    const position = positionFor(order)
    const client = createClient()
    client.setQueryData(['proposals'], [proposalFor(order)])
    client.setQueryData(['positions'], [position])
    vi.spyOn(api, 'getTrades').mockRejectedValue(new Error('read failed'))
    vi.spyOn(api, 'getProposalRiskDecision').mockResolvedValue({
      proposal_id: order.proposal_id,
      status: 'available',
      reason_code: null,
      decision: { id: 'preflight-only', approved: true },
    })

    const node = mountChain({ kind: 'position', position }, client)
    await flushQueries()

    expect(stageNode(node, 'Order').textContent).toContain('order-1')
    expect(stageNode(node, 'Order').textContent).toContain('Data unavailable')
    expect(stageNode(node, 'Fill').textContent).toContain('Data unavailable')
    expect(stageNode(node, 'Risk Decision').textContent).toContain('Data unavailable')
    expect(stageNode(node, 'Risk Decision').textContent).not.toContain('preflight-only')
    expect(stageNode(node, 'Position').textContent).toContain('position-1')
    expect(stageNode(node, 'Position').textContent).not.toContain('Data unavailable')
  })

  it('marks retained empty Order linkage stale instead of missing', async () => {
    const position = positionFor(order)
    const client = createClient(0)
    client.setQueryData(['proposal', order.proposal_id], proposalFor(order), { updatedAt: 1 })
    client.setQueryData(['trades'], [] as Order[], { updatedAt: 1 })
    client.setQueryData(['positions'], [position], { updatedAt: 1 })
    vi.spyOn(api, 'getProposal').mockResolvedValue(proposalFor(order))
    vi.spyOn(api, 'getTrades').mockRejectedValue(new Error('refresh failed'))
    vi.spyOn(api, 'getPositions').mockResolvedValue([position])
    vi.spyOn(api, 'getProposalRiskDecision').mockResolvedValue({
      proposal_id: order.proposal_id,
      status: 'available',
      reason_code: null,
      decision: { id: 'preflight-only', approved: true },
    })

    const node = mountChain({ kind: 'position', position }, client)
    await flushQueries()

    expect(stageNode(node, 'Order').textContent).toContain('order-1')
    expect(stageNode(node, 'Order').textContent).toContain('Data is stale')
    expect(stageNode(node, 'Order').textContent).not.toContain('detail not in recent list')
    expect(stageNode(node, 'Fill').textContent).toContain('Data is stale')
    expect(stageNode(node, 'Fill').textContent).not.toContain('none')
    expect(stageNode(node, 'Fill').textContent).not.toContain('not recorded')
    expect(stageNode(node, 'Risk Decision').textContent).toContain('Data is stale')
    expect(stageNode(node, 'Risk Decision').textContent).not.toContain('preflight-only')
  })
})

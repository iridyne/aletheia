// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
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

function renderChain(order: Order) {
  const proposal: TradeProposal = {
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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(['proposals'], [proposal])
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

  const node = document.createElement('div')
  document.body.appendChild(node)
  root = createRoot(node)
  act(() => root!.render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <CausalChain anchor={{ kind: 'order', order }} />
      </QueryClientProvider>
    </MemoryRouter>,
  ))
  return node
}

describe('CausalChain risk authority', () => {
  it('renders the exact RiskDecision consumed by an Order', async () => {
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
      execution_mode: 'paper_internal',
      exchange_order_id: null,
      status: 'filled',
      created_at: '2026-08-26T00:00:00Z',
      filled_at: '2026-08-26T00:01:00Z',
      error_reason: null,
    }

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

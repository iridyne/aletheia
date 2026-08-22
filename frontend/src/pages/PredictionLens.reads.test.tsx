// @vitest-environment happy-dom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PredictionMarket, PredictionStatus } from '../types'
import { api } from '../api/client'

vi.mock('../charts/ProbabilityChart', () => ({
  default: () => <div data-testid="prediction-chart" />,
}))

import PredictionLens from './PredictionLens'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const market: PredictionMarket = {
  id: 'market-1', event_title: 'Read-state test market', yes_probability: 0.5,
  probability_change_24h: 0.02, volume_24h: 1000, liquidity: 500,
  resolution_at: null, category: 'test', direction_bias: 'long', confidence: null,
  confidence_score: null, status: 'open', created_at: '2026-08-22T00:00:00Z',
  market_id: 'external-market-1', enable_orderbook: true, source: 'test',
}

const status: PredictionStatus = {
  enabled: true, last_run: null, categories: ['test'], total_opportunities: 0,
  open_opportunities: 0, observer_interval_minutes: 30,
}

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

function renderLens() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(['prediction-status'], status)
  client.setQueryData(['prediction-markets'], [market])
  client.setQueryData(['prediction-runs'], [])
  vi.spyOn(api, 'getPredictionMarketHistory').mockRejectedValue(new Error('history unavailable'))
  vi.spyOn(api, 'getPredictionMarketClaims').mockRejectedValue(new Error('claims unavailable'))
  vi.spyOn(api, 'getSerenityEvidence').mockRejectedValue(new Error('evidence unavailable'))

  const node = document.createElement('div')
  document.body.appendChild(node)
  root = createRoot(node)
  act(() => root!.render(
    <MemoryRouter initialEntries={['/prediction-markets']}>
      <QueryClientProvider client={client}>
        <PredictionLens />
      </QueryClientProvider>
    </MemoryRouter>,
  ))
  return node
}

describe('Prediction Lens read-state presentation', () => {
  it('keeps failed detail reads visible as unavailable instead of empty', async () => {
    const node = renderLens()
    const card = node.querySelector<HTMLElement>('.pred-market-card')
    expect(card).toBeTruthy()
    act(() => card!.click())
    await flushQueries()

    expect(node.textContent).toContain('Probability history unavailable')
    expect(node.textContent).toContain('Move explanations unavailable')
    expect(node.textContent).toContain('Linked Serenity evidence unavailable')
    expect(node.textContent).not.toContain('No probability history yet')
  })
})

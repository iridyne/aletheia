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
  id: 'market-1',
  event_title: 'Will the test market resolve in 2026?',
  yes_probability: 0.64,
  probability_change_24h: 0.08,
  volume_24h: 12000,
  liquidity: 8000,
  resolution_at: '2026-12-31T00:00:00Z',
  category: 'macro',
  direction_bias: 'long',
  confidence: 'high',
  confidence_score: 0.82,
  status: 'open',
  created_at: '2026-08-22T00:00:00Z',
  market_id: 'external-market-1',
  enable_orderbook: true,
  source: 'test',
}

const status: PredictionStatus = {
  enabled: true,
  last_run: null,
  categories: ['macro'],
  total_opportunities: 1,
  open_opportunities: 1,
  observer_interval_minutes: 30,
}

let root: Root | undefined

afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  document.body.replaceChildren()
  vi.restoreAllMocks()
})

function renderLens() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(['prediction-status'], status)
  client.setQueryData(['prediction-markets'], [market])
  client.setQueryData(['prediction-runs'], [])
  vi.spyOn(api, 'getPredictionMarketHistory').mockResolvedValue({ source: 'test', points: [] })
  vi.spyOn(api, 'getPredictionMarketClaims').mockResolvedValue([])
  vi.spyOn(api, 'getSerenityEvidence').mockResolvedValue([])

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

function closeDrawer(node: ParentNode) {
  act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  expect(node.querySelector('[role="dialog"]')).toBeNull()
}

describe('Prediction Lens keyboard entrypoints', () => {
  it('opens a radar card from its native button and restores focus after close', () => {
    const node = renderLens()
    const card = node.querySelector<HTMLButtonElement>('button.pred-market-card')
    expect(card?.getAttribute('aria-label')).toBe(`Open details for ${market.event_title}`)
    expect(card).toBeTruthy()

    act(() => {
      card!.focus()
      card!.click()
    })
    expect(node.querySelector('[role="dialog"]')).toBeTruthy()

    closeDrawer(node)
    expect(document.activeElement).toBe(card)
  })

  it('opens a probability mover with Enter or Space and restores row focus', () => {
    const node = renderLens()
    const moversTab = [...node.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find(button => button.textContent?.includes('Probability Movers'))
    expect(moversTab).toBeTruthy()
    act(() => moversTab!.click())

    const row = node.querySelector<HTMLTableRowElement>('tr.clickable')
    expect(row?.getAttribute('role')).toBe('button')
    expect(row?.tabIndex).toBe(0)
    expect(row?.getAttribute('aria-label')).toBe(`Open details for ${market.event_title}`)
    expect(row).toBeTruthy()

    act(() => {
      row!.focus()
      row!.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }))
    })
    expect(node.querySelector('[role="dialog"]')).toBeTruthy()

    closeDrawer(node)
    expect(document.activeElement).toBe(row)
  })
})

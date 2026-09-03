// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const mocks = vi.hoisted(() => ({
  news: [] as Array<Record<string, unknown>>,
  analyzeNews: vi.fn(),
  invalidateQueries: vi.fn(),
}))

vi.mock('../api/client', () => ({
  api: {
    getNews: vi.fn(),
    analyzeNews: mocks.analyzeNews,
    createNews: vi.fn(),
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: mocks.news, isPending: false, isError: false }),
  useMutation: (options: any) => ({
    isPending: false,
    mutate: (variables: unknown) => {
      options.mutationFn(variables)
      options.onSuccess?.({ success: true, impact_summary: 'Updated impact' })
    },
  }),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
}))

import NewsIntel from './NewsIntel'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  mocks.news = []
  mocks.analyzeNews.mockReset()
  mocks.invalidateQueries.mockReset()
})

function item(overrides: Record<string, unknown> = {}) {
  return {
    id: 'news-1',
    title: 'Market headline',
    source: 'Wire',
    summary: 'Summary',
    symbols_mentioned: ['BTC/USDT'],
    event_type: 'market',
    sentiment: 'neutral',
    relevance_score: 0.8,
    published_at: '2026-08-22T10:00:00Z',
    fetched_at: '2026-08-22T10:01:00Z',
    status: 'new',
    ...overrides,
  }
}

function mount() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(<MemoryRouter><NewsIntel /></MemoryRouter>))
}

describe('NewsIntel selected drawer identity', () => {
  it('renders the refreshed row facts together while the drawer stays open', () => {
    mocks.news = [item()]
    mount()

    act(() => (host.querySelector('.news-card') as HTMLButtonElement).click())
    expect(host.textContent).toContain('Neutral')
    act(() => (host.querySelector('button[aria-label="Analyze Impact"]') as HTMLButtonElement).click())

    mocks.news = [item({
      symbols_mentioned: ['ETH/USDT'],
      sentiment: 'bullish',
      relevance_score: 0.95,
      status: 'analyzed',
    })]
    act(() => root.render(<MemoryRouter><NewsIntel /></MemoryRouter>))

    expect(host.textContent).toContain('Bullish')
    expect(host.textContent).toContain('Analyzed')
    expect(host.textContent).toContain('95%')
    expect(host.textContent).toContain('ETH/USDT')
    expect(host.textContent).toContain('Updated impact')
    expect(host.querySelector('.drawer-panel')).not.toBeNull()
  })

  it('clears a selection removed by a completed refresh', () => {
    mocks.news = [item()]
    mount()

    act(() => (host.querySelector('.news-card') as HTMLButtonElement).click())
    expect(host.querySelector('.drawer-panel')).not.toBeNull()

    mocks.news = []
    act(() => root.render(<MemoryRouter><NewsIntel /></MemoryRouter>))
    expect(host.querySelector('.drawer-panel')).toBeNull()

    mocks.news = [item()]
    act(() => root.render(<MemoryRouter><NewsIntel /></MemoryRouter>))
    expect(host.querySelector('.drawer-panel')).toBeNull()
  })
})

// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SRC from './NewsIntel.tsx?raw'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const newsItem = {
  id: 'news-1', title: 'Market headline', source: 'Wire', url: 'https://example.test/news-1',
  summary: 'Summary', symbols_mentioned: ['BTC'], event_type: 'market', sentiment: 'neutral',
  relevance_score: 0.8, published_at: '2026-08-22T10:00:00Z', fetched_at: '2026-08-22T10:01:00Z', status: 'new',
}

vi.mock('../api/client', () => ({ api: {} }))
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: [newsItem], isPending: false, isError: false }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

import NewsIntel from './NewsIntel'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
})

function mount() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(<MemoryRouter><NewsIntel /></MemoryRouter>))
}

describe('News Intelligence keyboard interaction', () => {
  it('opens detail from the named card and restores focus on Escape', () => {
    mount()
    const card = host.querySelector<HTMLButtonElement>('.news-card')!
    expect(card.getAttribute('aria-label')).toBe('Open news detail: Market headline')
    card.focus()
    act(() => card.click())
    expect(host.textContent).toContain('News Detail')
    expect(host.querySelector('button[aria-label="Analyze Impact"]')).not.toBeNull()

    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(host.querySelector('.news-card')).toBe(card)
    expect(document.activeElement).toBe(card)
  })

  it('uses the filter bar as a safe focus fallback when the opener is filtered out', () => {
    mount()
    const card = host.querySelector<HTMLButtonElement>('.news-card')!
    card.focus()
    act(() => card.click())
    const reviewed = Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Reviewed')!
    act(() => reviewed.click())
    const close = host.querySelector<HTMLButtonElement>('.drawer-close')!
    act(() => close.click())
    expect(document.activeElement).toBe(Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'All'))
  })
})

describe('News Intelligence keyboard source contract', () => {
  it('uses a native, named card opener without nested controls', () => {
    expect(SRC).toContain('<button')
    expect(SRC).toContain('data-news-id={item.id}')
    expect(SRC).toContain('aria-label={`Open news detail: ${item.title}`}')
    expect(SRC).toContain('newsFilterFallback')
    expect(SRC).not.toContain('<Card\n              key={item.id}')
  })
})

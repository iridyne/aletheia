// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import SRC from './App.tsx?raw'
import CSS from './index.css?raw'

vi.mock('./api/boot', () => ({
  bootApi: { getShellBootstrap: vi.fn().mockReturnValue(new Promise(() => {})) },
}))
vi.mock('./components/BackendConnectionGuard', () => ({ default: () => null }))
vi.mock('./components/FetchProgress', () => ({ default: () => null }))
vi.mock('./components/MarketTicker', () => ({ default: () => null }))
vi.mock('./components/RouteErrorBoundary', () => ({ default: ({ children }: { children: React.ReactNode }) => children }))
vi.mock('./components/StatusCluster', () => ({ default: () => null }))
vi.mock('./dev-preview', () => ({ devPreviewLabel: () => '' }))
vi.mock('./lib/deferred', () => ({ deferTask: (task: () => void) => { task(); return () => {} } }))
vi.mock('./lib/useRealtimeUpdates', () => ({
  useRealtimeUpdates: () => ({ state: 'connected', malformedFrameCount: 0, unknownCategoryCount: 0, lastFrameAt: null }),
}))
vi.mock('./pages/Overview', () => ({ default: () => <div>overview</div> }))

let mediaMatches = true
let mediaListener: ((event: MediaQueryListEvent) => void) | undefined

beforeEach(() => {
  mediaMatches = true
  mediaListener = undefined
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation(() => ({
    matches: mediaMatches,
    media: '(max-width: 700px)',
    onchange: null,
    addEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => { mediaListener = listener },
    removeEventListener: () => undefined,
  })))
})

afterEach(() => {
  document.body.replaceChildren()
  vi.unstubAllGlobals()
})

describe('mobile navigation accessibility contract', () => {
  it('contains the mobile drawer and keeps closed navigation non-interactive', () => {
    expect(SRC).toContain('aria-expanded={isMobile ? sidebarOpen : undefined}')
    expect(SRC).toContain('aria-controls="primary-navigation"')
    expect(SRC).toContain('useDialogFocus(sidebarOpen && isMobile')
    expect(SRC).toContain('ref={sidebarRef}')
    expect(CSS).toContain('visibility: hidden')
    expect(CSS).toContain('.sidebar.open { transform: translateX(0); visibility: visible;')
  })

  it('traps mobile focus, restores the opener, and clears modal state on resize', () => {
    const host = document.createElement('div')
    document.body.append(host)
    const root = createRoot(host)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    act(() => root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/']}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    ))

    const hamburger = host.querySelector<HTMLButtonElement>('.hamburger')!
    const sidebar = host.querySelector<HTMLElement>('#primary-navigation')!
    expect(hamburger.getAttribute('aria-expanded')).toBe('false')
    expect(sidebar.getAttribute('aria-hidden')).toBe('true')

    act(() => { hamburger.focus(); hamburger.click() })
    expect(hamburger.getAttribute('aria-expanded')).toBe('true')
    expect(sidebar.getAttribute('aria-hidden')).toBeNull()
    expect(document.activeElement).toBe(sidebar)

    const firstLink = sidebar.querySelector<HTMLAnchorElement>('a')!
    const lastFocusable = sidebar.querySelector<HTMLButtonElement>('.sidebar-toggle')!
    firstLink.focus()
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true })))
    expect(document.activeElement).toBe(lastFocusable)

    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })))
    expect(document.activeElement).toBe(firstLink)

    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(hamburger.getAttribute('aria-expanded')).toBe('false')
    expect(sidebar.getAttribute('aria-hidden')).toBe('true')
    expect(document.activeElement).toBe(hamburger)

    act(() => { hamburger.focus(); hamburger.click() })
    const backdrop = host.querySelector<HTMLElement>('.sidebar-backdrop')!
    act(() => backdrop.click())
    expect(hamburger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(hamburger)

    act(() => { hamburger.focus(); hamburger.click() })
    const routeLink = sidebar.querySelector<HTMLAnchorElement>('a[href="/command-center"]')!
    act(() => routeLink.click())
    expect(hamburger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(hamburger)

    act(() => { hamburger.focus(); hamburger.click() })
    mediaMatches = false
    act(() => mediaListener?.({ matches: false } as MediaQueryListEvent))
    expect(hamburger.getAttribute('aria-expanded')).toBeNull()
    expect(sidebar.getAttribute('aria-hidden')).toBeNull()

    act(() => root.unmount())
    host.remove()
  })
})

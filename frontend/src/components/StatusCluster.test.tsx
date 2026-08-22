// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import StatusCluster from './StatusCluster'
import SRC from './StatusCluster.tsx?raw'
import CSS from '../index.css?raw'

vi.mock('../api/client', () => ({
  api: {
    getStatus: vi.fn().mockResolvedValue({}),
    getOperatorStatus: vi.fn().mockResolvedValue({}),
    getKillSwitch: vi.fn().mockResolvedValue({ active: false }),
    getAgentRuns: vi.fn().mockResolvedValue([]),
    getHealth: vi.fn().mockResolvedValue({ issues: [] }),
    getInfraEvents: vi.fn().mockResolvedValue({ events: [] }),
    getCollectorHealth: vi.fn().mockResolvedValue(null),
    getRiskStatus: vi.fn().mockResolvedValue({ status: 'healthy' }),
    pause: vi.fn().mockResolvedValue({}),
    resume: vi.fn().mockResolvedValue({}),
    setPermissionMode: vi.fn().mockResolvedValue({}),
    restartGateway: vi.fn().mockResolvedValue({}),
  },
}))

describe('StatusCluster popover contract', () => {
  it('exposes an interactive, touch-discoverable status popover', () => {
    expect(SRC).toContain('aria-expanded={popoverOpen}')
    expect(SRC).toContain('aria-controls={popoverId}')
    expect(SRC).toContain('role="dialog"')
    expect(SRC).toContain('onPointerDown')
    expect(SRC).toContain("event.key !== 'Escape'")
    expect(SRC).toContain('tabIndex={popoverOpen ? 0 : -1}')
    expect(CSS).toContain('.status-cluster-wrap:hover .status-popover')
    expect(CSS).toContain('.status-popover.is-open')
  })

  it('opens from click, closes from Escape/outside, and restores opener focus', () => {
    const host = document.createElement('div')
    document.body.append(host)
    const root = createRoot(host)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const data = {
      gateway: { running: true, paused: false },
      permission: { mode: 'Observe' },
      live: { state: 'live_disabled', enabled: false },
      scheduler: { running: true, enabled: true, state: 'running' },
      health: { issues: ['code_updated_pending_restart:abc'] },
    }
    const realtime = { state: 'connected', malformedFrameCount: 0, unknownCategoryCount: 0, lastFrameAt: null }

    act(() => root.render(
      <QueryClientProvider client={client}>
        <StatusCluster data={data as any} realtime={realtime as any} />
      </QueryClientProvider>,
    ))

    const opener = host.querySelector<HTMLButtonElement>('.status-cluster')!
    const popover = host.querySelector<HTMLElement>('[role="dialog"]')!
    const restart = host.querySelector<HTMLButtonElement>('.sp-drift button')!
    expect(opener.tagName).toBe('BUTTON')
    expect(opener.getAttribute('aria-expanded')).toBe('false')
    expect(popover.getAttribute('aria-hidden')).toBe('true')
    expect(restart.tabIndex).toBe(-1)

    act(() => opener.click())
    expect(opener.getAttribute('aria-expanded')).toBe('true')
    expect(popover.getAttribute('aria-hidden')).toBe('false')
    expect(restart.tabIndex).toBe(0)

    act(() => opener.click())
    expect(opener.getAttribute('aria-expanded')).toBe('false')
    act(() => opener.click())
    expect(opener.getAttribute('aria-expanded')).toBe('true')

    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(opener.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(opener)
    expect(restart.tabIndex).toBe(-1)

    act(() => opener.click())
    act(() => document.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })))
    expect(opener.getAttribute('aria-expanded')).toBe('false')

    act(() => root.unmount())
    host.remove()
  })
})

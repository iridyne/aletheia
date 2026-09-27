// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import StatusCluster from './StatusCluster'
import SRC from './StatusCluster.tsx?raw'
import BOOT_SRC from '../api/boot.ts?raw'
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
    // tabIndex contract moved to DriftUpdatePill which manages the restart button
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
    expect(opener.tagName).toBe('BUTTON')
    expect(opener.getAttribute('aria-expanded')).toBe('false')
    expect(popover.getAttribute('aria-hidden')).toBe('true')

    act(() => opener.click())
    expect(opener.getAttribute('aria-expanded')).toBe('true')
    expect(popover.getAttribute('aria-hidden')).toBe('false')
    // sp-drift section removed; drift pill now lives in topbar (DriftUpdatePill)

    act(() => opener.click())
    expect(opener.getAttribute('aria-expanded')).toBe('false')
    act(() => opener.click())
    expect(opener.getAttribute('aria-expanded')).toBe('true')

    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(opener.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(opener)

    act(() => opener.click())
    act(() => document.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })))
    expect(opener.getAttribute('aria-expanded')).toBe('false')

    act(() => root.unmount())
    host.remove()
  })
})

describe('StatusCluster permission scope presentation', () => {
  it('types and renders worker and operator permissions separately', () => {
    expect(BOOT_SRC).toContain('permission_mode?: string')
    expect(SRC).toContain("const operatorPermission = String(operator?.permission_mode || data?.operator?.permission_mode || '')")
    expect(SRC).toContain('Worker / Trading permission')
    expect(SRC).toContain('Operator permission')
  })

  it('names the cycle control as the worker/trading scope', () => {
    expect(SRC).toContain('Worker / Trading permission mode:')
    expect(SRC).not.toContain('title={`Permission mode: ${permission} — click to cycle`}')
  })

  it('passes the observed scheduler snapshot into the shared master state', () => {
    expect(SRC).toContain('const schedulerEnabled = data?.scheduler?.enabled')
    expect(SRC).toContain('const schedulerRunning = data?.scheduler?.running')
    expect(SRC).toContain('schedulerEnabled,')
    expect(SRC).toContain('schedulerRunning,')
    expect(SRC).toContain('Scheduler: ${schedulerState}')
    expect(SRC).not.toContain('!paused && !!data?.scheduler?.enabled')
  })
})

describe('StatusCluster outstanding infra events', () => {
  const realtime = { state: 'connected', malformedFrameCount: 0, unknownCategoryCount: 0, lastFrameAt: null }

  function render(data: Record<string, unknown>) {
    const host = document.createElement('div')
    document.body.append(host)
    const root = createRoot(host)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    act(() => root.render(
      <QueryClientProvider client={client}>
        <StatusCluster data={data as any} realtime={realtime as any} />
      </QueryClientProvider>,
    ))
    return {
      host,
      cleanup: () => {
        act(() => root.unmount())
        host.remove()
      },
    }
  }

  it('test_missing_outstanding_key_renders_unavailable_not_green', () => {
    const { host, cleanup } = render({
      gateway: { running: true, paused: false },
      permission: { mode: 'Observe' },
      health: { issues: [] },
    })
    expect(host.textContent).toContain('Outstanding events unavailable')
    cleanup()
  })

  it('test_empty_outstanding_array_renders_empty_state', () => {
    const { host, cleanup } = render({
      gateway: { running: true, paused: false },
      permission: { mode: 'Observe' },
      health: { issues: [] },
      outstanding_infra_events: [],
    })
    expect(host.textContent).not.toContain('Outstanding events unavailable')
    expect(host.querySelectorAll('.sp-event-code').length).toBe(0)
    cleanup()
  })

  it('test_critical_manual_close_event_sorts_first', () => {
    const { host, cleanup } = render({
      gateway: { running: true, paused: false },
      permission: { mode: 'Observe' },
      health: { issues: [] },
      outstanding_infra_events: [
        { created_at: '2026-01-01T00:00:00Z', severity: 'warning', source: 'position_manager', code: 'position_manager_stable_escalation', message: 'stable escalation' },
        { created_at: '2026-01-01T00:00:01Z', severity: 'critical', source: 'position_manager', code: 'manual_close_reconciliation_required', message: 'manual close needed' },
        { created_at: '2026-01-01T00:00:02Z', severity: 'info', source: 'connectivity_monitor', code: 'external_connectivity_restored', message: 'restored' },
      ],
    })
    const codes = Array.from(host.querySelectorAll('.sp-event-code')).map(node => node.textContent)
    expect(codes[0]).toBe('manual_close_reconciliation_required')
    cleanup()
  })
})

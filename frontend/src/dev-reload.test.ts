// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { initDevReload } from './dev-reload'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it.each([false, true])('recovers admission without health/reload; build reload respects disposal=%s', async disposeBeforeReload => {
  vi.useFakeTimers()
  const sources: Array<EventTarget & { readyState: number; onerror?: () => void }> = []
  vi.stubGlobal('EventSource', class extends EventTarget {
    static CLOSED = 2
    readyState = 0
    close() { this.readyState = 2 }
    constructor() { super(); sources.push(this) }
  })
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: true }) })
  vi.stubGlobal('fetch', fetch)
  const reload = vi.fn()
  vi.stubGlobal('window', { location: { reload } })
  const dispose = initDevReload()
  await vi.advanceTimersByTimeAsync(0)
  sources[0].readyState = 2
  sources[0].onerror?.()
  sources[0].onerror?.()
  await vi.advanceTimersByTimeAsync(3000)
  expect(sources).toHaveLength(2)
  sources[1].dispatchEvent(new Event('backend-ready'))
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/dev/reload/status', { cache: 'no-store' })
  expect(reload).not.toHaveBeenCalled()
  sources[1].dispatchEvent(new Event('frontend-built'))
  if (disposeBeforeReload) dispose()
  await vi.advanceTimersByTimeAsync(300)
  expect(reload).toHaveBeenCalledTimes(disposeBeforeReload ? 0 : 1)
  sources[1].readyState = 2
  sources[1].onerror?.()
  dispose()
  await vi.advanceTimersByTimeAsync(30000)
  expect(sources).toHaveLength(2)
})

it('does not connect after disposal while the enablement request is pending', async () => {
  let resolve!: (value: unknown) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise(r => { resolve = r })))
  const source = vi.fn()
  vi.stubGlobal('EventSource', source)
  const dispose = initDevReload()
  dispose()
  resolve({ ok: true, json: async () => ({ enabled: true }) })
  await new Promise(r => setTimeout(r, 0))
  expect(source).not.toHaveBeenCalled()
})

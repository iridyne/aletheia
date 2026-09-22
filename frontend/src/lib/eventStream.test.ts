import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { connectEventStream } from './eventStream'

class FakeEventSource extends EventTarget {
  static CLOSED = 2
  static instances: FakeEventSource[] = []
  readyState = 0
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn(() => { this.readyState = 2 })
  constructor(public url: string) {
    super()
    FakeEventSource.instances.push(this)
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('recovers a terminal admission failure, with one bounded timer and the same event projection', () => {
  const receive = vi.fn()
  const dispose = connectEventStream('/events', stream => {
    stream.addEventListener('operator_event', receive)
  })
  let source = FakeEventSource.instances[0]
  source.readyState = 2 // HTTP 503: native EventSource fails CLOSED, not CONNECTING.
  source.onerror?.()
  source.onerror?.()
  expect(vi.getTimerCount()).toBe(1)
  for (const delay of [3000, 6000, 12000, 24000, 30000, 30000]) {
    const count = FakeEventSource.instances.length
    vi.advanceTimersByTime(delay - 1)
    expect(FakeEventSource.instances).toHaveLength(count)
    vi.advanceTimersByTime(1)
    expect(FakeEventSource.instances).toHaveLength(count + 1)
    source = FakeEventSource.instances.at(-1)!
    source.readyState = 2
    source.onerror?.()
  }
  vi.advanceTimersByTime(30000)
  source = FakeEventSource.instances.at(-1)!
  source.readyState = 1
  source.onopen?.()
  source.dispatchEvent(new Event('operator_event'))
  expect(receive).toHaveBeenCalledTimes(1)
  source.readyState = 2
  source.onerror?.()
  const count = FakeEventSource.instances.length
  vi.advanceTimersByTime(3000)
  expect(FakeEventSource.instances).toHaveLength(count + 1)
  dispose()
  expect(FakeEventSource.instances.at(-1)!.close).toHaveBeenCalledOnce()
})

it('leaves native CONNECTING retries alone and cancels CLOSED retry on disposal', () => {
  const dispose = connectEventStream('/events', () => {})
  const source = FakeEventSource.instances[0]
  source.onerror?.()
  expect(vi.getTimerCount()).toBe(0)
  expect(source.close).not.toHaveBeenCalled()
  source.readyState = 2
  source.onerror?.()
  expect(vi.getTimerCount()).toBe(1)
  dispose()
  expect(vi.getTimerCount()).toBe(0)
  vi.advanceTimersByTime(30000)
  expect(FakeEventSource.instances).toHaveLength(1)
})

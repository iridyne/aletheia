import { afterEach, describe, expect, it, vi } from 'vitest'
import { MUTATION_ERROR_EVENT, reportMutationError, safeErrorText } from './mutationErrors'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('mutation error reporting', () => {
  it('redacts credential-shaped values and bounds the display text', () => {
    expect(safeErrorText(new Error('token=owner-secret api_key=abc Bearer abc.def')))
      .toBe('token: [redacted] api_key: [redacted] Bearer [redacted]')
    expect(safeErrorText(new Error('Authorization: Bearer abc.def')))
      .toBe('Authorization: [redacted]')
    expect(safeErrorText(new Error('x'.repeat(500))).length).toBe(240)
  })

  it('publishes a visible event for mutations without a local error surface', () => {
    const dispatchEvent = vi.fn()
    vi.stubGlobal('window', { dispatchEvent })

    reportMutationError(new Error('backend unavailable'))

    expect(dispatchEvent).toHaveBeenCalledTimes(1)
    expect(dispatchEvent.mock.calls[0][0]).toMatchObject({
      type: MUTATION_ERROR_EVENT,
      detail: { message: 'Action failed: backend unavailable' },
    })
  })
})

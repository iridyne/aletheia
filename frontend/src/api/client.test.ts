import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Remote Control Token browser storage', () => {
  it('reports a successful write without returning the token', () => {
    const setItem = vi.fn()
    vi.stubGlobal('window', {
      localStorage: {
        getItem: vi.fn(),
        removeItem: vi.fn(),
        setItem,
      },
    })

    const result = api.setRemoteControlToken('  owner-token  ')

    expect(setItem).toHaveBeenCalledWith('aletheia.remoteControlToken', 'owner-token')
    expect(result).toEqual({ ok: true })
    expect(JSON.stringify(result)).not.toContain('owner-token')
  })

  it('reports browser storage rejection without exposing the token', () => {
    vi.stubGlobal('window', {
      localStorage: {
        getItem: vi.fn(),
        removeItem: vi.fn(),
        setItem: vi.fn(() => {
          throw new Error('storage disabled')
        }),
      },
    })

    const result = api.setRemoteControlToken('owner-token')

    expect(result).toEqual({
      ok: false,
      error: 'Browser storage is unavailable; the Remote Control Token was not saved.',
    })
    expect(JSON.stringify(result)).not.toContain('owner-token')
  })
})

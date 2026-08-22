import { describe, expect, it, vi } from 'vitest'
import { runCommandAction, executeKillSwitch } from './CommandPalette'
import SRC from './CommandPalette.tsx?raw'

describe('Command Palette mutation truthfulness', () => {
  it('turns a rejected command promise into an operator-facing error result', async () => {
    const result = await runCommandAction(
      () => Promise.reject(new Error('forced backend 422')),
    )

    expect(result).toEqual({ ok: false, error: 'forced backend 422' })
  })

  it('reports success only after the command promise resolves', async () => {
    const { promise: pending, resolve: release } = Promise.withResolvers<void>()
    let settled = false

    const resultPromise = runCommandAction(() => pending).then((result) => {
      settled = true
      return result
    })
    await Promise.resolve()
    expect(settled).toBe(false)

    release()
    await expect(resultPromise).resolves.toEqual({ ok: true })
  })

  it('awaits trust commands, refreshes their facts, and renders sticky inline failure', () => {
    expect(SRC).toContain('const result = await runCommandAction(command.action)')
    expect(SRC).toContain('setCommandError(`${command.label}: ${result.error}`)')
    expect(SRC).toContain('await (paused ? api.pause() : api.resume())')
    expect(SRC).not.toContain('useMutation')
    expect(SRC).toContain('executeKillSwitch(active, api.setKillSwitch')
    expect(SRC).not.toContain("qc.setQueryData(['kill-switch']")
    expect(SRC).toContain("if (active && confirmed.active !== true) throw new Error('Kill switch engage was not confirmed by backend')")
    expect(SRC).toContain("if (!active && confirmed.active !== false) throw new Error('Kill switch release was not confirmed by backend')")
    expect(SRC).toContain("refetch({ queryKey: ['kill-switch'] }, { throwOnError: true })")
    expect(SRC).toContain("refetch({ queryKey: ['risk-status'] }, { throwOnError: true })")
    expect(SRC).toContain("queryKey: ['ui-shell-bootstrap']")
    expect(SRC).toContain('initialOpen = false')
    expect(SRC).toContain('await api.startAgentRun')
    expect(SRC).toContain("queryKey: ['agent-runs']")
    expect(SRC).toContain('if (executingRef.current) return')
    expect(SRC).toContain('disabled={executingCommand !== null}')
    expect(SRC).toContain('className="lever-err"')
    expect(SRC).toContain('role="alert"')
  })
})

describe('Kill-switch mutation authority contract (regression proof for #1190)', () => {
  it('POST success + GET success: mutation completes, cache updated by refetch', async () => {
    const mockApi = vi.fn(async (active: boolean) => ({ active }))
    let cacheState: unknown = { active: false }
    const mockRefetch = vi.fn(async () => {
      cacheState = { active: true, refetched: true }
      return Promise.resolve()
    })
    const mockInvalidate = vi.fn(async () => {})

    await executeKillSwitch(true, mockApi, mockRefetch, mockInvalidate)

    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(mockApi).toHaveBeenCalledWith(true, 'cmd_palette', false)
    expect(mockRefetch).toHaveBeenCalledTimes(2)
    expect(mockRefetch).toHaveBeenCalledWith({ queryKey: ['kill-switch'] }, { throwOnError: true })
    expect(mockRefetch).toHaveBeenCalledWith({ queryKey: ['risk-status'] }, { throwOnError: true })
    expect(mockInvalidate).toHaveBeenCalledTimes(1)
  })

  it('POST success + GET failure: mutation throws, prior cache preserved', async () => {
    const mockApi = vi.fn(async (active: boolean) => ({ active }))
    const mockRefetch = vi.fn(async () => {
      throw new Error('GET /api/kill-switch failed: 503')
    })
    const mockInvalidate = vi.fn(async () => {})

    await expect(
      executeKillSwitch(true, mockApi, mockRefetch, mockInvalidate),
    ).rejects.toThrow('GET /api/kill-switch failed: 503')

    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(mockRefetch).toHaveBeenCalled()
  })

  it('POST failure: mutation throws, no cache update', async () => {
    const mockApi = vi.fn(async () => {
      throw new Error('POST /api/kill-switch failed: 422 resource_locked')
    })
    const mockRefetch = vi.fn()
    const mockInvalidate = vi.fn()

    await expect(
      executeKillSwitch(true, mockApi, mockRefetch, mockInvalidate),
    ).rejects.toThrow('POST /api/kill-switch failed: 422 resource_locked')

    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(mockRefetch).not.toHaveBeenCalled()
    expect(mockInvalidate).not.toHaveBeenCalled()
  })

  it('POST acknowledges opposite direction: throws, no cache update', async () => {
    const mockApi = vi.fn(async () => ({ active: false }))
    const mockRefetch = vi.fn()
    const mockInvalidate = vi.fn()

    await expect(
      executeKillSwitch(true, mockApi, mockRefetch, mockInvalidate),
    ).rejects.toThrow('Kill switch engage was not confirmed by backend')

    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(mockRefetch).not.toHaveBeenCalled()
  })

  it('Concurrent transitions: each call independently validates and refetches', async () => {
    const mockApi = vi.fn(async (active: boolean) => ({ active }))
    const mockRefetch = vi.fn(async () => {})
    const mockInvalidate = vi.fn(async () => {})

    const [result1, result2] = await Promise.all([
      executeKillSwitch(true, mockApi, mockRefetch, mockInvalidate),
      executeKillSwitch(false, mockApi, mockRefetch, mockInvalidate),
    ])

    expect(result1).toBeUndefined()
    expect(result2).toBeUndefined()
    expect(mockApi).toHaveBeenCalledTimes(2)
    expect(mockApi).toHaveBeenCalledWith(true, 'cmd_palette', false)
    expect(mockApi).toHaveBeenCalledWith(false, 'cmd_palette', true)
    expect(mockRefetch).toHaveBeenCalledTimes(4)
  })
})

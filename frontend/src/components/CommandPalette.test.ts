import { describe, expect, it } from 'vitest'
import { runCommandAction } from './CommandPalette'
import SRC from './CommandPalette.tsx?raw'

describe('Command Palette mutation truthfulness', () => {
  it('turns a rejected command promise into an operator-facing error result', async () => {
    const result = await runCommandAction(
      () => Promise.reject(new Error('forced backend 422')),
    )

    expect(result).toEqual({ ok: false, error: 'forced backend 422' })
  })

  it('reports success only after the command promise resolves', async () => {
    let release: (() => void) | undefined
    const pending = new Promise<void>((resolve) => { release = resolve })
    let settled = false

    const resultPromise = runCommandAction(() => pending).then((result) => {
      settled = true
      return result
    })
    await Promise.resolve()
    expect(settled).toBe(false)

    release?.()
    await expect(resultPromise).resolves.toEqual({ ok: true })
  })

  it('awaits trust commands, refreshes their facts, and renders sticky inline failure', () => {
    expect(SRC).toContain('const result = await runCommandAction(command.action)')
    expect(SRC).toContain('setCommandError(`${command.label}: ${result.error}`)')
    expect(SRC).toContain('await (paused ? api.pause() : api.resume())')
    expect(SRC).not.toContain('useMutation')
    expect(SRC).toContain('await api.setKillSwitch')
    expect(SRC).toContain("qc.setQueryData(['kill-switch'], confirmedState)")
    expect(SRC).toContain("queryKey: ['kill-switch']")
    expect(SRC).toContain("queryKey: ['risk-status']")
    expect(SRC).toContain('await api.startAgentRun')
    expect(SRC).toContain("queryKey: ['agent-runs']")
    expect(SRC).toContain('if (executingRef.current) return')
    expect(SRC).toContain('disabled={executingCommand !== null}')
    expect(SRC).toContain('className="lever-err"')
    expect(SRC).toContain('role="alert"')
  })
})

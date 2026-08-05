import { describe, expect, it } from 'vitest'
import { operatorMutationError } from './Overview'
import SRC from './Overview.tsx?raw'

describe('Overview operator mutation truthfulness', () => {
  it('keeps the action identity and a usable rejection reason', () => {
    expect(operatorMutationError('Pause gateway failed', new Error('backend 503')))
      .toBe('Pause gateway failed: backend 503')
    expect(operatorMutationError('Resume gateway failed', 'startup gate blocked'))
      .toBe('Resume gateway failed: startup gate blocked')
    expect(operatorMutationError('Pause gateway failed', null))
      .toBe('Pause gateway failed: request failed')
  })

  it('renders sticky failure and prevents overlapping pause/resume commands', () => {
    expect(SRC).toContain("operatorMutationError('Pause gateway failed', error)")
    expect(SRC).toContain("operatorMutationError('Resume gateway failed', error)")
    expect(SRC).toContain('const gatewayCommandPending = pauseMut.isPending || resumeMut.isPending')
    expect(SRC).toContain('disabled={gatewayCommandPending || paused || !running}')
    expect(SRC).toContain('disabled={gatewayCommandPending || !paused}')
    expect(SRC).toContain('role="alert"')
    expect(SRC.match(/onMutate: \(\) => setGatewayCommandError\(''\)/g)).toHaveLength(2)
    expect(SRC).not.toContain("qc.setQueryData(['status']")
  })
})

import { describe, expect, it } from 'vitest'
import SRC from './SerenityLens.tsx?raw'

describe('Serenity Lens mutation trust contract', () => {
  it('keeps every user-triggered mutation on a visible failure path', () => {
    for (const mutation of [
      'retryMut',
      'cancelMut',
      'statusMut',
      'deepDiveMut',
      'promoteMut',
      'feedbackMut',
      'parseMut',
      'saveMut',
    ]) {
      expect(SRC).toContain(`const ${mutation} = useMutation`)
    }

    expect(SRC).toContain('const [retryError, setRetryError]')
    expect(SRC).toContain('const [cancelError, setCancelError]')
    expect(SRC).toContain('const [promoteErrors, setPromoteErrors]')
    expect(SRC).toContain('safeErrorText')
    expect(SRC).toContain('role="alert"')
  })
})

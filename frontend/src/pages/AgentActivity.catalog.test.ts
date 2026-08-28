import { describe, expect, it } from 'vitest'
import SRC from './AgentActivity.tsx?raw'

describe('Agent Activity catalog admission', () => {
  it('renders server-provided blocked reasons and disables unavailable agents', () => {
    expect(SRC).toContain('a.runnable === false')
    expect(SRC).toContain('a.blocked_reason')
    expect(SRC).toContain('disabled={run.isPending || a.runnable === false}')
    expect(SRC).toContain('aria-label={a.blocked_reason ? `${a.name}: ${a.blocked_reason}` : a.name}')
  })

  it('does not render cached steps after the run identity is lost', () => {
    expect(SRC).toContain('{!runNotFound && (steps as any[]).length > 0 && (')
  })
})

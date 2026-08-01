import { describe, expect, it } from 'vitest'
import { preferredAccessMethod } from './accessMethod'

describe('preferredAccessMethod', () => {
  it('prefers the exact routed CLI identity when both methods are enabled', () => {
    expect(preferredAccessMethod({
      oauthName: 'openai_native',
      cliName: 'codex_cli',
      defaultName: 'codex_cli',
      deepName: 'openai_native',
      fastName: null,
      oauthEnabled: true,
      cliEnabled: true,
    })).toBe('cli')
  })

  it('falls back to the enabled method when neither identity is routed', () => {
    expect(preferredAccessMethod({
      oauthName: 'anthropic_native',
      cliName: 'claude_cli',
      defaultName: 'databricks',
      deepName: null,
      fastName: null,
      oauthEnabled: false,
      cliEnabled: true,
    })).toBe('cli')
  })
})

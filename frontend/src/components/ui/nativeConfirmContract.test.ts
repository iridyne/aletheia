import { describe, expect, it } from 'vitest'

const sources = import.meta.glob('../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

describe('confirmation dialog contract', () => {
  it('keeps browser-native confirm out of production source', () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.includes('.test.'))
      .filter(([, source]) => /(?:window\.)?\bconfirm\s*\(\s*[\x27"`]/.test(source))
      .map(([path]) => path)

    expect(offenders).toEqual([])
  })
})

import { describe, expect, it } from 'vitest'

const SOURCE_ENTRIES = Object.entries(
  import.meta.glob('../../**/*.tsx', {
    eager: true,
    query: '?raw',
    import: 'default',
  }),
)
  .filter(([path]) => !path.includes('.test.'))

const SOURCES = SOURCE_ENTRIES
  .map(([, source]) => source as string)

const SWITCH_SITES = SOURCES.flatMap(source =>
  source.match(/<(?:Toggle|Switch)\b[\s\S]*?\/>/g) ?? [],
)

describe('Settings switch persistence contract', () => {
  it('keeps the checked-in switch inventory explicit', () => {
    expect(SWITCH_SITES).toHaveLength(65)
    expect(SWITCH_SITES.every(site => /\bpersistence\s*=/.test(site))).toBe(true)
    const modes = SWITCH_SITES.map(site => site.match(/\bpersistence\s*=\s*"(immediate|staged)"/)?.[1])
    expect(modes).not.toContain(undefined)
    expect(modes.filter(mode => mode === 'immediate')).toHaveLength(28)
    expect(modes.filter(mode => mode === 'staged')).toHaveLength(37)
  })

  it('keeps staged switches inside a draft boundary', () => {
    for (const [path, rawSource] of SOURCE_ENTRIES) {
      const source = rawSource as string
      if (!source.includes('persistence="staged"')) continue
      if (path.endsWith('/pages/SerenityLens.tsx')) {
        expect(source).toContain('Save to Profile')
      } else {
        expect(path).toContain('/features/settings/')
        expect(source).toContain('useSettingsDraftLifecycle')
      }
    }
  })
})

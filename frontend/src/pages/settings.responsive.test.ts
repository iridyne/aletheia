import { describe, expect, it } from 'vitest'
import CSS from '../index.css?raw'
import SETTINGS from './Settings.tsx?raw'

function mediaBlock(source: string, query: string, from = 0): string {
  const start = source.indexOf(`@media ${query}`, from)
  expect(start, `${query} media query is missing`).toBeGreaterThan(-1)
  const open = source.indexOf('{', start)
  let depth = 0
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1
    if (source[i] === '}') {
      depth -= 1
      if (depth === 0) return source.slice(open + 1, i)
    }
  }
  throw new Error(`unterminated ${query} media query`)
}

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ')
}

describe('Settings responsive single-column layout', () => {
  const css = stripComments(CSS)
  const shellStart = css.indexOf('.settings-shell {')
  expect(shellStart).toBeGreaterThan(-1)
  const mobile = mediaBlock(css, '(max-width: 767px)', shellStart)

  it('renders a single-column full-width container on desktop and tablet with overflow protection', () => {
    const shellBase = css.slice(shellStart, shellStart + 400)
    expect(shellBase).toContain('display: flex')
    expect(shellBase).toContain('flex-direction: column')
    expect(shellBase).toContain('max-width: 1200px')
    expect(shellBase).toContain('overflow-x: clip')
    expect(css).toContain('.settings-shell,')
    expect(css).toContain('min-width: 0;')
    expect(css).toContain('max-width: 100%;')
    expect(css).toMatch(/\.settings-panel \.mini-table\s*\{[\s\S]*?overflow-x:\s*auto/)
  })

  it('adapts Category Hub grid and breadcrumbs cleanly for mobile screens', () => {
    expect(css).toContain('.settings-hub-grid')
    expect(css).toMatch(/grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(280px,\s*1fr\)\)/)
    expect(mobile).toMatch(/\.settings-hub-grid\s*\{[\s\S]*?grid-template-columns:\s*1fr/)
    expect(mobile).toMatch(/\.settings-breadcrumb-bar\s*\{[\s\S]*?flex-direction:\s*column/)
    expect(mobile).toMatch(/\.settings-sibling-switcher\s*\{[\s\S]*?width:\s*100%/)
  })

  it('enforces touch target sizes and keyboard focus indicators for accessibility', () => {
    expect(css).toContain('.settings-hub-card:focus-visible')
    expect(css).toContain('.settings-breadcrumb-back:focus-visible')
    expect(css).toContain('.settings-pill:focus-visible')
    expect(css).toMatch(/\.settings-hub-card\s*\{[\s\S]*?min-height:\s*110px/)
    expect(css).toMatch(/\.settings-breadcrumb-back\s*\{[\s\S]*?min-height:\s*32px/)
    expect(css).toMatch(/\.settings-pill\s*\{[\s\S]*?min-height:\s*32px/)
  })

  it('wires top Tabs, CategoryHub, breadcrumbs, and sibling pills in Settings.tsx', () => {
    expect(SETTINGS).toContain('<Tabs tabs={tabsDef} value={tab}')
    expect(SETTINGS).toContain('<CategoryHub')
    expect(SETTINGS).toContain('className="settings-breadcrumb-bar"')
    expect(SETTINGS).toContain('className="settings-sibling-switcher"')
    expect(SETTINGS).toContain('aria-label="Settings section breadcrumb"')
  })
})

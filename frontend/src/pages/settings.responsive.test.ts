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

describe('Settings responsive shell', () => {
  const css = stripComments(CSS)
  const settingsStart = css.indexOf('.settings-nav {')
  const tablet = mediaBlock(css, '(max-width: 1023px)', settingsStart)
  const mobile = mediaBlock(css, '(max-width: 767px)', settingsStart)

  it('keeps the persisted nav width on desktop and caps only expanded tablet nav', () => {
    const navBase = css.slice(settingsStart, css.indexOf('@media (max-width: 1023px)', settingsStart))
    expect(navBase).toContain('width: var(--settings-nav-width)')
    expect(navBase).toContain('flex: 0 0 var(--settings-nav-width)')
    expect(tablet).toMatch(/\.settings-nav:not\(\.is-collapsed\)\s*\{[\s\S]*?width:\s*220px !important/)
    expect(tablet).toMatch(/\.settings-nav:not\(\.is-collapsed\)\s*\{[\s\S]*?flex-basis:\s*220px !important/)
    expect(tablet).not.toMatch(/\.settings-nav\s*\{[\s\S]*?width:\s*220px !important/)
  })

  it('forces both persisted states to the 52px mobile rail and protects panel width', () => {
    expect(mobile).toMatch(/\.settings-nav,\s*\.settings-nav:not\(\.is-collapsed\)\s*\{[\s\S]*?width:\s*52px !important/)
    expect(mobile).toMatch(/\.settings-nav,\s*\.settings-nav:not\(\.is-collapsed\)\s*\{[\s\S]*?flex-basis:\s*52px !important/)
    expect(mobile).toContain('.settings-nav .settings-nav-heading')
    expect(mobile).toContain('.settings-nav .settings-nav-copy')
    expect(mobile).toContain('.settings-nav-item')
    expect(mobile).toContain('width: 36px')
    expect(mobile).toContain('overflow: visible')
    expect(mobile).toContain('.settings-panel { margin-inline-start: var(--space-3); }')
    expect(css).toContain('overflow-x: clip')
    expect(css).toMatch(/\.settings-panel \.mini-table\s*\{[\s\S]*?overflow-x:\s*auto/)
  })

  it('retains persisted expanded/collapsed state and keyboard labels/tooltips', () => {
    expect(SETTINGS).toContain("const SETTINGS_NAV_KEY = 'aletheia.settings.navLayout.v1'")
    expect(SETTINGS).toContain('window.localStorage.getItem(SETTINGS_NAV_KEY)')
    expect(SETTINGS).toContain('window.localStorage.setItem(SETTINGS_NAV_KEY')
    expect(SETTINGS).toMatch(/if \(parsed\.collapsed\) \{[\s\S]*?setIsNavCollapsed\(true\)[\s\S]*?setNavWidth\(SETTINGS_NAV_COLLAPSED\)/)
    expect(SETTINGS).toMatch(/setIsNavCollapsed\(false\)[\s\S]*?setNavWidth\(width\)/)
    expect(SETTINGS).toContain("aria-label={isNavCollapsed ? 'Expand Settings navigation' : 'Collapse Settings navigation'}")
    expect(SETTINGS).toContain('aria-expanded={!isNavCollapsed}')
    expect(SETTINGS).toContain('aria-label={t.label}')
    expect(SETTINGS).toContain('title={isNavCollapsed ? `${t.label}: ${t.description}` : undefined}')
    expect(css).toContain('.settings-nav-toggle:focus-visible')
  })
})

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RouteLoading } from './App'
import CSS from './index.css?raw'

describe('route loading state', () => {
  it('keeps an accessible, visible skeleton for lazy route transitions', () => {
    const html = renderToStaticMarkup(<RouteLoading />)
    expect(html).toContain('route-state route-skeleton')
    expect(html).toContain('role="status"')
    expect(html).toContain('aria-label="Loading page"')
    expect(CSS).toContain('.route-skeleton {')
    expect(CSS).toContain("content: 'Loading page…'")
  })
})

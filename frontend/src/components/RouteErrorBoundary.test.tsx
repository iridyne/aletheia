// @vitest-environment happy-dom
import { Component, type ReactNode } from 'react'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RouteErrorBoundary from './RouteErrorBoundary'

class BrokenRoute extends Component {
  render(): ReactNode {
    throw new Error('broken route')
  }
}

describe('RouteErrorBoundary', () => {
  afterEach(() => vi.restoreAllMocks())

  it('keeps the shell recoverable when a route render fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const container = document.createElement('div')
    const root = createRoot(container)

    await act(async () => {
      root.render(
        <RouteErrorBoundary destination="/broken">
          <BrokenRoute />
        </RouteErrorBoundary>,
      )
    })

    expect(container.textContent).toContain('Page could not be rendered')
    expect(container.textContent).toContain('/broken')
    expect(container.querySelector('button')?.textContent).toBe('Retry')
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/')
    await act(async () => root.unmount())
  })
})

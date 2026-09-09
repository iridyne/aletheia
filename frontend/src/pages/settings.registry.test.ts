// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../components/ui'
import Settings from './Settings'

let root: Root | undefined
let container: HTMLDivElement
let client: QueryClient

async function renderSettings(url: string, paperMode = 'bitget_demo') {
  window.history.replaceState(null, '', url)
  client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } })
  client.setQueryData(['status'], { mode: 'paper' })
  client.setQueryData(['settings'], { exchange: { paper_execution_mode: paperMode }, trading: {}, agent: {} })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  await act(async () => {
    root?.render(createElement(QueryClientProvider, { client },
      createElement(BrowserRouter, null, createElement(ConfirmProvider, null, createElement(Settings)))))
  })
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  root = undefined
  container?.remove()
  client?.clear()
  vi.restoreAllMocks()
})

it('searches by an alternate term and navigates to the rendered section', async () => {
  await renderSettings('/settings?tab=risk')
  const input = container.querySelector<HTMLInputElement>('[aria-label="Search settings"]')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'pnl baseline')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  const result = container.querySelector('[role="option"]')!
  expect(result.textContent).toContain('Capital Events')
  await act(async () => { result.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })) })
  expect(new URLSearchParams(window.location.search).get('section')).toBe('capital-events')
  expect(container.querySelector('#settings-section-capital-events')?.textContent).toContain('Net deposits')
  expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Execution')
})

it('preserves the Universe deep link and scrolls to its rendered section', async () => {
  const scroll = vi.spyOn(Element.prototype, 'scrollIntoView')
  await renderSettings('/settings?tab=risk&section=universe')
  const section = container.querySelector('#settings-section-universe')!
  expect(section.textContent).toContain('Universe & Candidate Attention')
  await vi.waitFor(() => { expect(scroll.mock.contexts).toContain(section) })
})

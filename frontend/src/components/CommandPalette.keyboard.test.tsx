// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import CommandPalette from './CommandPalette'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

afterEach(() => {
  vi.restoreAllMocks()
  document.body.replaceChildren()
})

it('closes on Escape and restores focus to the keyboard opener', async () => {
  const opener = document.createElement('button')
  const host = document.createElement('div')
  document.body.append(opener, host)
  opener.focus()
  const root = createRoot(host)

  await act(async () => root.render(
    <StrictMode>
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <CommandPalette initialOpen />
        </QueryClientProvider>
      </MemoryRouter>
    </StrictMode>,
  ))

  const dialog = document.querySelector<HTMLElement>('[role="dialog"]')
  const input = document.querySelector<HTMLInputElement>('.cmd-input')
  expect(dialog?.getAttribute('aria-modal')).toBe('true')
  expect(input).not.toBeNull()
  expect(document.activeElement).toBe(input)

  const forwardTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
  document.dispatchEvent(forwardTab)
  expect(forwardTab.defaultPrevented).toBe(true)
  expect(document.activeElement).toBe(input)
  const reverseTab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
  document.dispatchEvent(reverseTab)
  expect(reverseTab.defaultPrevented).toBe(true)
  expect(document.activeElement).toBe(input)

  await act(async () => input?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))

  expect(document.querySelector('[role="dialog"]')).toBeNull()
  expect(document.activeElement).toBe(opener)
  await act(async () => root.unmount())
})

it('refuses Escape, backdrop, and shortcut dismissal while a command is pending', async () => {
  const { promise, resolve } = Promise.withResolvers<void>()
  vi.spyOn(api, 'pause').mockReturnValue(promise)
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)

  await act(async () => root.render(
    <StrictMode>
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <CommandPalette initialOpen />
        </QueryClientProvider>
      </MemoryRouter>
    </StrictMode>,
  ))

  const pause = [...document.querySelectorAll<HTMLElement>('[cmdk-item]')]
    .find(item => item.textContent === 'Pause Gateway')
  await act(async () => pause?.click())
  expect(document.body.textContent).toContain('Pause Gateway…')

  const input = document.querySelector<HTMLInputElement>('.cmd-input')
  const backdrop = document.querySelector<HTMLElement>('.cmd-backdrop')
  await act(async () => input?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  await act(async () => backdrop?.click())
  await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true })))
  expect(document.querySelector('[role="dialog"]')).not.toBeNull()

  await act(async () => resolve())
  expect(document.querySelector('[role="dialog"]')).toBeNull()
  await act(async () => root.unmount())
})

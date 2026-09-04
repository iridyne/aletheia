// @vitest-environment happy-dom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ToastProvider } from '../components/ui/Toast'

const mocks = vi.hoisted(() => ({
  getPredictionMarkets: vi.fn(), getPredictionPreferences: vi.fn(), getPredictionRuns: vi.fn(),
  getPredictionStatus: vi.fn(), refreshPrediction: vi.fn(), updatePredictionPreferences: vi.fn(),
}))

vi.mock('../api/client', () => ({ api: mocks }))

import PredictionLens from './PredictionLens'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  mocks.getPredictionStatus.mockResolvedValue({
    enabled: true, last_run: null, categories: ['macro'], total_opportunities: 0,
    open_opportunities: 0, observer_interval_minutes: 30,
  })
  mocks.getPredictionMarkets.mockResolvedValue([])
  mocks.getPredictionRuns.mockResolvedValue([])
})

afterEach(() => {
  act(() => root?.unmount())
  client.clear()
  host?.remove()
  vi.clearAllMocks()
})

function button(label: string) {
  return Array.from(host.querySelectorAll('button')).find(candidate => candidate.textContent?.includes(label)) as HTMLButtonElement
}

function setInput(input: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function renderLens() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><ToastProvider><PredictionLens /></ToastProvider></QueryClientProvider></MemoryRouter>,
  ))
  await act(async () => {})
  act(() => button('Preferences').click())
}

it('fails closed when the initial preferences read fails', async () => {
  mocks.getPredictionPreferences.mockRejectedValue(new Error('preferences unavailable'))
  await renderLens()

  expect(host.textContent).toContain('Observer preferences unavailable')
  expect(host.textContent).not.toContain('every 30 minutes')
  const input = host.querySelector('input[placeholder^="Add categories"]') as HTMLInputElement
  expect(input.disabled).toBe(true)
  expect(button('Add').disabled).toBe(true)
  setInput(input, 'energy')
  act(() => {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    button('Add').click()
  })
  expect(mocks.updatePredictionPreferences).not.toHaveBeenCalled()
})

it('allows the first category after a successful empty preferences read', async () => {
  mocks.getPredictionPreferences.mockResolvedValue({ categories: [], observer_interval_minutes: 30 })
  mocks.updatePredictionPreferences.mockResolvedValue({ status: 'ok', categories: ['energy'] })
  await renderLens()

  const input = host.querySelector('input[placeholder^="Add categories"]') as HTMLInputElement
  setInput(input, 'energy')
  await act(async () => button('Add').click())

  expect(mocks.updatePredictionPreferences).toHaveBeenCalledWith({ categories: ['energy'] })
})

it('keeps a preference write locked across tab remounts and reports PATCH success when refresh fails', async () => {
  let resolveSave!: (value: { status: string; categories: string[] }) => void
  const save = new Promise<{ status: string; categories: string[] }>(resolve => { resolveSave = resolve })
  mocks.getPredictionPreferences
    .mockResolvedValueOnce({ categories: ['macro'], observer_interval_minutes: 30 })
    .mockRejectedValue(new Error('temporary preferences read failure'))
  mocks.updatePredictionPreferences.mockReturnValue(save)
  await renderLens()
  const input = host.querySelector('input[placeholder^="Add categories"]') as HTMLInputElement
  setInput(input, 'energy')
  await act(async () => button('Add').click())
  expect(mocks.updatePredictionPreferences).toHaveBeenCalledWith({ categories: ['macro', 'energy'] })

  act(() => button('Market Radar').click())
  act(() => button('Preferences').click())
  const remounted = host.querySelector('input[placeholder^="Add categories"]') as HTMLInputElement
  expect(remounted.value).toBe('energy')
  expect(remounted.disabled).toBe(true)
  await act(async () => button('Add').click())
  expect(mocks.updatePredictionPreferences).toHaveBeenCalledTimes(1)

  resolveSave({ status: 'ok', categories: ['macro', 'energy'] })
  await act(async () => {})
  expect(remounted.value).toBe('')
  expect(remounted.disabled).toBe(true)
  expect(button('Clear all categories').disabled).toBe(true)
  expect(host.textContent).toContain('Observer preferences are stale')
  setInput(remounted, 'politics')
  await act(async () => {
    button('Add').click()
    button('Clear all categories').click()
  })
  expect(mocks.updatePredictionPreferences).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('energy')
  expect(host.textContent).toContain('Preferences saved')
  expect(host.textContent).not.toContain('Save failed')
})

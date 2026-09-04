// @vitest-environment happy-dom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ToastProvider } from '../components/ui/Toast'

const mocks = vi.hoisted(() => ({
  getAgentCatalog: vi.fn(), getAgentRuns: vi.fn(), getAgentRunSteps: vi.fn(),
  getAgentRunToolCalls: vi.fn(), getOpportunities: vi.fn(), startAgentRun: vi.fn(),
}))

vi.mock('../api/client', () => ({
  ApiError: class extends Error { code = '' },
  api: mocks,
}))

import AgentActivity from './AgentActivity'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const RUN = {
  id: 'run-1', agent_name: 'research_agent', status: 'completed', trigger_type: 'manual',
  model_provider: null, model_name: null, outputs_created: '[]', started_at: '2026-09-04T00:00:00Z',
  ended_at: null, cost_estimate: null, tools_called: '["legacy-search"]', errors: '[]',
}

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  mocks.getAgentRuns.mockResolvedValue([RUN])
  mocks.getOpportunities.mockResolvedValue([])
  mocks.getAgentCatalog.mockResolvedValue({ agents: [] })
  mocks.getAgentRunToolCalls.mockResolvedValue([])
  mocks.getAgentRunSteps.mockResolvedValue([])
})

afterEach(() => {
  act(() => root?.unmount())
  client.clear()
  host?.remove()
  vi.clearAllMocks()
})

async function renderActivity() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/agent-activity?tab=runs']}>
      <QueryClientProvider client={client}><ToastProvider><AgentActivity /></ToastProvider></QueryClientProvider>
    </MemoryRouter>,
  ))
  await act(async () => {})
}

async function openRun() {
  await act(async () => {
    await vi.waitFor(() => expect(host.querySelector('tbody tr')).not.toBeNull())
  })
  const row = host.querySelector('tbody tr') as HTMLTableRowElement
  await act(async () => row.click())
}

async function waitForText(text: string) {
  await act(async () => {
    await vi.waitFor(() => expect(host.textContent).toContain(text))
  })
}

it('distinguishes a failed catalog read from a successful empty catalog', async () => {
  mocks.getAgentCatalog.mockRejectedValue(new Error('catalog failed'))
  await renderActivity()
  await waitForText('Agent catalog unavailable')
  expect(host.textContent).toContain('Agent catalog unavailable')
  expect(host.textContent).not.toContain('No agents are available for manual runs')
})

it('renders an intentional empty catalog only after a successful read', async () => {
  await renderActivity()
  await waitForText('No agents are available for manual runs')
  expect(host.textContent).toContain('No agents are available for manual runs')
  expect(host.textContent).not.toContain('Agent catalog unavailable')
})

it('retains a cached catalog as stale without authorizing manual runs', async () => {
  let rejectCatalog!: (error: Error) => void
  client.setQueryData(['agent-catalog'], { agents: [{
    name: 'research_agent', side_effect_class: 'research', cost_class: 'high', gate_check: false,
  }] })
  mocks.getAgentCatalog.mockReturnValue(new Promise((_, reject) => { rejectCatalog = reject }))
  await renderActivity()
  await waitForText('Agent catalog is stale')

  const runButton = host.querySelector('button[aria-label="research_agent"]') as HTMLButtonElement
  expect(runButton).not.toBeNull()
  expect(runButton.disabled).toBe(true)
  act(() => runButton.click())
  expect(mocks.startAgentRun).not.toHaveBeenCalled()

  await act(async () => rejectCatalog(new Error('catalog refresh failed')))
  expect(host.textContent).toContain('Agent catalog is stale')
  expect(runButton.disabled).toBe(true)
  act(() => runButton.click())
  expect(mocks.startAgentRun).not.toHaveBeenCalled()
})

it('keeps tool-call failure independent from a successful empty reasoning trace', async () => {
  mocks.getAgentRunToolCalls.mockRejectedValue(new Error('tool calls failed'))
  await renderActivity()
  await openRun()
  await waitForText('Tool-call ledger unavailable')
  expect(host.textContent).toContain('Tool-call ledger unavailable')
  expect(host.textContent).not.toContain('No tool calls recorded')
  expect(host.textContent).not.toContain('legacy-search')
  expect(host.textContent).toContain('No reasoning steps recorded')
})

it('keeps reasoning-step failure independent from a successful empty tool ledger', async () => {
  mocks.getAgentRunSteps.mockRejectedValue(new Error('steps failed'))
  await renderActivity()
  await openRun()
  await waitForText('Reasoning-step trace unavailable')
  expect(host.textContent).toContain('Legacy run snapshot')
  expect(host.textContent).toContain('legacy-search')
  expect(host.textContent).toContain('Reasoning-step trace unavailable')
  expect(host.textContent).not.toContain('No reasoning steps recorded')
})

it('labels the legacy fallback when the canonical tool ledger succeeds empty', async () => {
  await renderActivity()
  await openRun()
  await waitForText('Legacy run snapshot')
  expect(host.textContent).toContain('Legacy run snapshot (canonical tool-call ledger is empty)')
  expect(host.textContent).toContain('legacy-search')
})

it('retains cached canonical rows with a stale warning after refetch failure', async () => {
  client.setQueryData(['tool-calls', 'run-1'], [{
    id: 'tool-1', tool_name: 'web_search', tool_class: 'observe', permitted: true,
    created_at: '2026-09-04T00:00:01Z',
  }])
  mocks.getAgentRunToolCalls.mockRejectedValue(new Error('tool calls refresh failed'))
  await renderActivity()
  await openRun()
  await waitForText('Tool-call ledger is stale')
  expect(host.textContent).toContain('Tool-call ledger is stale')
  expect(host.textContent).toContain('web_search')
})

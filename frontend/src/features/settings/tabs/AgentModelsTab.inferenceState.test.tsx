// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const state = vi.hoisted(() => ({
  model: { providers: { openai_compatible: { enabled: true, model: 'default' } } } as unknown,
  modelPending: false,
  modelError: false,
  broker: undefined as unknown,
  brokerPending: false,
  brokerError: false,
}))

vi.mock('../../../api/client', () => ({
  api: {
    getModelConfig: vi.fn(),
    getInferenceBroker: vi.fn(),
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => queryKey[0] === 'model'
    ? { data: state.model, isPending: state.modelPending, isError: state.modelError }
    : { data: state.broker, isPending: state.brokerPending, isError: state.brokerError },
  useMutation: () => ({ mutate: vi.fn(), isPending: false, isSuccess: false, isError: false, error: null }),
}))

import { InferenceAdmissionCard } from './AgentModelsTab'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  state.model = { providers: { openai_compatible: { enabled: true, model: 'default' } } }
  state.modelPending = false
  state.modelError = false
  state.broker = undefined
  state.brokerPending = false
  state.brokerError = false
})

function renderCard() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(<InferenceAdmissionCard qc={{ invalidateQueries: vi.fn() } as any} />))
}

describe('Inference admission read-state contract', () => {
  it('keeps initial provider failures unavailable instead of loading forever', () => {
    state.model = undefined
    state.modelError = true
    renderCard()
    expect(host.textContent).toContain('Provider list unavailable')
    expect(host.textContent).not.toContain('Loading provider list')
  })

  it('does not turn a failed broker read into not yet started', () => {
    state.brokerError = true
    renderCard()
    expect(host.textContent).toContain('Broker: unavailable')
    expect(host.textContent).not.toContain('not yet started')
  })

  it('marks retained broker metrics stale while keeping them visible', () => {
    state.broker = {
      running: true,
      providers: { openai_compatible: { metrics: { active: 2, queued: 3, queue_wait_avg_ms: 4 } } },
    }
    state.brokerError = true
    renderCard()
    expect(host.textContent).toContain('Broker: running')
    expect(host.textContent).toContain('status stale')
    expect(host.textContent).toContain('active 2')
    expect(host.textContent).toContain('queued 3')
  })

  it('keeps not yet started for a successful stopped snapshot', () => {
    state.broker = { running: false, providers: {} }
    renderCard()
    expect(host.textContent).toContain('Broker: not yet started')
    expect(host.textContent).not.toContain('status unavailable')
  })
})

// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const mocks = vi.hoisted(() => ({
  createSerenityRun: vi.fn(),
  invalidateQueries: vi.fn(),
}))

vi.mock('../api/client', () => ({
  api: {
    createSerenityRun: mocks.createSerenityRun,
    getSerenityRuns: vi.fn(),
    getSerenityStatus: vi.fn(),
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => {
    if (queryKey[0] === 'serenity-status') return { data: { currentRun: null } }
    if (queryKey[0] === 'serenity-runs') return { data: { items: [] } }
    return { data: undefined }
  },
  useMutation: (options: any) => ({
    isPending: false,
    isError: false,
    mutate: (variables: unknown) => {
      Promise.resolve(options.mutationFn(variables)).then(
        data => options.onSuccess?.(data, variables),
        error => options.onError?.(error, variables),
      )
    },
  }),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
}))

import { OverviewTab } from './SerenityLens'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  mocks.createSerenityRun.mockReset()
  mocks.invalidateQueries.mockReset()
})

function mount() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(<OverviewTab />))
}

function setTextareaValue(value: string) {
  const textarea = host.querySelector('textarea') as HTMLTextAreaElement
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
    setter?.call(textarea, value)
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function clickRun() {
  const button = Array.from(host.querySelectorAll('button'))
    .find(candidate => candidate.textContent?.includes('Run Research')) as HTMLButtonElement
  act(() => button.click())
}

describe('Serenity Lens brief ownership', () => {
  it('keeps a newer draft when the submitted run completes later', async () => {
    let resolveRun!: (value: unknown) => void
    mocks.createSerenityRun.mockReturnValue(new Promise(resolve => { resolveRun = resolve }))
    mount()

    setTextareaValue('brief A')
    clickRun()
    setTextareaValue('brief B')
    resolveRun({ id: 'run-a' })
    await act(async () => {})

    expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('brief B')
    expect(mocks.createSerenityRun).toHaveBeenCalledWith({
      mode: 'standard',
      triggerType: 'manual',
      brief: 'brief A',
      saveToProfile: false,
    })
  })

  it('keeps a retyped matching draft when the submitted run completes later', async () => {
    let resolveRun!: (value: unknown) => void
    mocks.createSerenityRun.mockReturnValue(new Promise(resolve => { resolveRun = resolve }))
    mount()

    setTextareaValue('brief A')
    clickRun()
    setTextareaValue('brief B')
    setTextareaValue('brief A')
    resolveRun({ id: 'run-a' })
    await act(async () => {})

    expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('brief A')
  })

  it('clears the submitted draft when it is still current at completion', async () => {
    mocks.createSerenityRun.mockResolvedValue({ id: 'run-a' })
    mount()

    setTextareaValue('brief A')
    clickRun()
    await act(async () => {})

    expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')
  })
})

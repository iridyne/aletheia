// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const mocks = vi.hoisted(() => ({
  parseSerenityBrief: vi.fn(),
  updateSerenityProfile: vi.fn(),
  invalidateQueries: vi.fn(),
}))

vi.mock('../api/client', () => ({
  api: {
    getSerenityProfile: vi.fn(),
    getSerenityStatus: vi.fn(),
    parseSerenityBrief: mocks.parseSerenityBrief,
    updateSerenityProfile: mocks.updateSerenityProfile,
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === 'serenity-profile'
      ? { profile: null }
      : { engineStatus: 'idle', artifactCounts: { activeThemes: 0 }, lastSuccessfulRun: null, currentRun: null },
  }),
  useMutation: (options: any) => ({
    isPending: false,
    mutate: (variables: unknown) => {
      options.onMutate?.(variables)
      let result: unknown
      try {
        result = options.mutationFn(variables)
      } catch (error) {
        options.onError?.(error, variables)
        return
      }
      Promise.resolve(result).then(
        value => options.onSuccess?.(value, variables),
        error => options.onError?.(error, variables),
      )
    },
  }),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
}))

import SerenityLens from './SerenityLens'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  mocks.parseSerenityBrief.mockReset()
  mocks.updateSerenityProfile.mockReset()
  mocks.invalidateQueries.mockReset()
  vi.restoreAllMocks()
})

function mount() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(<MemoryRouter initialEntries={['/serenity-lens?tab=profile']}><SerenityLens /></MemoryRouter>))
}

function inputBrief(brief: string) {
  const textarea = host.querySelector('textarea') as HTMLTextAreaElement
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
    setter?.call(textarea, brief)
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function clickButton(label: string) {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent?.startsWith(label))
  expect(button, `button ${label}`).toBeTruthy()
  act(() => button!.click())
}

function clickTab(label: string) {
  const tab = [...host.querySelectorAll('[role="tab"]')].find(item => item.textContent === label)
  expect(tab, `tab ${label}`).toBeTruthy()
  act(() => (tab as HTMLElement).click())
}

async function flush() {
  await act(async () => { await Promise.resolve() })
}

const preview = (includeTopics: string[]) => ({ parsed: { includeTopics, excludeTopics: [], timeHorizon: 'long' }, warnings: [] })

describe('Serenity profile draft lifecycle', () => {
  it('preserves the brief and matching preview through pointer and keyboard tab changes', async () => {
    mocks.parseSerenityBrief.mockResolvedValue(preview(['AI']))
    mount()

    inputBrief('brief A')
    clickButton('Preview')
    await flush()
    expect(host.textContent).toContain('AI')

    clickTab('Evidence')
    clickTab('Profile')
    expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('brief A')
    expect(host.textContent).toContain('AI')

    const profileTab = [...host.querySelectorAll('[role="tab"]')].find(item => item.textContent === 'Profile') as HTMLButtonElement
    profileTab.focus()
    act(() => profileTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })))
    const evidenceTab = [...host.querySelectorAll('[role="tab"]')].find(item => item.textContent === 'Evidence') as HTMLButtonElement
    act(() => evidenceTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })))
    expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('brief A')
    expect(host.textContent).toContain('AI')
  })

  it('rejects a late preview for an older brief and saves only the current snapshot', async () => {
    let resolvePreview!: (value: unknown) => void
    mocks.parseSerenityBrief.mockImplementation(() => new Promise(resolve => { resolvePreview = resolve }))
    mocks.updateSerenityProfile.mockResolvedValue({ ok: true })
    mount()

    inputBrief('brief A')
    clickButton('Preview')
    inputBrief('brief B')
    await act(async () => {
      resolvePreview(preview(['A']))
      await Promise.resolve()
    })
    expect(host.querySelector('.lens-parse-result')).toBeNull()
    expect([...host.querySelectorAll('button')].some(item => item.textContent?.startsWith('Save to Profile'))).toBe(false)

    mocks.parseSerenityBrief.mockResolvedValue(preview(['B']))
    clickButton('Preview')
    await flush()
    clickButton('Save to Profile')
    await flush()
    expect(mocks.updateSerenityProfile).toHaveBeenCalledWith({
      includeTopics: ['B'],
      excludeTopics: [],
      timeHorizon: 'long',
      rawBrief: 'brief B',
    })
  })

  it('clears the retained draft only after explicit discard confirmation', async () => {
    mocks.parseSerenityBrief.mockResolvedValue(preview(['AI']))
    vi.stubGlobal('confirm', vi.fn(() => true))
    mount()

    inputBrief('brief A')
    clickButton('Preview')
    await flush()
    clickButton('Discard draft')
    expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')
    expect(host.querySelector('.lens-parse-result')).toBeNull()
  })
})

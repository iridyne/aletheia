// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SRC from './SerenityLens.tsx?raw'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const queryData = {
  'serenity-runs': {
    items: [{
      id: 'run-1', status: 'failed', triggerType: 'manual', mode: 'standard',
      startedAt: '2026-08-22T10:00:00Z', finishedAt: '2026-08-22T10:01:00Z',
      artifactCounts: { themes: 1, claims: 0 },
    }],
  },
  'serenity-run-detail': {
    run: {
      id: 'run-1', status: 'failed', triggerType: 'manual', mode: 'standard',
      startedAt: '2026-08-22T10:00:00Z', finishedAt: '2026-08-22T10:01:00Z',
    },
    events: [],
  },
  'serenity-run-artifacts': { counts: { themes: 0, claims: 0, evidence: 0 }, themes: [] },
  'serenity-themes': [{ id: 'theme-1', name: 'AI supply chain', status: 'active', thesis: 'A thesis' }],
  'serenity-theme-detail': {
    theme: { id: 'theme-1', name: 'AI supply chain', status: 'active' },
    claims: [], evidence: [], feedback: [], opportunities: [],
  },
}

vi.mock('../api/client', () => ({ api: {} }))
vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({ data: queryData[queryKey[0] as keyof typeof queryData] }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

import { OpportunityRow, RunsTab, ThemesTab } from './SerenityLens'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
})

function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(node))
}

describe('Serenity Lens keyboard interactions', () => {
  it('opens a run from Enter and restores focus to the originating row', () => {
    mount(<RunsTab />)
    const row = host.querySelector<HTMLTableRowElement>('[data-serenity-run-id="run-1"]')!
    row.focus()
    act(() => row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    expect(host.textContent).toContain('Run Detail')

    const back = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('Back'))!
    act(() => back.click())
    expect(document.activeElement).toBe(host.querySelector('[data-serenity-run-id="run-1"]'))
  })

  it('opens a theme from its named button and restores focus after Back', () => {
    mount(<ThemesTab />)
    const opener = host.querySelector<HTMLButtonElement>('.lens-theme-open')!
    opener.focus()
    act(() => opener.click())
    expect(host.textContent).toContain('AI supply chain')

    const back = Array.from(host.querySelectorAll('button')).find(button => button.textContent?.includes('Back'))!
    act(() => back.click())
    expect(document.activeElement).toBe(host.querySelector('.lens-theme-open'))
  })

  it('uses a keyboard-accessible Bear Case disclosure', () => {
    mount(
      <table><tbody>
        <OpportunityRow
          id="opp-1"
          symbol="NVDA"
          direction="Long"
          rec="long"
          grade="A"
          evidenceTier="A"
          status="open"
          bearCase="Demand could slow."
        />
      </tbody></table>,
    )
    const toggle = host.querySelector<HTMLButtonElement>('.serenity-bearcase-toggle')!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-controls')).toBe('serenity-bear-case-opp-1')
    act(() => toggle.click())
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(host.textContent).toContain('Demand could slow.')
  })
})

describe('Serenity Lens keyboard source contract', () => {
  it('keeps pointer-only research paths out of the interaction model', () => {
    expect(SRC).toContain('tabIndex={onSelect ? 0 : undefined}')
    expect(SRC).toContain("event.key === 'Enter' || event.key === ' '")
    expect(SRC).toContain('className="lens-theme-open"')
    expect(SRC).toContain('data-serenity-theme-id')
    expect(SRC).toContain('aria-expanded={expanded}')
    expect(SRC).toContain('aria-controls={bearCaseId}')
    expect(SRC).not.toContain('className="clickable" onClick={() => setSelectedTheme(t.id)}')
  })
})

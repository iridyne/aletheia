// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const mocks = vi.hoisted(() => ({
  sendOperatorCommand: vi.fn(),
  queries: new Map<string, { enabled?: boolean }>(),
  state: {
    status: null as Record<string, unknown> | null,
    sessions: [] as Array<Record<string, unknown>>,
    sessionDetailError: null as unknown,
  },
}))

vi.mock('../api/client', () => {
  class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  }
  return { ApiError, api: { sendOperatorCommand: mocks.sendOperatorCommand } }
})

vi.mock('../lib/eventStream', () => ({ connectEventStream: () => () => {} }))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey, enabled }: { queryKey: unknown[]; enabled?: boolean }) => {
    const key = queryKey.map(String).join('|')
    mocks.queries.set(key, { enabled })
    if (queryKey[0] === 'operator-status') return { data: mocks.state.status }
    if (queryKey[0] === 'operator-sessions') return { data: mocks.state.sessions }
    if (queryKey[0] === 'operator-session') {
      return mocks.state.sessionDetailError ? { data: undefined, error: mocks.state.sessionDetailError } : { data: undefined }
    }
    return { data: undefined }
  },
  useMutation: (options: any) => ({
    isPending: false,
    mutate: (variables: unknown) => {
      Promise.resolve(options.mutationFn(variables)).then(
        data => options.onSuccess?.(data, variables),
        error => options.onError?.(error, variables),
      )
    },
  }),
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}))

import CommandCenter from './CommandCenter'
import { ApiError } from '../api/client'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function session(id: string, createdAt: string) {
  return {
    id, user_input: id, created_at: createdAt, intent: null, command: null, response: null,
    permitted: 1, duration_ms: null, message_count: 0, command_count: 0, kind: 'command_session',
    parent_session_id: 'operator-main', title: id,
  }
}

function mount() {
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(<MemoryRouter><CommandCenter /></MemoryRouter>))
}

function composer() {
  return host.querySelector('.command-composer textarea') as HTMLTextAreaElement
}

function type(value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(composer(), value)
    composer().dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function sendButton() {
  return Array.from(host.querySelectorAll('.command-composer button'))
    .find(button => button.textContent?.includes('Send')) as HTMLButtonElement
}

async function clickSend() {
  await act(async () => { sendButton().click() })
}

function response(overrides: Record<string, unknown>) {
  return {
    intent: 'pause_agents', risk_level: 'operational', response: 'ok', action: null,
    needs_confirmation: false, permitted: true, executed: true, session_id: 'operator-main', ...overrides,
  }
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  mocks.sendOperatorCommand.mockReset()
  mocks.queries.clear()
  mocks.state.status = null
  mocks.state.sessions = []
  mocks.state.sessionDetailError = null
})

describe('Command Center composer (#1607)', () => {
  it('keeps the draft when the backend did not execute the command', async () => {
    mocks.sendOperatorCommand.mockResolvedValue(response({
      executed: false,
      needs_confirmation: true,
      response: "'pause_agents' requires operator confirmation and was not executed.",
    }))
    mount()
    type('pause agents')
    await clickSend()
    await act(async () => {})

    expect(mocks.sendOperatorCommand).toHaveBeenCalledTimes(1)
    expect(composer().value).toBe('pause agents')
  })

  it('keeps draft B typed while command A was in flight', async () => {
    let resolve!: (value: unknown) => void
    mocks.sendOperatorCommand.mockReturnValue(new Promise(r => { resolve = r }))
    mount()
    type('status')
    await clickSend()
    type('explain the last run')
    await act(async () => { resolve(response({ intent: 'get_system_status' })) })

    expect(mocks.sendOperatorCommand).toHaveBeenCalledWith('status', { sessionId: 'operator-main', confirmedCommand: undefined })
    expect(composer().value).toBe('explain the last run')
  })

  it('clears the sent draft once it executed', async () => {
    mocks.sendOperatorCommand.mockResolvedValue(response({ intent: 'get_system_status' }))
    mount()
    type('status')
    await clickSend()
    await act(async () => {})
    expect(composer().value).toBe('')
  })
})

describe('Command Center session selection (#1739)', () => {
  it('sends to the active branch even when it is older than the session page', async () => {
    mocks.state.status = { session_id: 'operator-main', active_branch_id: 'branch-old' }
    mocks.state.sessions = Array.from({ length: 50 }, (_, i) => session(`child-${i}`, `2026-10-09T00:${String(i).padStart(2, '0')}:00Z`))
    mocks.sendOperatorCommand.mockResolvedValue(response({ intent: 'get_system_status', session_id: 'branch-old' }))
    mount()
    await act(async () => {})

    expect(mocks.queries.get('operator-session|branch-old')?.enabled).toBe(true)
    type('status')
    await clickSend()
    expect(mocks.sendOperatorCommand).toHaveBeenCalledWith('status', { sessionId: 'branch-old', confirmedCommand: undefined })
  })

  it('disables Send instead of re-targeting when the selected branch is gone', async () => {
    mocks.state.status = { session_id: 'operator-main', active_branch_id: 'branch-deleted' }
    mocks.state.sessions = [session('child-new', '2026-10-09T00:00:00Z')]
    mocks.state.sessionDetailError = new ApiError(404, 'Session not found')
    mount()
    await act(async () => {})
    type('status')

    expect(host.textContent).toContain('Session branch-deleted is not available')
    expect(sendButton().disabled).toBe(true)
  })
})

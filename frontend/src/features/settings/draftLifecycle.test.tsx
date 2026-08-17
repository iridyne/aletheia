// @vitest-environment happy-dom

import { useState } from 'react'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import { useSettingsDraftLifecycle } from './draftLifecycle'

function Harness({ server, initial = server.value, onDirty }: { server: { value: string }; initial?: string; onDirty?: (id: string, dirty: boolean) => void }) {
  const [draft, setDraft] = useState(initial)
  const dirty = draft !== server.value
  useSettingsDraftLifecycle({
    id: 'probe',
    dirty,
    onDirty,
    serverKey: server,
    adoptServer: () => setDraft(server.value),
  })
  return (
    <>
      <input value={draft} onChange={event => setDraft(event.target.value)} />
      <button onClick={() => setDraft(server.value)}>Save</button>
    </>
  )
}

describe('useSettingsDraftLifecycle', () => {
  it('adopts the initial server state before tracking user edits', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    await act(async () => root.render(<Harness server={{ value: 'server-1' }} initial="default-draft" onDirty={() => undefined} />))
    expect((container.querySelector('input') as HTMLInputElement).value).toBe('server-1')

    await act(async () => root.unmount())
    container.remove()
  })

  it('preserves dirty drafts across unrelated server refreshes and adopts clean state', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    await act(async () => root.render(<Harness server={{ value: 'server-1' }} onDirty={() => undefined} />))
    const input = container.querySelector('input') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setter?.call(input, 'local-draft')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => root.render(<Harness server={{ value: 'server-2' }} onDirty={() => undefined} />))
    expect(input.value).toBe('local-draft')

    const save = container.querySelector('button') as HTMLButtonElement
    await act(async () => save.click())
    await act(async () => root.render(<Harness server={{ value: 'server-2' }} onDirty={() => undefined} />))
    await act(async () => root.render(<Harness server={{ value: 'server-3' }} onDirty={() => undefined} />))
    expect(input.value).toBe('server-3')

    await act(async () => root.unmount())
    container.remove()
  })

  it('preserves a draft when the edit and refresh are batched together', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    await act(async () => root.render(<Harness server={{ value: 'server-1' }} onDirty={() => undefined} />))
    const input = container.querySelector('input') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setter?.call(input, 'local-draft')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      root.render(<Harness server={{ value: 'server-2' }} onDirty={() => undefined} />)
    })
    expect(input.value).toBe('local-draft')

    await act(async () => root.unmount())
    container.remove()
  })
})

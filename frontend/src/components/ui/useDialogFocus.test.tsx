// @vitest-environment happy-dom
import { act, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { useDialogFocus } from './useDialogFocus'

afterEach(() => {
  document.body.replaceChildren()
})

it('traps focus, closes on Escape, and restores the opener', () => {
  const opener = document.createElement('button')
  document.body.append(opener)
  opener.focus()
  const close = vi.fn()
  const host = document.createElement('div')
  document.body.append(host)

  function Dialog() {
    const ref = useRef<HTMLDivElement>(null)
    useDialogFocus(true, ref, close)
    return <div ref={ref} tabIndex={-1}><button id="first" /><button id="last" /></div>
  }

  const root = createRoot(host)
  act(() => root.render(<Dialog />))
  const first = document.querySelector<HTMLElement>('#first')!
  const last = document.querySelector<HTMLElement>('#last')!
  last.focus()
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
  expect(document.activeElement).toBe(first)

  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  expect(close).toHaveBeenCalledOnce()
  act(() => root.unmount())
  expect(document.activeElement).toBe(opener)
})

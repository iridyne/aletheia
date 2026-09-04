import { useEffect, useRef, type RefObject } from 'react'

const FOCUSABLE = 'a[href], button, textarea, input, select, [tabindex]:not([tabindex="-1"])'
const dialogStack: symbol[] = []

export function useDialogFocus(
  open: boolean,
  containerRef: RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  const closeRef = useRef(onClose)
  const openerRef = useRef<HTMLElement | null>(null)
  const wasOpenRef = useRef(false)
  closeRef.current = onClose
  if (open && !wasOpenRef.current) {
    openerRef.current = typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
  }
  wasOpenRef.current = open
  useEffect(() => {
    if (!open || !containerRef.current) return
    const container = containerRef.current
    const dialog = Symbol()
    dialogStack.push(dialog)
    if (!container.contains(document.activeElement)) container.focus()

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (dialogStack[dialogStack.length - 1] !== dialog) return
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = container.querySelectorAll<HTMLElement>(FOCUSABLE)
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === container)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('keydown', handleKey)
      const index = dialogStack.lastIndexOf(dialog)
      if (index >= 0) dialogStack.splice(index, 1)
      if (openerRef.current?.isConnected) openerRef.current.focus()
    }
  }, [open, containerRef])
}

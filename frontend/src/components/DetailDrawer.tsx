import { useEffect, useRef } from 'react'

interface DetailDrawerProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
}

export function DetailDrawer({ open, onClose, title, children }: DetailDrawerProps) {
  const panelRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  // Focus trap: keep Tab cycling inside the drawer
  useEffect(() => {
    if (!open || !panelRef.current) return
    const panel = panelRef.current
    panel.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const focusable = panel.querySelectorAll<HTMLElement>(
        'a[href], button, textarea, input, select, [tabindex]:not([tabindex="-1"])'
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    panel.addEventListener('keydown', handler)
    return () => panel.removeEventListener('keydown', handler)
  }, [open])

  if (!open) return null

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="drawer-panel"
        onClick={e => e.stopPropagation()}
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title || 'Detail'}
      >
        <div className="drawer-header">
          {title && <h3 className="drawer-title">{title}</h3>}
          <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="drawer-body">
          {children}
        </div>
      </aside>
    </div>
  )
}

export function DrawerSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="drawer-section">
      <div className="drawer-section-label">{label}</div>
      <div className="drawer-section-content">{children}</div>
    </div>
  )
}

export function DrawerField({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="drawer-field">
      <span className="drawer-field-label">{label}</span>
      <span className={mono ? 'drawer-field-value mono' : 'drawer-field-value'}>{value ?? '—'}</span>
    </div>
  )
}

import { useRef } from 'react'
import { useDialogFocus } from './ui/useDialogFocus'

interface DetailDrawerProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
}

export function DetailDrawer({ open, onClose, title, children }: DetailDrawerProps) {
  const panelRef = useRef<HTMLElement>(null)

  useDialogFocus(open, panelRef, onClose)

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

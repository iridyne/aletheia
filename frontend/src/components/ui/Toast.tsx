import { useEffect, useState, useCallback, createContext, useContext } from 'react'
import { MUTATION_ERROR_EVENT } from '../../lib/mutationErrors'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface ToastItem {
  id: number
  type: ToastType
  message: string
  duration: number
}

interface ToastContextValue {
  toast: (type: ToastType, message: string, duration?: number) => void
}

const ToastContext = createContext<ToastContextValue>({ toast: () => {} })

export function useToast() {
  return useContext(ToastContext)
}

let nextId = 0

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const toast = useCallback((type: ToastType, message: string, duration = 4000) => {
    const id = ++nextId
    setToasts(prev => [...prev, { id, type, message, duration }])
  }, [])

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  useEffect(() => {
    const onMutationError = (event: Event) => {
      const message = (event as CustomEvent<{ message?: string }>).detail?.message
      toast('error', message || 'Action failed: request failed')
    }
    window.addEventListener(MUTATION_ERROR_EVENT, onMutationError)
    return () => window.removeEventListener(MUTATION_ERROR_EVENT, onMutationError)
  }, [toast])

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="toast-container" aria-live="polite">
        {toasts.map(t => (
          <ToastMessage key={t.id} item={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastMessage({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(item.id), item.duration)
    return () => clearTimeout(timer)
  }, [item.id, item.duration, onDismiss])

  const icons: Record<ToastType, string> = {
    success: '✓',
    error: '✗',
    warning: '⚠',
    info: 'ℹ',
  }

  return (
    <div className={`toast-item toast-${item.type}`} role="alert">
      <span className="toast-icon">{icons[item.type]}</span>
      <span className="toast-msg">{item.message}</span>
      <button className="toast-close" onClick={() => onDismiss(item.id)} aria-label="Dismiss">&times;</button>
    </div>
  )
}

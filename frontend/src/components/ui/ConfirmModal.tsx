import { createContext, useContext, useState, useCallback, useRef } from 'react'
import { useDialogFocus } from './useDialogFocus'

interface ConfirmOptions {
  title: string
  description?: string
  detail?: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'danger' | 'warning' | 'default'
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>

const ConfirmContext = createContext<ConfirmFn>(async () => false)

export function useConfirm() {
  return useContext(ConfirmContext)
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<(ConfirmOptions & { visible: boolean }) | null>(null)
  const resolveRef = useRef<((v: boolean) => void) | null>(null)
  const modalRef = useRef<HTMLDivElement>(null)

  const confirm: ConfirmFn = useCallback((opts) => {
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve
      setState({ ...opts, visible: true })
    })
  }, [])

  const handleClose = useCallback((result: boolean) => {
    setState(null)
    resolveRef.current?.(result)
    resolveRef.current = null
  }, [])
  const closeWithoutConfirm = useCallback(() => handleClose(false), [handleClose])

  useDialogFocus(Boolean(state?.visible), modalRef, closeWithoutConfirm)

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state?.visible && (
        <div className="confirm-backdrop" onClick={() => handleClose(false)}>
          <div ref={modalRef} tabIndex={-1} className="confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onClick={e => e.stopPropagation()}>
            <h3 id="confirm-title" className={`confirm-title confirm-${state.variant || 'default'}`}>
              {state.title}
            </h3>
            {state.description && <p className="confirm-desc">{state.description}</p>}
            {state.detail && <div className="confirm-detail">{state.detail}</div>}
            <div className="confirm-actions">
              <button className="btn btn-secondary" onClick={() => handleClose(false)}>
                {state.cancelLabel || 'Cancel'}
              </button>
              <button
                className={`btn ${state.variant === 'danger' ? 'btn-danger' : 'btn-primary'}`}
                onClick={() => handleClose(true)}
                autoFocus
              >
                {state.confirmLabel || 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  )
}

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export type InspectorObject = {
  type: 'agent_run' | 'proposal' | 'opportunity' | 'strategy' | 'trade' | 'position' | 'news' | 'risk_event' | 'research_note' | 'report' | 'activity'
  id: string
  data: Record<string, any>
}

type InspectorState = {
  object: InspectorObject | null
  open: boolean
  setObject: (obj: InspectorObject) => void
  close: () => void
}

const InspectorContext = createContext<InspectorState>({
  object: null,
  open: false,
  setObject: () => {},
  close: () => {},
})

export function InspectorProvider({ children }: { children: ReactNode }) {
  const [object, setObj] = useState<InspectorObject | null>(null)
  const [open, setOpen] = useState(false)

  const setObject = useCallback((obj: InspectorObject) => {
    setObj(obj)
    setOpen(true)
  }, [])

  const close = useCallback(() => {
    setOpen(false)
  }, [])

  return (
    <InspectorContext.Provider value={{ object, open, setObject, close }}>
      {children}
    </InspectorContext.Provider>
  )
}

export function useInspector() {
  return useContext(InspectorContext)
}

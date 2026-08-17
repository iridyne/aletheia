import { useEffect, useRef } from 'react'

export type SettingsDirtyReporter = (id: string, isDirty: boolean) => void

/** Keep a local editor draft while unrelated settings data is refreshed. */
export function useSettingsDraftLifecycle({
  id,
  dirty,
  onDirty,
  serverKey,
  adoptServer,
}: {
  id: string
  dirty: boolean
  onDirty?: SettingsDirtyReporter
  serverKey: unknown
  adoptServer: () => void
}) {
  const dirtyRef = useRef(dirty)
  const adoptServerRef = useRef(adoptServer)
  const onDirtyRef = useRef(onDirty)
  dirtyRef.current = dirty
  adoptServerRef.current = adoptServer
  onDirtyRef.current = onDirty

  useEffect(() => {
    if (!dirtyRef.current) adoptServerRef.current()
  }, [serverKey])

  useEffect(() => {
    onDirtyRef.current?.(id, dirty)
    return () => onDirtyRef.current?.(id, false)
  }, [id, dirty])
}

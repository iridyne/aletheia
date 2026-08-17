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
  const previousDirtyRef = useRef(dirty)
  const adoptServerRef = useRef(adoptServer)
  const onDirtyRef = useRef(onDirty)
  adoptServerRef.current = adoptServer
  onDirtyRef.current = onDirty

  useEffect(() => {
    if (!previousDirtyRef.current) adoptServerRef.current()
  }, [serverKey])

  useEffect(() => {
    previousDirtyRef.current = dirty
  }, [dirty])

  useEffect(() => {
    onDirtyRef.current?.(id, dirty)
    return () => onDirtyRef.current?.(id, false)
  }, [id, dirty])
}

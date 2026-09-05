import { useEffect } from 'react'
import type { QueryClient } from '@tanstack/react-query'

const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('aletheia-tab-sync') : null

export interface TabMessage {
  type: 'REALTIME_INVALIDATE' | 'SETTINGS_DIRTY' | 'SETTINGS_SAVED'
  payload?: unknown
}

export function broadcastTabMessage(msg: TabMessage) {
  channel?.postMessage(msg)
}

/** Subscribe to cross-tab messages. Call once at app root. */
export function useTabSync(qc: QueryClient) {
  useEffect(() => {
    if (!channel) return
    const handler = (e: MessageEvent<TabMessage>) => {
      if (e.data.type === 'REALTIME_INVALIDATE') {
        const categories = e.data.payload as string[] | undefined
        if (categories?.length) {
          for (const key of categories) void qc.invalidateQueries({ queryKey: [key] })
        }
      } else if (e.data.type === 'SETTINGS_SAVED') {
        void qc.invalidateQueries({ queryKey: ['settings'] })
      }
    }
    channel.addEventListener('message', handler)
    return () => channel.removeEventListener('message', handler)
  }, [qc])
}

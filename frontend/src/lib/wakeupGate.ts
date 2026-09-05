import type { QueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

/** Call once at app boot. Detects laptop lid-open / system resume via clock jump
 *  and cancels stale queries before issuing a single readiness probe. */
export function initWakeupGate(qc: QueryClient) {
  let lastTick = Date.now()

  const id = window.setInterval(() => {
    const now = Date.now()
    const diff = now - lastTick
    lastTick = now

    // > 8s gap: system was suspended
    if (diff > 8_000) {
      void qc.cancelQueries()
      api.getReady()
        .then(() => {
          void qc.invalidateQueries({ queryKey: ['ui-shell-bootstrap'] })
        })
        .catch(() => { /* BackendConnectionGuard handles full disconnects */ })
    }
  }, 2_000)

  return () => window.clearInterval(id)
}

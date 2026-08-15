export type DeferredPriority = 'idle' | 'intent'

export function deferTask(task: () => void, options: { delayMs?: number; priority?: DeferredPriority } = {}): () => void {
  const delayMs = Math.max(0, options.delayMs ?? 5000)
  let cancelled = false
  let timeout: ReturnType<typeof setTimeout> | null = null
  let idle: number | null = null
  let waitingForVisible = false
  const run = () => {
    if (cancelled) return
    if (document.visibilityState === 'hidden') {
      if (!waitingForVisible) {
        waitingForVisible = true
        document.addEventListener('visibilitychange', run, { once: true })
      }
      return
    }
    cancelled = true
    task()
  }
  if (options.priority === 'intent') {
    run()
  } else if ('requestIdleCallback' in window) {
    idle = window.requestIdleCallback(run, { timeout: delayMs })
    timeout = setTimeout(run, delayMs + 50)
  } else {
    timeout = setTimeout(run, delayMs)
  }
  return () => {
    cancelled = true
    if (waitingForVisible) document.removeEventListener('visibilitychange', run)
    if (idle !== null && 'cancelIdleCallback' in window) window.cancelIdleCallback(idle)
    if (timeout !== null) clearTimeout(timeout)
  }
}

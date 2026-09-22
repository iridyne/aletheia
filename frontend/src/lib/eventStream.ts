/** Retry terminal HTTP failures; leave CONNECTING retries to EventSource itself. */
export function connectEventStream(url: string, configure: (stream: EventSource) => void, onError?: () => void) {
  let stream: EventSource
  let timer: ReturnType<typeof setTimeout> | undefined
  // Matches the frontdoor admission Retry-After: 3 minimum.
  let delay = 3000
  let disposed = false

  function connect() {
    stream = new EventSource(url)
    configure(stream)
    stream.onopen = () => { delay = 3000 }
    stream.onerror = () => {
      if (disposed) return
      onError?.()
      // Non-200 responses (including admission 503) close the source permanently.
      // EventSource exposes neither the status nor Retry-After to this callback.
      if (stream.readyState !== EventSource.CLOSED || timer !== undefined) return
      stream.onopen = null
      stream.onerror = null
      stream.close()
      timer = setTimeout(() => {
        timer = undefined
        delay = Math.min(delay * 2, 30000)
        connect()
      }, delay)
    }
  }

  connect()
  return () => {
    disposed = true
    clearTimeout(timer)
    stream.onopen = null
    stream.onerror = null
    stream.close()
  }
}

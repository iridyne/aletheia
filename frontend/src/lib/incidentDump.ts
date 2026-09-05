import type { QueryClient } from '@tanstack/react-query'

/** One-click download of the current TanStack Query cache as a JSON incident snapshot.
 *  Zero backend dependency — pure browser Blob + ObjectURL. */
export function dumpIncidentSnapshot(qc: QueryClient) {
  const snapshot = {
    dumped_at: new Date().toISOString(),
    url: window.location.href,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    queries: qc.getQueryCache().getAll().map(q => ({
      queryKey: q.queryKey,
      state: q.state.data,
      error: q.state.error ? String(q.state.error) : null,
      updatedAt: new Date(q.state.dataUpdatedAt).toISOString(),
    })),
    performance_marks: performance.getEntriesByType('mark').map(m => ({
      name: m.name,
      time: m.startTime,
    })),
  }

  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `aletheia-incident-${Date.now()}.json`
  a.click()
  URL.revokeObjectURL(url)
}

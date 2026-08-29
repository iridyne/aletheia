import { useIsFetching } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

// Thin accent bar at the very top of the viewport that lights up whenever
// any React Query fetch is in-flight. Width animates to ~90% while fetching,
// then reaches 100% before fading out on completion. This gives instant visual
// feedback on every page switch without any per-page wiring.
// Static styling lives in .fetch-progress (index.css); only the dynamic width
// stays inline since it tracks fetch state.
export default function FetchProgress() {
  const isFetching = useIsFetching({
    predicate: (query) => {
      if (query.meta?.background === true) return false
      // Always count first-time loads (status === 'pending'), even if the query
      // has refetchInterval. Without this, pages like Overview that only use
      // polling queries never show the progress bar on navigation.
      if (query.state.status === 'pending') return true
      // For background refetches, exclude polling queries to avoid bar flickering
      // on every scheduled refresh interval.
      return !(query.options as { refetchInterval?: unknown }).refetchInterval
    },
  })
  const [visible, setVisible] = useState(false)
  const [width, setWidth] = useState(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current)

    if (isFetching > 0) {
      setVisible(true)
      setWidth(Math.min(70 + Math.min(isFetching * 5, 30), 95))
    } else {
      setWidth(100)
      timerRef.current = setTimeout(() => {
        setVisible(false)
        setWidth(0)
      }, 300)
    }

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [isFetching])

  if (!visible) return null

  return (
    <div
      className={`fetch-progress${isFetching > 0 ? ' is-active' : ' is-done'}`}
      style={{ width: `${width}%` }}
    />
  )
}

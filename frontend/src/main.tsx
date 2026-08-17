import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ToastProvider } from './components/ui/Toast'
import { ConfirmProvider } from './components/ui/ConfirmModal'
import './index.css'
import { initDevReload } from './dev-reload'
import { reportMutationError } from './lib/mutationErrors'

performance.mark('aletheia:boot-script-start')

window.addEventListener('vite:preloadError', (e) => {
  e.preventDefault()
  window.location.reload()
})

if (!import.meta.env.DEV && import.meta.env.VITE_ENABLE_DEV_RELOAD === 'true') initDevReload()

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      // Local inline/toast handlers remain the source of truth for trust paths.
      // Mutations without one still need a visible failure instead of a rejected
      // Promise that exists only in DevTools.
      if (typeof mutation.options.onError !== 'function' && mutation.options.meta?.errorSurface !== 'inline') {
        reportMutationError(error)
      }
    },
  }),
  defaultOptions: {
    queries: {
      // staleTime:0 (default) + refetchOnMount:'always' means every page switch
      // discards cached data and shows a blank skeleton instead of serving the
      // cached view while re-fetching in the background. 30s stale window gives
      // instant navigation without sacrificing data freshness on a 10s-polling app.
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnMount: true,          // re-fetch when stale, NOT always
      refetchOnWindowFocus: false,   // avoid bursts when user alt-tabs
      // No global refetchInterval — real-time queries set their own interval
      // (e.g. StatusBar queries set refetchInterval:10000 explicitly).
      retry: (failureCount, error) => {
        // Never retry a request that was aborted — either we timed it out
        // (fetchJson 15s cap) or React Query cancelled it. Retrying an aborted
        // request would double the loading-bar wait time for no benefit.
        if (error instanceof DOMException && error.name === 'AbortError') return false
        return failureCount < 1
      },
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)

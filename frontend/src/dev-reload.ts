/**
 * Dev-reload client — connects to /api/dev/reload/events SSE when available.
 * Auto-reloads on frontend-built; backend-ready confirms only stream connectivity.
 * Only activates if the endpoint exists (ALETHEIA_DEV_RELOAD=1 on server).
 */

import { connectEventStream } from './lib/eventStream'

let statusEl: HTMLElement | null = null
let reloading = false

function showStatus(msg: string) {
  if (!statusEl) {
    statusEl = document.createElement('div')
    statusEl.style.cssText =
      'position:fixed;bottom:8px;right:8px;padding:4px 10px;' +
      'background:rgba(0,0,0,0.85);color:#0ff;font:11px/1.4 monospace;' +
      'border-radius:4px;z-index:99999;pointer-events:none;'
    document.body.appendChild(statusEl)
  }
  statusEl.textContent = msg
  statusEl.style.display = 'block'
}

function hideStatus() {
  if (statusEl) statusEl.style.display = 'none'
}

export function initDevReload() {
  let disposed = false
  let disconnect: (() => void) | undefined
  let reloadTimer: ReturnType<typeof setTimeout> | undefined
  fetch('/api/dev/reload/status', { cache: 'no-store' })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (disposed || !data?.enabled) return
      disconnect = connectEventStream('/api/dev/reload/events', stream => {
        stream.addEventListener('backend-ready', hideStatus)
        stream.addEventListener('frontend-built', () => {
          if (reloading) return
          reloading = true
          showStatus('Build updated — reloading…')
          reloadTimer = setTimeout(() => window.location.reload(), 300)
        })
      }, () => showStatus('Dev event stream unavailable — reconnecting…'))
    })
    .catch(() => { /* endpoint not available — dev reload not enabled */ })
  return () => {
    disposed = true
    disconnect?.()
    clearTimeout(reloadTimer)
    reloading = false
    hideStatus()
  }
}

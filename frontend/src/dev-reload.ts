/**
 * Dev-reload client — connects to /api/dev/reload/events SSE when available.
 * Auto-reloads the page on frontend-built or backend-ready events.
 * Only activates if the endpoint exists (ALETHEIA_DEV_RELOAD=1 on server).
 */

let es: EventSource | null = null
let reconnectDelay = 1000
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let statusEl: HTMLElement | null = null

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

function connect() {
  if (es) {
    es.close()
    es = null
  }

  es = new EventSource('/api/dev/reload/events')

  es.addEventListener('backend-ready', () => {
    hideStatus()
    reconnectDelay = 1000
  })

  es.addEventListener('frontend-built', () => {
    showStatus('Build updated — reloading…')
    setTimeout(() => window.location.reload(), 300)
  })

  es.onerror = () => {
    es?.close()
    es = null
    showStatus('Gateway restarting…')
    scheduleReconnect()
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    reconnectDelay = Math.min(reconnectDelay * 1.5, 8000)
    pollUntilReady()
  }, reconnectDelay)
}

async function pollUntilReady() {
  try {
    const res = await fetch('/api/health', { cache: 'no-store' })
    if (res.ok) {
      window.location.reload()
      return
    }
  } catch { /* server still down */ }
  scheduleReconnect()
}

export function initDevReload() {
  fetch('/api/dev/reload/status', { cache: 'no-store' })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (data?.enabled) connect()
    })
    .catch(() => { /* endpoint not available — dev reload not enabled */ })
}

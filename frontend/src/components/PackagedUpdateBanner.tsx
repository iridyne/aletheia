import { useEffect, useRef, useState } from 'react'

type UpdateState = {
  mode?: string
  operation_id?: string | null
  phase?: string
  status?: string
  progress?: number | null
  current_release?: string | null
  previous_release?: string | null
  target_release?: string | null
  message?: string | null
  error?: string | null
  resume_warning?: string | null
  updated_at?: string | null
}

const ACTIVE_PHASES = new Set([
  'checking',
  'downloading',
  'verifying',
  'installing',
  'preflight',
  'ready_to_switch',
  'quiescing',
  'migrating',
  'starting',
  'soaking',
  'rolling_back',
])

const TERMINAL_PHASES = new Set(['committed', 'rolled_back', 'rollback_failed', 'failed'])
const TERMINAL_RELOAD_KEY = 'aletheia.packaged-update.last-reload'

function phaseLabel(phase: string): string {
  const labels: Record<string, string> = {
    checking: 'Checking signed release',
    downloading: 'Downloading update',
    verifying: 'Verifying signature and package',
    installing: 'Installing immutable runtime',
    preflight: 'Testing migration on a database copy',
    ready_to_switch: 'Ready to switch versions',
    quiescing: 'Pausing work and switching runtime',
    migrating: 'Applying verified migration',
    starting: 'Starting the new runtime',
    soaking: 'Verifying the new runtime',
    rolling_back: 'Restoring the previous version',
    committed: 'Update installed',
    rolled_back: 'Previous version restored',
    rollback_failed: 'Manual recovery required',
    failed: 'Update could not be prepared',
  }
  return labels[phase] || phase.replace(/_/g, ' ')
}

function stateTimestamp(value: string | null | undefined): number | null {
  if (!value) return null
  if (value.startsWith('unix:')) {
    const seconds = Number(value.slice('unix:'.length))
    return Number.isFinite(seconds) ? seconds * 1000 : null
  }
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function terminalStillVisible(state: UpdateState, phase: string): boolean {
  if (!TERMINAL_PHASES.has(phase)) return false
  const timestamp = stateTimestamp(state.updated_at)
  if (timestamp === null) return true
  const ageMs = Math.max(0, Date.now() - timestamp)
  return ageMs <= (phase === 'committed' || phase === 'rolled_back' ? 30_000 : 5 * 60_000)
}

export default function PackagedUpdateBanner() {
  const [state, setState] = useState<UpdateState | null>(null)
  const [temporarilyOffline, setTemporarilyOffline] = useState(false)
  const activeRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let packagedRuntime: boolean | null = null

    const poll = async () => {
      try {
        const response = await fetch('/api/update/progress', { cache: 'no-store' })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const next = await response.json() as UpdateState
        if (cancelled) return

        if (next.mode !== 'packaged') {
          packagedRuntime = false
          setState(null)
          activeRef.current = false
          setTemporarilyOffline(false)
        } else {
          packagedRuntime = true
          const phase = String(next.phase || 'idle')
          const active = ACTIVE_PHASES.has(phase)
          activeRef.current = active
          setTemporarilyOffline(false)
          setState(active || terminalStillVisible(next, phase) ? next : null)

          if (TERMINAL_PHASES.has(phase) && next.operation_id) {
            const terminalKey = `${next.operation_id}:${phase}`
            const alreadyReloaded = window.sessionStorage.getItem(TERMINAL_RELOAD_KEY)
            // The selected runtime may contain a newer frontend bundle. Reload exactly
            // once per terminal operation after the replacement backend is healthy.
            if (
              alreadyReloaded !== terminalKey
              && (phase === 'committed' || phase === 'rolled_back')
            ) {
              window.sessionStorage.setItem(TERMINAL_RELOAD_KEY, terminalKey)
              window.setTimeout(() => window.location.reload(), 1200)
            }
          }
        }
      } catch {
        if (cancelled) return
        // During the short stop/switch window the API is expected to disappear. Keep
        // the last truthful state visible rather than replacing it with a generic error.
        if (activeRef.current) setTemporarilyOffline(true)
      } finally {
        // Source/dev installs cannot become packaged without loading a different
        // frontend bundle, so one truthful mode response is enough. Packaged but
        // idle installs retain a low-frequency recovery check.
        if (!cancelled && packagedRuntime !== false) {
          timer = setTimeout(poll, activeRef.current ? 1000 : 30000)
        }
      }
    }

    // Update progress is non-critical for the first paint. Leave the initial
    // connection slot for dashboard data, then keep the existing poll cadence.
    const initialTimer = window.setTimeout(() => { void poll() }, 1000)
    return () => {
      cancelled = true
      window.clearTimeout(initialTimer)
      if (timer) clearTimeout(timer)
    }
  }, [])

  if (!state || state.mode !== 'packaged') return null
  const phase = String(state.phase || 'idle')
  const visible = ACTIVE_PHASES.has(phase) || terminalStillVisible(state, phase)
  if (!visible) return null

  const failed = phase === 'failed' || phase === 'rollback_failed'
  const restored = phase === 'rolled_back'
  const complete = phase === 'committed'
  const progress = typeof state.progress === 'number'
    ? Math.max(0, Math.min(100, Math.round(state.progress * 100)))
    : null
  const background = failed
    ? 'var(--red, #d9534f)'
    : restored
      ? 'var(--yellow, #d4a017)'
      : complete
        ? 'var(--green, #2f9e62)'
        : 'var(--surface-raised, #17202b)'

  return (
    <div
      role={failed ? 'alert' : 'status'}
      aria-live="polite"
      style={{
        position: 'fixed',
        left: '50%',
        bottom: '18px',
        transform: 'translateX(-50%)',
        zIndex: 10000,
        width: 'min(680px, calc(100vw - 32px))',
        padding: '12px 16px',
        borderRadius: '10px',
        background,
        color: '#fff',
        boxShadow: '0 12px 34px rgba(0,0,0,.35)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', alignItems: 'center' }}>
        <div>
          <strong>{phaseLabel(phase)}</strong>
          <div style={{ marginTop: '3px', fontSize: '.82rem', opacity: .9 }}>
            {temporarilyOffline
              ? 'The runtime is switching. This page will reconnect automatically.'
              : state.error || state.resume_warning || state.message || (
                state.target_release ? `Target ${state.target_release}` : 'Working in the background'
              )}
          </div>
        </div>
        {progress !== null && <span style={{ fontFamily: 'var(--mono)', whiteSpace: 'nowrap' }}>{progress}%</span>}
      </div>
      {progress !== null && (
        <div style={{ height: '3px', marginTop: '9px', borderRadius: '2px', background: 'rgba(255,255,255,.25)' }}>
          <div style={{ width: `${progress}%`, height: '100%', borderRadius: '2px', background: '#fff' }} />
        </div>
      )}
    </div>
  )
}

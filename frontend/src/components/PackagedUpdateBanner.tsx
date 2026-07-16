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
  return labels[phase] || phase.replaceAll('_', ' ')
}

export default function PackagedUpdateBanner() {
  const [state, setState] = useState<UpdateState | null>(null)
  const [temporarilyOffline, setTemporarilyOffline] = useState(false)
  const activeRef = useRef(false)
  const terminalSeenRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const response = await fetch('/api/update/progress', { cache: 'no-store' })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const next = await response.json() as UpdateState
        if (cancelled) return

        if (next.mode !== 'packaged') {
          setState(null)
          activeRef.current = false
          setTemporarilyOffline(false)
        } else {
          const phase = String(next.phase || 'idle')
          const active = ACTIVE_PHASES.has(phase)
          setState(next)
          setTemporarilyOffline(false)
          activeRef.current = active

          if (TERMINAL_PHASES.has(phase) && next.operation_id) {
            const terminalKey = `${next.operation_id}:${phase}`
            if (terminalSeenRef.current !== terminalKey) {
              terminalSeenRef.current = terminalKey
              // The selected runtime may contain a newer frontend bundle. Reload once
              // after the replacement backend is healthy so the browser follows it.
              if (phase === 'committed' || phase === 'rolled_back') {
                window.setTimeout(() => window.location.reload(), 1200)
              }
            }
          }
        }
      } catch {
        if (cancelled) return
        // During the short stop/switch window the API is expected to disappear. Keep
        // the last truthful state visible rather than replacing it with a generic error.
        if (activeRef.current) setTemporarilyOffline(true)
      } finally {
        if (!cancelled) timer = setTimeout(poll, activeRef.current ? 1000 : 5000)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  if (!state || state.mode !== 'packaged') return null
  const phase = String(state.phase || 'idle')
  const visible = ACTIVE_PHASES.has(phase) || TERMINAL_PHASES.has(phase)
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

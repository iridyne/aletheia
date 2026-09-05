import { useMutation } from '@tanstack/react-query'
import { api } from '../api/client'

interface Props {
  driftIssue: string
  driftRange: string
}

/** Topbar pill: shown when code_updated_pending_restart is detected.
 *  Restarts the gateway and reloads the frontend bundle after 1.2s. */
export function DriftUpdatePill({ driftIssue, driftRange }: Props) {
  const restart = useMutation({
    mutationFn: api.restartGateway,
    onSuccess: () => {
      window.setTimeout(() => window.location.reload(), 1200)
    },
  })

  if (!driftIssue) return null

  return (
    <button
      type="button"
      className="runtime-reload-pill mono"
      onClick={() => restart.mutate()}
      disabled={restart.isPending}
      title={`Code drift detected (${driftRange}). Click to restart gateway and reload frontend.`}
    >
      <span className="dot dot-yellow" aria-hidden="true" />
      <span>{restart.isPending ? 'Restarting…' : 'Code Updated · Reload'}</span>
    </button>
  )
}

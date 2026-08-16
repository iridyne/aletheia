import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function StatusDiagnostics() {
  const { data, isPending, error } = useQuery({ queryKey: ['status-diagnostics'], queryFn: api.getHealth, staleTime: 15_000 })
  const restart = useMutation({ mutationFn: api.restartGateway })
  if (isPending) return <span className="topbar-err">checking…</span>
  if (error) return <span className="topbar-err">not available</span>
  return <span className="mono" title={(data?.issues || []).join(', ')}>{data?.status || 'unknown'} <button className="topbar-btn secondary" onClick={() => restart.mutate()} disabled={restart.isPending}>{restart.isPending ? '…' : 'Restart'}</button></span>
}

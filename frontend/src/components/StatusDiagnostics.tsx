import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import { safeErrorText } from '../lib/mutationErrors'

export default function StatusDiagnostics() {
  const qc = useQueryClient()
  const [errorMessage, setErrorMessage] = useState('')
  const { data, isPending, error } = useQuery({ queryKey: ['status-diagnostics'], queryFn: api.getHealth, staleTime: 15_000 })
  const restart = useMutation({
    mutationFn: api.restartGateway,
    onMutate: () => setErrorMessage(''),
    onSuccess: async () => { await qc.refetchQueries({ queryKey: ['status-diagnostics'] }, { throwOnError: true }) },
    onError: (value: Error) => setErrorMessage(safeErrorText(value)),
  })
  if (isPending) return <span className="topbar-err">checking…</span>
  if (error) return <span className="topbar-err">not available</span>
  return <span className="mono" title={(data?.issues || []).join(', ')}>{data?.status || 'unknown'} <button className="topbar-btn secondary" onClick={() => restart.mutate()} disabled={restart.isPending}>{restart.isPending ? '…' : 'Restart'}</button>{errorMessage && <span className="topbar-err" role="alert">{errorMessage}</span>}</span>
}

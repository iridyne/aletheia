export type DataState =
  | 'loading'
  | 'available'
  | 'empty'
  | 'stale'
  | 'partial'
  | 'unavailable'
  | 'not-configured'
  | 'permission-limited'

const DEFAULT_COPY: Record<Exclude<DataState, 'available'>, string> = {
  loading: 'Loading data…',
  empty: 'No data.',
  stale: 'Data is stale. Retained rows may not reflect the latest read.',
  partial: 'Data is partial. Available rows remain visible.',
  unavailable: 'Data unavailable. The read failed; this is not an empty result.',
  'not-configured': 'Data is not configured.',
  'permission-limited': 'Data is unavailable with the current permission.',
}

export function DataStateMessage({
  state,
  message,
}: {
  state: Exclude<DataState, 'available'>
  message?: string
}) {
  return (
    <p
      className={`data-state data-state-${state}`}
      role={state === 'unavailable' ? 'alert' : 'status'}
    >
      {message ?? DEFAULT_COPY[state]}
    </p>
  )
}

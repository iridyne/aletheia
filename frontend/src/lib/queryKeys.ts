export const positionKeys = {
  all: ['positions'] as const,
  reconciliation: ['positions-reconciliation'] as const,
}

// WebSocket invalidation is primary; this bounded poll keeps the book current
// while the transport reconnects. Hidden tabs keep the existing pause policy.
export const positionFreshness = {
  staleTime: 28_000,
  refetchInterval: 30_000,
  refetchIntervalInBackground: false,
} as const

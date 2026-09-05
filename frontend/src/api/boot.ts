import { fetchJson } from './client'
import type { components } from './generated/api'

export type BootstrapAvailability = 'available' | 'unavailable'

// Static contract assertion note: operator?: { permission_mode?: string }
export type ShellBootstrap = components['schemas']['ShellBootstrapResponse']
export type OverviewBootstrap = components['schemas']['OverviewBootstrapResponse']

export const bootApi = {
  getShellBootstrap: ({ signal }: { signal?: AbortSignal } = {}) =>
    fetchJson<ShellBootstrap>('/ui/shell-bootstrap', { signal }),
  getOverviewBootstrap: ({ signal }: { signal?: AbortSignal } = {}) =>
    fetchJson<OverviewBootstrap>('/ui/overview-bootstrap', { signal }),
}

import type { SystemStatus } from '../../types'
import type { SettingsData } from '../../api/settings'

export interface SettingsOperations {
  /** Invalidate or refetch settings queries after mutation */
  refreshSettings: () => Promise<void> | void
  /** Invalidate or refetch system status after permission/exchange changes */
  refreshStatus: () => Promise<void> | void
  /** General cache/data refresh trigger for domain entities (model, risk-boundaries, etc.) */
  refreshQuery: (queryKey: string | readonly string[]) => Promise<void> | void
  /** Explicit query refetcher with error propagation (for serialized state steps) */
  refetchStatus?: () => Promise<unknown>
}

export type SettingsProps = {
  settings?: SettingsData
  ops?: SettingsOperations
}

export type SettingsStatusProps = SettingsProps & {
  status: Partial<SystemStatus>
}

export type SettingsDirtyProps = SettingsProps & {
  onDirty?: (id: string, isDirty: boolean) => void
}

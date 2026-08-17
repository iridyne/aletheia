import type { QueryClient } from '@tanstack/react-query'
import type { SystemStatus } from '../../types'
import type { SettingsData } from '../../api/settings'

export type SettingsProps = {
  settings?: SettingsData
  qc: QueryClient
}

export type SettingsStatusProps = SettingsProps & {
  status: Partial<SystemStatus>
}

export type SettingsDirtyProps = SettingsProps & {
  onDirty?: (id: string, isDirty: boolean) => void
}

import type { QueryClient } from '@tanstack/react-query'
import type { SettingsOperations } from './types'

export function createSettingsOperations(qc: QueryClient): SettingsOperations {
  return {
    refreshSettings: async () => {
      await qc.invalidateQueries({ queryKey: ['settings'] })
    },
    refreshStatus: async () => {
      await qc.invalidateQueries({ queryKey: ['status'] })
    },
    refreshQuery: async (key: string | string[]) => {
      const queryKey = Array.isArray(key) ? key : [key]
      await qc.invalidateQueries({ queryKey })
    },
  }
}
